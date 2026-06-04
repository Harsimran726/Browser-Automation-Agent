from __future__ import annotations

import logging
import re
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from actions import navigate_to
from agent_loop import run_agent
from config import configuration, setup_logging


def _extract_start_url(task: str, fallback_url: str) -> str:
    if fallback_url:
        return fallback_url

    url_match = re.search(r"https?://[^\s)]+", task)
    if url_match:
        return url_match.group(0).rstrip(".,")

    domain_match = re.search(r"\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s)]*)?", task, re.IGNORECASE)
    if domain_match:
        candidate = domain_match.group(0).rstrip(".,")
        if candidate.startswith("www."):
            return f"https://{candidate}"
        return f"https://{candidate}"

    return "about:blank"


def _load_task() -> str:
    # # task_path = Path("task.text")
    # if task_path.exists():
    #     content = task_path.read_text(encoding="utf-8").strip()
    #     if content:
    #         return content

    user_query = input("Enter your task (or press Enter to use the default task): ").strip()
    if user_query:
        return user_query

    return (
        "Go to books.toscrape.com. Find the book called 'A Light in the Attic'. "
        "Click on it to open the product page. Read and remember its price. When you have found the price, "
        "your task is complete. Do not click Add to basket or anything else after finding the price."
    )


def main() -> None:
    load_dotenv()
    config = configuration()
    log_path = setup_logging(config.log_dir)
    logger = logging.getLogger(__name__)
    print(f"-----ENTERING THE BROWSER AGENT MODE-----")
    task = _load_task()
    start_url = _extract_start_url(task, config.start_url)
    logger.info("Loaded task: %s", task)
    logger.info("Resolved start URL: %s", start_url)
    logger.info("Writing run log to: %s", log_path)
    print(f"----URL GIVEN BY USER: {start_url}")
    print(f"----OPENNING THE BROWSER")
    with sync_playwright() as playwright:
        logger.info("Launching Chromium browser")
        browser = playwright.chromium.launch(
            headless=config.headless,
            slow_mo=config.browser_slow_mo_ms,
            args=["--start-maximized"],
        )

        context = browser.new_context(
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            user_agent=config.user_agent,
        )
        page = context.new_page()
        logger.info("Navigating to start URL")
        # print(f"----NAVIGATING TO THE START URL")
        navigate_to(
            page,
            start_url,
            timeout_ms=config.navigation_timeout_ms,
            stability_delay_seconds=config.stability_delay_seconds,
        )

        run_agent(page, task, config, logger=logger)

        logger.info("Closing browser")
        browser.close()


if __name__ == "__main__":
    main()

