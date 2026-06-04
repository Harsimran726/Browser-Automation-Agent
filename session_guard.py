from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


@dataclass(slots=True)
class SessionCheck:
	blocked: bool = False
	reason: str = ""
	kind: str = ""
	needs_login: bool = False
	credentials_used: bool = False


AUTH_URL_MARKERS = ["login", "signin", "sign-in", "auth", "oauth", "sso", "mfa", "account"]
AUTH_TEXT_MARKERS = [
	"session expired",
	"sign in",
	"log in",
	"password",
	"two-factor",
	"verification code",
	"continue with",
]


def extract_credentials(task: str) -> dict[str, str]:
	patterns = [
		r"login\s+with\s+(?:this|these)?\s*details\s*([^,\n]+)\s*,\s*([^,\n]+)",
		r"login\s+with\s+credentials\s*([^,\n]+)\s*,\s*([^,\n]+)",
		r"username\s*[:=]\s*([^,\n]+)\s*,\s*password\s*[:=]\s*([^,\n]+)",
		r"log\s*in\s+with\s+([^,\n]+)\s*,\s*([^,\n]+)",
	]
	for pattern in patterns:
		match = re.search(pattern, task, re.IGNORECASE)
		if match:
			return {"username": match.group(1).strip(), "password": match.group(2).strip()}

	return {}


def _page_text(page: Page) -> str:
	try:
		return page.locator("body").inner_text(timeout=1500).lower()
	except Exception:
		return ""


def check_session_health(page: Page) -> SessionCheck:
	url = page.url.lower()
	if any(marker in url for marker in AUTH_URL_MARKERS):
		return SessionCheck(blocked=True, kind="auth", needs_login=True, reason=f"Authentication URL detected: {page.url}")

	text = _page_text(page)
	if any(marker in text for marker in AUTH_TEXT_MARKERS):
		return SessionCheck(blocked=True, kind="auth", reason="Authentication or expired-session content detected")

	try:
		if page.get_by_label("Password").count() > 0 or page.get_by_placeholder("Password").count() > 0:
			return SessionCheck(blocked=True, kind="auth", needs_login=True, reason="Password field detected")
	except Exception:
		pass

	return SessionCheck()


def _fill_field(page: Page, candidates: list[str], value: str) -> bool:
	for candidate in candidates:
		try:
			locator = page.get_by_label(candidate)
			if locator.count() > 0:
				locator.first.fill(value)
				return True
		except Exception:
			pass

		try:
			locator = page.get_by_placeholder(candidate)
			if locator.count() > 0:
				locator.first.fill(value)
				return True
		except Exception:
			pass

		try:
			locator = page.get_by_role("textbox", name=re.compile(candidate, re.IGNORECASE))
			if locator.count() > 0:
				locator.first.fill(value)
				return True
		except Exception:
			pass

	return False


def _fill_first_visible_input(page: Page, selector: str, value: str) -> bool:
	try:
		locator = page.locator(selector)
		if locator.count() > 0:
			for index in range(locator.count()):
				field = locator.nth(index)
				try:
					if field.is_visible(timeout=800):
						field.fill(value)
						return True
				except Exception:
					continue
	except Exception:
			pass

	return False


def _submit_login_form(page: Page) -> bool:
	selectors = [
		"button:has-text('Login')",
		"button:has-text('Log in')",
		"button:has-text('Sign in')",
		"input[type='submit']",
		"button[type='submit']",
	]
	for selector in selectors:
		try:
			locator = page.locator(selector)
			if locator.count() > 0:
				locator.first.click(timeout=1500)
				return True
		except Exception:
			continue

	try:
		page.keyboard.press("Enter")
		return True
	except Exception:
		return False


def try_login_fallback(page: Page, task: str) -> bool:
	credentials = extract_credentials(task)
	if not credentials:
		return False

	username = credentials.get("username", "")
	password = credentials.get("password", "")
	if not username or not password:
		return False

	filled_user = _fill_field(
		page,
		[
			"Username",
			"User name",
			"Email",
			"Email address",
			"Login",
			"User",
			"Admin",
			"Account",
		],
		username,
	)
	filled_password = _fill_field(
		page,
		["Password", "Current password"],
		password,
	)

	if not filled_user:
		filled_user = _fill_first_visible_input(page, "input[type='text'], input[type='email'], input:not([type]), input[name*='user' i], input[name*='email' i], input[name*='login' i]", username)

	if not filled_password:
		filled_password = _fill_first_visible_input(page, "input[type='password']", password)

	if not (filled_user and filled_password):
		return False

	_submit_login_form(page)
	try:
		page.wait_for_load_state("networkidle", timeout=8000)
	except Exception:
		pass

	return True
