from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
from pathlib import Path


@dataclass(slots=True)
class configuration:
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    popup_model: str = field(default_factory=lambda: os.getenv("OPENAI_POPUP_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")))
    api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    start_url: str = field(default_factory=lambda: os.getenv("START_URL", ""))
    max_steps: int = 30
    step_delay: float = 1.5
    screenshot_dir: Path = field(default_factory=lambda: Path("screenshots"))
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    viewport_width: int = 1380
    viewport_height: int = 800
    navigation_timeout_ms: int = 120000
    readiness_timeout_ms: int = 80000
    stability_delay_seconds: float = 0.5
    browser_slow_mo_ms: int = 200
    headless: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    def __post_init__(self) -> None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return log_path


