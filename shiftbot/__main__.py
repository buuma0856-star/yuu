"""コマンドライン:  python -m shiftbot <コマンド>

  serve                   LINE Bot を起動
  generate [--month 11]   シフトを自動作成
  show [--month 11]       シフト表を表示
  export [--month 11]     シフト表を CSV で保存
  publish [--month 11]    シフトを全員に LINE で送信
  remind [--month 11]     未提出者に LINE でリマインド
  staff                   スタッフ一覧
  admin <名前>            指定スタッフを管理者にする
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .bot import ShiftBot, parse_month_arg
from .config import load_config, load_settings
from .db import Database
from .line_api import LineClient
from .server import serve
from .service import ShiftService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shiftbot", description="LINE連携シフト自動作成ツール")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    for name in ("generate", "show", "export", "publish", "remind"):
        p = sub.add_parser(name)
        p.add_argument("--month", default="", help="例: 11 / 2026-11 (省略時は翌月)")
        if name == "export":
            p.add_argument("--out", default="")
    sub.add_parser("staff")
    p = sub.add_parser("admin")
    p.add_argument("name")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    config = load_config(settings.config_path)
    db = Database(settings.db_path)
    line = LineClient(settings.channel_access_token) if settings.channel_access_token else None
    service = ShiftService(db, config, line)

    if args.cmd == "serve":
        serve(ShiftBot(service, settings.admin_passcode), settings)
        return 0
    if args.cmd == "staff":
        for s in db.list_staff():
            print(f"{s['name']}\t{'管理者' if s['is_admin'] else ''}\t{s['user_id']}")
        return 0
    if args.cmd == "admin":
        matches = db.find_staff_by_name(args.name)
        if not matches:
            print(f"「{args.name}」さんが見つかりません", file=sys.stderr)
            return 1
        for s in matches:
            db.set_admin(s["user_id"], True)
        print(f"「{args.name}」さんを管理者にしました")
        return 0

    year, month = parse_month_arg(args.month, service)
    if args.cmd == "generate":
        print(service.summary_text(year, month, service.generate(year, month)))
    elif args.cmd == "show":
        print(service.table_text(year, month))
    elif args.cmd == "export":
        out = args.out or os.path.join("exports", f"shift-{year}-{month:02d}.csv")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(service.csv_text(year, month))
        print(f"保存しました: {out}")
    elif args.cmd in ("publish", "remind"):
        if not line:
            print("LINE_CHANNEL_ACCESS_TOKEN が未設定です", file=sys.stderr)
            return 1
        sent = service.publish(year, month) if args.cmd == "publish" else service.remind(year, month)
        print(f"{sent}名に送信しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
