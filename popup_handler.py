from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from playwright.sync_api import Page

from config import configuration


@dataclass(slots=True)
class PopupResult:
	handled: bool = False
	blocking: bool = False
	requires_confirmation: bool = False
	kind: str = ""
	reason: str = ""
	details: str = ""


POPUP_CATEGORIES = {"location_delivery", "cookie_consent", "transaction_confirmation", "unknown"}

SYSTEM_PROMPT = """You classify browser popups. Return exactly one JSON object with this schema:
{
	"category": "location_delivery" | "cookie_consent" | "transaction_confirmation" | "auth_security" | "irrelevant" | "unknown",
	"reason": "short explanation",
	"confidence": 0.0
}

Rules:
- location_delivery means a location, delivery, shipping, region, or nearby-store popup.
- cookie_consent means a cookie preferences or consent popup.
- transaction_confirmation means an irreversible action, financial transaction, purchase, order placement, deletion, or other dangerous confirmation.
- auth_security means a login wall, account lockout, CAPTCHA, session expiry, verification challenge, or security warning that blocks progress.
- irrelevant means the popup is not useful for the current task and can be safely dismissed or ignored.
- unknown means the popup does not clearly fit any of the above.
- Use the screenshot, page text, title, URL, visible buttons/labels, and the user task. Decide whether the popup helps the task or is just noise.
- Respond with JSON only."""


def _page_text(page: Page) -> str:
	try:
		return page.locator("body").inner_text(timeout=1500).lower()
	except Exception:
		return ""


def _page_title(page: Page) -> str:
	try:
		return page.title()
	except Exception:
		return ""


def _dialog_snapshot(page: Page) -> dict[str, Any]:
	selectors = ["[role='dialog']", "[aria-modal='true']", "dialog", ".modal", ".popup", ".overlay"]
	for selector in selectors:
		try:
			locator = page.locator(selector)
			if locator.count() > 0 and locator.first.is_visible(timeout=700):
				text = locator.first.inner_text(timeout=1000)
				buttons = []
				try:
					buttons = locator.first.locator("button, [role='button'], a").all_inner_texts()
				except Exception:
					buttons = []
				return {
					"selector": selector,
					"text": text,
					"buttons": buttons,
				}
		except Exception:
			continue
	return {"selector": "", "text": "", "buttons": []}


def _build_client(api_key: str | None) -> OpenAI:
	if not api_key:
		raise RuntimeError("OPENAI_API_KEY is not set")
	return OpenAI(api_key=api_key)


def _strip_json_response(raw_text: str) -> dict[str, Any]:
	cleaned = raw_text.strip()
	if cleaned.startswith("```"):
		cleaned = cleaned.split("```", 2)[1]
		if cleaned.startswith("json"):
			cleaned = cleaned[4:]
	return json.loads(cleaned)


def classify_popup(page: Page, config: configuration, logger: logging.Logger, task: str) -> dict[str, Any]:
	client = _build_client(config.api_key)
	dialog = _dialog_snapshot(page)
	user_text = f"""URL: {page.url}
Title: {_page_title(page)}

User task:
{task}

Visible page text:
{_page_text(page)[:4000]}

Popup text:
{dialog.get('text', '')[:3000]}

Visible popup buttons/labels:
{', '.join(dialog.get('buttons', [])[:20])}

Classify the popup."""

	response = client.chat.completions.create(
		model=config.popup_model,
		temperature=0,
		max_tokens=120,
		response_format={"type": "json_object"},
		messages=[
			{"role": "system", "content": SYSTEM_PROMPT},
			{"role": "user", "content": user_text},
		],
	)

	raw_text = response.choices[0].message.content or "{}"
	logger.info("Popup classifier raw response: %s", raw_text)
	try:
		classification = _strip_json_response(raw_text)
	except Exception:
		logger.exception("Failed to parse popup classifier response")
		classification = {"category": "unknown", "reason": "classifier parse failure", "confidence": 0.0}

	if classification.get("category") not in POPUP_CATEGORIES:
		classification["category"] = "unknown"

	return classification


def _click_first_visible(page: Page, selectors: list[str]) -> bool:
	for selector in selectors:
		locator = page.locator(selector)
		try:
			if locator.count() > 0 and locator.first.is_visible(timeout=800):
				locator.first.click(timeout=1500)
				return True
		except Exception:
			continue
	return False


def _press_escape(page: Page) -> bool:
	try:
		page.keyboard.press("Escape")
		return True
	except Exception:
		return False


def _dismiss_location_popup(page: Page) -> bool:
	selectors = [
		"button:has-text('Close')",
		"button:has-text('Dismiss')",
		"button[aria-label*='close' i]",
		"button[title*='close' i]",
		"[role='button'][aria-label*='close' i]",
	]
	if _click_first_visible(page, selectors):
		return True
	return _press_escape(page)


def _dismiss_cookie_popup(page: Page) -> bool:
	selectors = [
		"button:has-text('Accept')",
		"button:has-text('Accept all')",
		"button:has-text('I agree')",
		"button:has-text('Agree')",
		"button:has-text('Allow all')",
		"button:has-text('Got it')",
		"button:has-text('OK')",
		"button:has-text('Okay')",
	]
	return _click_first_visible(page, selectors)


def _dismiss_generic_popup(page: Page) -> bool:
	selectors = [
		"button:has-text('Not now')",
		"button:has-text('Skip')",
		"button:has-text('Continue')",
		"button:has-text('Close')",
		"button:has-text('Dismiss')",
		"button:has-text('No thanks')",
		"button[aria-label*='close' i]",
		"button[title*='close' i]",
		"[role='button'][aria-label*='close' i]",
	]
	if _click_first_visible(page, selectors):
		return True
	return _press_escape(page)


def _needs_human_confirmation(classification: dict[str, Any]) -> bool:
	return classification.get("category") == "transaction_confirmation"


def handle_popups(page: Page, config: configuration, logger: logging.Logger, task: str) -> PopupResult:
	modal_selectors = ["[role='dialog']", "[aria-modal='true']", "dialog", ".modal", ".popup", ".overlay"]
	modal_found = False
	for selector in modal_selectors:
		try:
			locator = page.locator(selector)
			if locator.count() > 0 and locator.first.is_visible(timeout=700):
				modal_found = True
				break
		except Exception:
			continue

	if not modal_found:
		return PopupResult()

	# One additional model call per popup to classify by intent before any gate logic.
	classification = classify_popup(page, config, logger, task)
	category = classification.get("category", "unknown")
	reason = classification.get("reason", "")

	if category == "location_delivery":
		if _dismiss_location_popup(page):
			return PopupResult(handled=True, kind=category, reason=reason or "Dismissed location or delivery popup")
		return PopupResult(handled=False, kind=category, reason=reason or "Location popup not dismissed")

	if category == "cookie_consent":
		if _dismiss_cookie_popup(page):
			return PopupResult(handled=True, kind=category, reason=reason or "Accepted cookie consent")
		return PopupResult(handled=False, kind=category, reason=reason or "Cookie popup not dismissed")

	if category == "irrelevant":
		if _dismiss_generic_popup(page):
			return PopupResult(handled=True, kind=category, reason=reason or "Dismissed irrelevant popup")
		return PopupResult(handled=False, kind=category, reason=reason or "Irrelevant popup could not be dismissed")

	if category == "auth_security":
		return PopupResult(blocking=True, requires_confirmation=True, kind=category, reason=reason or "Authentication or security wall detected")

	if _needs_human_confirmation(classification):
		return PopupResult(blocking=True, requires_confirmation=True, kind=category, reason=reason or "Popup requires human confirmation")

	if _dismiss_generic_popup(page):
		return PopupResult(handled=True, kind="unknown", reason=reason or "Dismissed non-critical popup")

	return PopupResult(handled=False, kind="unknown", reason=reason or "Popup left open because it was not clearly actionable")


def pre_session_popups(page: Page, config: configuration, logger: logging.Logger, task: str) -> PopupResult:
	return handle_popups(page, config, logger, task)
