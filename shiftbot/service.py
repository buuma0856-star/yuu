"""シフト作成・公開・リマインドなど、LINE と CLI で共通の処理。"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .config import format_day, min_to_time, patterns_in_minutes, requirements_for
from .db import Database, month_key
from .line_api import LineClient
from .scheduler import Availability, ScheduleResult, StaffInfo, generate_schedule, month_days


class ShiftService:
    def __init__(self, db: Database, config: dict, line: LineClient | None = None):
        self.db = db
        self.config = config
        self.line = line

    # --- 日付まわり ---
    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        tz = timezone(timedelta(hours=self.config.get("timezone_offset_hours", 9)))
        return datetime.now(tz)

    def collect_month(self) -> tuple[int, int]:
        """希望を集めている対象月 (既定は翌月)。"""
        t = self.today()
        if self.config.get("collect_month") == "current":
            return t.year, t.month
        return (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)

    @property
    def patterns(self) -> dict[str, tuple[int, int]]:
        return patterns_in_minutes(self.config)

    # --- シフト作成 ---
    def generate(self, year: int, month: int) -> ScheduleResult:
        days = month_days(year, month)
        staff = [
            StaffInfo(r["user_id"], r["name"], r["max_days_per_week"])
            for r in self.db.list_staff()
        ]
        avail = {
            (r["user_id"], date.fromisoformat(r["date"])): Availability(
                bool(r["available"]), r["start_min"], r["end_min"]
            )
            for r in self.db.get_availability(days[0], days[-1])
        }
        result = generate_schedule(
            days,
            staff,
            avail,
            self.patterns,
            lambda d: requirements_for(self.config, d),
            self.config["max_days_per_week"],
            self.config["max_hours_per_week"],
            self.config.get("max_shifts_per_day", 1),
            seed=year * 100 + month,
        )
        self.db.save_assignments(month_key(year, month), result.assignments)
        return result

    def summary_text(self, year: int, month: int, result: ScheduleResult) -> str:
        names = {r["user_id"]: r["name"] for r in self.db.list_staff()}
        lines = [f"【{year}年{month}月 シフト下書きを作成しました】"]
        lines.append(f"割り当て: {len(result.assignments)}件 / 人手不足: {len(result.shortages)}枠")
        if result.shortages:
            lines.append("\n▼人手不足の枠")
            for s in result.shortages:
                lines.append(f"{format_day(s.day)} {s.pattern} {s.filled}/{s.needed}人")
        lines.append("\n▼勤務時間")
        for uid, hours in sorted(result.hours.items(), key=lambda x: -x[1]):
            lines.append(f"{names.get(uid, uid)}: {hours:g}時間")
        lines.append("\n「シフト表」で内容確認、「公開」でスタッフ全員にLINEで送信します。")
        return "\n".join(lines)

    # --- 表示・出力 ---
    def _grouped(self, year: int, month: int) -> dict[date, dict[str, list[str]]]:
        grouped: dict[date, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for r in self.db.get_assignments(month_key(year, month)):
            grouped[date.fromisoformat(r["date"])][r["pattern"]].append(r["name"] or r["user_id"])
        return grouped

    def table_text(self, year: int, month: int) -> str:
        key = month_key(year, month)
        grouped = self._grouped(year, month)
        if not grouped:
            return f"{year}年{month}月のシフトはまだ作成されていません。「シフト作成」と送ってください。"
        status = "公開済み" if self.db.is_published(key) else "下書き"
        lines = [f"【{year}年{month}月 シフト表({status})】"]
        for day in month_days(year, month):
            req = requirements_for(self.config, day)
            if not req:
                continue
            lines.append(format_day(day))
            for pattern, needed in req.items():
                if needed <= 0:
                    continue
                members = grouped.get(day, {}).get(pattern, [])
                short = f" (不足{needed - len(members)})" if len(members) < needed else ""
                lines.append(f"  {pattern}: {'、'.join(members) or '-'}{short}")
        return "\n".join(lines)

    def my_shift_text(self, user_id: str, year: int, month: int) -> str:
        key = month_key(year, month)
        if not self.db.is_published(key):
            return f"{year}年{month}月のシフトはまだ公開されていません。"
        rows = self.db.get_assignments(key, user_id)
        if not rows:
            return f"{year}年{month}月のシフトはありません。"
        patterns = self.patterns
        lines = [f"【{year}年{month}月 あなたのシフト】"]
        for r in rows:
            s, e = patterns.get(r["pattern"], (0, 0))
            lines.append(
                f"{format_day(date.fromisoformat(r['date']))} {r['pattern']} "
                f"{min_to_time(s)}-{min_to_time(e)}"
            )
        return "\n".join(lines)

    def csv_text(self, year: int, month: int) -> str:
        """日付を行、スタッフを列にしたシフト表 (Excel でそのまま開ける)。"""
        staff = self.db.list_staff()
        cell: dict[tuple[date, str], list[str]] = defaultdict(list)
        for r in self.db.get_assignments(month_key(year, month)):
            cell[(date.fromisoformat(r["date"]), r["user_id"])].append(r["pattern"])
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["日付"] + [s["name"] for s in staff])
        for day in month_days(year, month):
            writer.writerow(
                [format_day(day)] + ["/".join(cell.get((day, s["user_id"]), [])) for s in staff]
            )
        return "﻿" + out.getvalue()  # Excel 用 BOM

    # --- 提出状況・連絡 ---
    def unsubmitted(self, year: int, month: int) -> list:
        days = month_days(year, month)
        submitted = {r["user_id"] for r in self.db.get_availability(days[0], days[-1])}
        return [s for s in self.db.list_staff() if s["user_id"] not in submitted]

    def remind(self, year: int, month: int) -> int:
        if not self.line:
            return 0
        sent = 0
        for s in self.unsubmitted(year, month):
            ok = self.line.push(
                s["user_id"],
                f"{s['name']}さん、{month}月のシフト希望がまだ届いていません。\n"
                "このトークに送ってください。\n例) 11/3 9-17\n　　11/5 休み\n"
                "書き方は「ヘルプ」と送ると確認できます。",
            )
            sent += ok
        return sent

    def publish(self, year: int, month: int) -> int:
        key = month_key(year, month)
        self.db.set_published(key)
        if not self.line:
            return 0
        sent = 0
        for s in self.db.list_staff():
            if self.db.get_assignments(key, s["user_id"]):
                text = "シフトが確定しました。\n\n" + self.my_shift_text(s["user_id"], year, month)
            else:
                text = f"{year}年{month}月のシフトが確定しました。今月の出勤はありません。"
            sent += self.line.push(s["user_id"], text)
        return sent

    def notify_admins(self, text: str) -> None:
        if not self.line:
            return
        for a in self.db.admins():
            self.line.push(a["user_id"], text)

    def run_scheduled_reminder(self) -> bool:
        """設定日時になったら未提出者へ自動リマインド (1日1回)。"""
        conf = self.config.get("reminder", {})
        if not conf.get("enabled"):
            return False
        now = self.now()
        if now.day not in conf.get("days", []) or now.hour < conf.get("hour", 10):
            return False
        today = now.date().isoformat()
        if self.db.get_meta("last_reminder") == today:
            return False
        self.db.set_meta("last_reminder", today)
        year, month = self.collect_month()
        sent = self.remind(year, month)
        self.notify_admins(f"[自動] {month}月分の未提出者 {sent}名にリマインドを送りました。")
        return True
