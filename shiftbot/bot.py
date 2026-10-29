"""LINE メッセージの読み取りと自動返信。"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date

from .config import format_day, min_to_time, time_to_min
from .parser import normalize, parse_availability, parse_dates_only
from .scheduler import month_days
from .service import ShiftService

log = logging.getLogger(__name__)

STAFF_HELP = """【シフト希望の送り方】
日付と時間をそのまま送ってください。
例)
11/3 9-17
11/5 休み
11/7, 11/8 終日
11/10〜11/12 早番
15日 18時から
20日 15時まで

※送らなかった日は「出勤できない日」として扱います。
※同じ日をもう一度送ると上書きされます。

【その他のコマンド】
確認 … 提出した希望を確認
取消 11/3 … その日の希望を削除
シフト … 確定したシフトを確認
登録 名前 … 表示名を変更"""

ADMIN_HELP = """【管理者コマンド】
シフト作成 [11月] … 希望から自動でシフト作成
シフト表 [11月] … 作成したシフトを表示
公開 [11月] … 全員にLINEでシフトを送信
未提出 [11月] … 希望を出していない人を表示
催促 [11月] … 未提出者にLINEでリマインド
スタッフ … 登録スタッフ一覧
退職 名前 … スタッフを一覧から外す"""


def parse_month_arg(arg: str, service: ShiftService) -> tuple[int, int]:
    arg = unicodedata.normalize("NFKC", arg)
    if m := re.search(r"(\d{4})[-/年](\d{1,2})", arg):
        return int(m.group(1)), int(m.group(2))
    if m := re.search(r"(\d{1,2})", arg):
        month = int(m.group(1))
        if 1 <= month <= 12:
            today = service.today()
            year = today.year + (1 if month < today.month - 6 else 0)
            return year, month
    return service.collect_month()


class ShiftBot:
    def __init__(self, service: ShiftService, admin_passcode: str = ""):
        self.service = service
        self.db = service.db
        self.admin_passcode = admin_passcode

    # --- イベント処理 ---
    def handle_event(self, event: dict) -> None:
        user_id = event.get("source", {}).get("userId")
        reply_token = event.get("replyToken")
        if not user_id:
            return
        etype = event.get("type")
        reply = None
        if etype == "follow":
            reply = self._on_follow(user_id)
        elif etype == "message" and event.get("message", {}).get("type") == "text":
            reply = self.handle_text(user_id, event["message"]["text"])
        elif etype == "unfollow":
            self.db.set_active(user_id, False)
        if reply and reply_token and self.service.line:
            self.service.line.reply(reply_token, reply)

    def _on_follow(self, user_id: str) -> str:
        name = self._ensure_staff(user_id)
        store = self.service.config.get("store_name", "")
        return (
            f"{name}さん、友だち追加ありがとうございます！\n"
            f"{store}のシフト受付Botです。\n\n" + STAFF_HELP
        )

    def _ensure_staff(self, user_id: str) -> str:
        staff = self.db.get_staff(user_id)
        if staff and staff["active"]:
            return staff["name"]
        name = staff["name"] if staff else None
        if not name and self.service.line:
            profile = self.service.line.get_profile(user_id)
            name = (profile or {}).get("displayName")
        name = name or "スタッフ"
        self.db.upsert_staff(user_id, name)
        return name

    # --- テキスト処理 (自動返信の中身を返す) ---
    def handle_text(self, user_id: str, text: str) -> str:
        name = self._ensure_staff(user_id)
        staff = self.db.get_staff(user_id)
        is_admin = bool(staff["is_admin"])
        body = unicodedata.normalize("NFKC", text).strip()
        head, _, arg = body.partition(" ")
        if not arg:
            head, _, arg = body.partition("　")
        head, arg = head.strip(), arg.strip()

        if head in ("ヘルプ", "使い方", "help", "?"):
            return STAFF_HELP + ("\n\n" + ADMIN_HELP if is_admin else "")
        if head in ("登録", "名前") and arg:
            self.db.upsert_staff(user_id, arg)
            return f"お名前を「{arg}」で登録しました。"
        if head == "管理者登録":
            if self.admin_passcode and arg == self.admin_passcode:
                self.db.set_admin(user_id, True)
                return "管理者として登録しました。\n\n" + ADMIN_HELP
            return "パスコードが違います。"
        if head == "確認":
            return self._my_requests(user_id, name, arg)
        if head in ("取消", "取り消し", "削除"):
            return self._cancel(user_id, arg)
        if head in ("シフト", "シフト確認", "確定シフト") and not arg.startswith("作成"):
            year, month = self._published_month(arg)
            return self.service.my_shift_text(user_id, year, month)

        if is_admin:
            admin_reply = self._admin_command(head, arg)
            if admin_reply is not None:
                return admin_reply

        return self._submit(user_id, text)

    def _published_month(self, arg: str) -> tuple[int, int]:
        if arg:
            return parse_month_arg(arg, self.service)
        t = self.service.today()
        nxt = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
        if self.db.is_published(f"{nxt[0]:04d}-{nxt[1]:02d}"):
            return nxt
        return t.year, t.month

    def _submit(self, user_id: str, text: str) -> str:
        cfg = self.service.config
        year, month = self.service.collect_month()
        result = parse_availability(
            text,
            self.service.today(),
            year,
            month,
            self.service.patterns,
            time_to_min(cfg["day_start"]),
            time_to_min(cfg["day_end"]),
        )
        if not result.entries:
            msg = "すみません、日付が読み取れませんでした。\n例) 11/3 9-17\n「ヘルプ」で書き方を確認できます。"
            if result.warnings:
                msg = "\n".join(result.warnings) + "\n\n" + msg
            return msg
        for e in result.entries:
            self.db.set_availability(user_id, e.day, e.available, e.start, e.end)
        lines = ["希望を受け付けました。ありがとうございます！"]
        lines += [self._entry_text(e.day, e.available, e.start, e.end) for e in result.entries]
        if result.warnings:
            lines.append("")
            lines += [f"※{w}" for w in result.warnings]
        lines.append("\n間違いがあれば同じ日付で送り直してください。")
        return "\n".join(lines)

    @staticmethod
    def _entry_text(day: date, available: bool, start: int | None, end: int | None) -> str:
        if not available:
            status = "✕ 休み"
        elif start is None:
            status = "○ 終日OK"
        else:
            status = f"○ {min_to_time(start)}-{min_to_time(end)}"
        return f"{format_day(day)} {status}"

    def _my_requests(self, user_id: str, name: str, arg: str) -> str:
        year, month = parse_month_arg(arg, self.service) if arg else self.service.collect_month()
        days = month_days(year, month)
        rows = self.db.get_availability(days[0], days[-1], user_id)
        if not rows:
            return f"{name}さんの{month}月の希望はまだ届いていません。"
        lines = [f"【{name}さんの{month}月の希望】"]
        for r in rows:
            lines.append(
                self._entry_text(
                    date.fromisoformat(r["date"]), bool(r["available"]), r["start_min"], r["end_min"]
                )
            )
        return "\n".join(lines)

    def _cancel(self, user_id: str, arg: str) -> str:
        year, month = self.service.collect_month()
        days = parse_dates_only(normalize(arg), self.service.today(), year, month)
        if not days:
            return "取り消す日付を指定してください。例) 取消 11/3"
        removed = [d for d in days if self.db.delete_availability(user_id, d)]
        if not removed:
            return "指定した日の希望は登録されていませんでした。"
        return "次の日の希望を取り消しました。\n" + "\n".join(format_day(d) for d in removed)

    # --- 管理者コマンド ---
    def _admin_command(self, head: str, arg: str) -> str | None:
        svc = self.service
        if head in ("シフト作成", "作成") or (head == "シフト" and arg.startswith("作成")):
            year, month = parse_month_arg(arg, svc)
            result = svc.generate(year, month)
            return svc.summary_text(year, month, result)
        if head in ("シフト表", "一覧"):
            year, month = parse_month_arg(arg, svc)
            return svc.table_text(year, month)
        if head in ("公開", "配信", "確定"):
            year, month = parse_month_arg(arg, svc)
            if not self.db.get_assignments(f"{year:04d}-{month:02d}"):
                return f"{month}月のシフトがまだありません。先に「シフト作成」を送ってください。"
            sent = svc.publish(year, month)
            return f"{month}月のシフトを公開し、{sent}名にLINEで送信しました。"
        if head == "未提出":
            year, month = parse_month_arg(arg, svc)
            people = svc.unsubmitted(year, month)
            if not people:
                return f"{month}月の希望は全員提出済みです。"
            return f"{month}月の未提出者 ({len(people)}名)\n" + "\n".join(p["name"] for p in people)
        if head in ("催促", "リマインド"):
            year, month = parse_month_arg(arg, svc)
            sent = svc.remind(year, month)
            return f"{month}月分の未提出者 {sent}名にリマインドを送りました。"
        if head == "スタッフ":
            staff = self.db.list_staff()
            lines = [f"登録スタッフ ({len(staff)}名)"]
            lines += [f"{s['name']}{' (管理者)' if s['is_admin'] else ''}" for s in staff]
            return "\n".join(lines)
        if head == "退職" and arg:
            matches = self.db.find_staff_by_name(arg)
            if not matches:
                return f"「{arg}」さんは見つかりませんでした。"
            for s in matches:
                self.db.set_active(s["user_id"], False)
            return f"「{arg}」さんをスタッフ一覧から外しました。"
        return None
