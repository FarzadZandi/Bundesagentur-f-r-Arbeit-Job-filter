from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    contact_email: str = "replace-with-your-email@example.com"
    keywords_path: Path = Path("keywords_jobseeking_student_v1.json")
    cache_dir: Path = Path("cache")
    database_path: Path = Path("seen.sqlite")
    output_dir: Path = Path("out")
    min_delay: float = 2.5
    max_delay: float = 6.0
    long_pause_every: int = 25
    long_pause_min: float = 15.0
    long_pause_max: float = 30.0
    position_delay_min: float = 0.5
    position_delay_max: float = 2.0
    position_batch_size_min: int = 90
    position_batch_size_max: int = 100
    position_batch_pause_min: float = 45.0
    position_batch_pause_max: float = 60.0
    max_retries: int = 3
    request_timeout: float = 30.0
    max_ads: int = 500
    max_pages: int = 0
    arbeitsagentur_use_api: bool = True
    thresholds: dict[str, int] = field(default_factory=lambda: {"priority": 12, "review": 6})
    user_agents: tuple[str, ...] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )

    @classmethod
    def load(cls, path: str | Path = "config.yaml", **overrides: Any) -> "Config":
        config_path = Path(path).expanduser().resolve()
        raw: dict[str, Any] = {}
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        allowed = {item.name for item in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        raw.update({key: value for key, value in overrides.items() if value is not None})
        base = config_path.parent
        for key in ("keywords_path", "cache_dir", "database_path", "output_dir"):
            if key in raw:
                value = Path(raw[key]).expanduser()
                raw[key] = value if value.is_absolute() else (base / value).resolve()
        if "user_agents" in raw:
            raw["user_agents"] = tuple(raw["user_agents"])
        result = cls(**raw)
        result.validate()
        return result

    def validate(self) -> None:
        if not self.contact_email or "@" not in self.contact_email:
            raise ValueError("contact_email must be a real contact email")
        if self.min_delay < 2.5 or self.max_delay < self.min_delay:
            raise ValueError("delays must satisfy 2.5 <= min_delay <= max_delay")
        if self.long_pause_every < 1:
            raise ValueError("long_pause_every must be positive")
        if self.position_delay_min < 0 or self.position_delay_max < self.position_delay_min:
            raise ValueError("position delays must satisfy 0 <= position_delay_min <= position_delay_max")
        if self.position_batch_size_min < 1 or self.position_batch_size_max < self.position_batch_size_min:
            raise ValueError("position batch sizes must satisfy 1 <= position_batch_size_min <= position_batch_size_max")
        if self.position_batch_pause_min < 0 or self.position_batch_pause_max < self.position_batch_pause_min:
            raise ValueError(
                "position batch pauses must satisfy 0 <= position_batch_pause_min <= position_batch_pause_max"
            )
        if not 0 <= self.max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if self.max_ads < 0:
            raise ValueError("max_ads must be zero (unlimited) or positive")
        if self.max_pages < 0:
            raise ValueError("max_pages must be zero (unlimited) or positive")
        if not self.user_agents:
            raise ValueError("at least one user agent is required")
