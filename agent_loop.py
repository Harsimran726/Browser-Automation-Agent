from __future__ import annotations

import logging
import json
import time
from typing import Any

from openai import OpenAI
from playwright.sync_api import Page

from actions import (
	click_element,
	encode_image_to_base64,
	get_accessibility_snapshot,
	navigate_to,
	scroll_down,
	summarize_accessibility_snapshot,
	take_screenshot,
	type_into_field,
	wait_for_page_ready,
)
from confirmation_gate import confirmation_gate
from config import configuration
from layout_waiter import wait_for_page_ready as wait_for_layout_ready
from popup_handler import handle_popups, pre_session_popups
from session_guard import check_session_health, try_login_fallback


SYSTEM_PROMPT = """
You are a precise browser automation agent. Your only job is to look at a 
screenshot and accessibility summary, then return the single best next action 
to make progress on the task.

─────────────────────────────────────────
OUTPUT FORMAT — return exactly this JSON:
─────────────────────────────────────────
{
  "action":        "click" | "type" | "scroll" | "navigate" | "done" | "stuck",
  "target":        "exact visible text, label, placeholder, or full URL",
  "text":          "only populate this for type actions, empty string otherwise",
  "reasoning":     "what you see + why this action + what you expect to happen next",
  "task_complete": true | false,
  "confidence":    "high" | "medium" | "low"
}

─────────────────────────────────────────
ACTION RULES
─────────────────────────────────────────
click    → buttons, links, checkboxes, dropdowns, tabs, icons with labels.
           Target must be the exact visible text or ARIA label of the element.
           Never target an element you cannot see in the screenshot.

type     → any input field, search bar, textarea.
           Always click the field first in the previous step, then type.
           Target is the field label or placeholder text.
           Text is exactly what to type — nothing more.

scroll   → only when you can see the page is cut off, or content you need
           is likely below the fold based on page structure.
           Do not scroll if the target is already visible.

navigate → only for a full absolute URL (https://...).
           Use this to recover from wrong pages or to start a new section.
           Do not use navigate when a click on a link would achieve the same.

done     → only when you have verifiable proof the task is complete.
           A success message, a confirmation page, or a visible state change
           that directly corresponds to the task goal counts as proof.
           Do not use done based on assumption.

stuck    → use when: CAPTCHA is present, 2FA is required, you have been
           redirected to a login page without credentials, the same action
           has failed twice in a row, or the page state is unrecognisable.
           In reasoning, describe exactly what is blocking progress.

─────────────────────────────────────────
PRIORITY ORDER FOR FINDING TARGETS
─────────────────────────────────────────
1. Accessibility label or ARIA label from the accessibility summary
2. Exact visible button or link text from the screenshot
3. Input placeholder text
4. Heading or nearby label text for unlabelled inputs
5. Extract all the text from the screenshot, and figure out the next step.
6. Make a plan to complete the goal of the user query and then proceed step by step.

Never use CSS classes, IDs, XPath, or coordinate guesses as targets.
If a target is not findable by any of the above, use stuck.

─────────────────────────────────────────
PAGE STATE RULES — CHECK BEFORE ACTING
─────────────────────────────────────────
- If a modal, popup, cookie banner, or overlay is present:
  handle it first before taking any task-related action.
  Dismiss it with click using the close/accept/decline button text.
  Do not interact with content underneath an overlay.

- If the page shows a loading spinner or skeleton screen:
  use scroll with reasoning "waiting for content to load" to signal a wait.
  Do not act on elements that appear to be placeholders.

- If you are on a login page and were not on one before:
  do not fill credentials unless they were explicitly given in the task.
  Use stuck immediately with reasoning explaining the redirect.

- If the last four actions were identical and page state has not changed:
  do not repeat the same action a third time. Use stuck.

─────────────────────────────────────────
REASONING FIELD — ALWAYS THREE PARTS
─────────────────────────────────────────
Write reasoning in this structure:
"I see [what is on screen]. I will [action + target]. This should [expected result]."

Example:
"I see the Amazon search bar at the top of the page with placeholder 
'Search Amazon.in'. I will type the product name into it. 
This should surface product results I can then filter."

Short vague reasoning like "clicking the button" is not acceptable.
Reasoning is your audit trail — make it useful.

─────────────────────────────────────────
HARD RULES — NEVER VIOLATE
─────────────────────────────────────────
- Never invent, guess, or hallucinate credentials, OTPs, or hidden values.
- Never target an element not visible in the current screenshot.
- If something releated to the authentication or login details user share then figure out username or mail id , password from the user query and try to fill the login form and submit it.
- Never use done unless the task outcome is visually confirmed on screen.
- Never produce any text outside the JSON object.
- try to use minimize steps, make a plan to complete the task.
- Never add markdown, code fences, or explanation around the JSON.
- if confirmation_gate triggers multiple time for same query or target then return stuck with reasoning "confirmation gate triggered multiple times for same action".
- If don't find the target object and you keep scrolling more than 5 times, return stuck with reasoning "cannot find target after scrolling".
- If cann't find the target object after scroll down, then try to scroll up to 5 times, if still cannot find the target, return stuck with reasoning "cannot find target after scrolling".
"""


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


def ask_openai_what_to_do(
	client: OpenAI,
	config: configuration,
	screenshot_path: str,
	task: str,
	action_history: list[dict[str, Any]],
	current_url: str,
	accessibility_summary: str,
	logger: logging.Logger,
) -> dict[str, Any]:
	history_text = ""
	if action_history:
		history_text = "\n\nActions taken so far:\n"
		for index, action in enumerate(action_history, 1):
			history_text += (
				f"  Step {index}: {action.get('action', '')} -> {action.get('target', '')} | "
				f"{action.get('reasoning', '')[:100]}\n"
			)

	user_text = f"""Current URL: {current_url}

Task to complete: {task}
{history_text}

Accessibility summary:
{accessibility_summary or 'No accessibility data available.'}

Choose the next single action."""

	response = client.chat.completions.create(
		model=config.model,
		temperature=0.1,
		max_tokens=600, # max_tokens is used for gpt 4o mini
		response_format={"type": "json_object"},
		messages=[
			{"role": "system", "content": SYSTEM_PROMPT},
			{
				"role": "user",
				"content": [
					{
						"type": "image_url",
						"image_url": {"url": f"data:image/png;base64,{encode_image_to_base64(screenshot_path)}"},
					},
					{"type": "text", "text": user_text},
				],
			},
		],
	)

	raw_text = response.choices[0].message.content or "{}"
	logger.info("Model raw response: %s", raw_text)
	try:
		decision = _strip_json_response(raw_text)
	except Exception:
		logger.exception("Failed to parse model response as JSON")
		decision = {
			"action": "stuck",
			"target": "",
			"text": "",
			"reasoning": "Model response could not be parsed as JSON",
			"task_complete": False,
		}

	if not isinstance(decision, dict):
		return {
			"action": "stuck",
			"target": "",
			"text": "",
			"reasoning": "Model returned an invalid payload",
			"task_complete": False,
		}

	return decision


def execute_action(page: Page, action: dict[str, Any], config: configuration) -> None:
	action_type = action.get("action", "")
	target = action.get("target", "")
	text = action.get("text", "")

	if action_type == "navigate":
		navigate_to(page, target, timeout_ms=config.navigation_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
		return

	if action_type == "click":
		if not click_element(page, target):
			raise RuntimeError(f"Could not click target: {target}")
		wait_for_page_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
		return

	if action_type == "type":
		if not type_into_field(page, target, text):
			raise RuntimeError(f"Could not type into field: {target}")
		return

	if action_type == "scroll":
		scroll_down(page)
		return


def run_agent(page: Page, task: str, config: configuration | None = None, logger: logging.Logger | None = None) -> None:
	config = config or configuration()
	logger = logger or logging.getLogger(__name__)
	client = _build_client(config.api_key)
	action_history: list[dict[str, Any]] = []
	login_attempted = False
    
	logger.info("Starting pre-session popup handling")
	pre_session_popups(page, config, logger, task)
	wait_for_page_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
	wait_for_layout_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
    # print(f"----STARTING PRE-SESSION POPUP CHECKS AND DISMISSALS")
	for step in range(1, config.max_steps + 1):
		logger.info("Starting step %s on URL %s", step, page.url)
		popup_state = handle_popups(page, config, logger, task)
		if popup_state.blocking:
			logger.warning("Blocking popup detected: %s", popup_state.reason)
			print(f"Blocking popup detected: {popup_state.reason}")
			break

		if popup_state.requires_confirmation:
			logger.warning("Confirmation popup detected: %s", popup_state.reason)
			print(f"Confirmation popup detected: {popup_state.reason}")
			break

		session_state = check_session_health(page)
		if session_state.blocked:
			if session_state.needs_login and not login_attempted:
				logger.info("Auth wall detected; attempting login fallback using task credentials")
				login_attempted = True
				if try_login_fallback(page, task):
					logger.info("Login fallback submitted; waiting for authenticated page state")
					wait_for_page_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
					wait_for_layout_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
					continue
				logger.warning("Login fallback could not fill or submit the form")
				print(f"Session blocked: {session_state.reason}")
				break

			logger.warning("Session blocked: %s", session_state.reason)
			print(f"Session blocked: {session_state.reason}")
			break

		wait_for_page_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
		wait_for_layout_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)

		screenshot_path = take_screenshot(page, step, config.screenshot_dir)
		accessibility_summary = summarize_accessibility_snapshot(get_accessibility_snapshot(page))

		decision = ask_openai_what_to_do(
			client=client,
			config=config,
			screenshot_path=screenshot_path,
			task=task,
			action_history=action_history,
			current_url=page.url,
			accessibility_summary=accessibility_summary,
			logger=logger,
		)

		logger.info("Step %s decision: %s -> %s", step, decision.get('action', '?'), decision.get('target', ''))
		logger.info("Step %s reasoning: %s", step, decision.get('reasoning', ''))
		print(f"Step {step}: {decision.get('action', '?')} -> {decision.get('target', '')}")
		print(f"Reason: {decision.get('reasoning', '')}")

		if decision.get("action") == "done" or decision.get("task_complete"):
			logger.info("Task complete at step %s", step)
			print("Task complete.")
			break

		if decision.get("action") == "stuck":
			logger.warning("Agent stuck at step %s: %s", step, decision.get('reasoning', ''))
			print(f"Agent stuck: {decision.get('reasoning', '')}")
			break

		if not confirmation_gate(decision):
			logger.warning("Action cancelled by user at step %s", step)
			print("Action cancelled by user.")
			break

		session_state = check_session_health(page)
		if session_state.blocked:
			if session_state.needs_login and not login_attempted:
				logger.info("Auth wall detected before execution; attempting login fallback using task credentials")
				login_attempted = True
				if try_login_fallback(page, task):
					logger.info("Login fallback submitted before execution; waiting for authenticated page state")
					wait_for_page_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
					wait_for_layout_ready(page, timeout_ms=config.readiness_timeout_ms, stability_delay_seconds=config.stability_delay_seconds)
					continue
				logger.warning("Login fallback could not fill or submit the form before execution")
				print(f"Session blocked before execution: {session_state.reason}")
				break

			logger.warning("Session blocked before execution at step %s: %s", step, session_state.reason)
			print(f"Session blocked before execution: {session_state.reason}")
			break

		logger.info("Executing action at step %s: %s", step, decision.get('action', ''))
		execute_action(page, decision, config)
		action_history.append(
			{
				"step": step,
				"action": decision.get("action", ""),
				"target": decision.get("target", ""),
				"text": decision.get("text", ""),
				"reasoning": decision.get("reasoning", ""),
			}
		)

		post_action_popup = handle_popups(page, config, logger, task)
		if post_action_popup.blocking:
			logger.warning("Blocking popup after action at step %s: %s", step, post_action_popup.reason)
			print(f"Blocking popup after action: {post_action_popup.reason}")
			break

		if post_action_popup.requires_confirmation:
			logger.warning("Confirmation popup after action at step %s: %s", step, post_action_popup.reason)
			print(f"Confirmation popup after action: {post_action_popup.reason}")
			break

		time.sleep(config.step_delay)

	take_screenshot(page, config.max_steps + 1, config.screenshot_dir)
	logger.info("Run finished; final screenshot saved")
