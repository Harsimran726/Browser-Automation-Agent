from __future__ import annotations

import time

from playwright.sync_api import Page


def wait_for_layout_stable(page: Page, timeout_ms: int = 4000, stability_delay_seconds: float = 0.5) -> None:
	try:
		page.wait_for_function(
			"""
			() => {
				const selectors = [
					'[aria-busy="true"]',
					'[data-loading="true"]',
					'[role="progressbar"]',
					'.loading',
					'.skeleton',
					'.spinner'
				];
				return !selectors.some((selector) => document.querySelector(selector));
			}
			""",
			timeout=timeout_ms,
		)
	except Exception:
		pass

	time.sleep(stability_delay_seconds)


def wait_for_page_ready(page: Page, timeout_ms: int = 8000, stability_delay_seconds: float = 0.5) -> None:
	try:
		page.wait_for_load_state("networkidle", timeout=timeout_ms)
	except Exception:
		pass

	try:
		page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
	except Exception:
		pass

	wait_for_layout_stable(page, timeout_ms=timeout_ms, stability_delay_seconds=stability_delay_seconds)
