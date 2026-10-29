import base64
import hashlib
import hmac
import unittest
from datetime import date, datetime, timedelta, timezone

from shiftbot.bot import ShiftBot
from shiftbot.config import DEFAULT_CONFIG
from shiftbot.db import Database
from shiftbot.line_api import split_text, verify_signature
from shiftbot.service import ShiftService


class FakeLine:
    def __init__(self):
        self.replies, self.pushes = [], []

    def reply(self, token, text):
        self.replies.append((token, text))
        return True

    def push(self, user_id, text):
        self.pushes.append((user_id, text))
        return True

    def get_profile(self, user_id):
        return {"displayName": f"name-{user_id}"}


class FixedService(ShiftService):
    def now(self):
        return datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=9)))


class BotTest(unittest.TestCase):
    def setUp(self):
        self.line = FakeLine()
        self.service = FixedService(Database(":memory:"), dict(DEFAULT_CONFIG), self.line)
        self.bot = ShiftBot(self.service, admin_passcode="secret")

    def test_follow_registers_and_replies(self):
        self.bot.handle_event({"type": "follow", "replyToken": "t", "source": {"userId": "u1"}})
        self.assertEqual(self.service.db.get_staff("u1")["name"], "name-u1")
        self.assertIn("シフト希望の送り方", self.line.replies[0][1])

    def test_submission_auto_reply_and_confirm(self):
        reply = self.bot.handle_text("u1", "11/3 9-17\n11/5 休み")
        self.assertIn("受け付けました", reply)
        self.assertIn("11/3(火) ○ 09:00-17:00", reply)
        self.assertIn("11/5(木) ✕ 休み", reply)
        self.assertIn("11/3(火)", self.bot.handle_text("u1", "確認"))
        self.assertIn("取り消しました", self.bot.handle_text("u1", "取消 11/3"))
        self.assertNotIn("11/3(火)", self.bot.handle_text("u1", "確認"))

    def test_unreadable_message(self):
        self.assertIn("読み取れませんでした", self.bot.handle_text("u1", "こんにちは"))

    def test_admin_flow(self):
        self.assertIn("パスコードが違います", self.bot.handle_text("boss", "管理者登録 x"))
        self.assertIn("管理者として登録", self.bot.handle_text("boss", "管理者登録 secret"))
        self.bot.handle_text("u1", "登録 山田")
        self.bot.handle_text("u2", "登録 佐藤")
        self.bot.handle_text("u1", "11/2 終日")
        self.assertIn("佐藤", self.bot.handle_text("boss", "未提出"))

        summary = self.bot.handle_text("boss", "シフト作成 11月")
        self.assertIn("2026年11月", summary)
        self.assertIn("人手不足", summary)
        table = self.bot.handle_text("boss", "シフト表")
        self.assertIn("山田", table)

        self.assertIn("まだ公開されていません", self.bot.handle_text("u1", "シフト 11月"))
        self.assertIn("送信しました", self.bot.handle_text("boss", "公開"))
        pushed = dict(self.line.pushes)
        self.assertIn("11/2(月) 早番 09:00-14:00", pushed["u1"])
        self.assertIn("出勤はありません", pushed["u2"])
        self.assertIn("あなたのシフト", self.bot.handle_text("u1", "シフト"))

    def test_staff_cannot_use_admin_commands(self):
        self.assertIn("読み取れませんでした", self.bot.handle_text("u1", "シフト作成"))

    def test_scheduled_reminder_runs_once(self):
        self.service.config["reminder"] = {"enabled": True, "days": [20], "hour": 10}
        self.service.db.upsert_staff("u1", "山田")
        self.assertTrue(self.service.run_scheduled_reminder())
        self.assertFalse(self.service.run_scheduled_reminder())
        self.assertEqual(self.line.pushes[0][0], "u1")

    def test_csv_export(self):
        self.bot.handle_text("u1", "登録 山田")
        self.bot.handle_text("u1", "11/2 終日")
        self.service.generate(2026, 11)
        csv_text = self.service.csv_text(2026, 11)
        self.assertIn("日付,山田", csv_text)
        self.assertIn("11/2(月),早番", csv_text)


class LineApiTest(unittest.TestCase):
    def test_signature(self):
        body = b'{"events":[]}'
        sig = base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()
        self.assertTrue(verify_signature("sec", body, sig))
        self.assertFalse(verify_signature("sec", body, "bad"))

    def test_split_text(self):
        chunks = split_text("\n".join("あ" * 100 for _ in range(200)))
        self.assertTrue(all(len(c) <= 5000 for c in chunks))
        self.assertLessEqual(len(chunks), 5)


if __name__ == "__main__":
    unittest.main()
