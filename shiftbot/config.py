"""設定の読み込み (環境変数 / .env / config.json)。"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import date

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

DEFAULT_CONFIG = {
    "store_name": "お店",
    # シフトの種類 (名前: 開始〜終了)
    "shift_patterns": {
        "早番": {"start": "09:00", "end": "14:00"},
        "遅番": {"start": "14:00", "end": "22:00"},
    },
    # 1日に必要な人数。dates > 曜日 (mon..sun) > default の順で優先
    "requirements": {
        "default": {"早番": 2, "遅番": 2},
        "sat": {"早番": 3, "遅番": 3},
        "sun": {"早番": 3, "遅番": 3},
        "dates": {},
    },
    # 「18時から」「15時まで」のような指定で使う営業時間
    "day_start": "09:00",
    "day_end": "22:00",
    "max_days_per_week": 5,
    "max_hours_per_week": 40,
    "max_shifts_per_day": 1,
    # 希望を集める対象月: "next" (翌月) または "current" (今月)
    "collect_month": "next",
    # 未提出者への自動リマインド
    "reminder": {"enabled": True, "days": [15, 20], "hour": 10},
    "timezone_offset_hours": 9,
}


@dataclass
class Settings:
    channel_secret: str
    channel_access_token: str
    admin_passcode: str
    web_token: str
    db_path: str
    config_path: str
    host: str
    port: int


def load_dotenv(path: str = ".env") -> None:
    """簡易 .env ローダー (既に設定済みの環境変数は上書きしない)。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        channel_secret=os.environ.get("LINE_CHANNEL_SECRET", ""),
        channel_access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
        admin_passcode=os.environ.get("ADMIN_PASSCODE", ""),
        web_token=os.environ.get("WEB_TOKEN", ""),
        db_path=os.environ.get("SHIFTBOT_DB", "shiftbot.db"),
        config_path=os.environ.get("SHIFTBOT_CONFIG", "config.json"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and key not in (
            "shift_patterns",
            "requirements",
        ):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _merge(DEFAULT_CONFIG, json.load(f))
    return copy.deepcopy(DEFAULT_CONFIG)


def time_to_min(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def min_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def patterns_in_minutes(config: dict) -> dict[str, tuple[int, int]]:
    return {
        name: (time_to_min(p["start"]), time_to_min(p["end"]))
        for name, p in config["shift_patterns"].items()
    }


def requirements_for(config: dict, day: date) -> dict[str, int]:
    req = config["requirements"]
    key = day.isoformat()
    if key in req.get("dates", {}):
        return req["dates"][key]
    weekday = WEEKDAY_KEYS[day.weekday()]
    if weekday in req:
        return req[weekday]
    return req.get("default", {})


def format_day(day: date) -> str:
    return f"{day.month}/{day.day}({WEEKDAY_JA[day.weekday()]})"
