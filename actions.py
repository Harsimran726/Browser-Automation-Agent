from __future__ import annotations

import base64
import re
import time
from pathlib import Path

from playwright.sync_api import Locator, Page


def take_screenshot(page: Page, step: int, screenshot_dir: Path) -> str:
	screenshot_dir.mkdir(parents=True, exist_ok=True)
	path = screenshot_dir / f"step_{step:02d}.png"
	page.screenshot(path=str(path), full_page=False)
	return str(path)


def wait_for_page_ready(page: Page, timeout_ms: int = 8000, stability_delay_seconds: float = 0.5) -> None:
	try:
		page.wait_for_load_state("networkidle", timeout=timeout_ms)
	except Exception:
		pass

	try:
		page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
	except Exception:
		pass

	try:
		page.wait_for_function(
			"""
			() => {
				const blockers = [
					'[aria-busy="true"]',
					'[data-loading="true"]',
					'[role="progressbar"]',
					'.loading',
					'.skeleton',
					'.spinner'
				];
				return !blockers.some((selector) => document.querySelector(selector));
			}
			""",
			timeout=timeout_ms,
		)
	except Exception:
		pass

	time.sleep(stability_delay_seconds)


def navigate_to(page: Page, url: str, timeout_ms: int = 12000, stability_delay_seconds: float = 0.5) -> None:
	page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
	wait_for_page_ready(page, timeout_ms=timeout_ms, stability_delay_seconds=stability_delay_seconds)


def _click(locator: Locator, timeout_ms: int = 3000) -> bool:
	try:
		locator.first.scroll_into_view_if_needed(timeout=timeout_ms)
		locator.first.click(timeout=timeout_ms)
		return True
	except Exception:
		return False


def _click_nearest_actionable(page: Page, description: str) -> bool:
	escaped = description.replace("\\", "\\\\").replace("'", "\\'")
	return bool(
		page.evaluate(
			"""
			(description) => {
				const matches = Array.from(document.querySelectorAll('*')).filter((element) => {
					const text = (element.innerText || element.textContent || '').trim();
					return text && text.toLowerCase().includes(description.toLowerCase());
				});

				const isActionable = (element) => {
					if (!element) return false;
					const tag = element.tagName ? element.tagName.toLowerCase() : '';
					if (tag === 'a' || tag === 'button') return true;
					const role = element.getAttribute && element.getAttribute('role');
					return role === 'button' || role === 'link' || role === 'menuitem' || role === 'tab' || role === 'option';
				};

				for (const match of matches) {
					let current = match;
					while (current) {
						if (isActionable(current)) {
							current.scrollIntoView({ block: 'center', inline: 'center' });
							current.click();
							return true;
						}
						current = current.parentElement;
					}
				}

				return false;
			}
			""",
			escaped,
		)
	)


def _fill_nearest_input(page: Page, description: str, text: str) -> bool:
	escaped = description.replace("\\", "\\\\").replace("'", "\\'")
	escaped_text = text.replace("\\", "\\\\").replace("'", "\\'")
	return bool(
		page.evaluate(
			"""
			({ description, value }) => {
				const normalize = (input) => (input || '').trim().toLowerCase();
				const isVisible = (element) => {
					if (!element) return false;
					const style = window.getComputedStyle(element);
					const rect = element.getBoundingClientRect();
					return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
				};

				const candidates = Array.from(document.querySelectorAll('label, [aria-label], [placeholder], input, textarea, select, [contenteditable="true"]'));
				const target = normalize(description);

				const directInputs = candidates.filter((element) => {
					const text = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || element.getAttribute('placeholder') || element.name || element.id);
					return text && (text === target || text.includes(target) || target.includes(text));
				});

				const setNativeValue = (element, value) => {
					const prototype = Object.getPrototypeOf(element);
					const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value') || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value') || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
					if (descriptor && descriptor.set) {
						descriptor.set.call(element, value);
					} else {
						element.value = value;
					}
					element.dispatchEvent(new Event('input', { bubbles: true }));
					element.dispatchEvent(new Event('change', { bubbles: true }));
				};

				const fillElement = (element) => {
					if (!element) return false;
					if (element.matches('input, textarea, select, [contenteditable="true"]')) {
						if (!isVisible(element)) return false;
						element.focus();
						if (element.matches('[contenteditable="true"]')) {
							element.textContent = value;
						} else {
							setNativeValue(element, value);
						}
						return true;
					}

					const scope = element.closest('form, .form-group, .field, .input-group, .control, .form-control, .form-field, .input-container, .form-floating, div, section, article');
					const scopedInput = scope ? scope.querySelector('input:not([type="hidden"]), textarea, select, [contenteditable="true"]') : null;
					if (scopedInput && isVisible(scopedInput)) {
						scopedInput.focus();
						if (scopedInput.matches('[contenteditable="true"]')) {
							scopedInput.textContent = value;
						} else {
							setNativeValue(scopedInput, value);
						}
						return true;
					}

					return false;
				};

				for (const element of directInputs) {
					if (fillElement(element)) return true;
				}

				for (const label of Array.from(document.querySelectorAll('label'))) {
					const labelText = normalize(label.innerText || label.textContent);
					if (!labelText || (!labelText.includes(target) && !target.includes(labelText))) continue;

					const forId = label.getAttribute('for');
					if (forId) {
						const forElement = document.getElementById(forId);
						if (fillElement(forElement)) return true;
					}

					let sibling = label.parentElement && label.parentElement.querySelector('input:not([type="hidden"]), textarea, select, [contenteditable="true"]');
					if (fillElement(sibling)) return true;

					sibling = label.nextElementSibling;
					while (sibling) {
						if (fillElement(sibling)) return true;
						sibling = sibling.nextElementSibling;
					}
				}

				return false;
			}
			""",
			{"description": escaped, "value": escaped_text},
		)
	)


def _candidate_locators(page: Page, description: str) -> list[Locator]:
	pattern = re.compile(re.escape(description), re.IGNORECASE)
	return [
		page.get_by_role("button", name=pattern),
		page.get_by_role("link", name=pattern),
		page.get_by_role("menuitem", name=pattern),
		page.get_by_role("tab", name=pattern),
		page.get_by_role("option", name=pattern),
		page.get_by_role("checkbox", name=pattern),
		page.get_by_role("radio", name=pattern),
		page.get_by_text(pattern),
		page.get_by_label(pattern),
		page.locator("a, button, [role='button'], [role='link'], [role='menuitem'], [role='tab'], [role='option']").filter(has_text=pattern),
		page.locator(f"text={description}").first,
	]


def click_element(page: Page, description: str, timeout_ms: int = 3000) -> bool:
	attempts = _candidate_locators(page, description)
	plain_text = page.get_by_text(description, exact=False)
	attempts.append(plain_text)

	for locator in attempts:
		if _click(locator, timeout_ms=timeout_ms):
			return True

	if _click_nearest_actionable(page, description):
		return True

	return False


def type_into_field(page: Page, field_description: str, text: str, timeout_ms: int = 3000) -> bool:
	attempts = [
		page.get_by_label(field_description),
		page.get_by_placeholder(field_description),
		page.get_by_role("textbox", name=re.compile(re.escape(field_description), re.IGNORECASE)),
		page.get_by_role("textbox", name=field_description),
		page.locator(f'input[name="{field_description}"]'),
		page.locator(f'input[id="{field_description}"]'),
		page.locator(f'input[aria-label="{field_description}"]'),
		page.locator(f'textarea[name="{field_description}"]'),
		page.locator(f'textarea[id="{field_description}"]'),
		page.locator(f'textarea[aria-label="{field_description}"]'),
	]

	for locator in attempts:
		try:
			locator.first.scroll_into_view_if_needed(timeout=timeout_ms)
			locator.first.click(timeout=timeout_ms)
			locator.first.fill(text, timeout=timeout_ms)
			return True
		except Exception:
			continue

	if _fill_nearest_input(page, field_description, text):
		return True

	try:
		generic_inputs = page.locator("input:not([type='hidden']), textarea, [contenteditable='true']")
		for index in range(generic_inputs.count()):
			field = generic_inputs.nth(index)
			if field.is_visible(timeout=500):
				field.scroll_into_view_if_needed(timeout=timeout_ms)
				field.click(timeout=timeout_ms)
				field.fill(text, timeout=timeout_ms)
				return True
	except Exception:
		pass

	return False


def scroll_down(page: Page) -> None:
	page.evaluate("window.scrollBy(0, window.innerHeight)")
	time.sleep(0.5)


def get_accessibility_snapshot(page: Page):
	try:
		return page.accessibility.snapshot(interesting_only=True)
	except Exception:
		return None


def summarize_accessibility_snapshot(snapshot, max_nodes: int = 40) -> str:
	if not snapshot:
		return ""

	lines: list[str] = []
	interesting_roles = {"button", "link", "textbox", "checkbox", "radio", "combobox", "dialog", "heading", "menuitem"}

	def walk(node, depth: int = 0) -> None:
		if len(lines) >= max_nodes:
			return

		role = node.get("role")
		name = node.get("name")
		if role in interesting_roles or role == "text":
			indent = "  " * depth
			label = name or ""
			lines.append(f"{indent}- {role}: {label}".rstrip())

		for child in node.get("children", []) or []:
			walk(child, depth + 1)

	walk(snapshot)
	return "\n".join(lines)


def encode_image_to_base64(image_path: str) -> str:
	with open(image_path, "rb") as file_handle:
		return base64.b64encode(file_handle.read()).decode("utf-8")
