"""LINEで届いた希望シフトの文章を読み取る。

対応する書き方の例:
    11/3 9-17
    11月5日 休み
    11/7, 11/8 終日
    11/10〜11/12 早番
    15日 18時から
    20日 15時まで
    2026-11-25 ×
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

DASH = r"(?:~|〜|ー|-|−|―|‐|から)"

OFF_WORDS = [
    "出勤不可", "出れません", "入れません", "お休み", "休み", "不可", "無理", "ムリ",
    "だめ", "ダメ", "NG", "ng", "×", "✕", "✖", "休",
]
ANY_WORDS = [
    "いつでも", "一日中", "出勤可", "出れます", "入れます", "行けます", "大丈夫",
    "終日", "全日", "フル", "可能", "OK", "ok", "Ok", "○", "〇", "◯", "可",
]

WEEKDAY_NOTE = re.compile(r"\((?:[月火水木金土日祝](?:曜日?)?)\)")


@dataclass
class Entry:
    day: date
    available: bool
    start: int | None = None  # 分 (09:00 -> 540)
    end: int | None = None


@dataclass
class ParseResult:
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return WEEKDAY_NOTE.sub("", text)


def _hm(hour: str, minute: str | None) -> int:
    if minute == "半":
        return int(hour) * 60 + 30
    return int(hour) * 60 + (int(minute) if minute else 0)


def _build_token_re(pattern_names: list[str]) -> re.Pattern:
    words = lambda ws: "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True))
    hm = r"(?P<{0}h>\d{{1,2}})(?:[:時](?P<{0}m>\d{{2}}|半)?分?)?"
    parts = [
        r"(?P<iso>(?P<iy>\d{4})[-/年](?P<im>\d{1,2})[-/月](?P<id>\d{1,2})日?)",
        rf"(?P<drange>(?P<r1m>\d{{1,2}})[/月](?P<r1d>\d{{1,2}})日?\s*{DASH}\s*"
        r"(?:(?P<r2m>\d{1,2})[/月](?P<r2d>\d{1,2})日?|(?P<r2dd>\d{1,2})日))",
        rf"(?P<ddrange>(?P<s1>\d{{1,2}})日\s*{DASH}\s*(?P<s2>\d{{1,2}})日)",
        r"(?P<date>(?P<m>\d{1,2})[/月](?P<d>\d{1,2})日?)",
        r"(?P<dayonly>(?P<dd>\d{1,2})日)",
        rf"(?P<trange>{hm.format('a')}\s*{DASH}\s*{hm.format('b')}(?:まで)?)",
        rf"(?P<tfrom>{hm.format('f')}\s*(?:から|以降|~|〜|-)(?!\s*\d))",
        rf"(?P<tuntil>{hm.format('u')}\s*まで)",
        rf"(?P<off>{words(OFF_WORDS)})",
    ]
    if pattern_names:
        parts.append(rf"(?P<pattern>{words(pattern_names)})")
    parts.append(rf"(?P<any>{words(ANY_WORDS)})")
    return re.compile("|".join(parts))


def _infer_year(month: int, day: int, today: date) -> date:
    candidate = date(today.year, month, day)
    if candidate < today - timedelta(days=90):
        candidate = date(today.year + 1, month, day)
    return candidate


def parse_availability(
    text: str,
    today: date,
    target_year: int,
    target_month: int,
    patterns: dict[str, tuple[int, int]],
    day_start: int,
    day_end: int,
) -> ParseResult:
    """文章から希望シフト (日付ごとの出勤可否と時間帯) を取り出す。"""
    token_re = _build_token_re(list(patterns))
    result = ParseResult()
    found: dict[date, Entry] = {}

    def apply(days: list[date], spec: tuple[bool, int | None, int | None]) -> None:
        available, start, end = spec
        for d in days:
            prev = found.get(d)
            if available and prev and prev.available and start is not None:
                # 「早番 遅番」のように複数指定された場合は時間帯をつなげる
                prev.start = min(prev.start, start)
                prev.end = max(prev.end, end)
            else:
                found[d] = Entry(d, available, start, end)

    for line in re.split(r"[\n。]", normalize(text)):
        pending: list[date] = []
        last: list[date] = []
        leading: tuple[bool, int | None, int | None] | None = None
        for m in token_re.finditer(line):
            g = m.groupdict()
            days: list[date] = []
            spec: tuple[bool, int | None, int | None] | None = None
            try:
                if g["iso"]:
                    days = [date(int(g["iy"]), int(g["im"]), int(g["id"]))]
                elif g["drange"]:
                    start_d = _infer_year(int(g["r1m"]), int(g["r1d"]), today)
                    if g["r2dd"]:
                        end_d = date(start_d.year, start_d.month, int(g["r2dd"]))
                    else:
                        end_d = _infer_year(int(g["r2m"]), int(g["r2d"]), today)
                    days = _date_span(start_d, end_d)
                elif g["ddrange"]:
                    days = _date_span(
                        date(target_year, target_month, int(g["s1"])),
                        date(target_year, target_month, int(g["s2"])),
                    )
                elif g["date"]:
                    days = [_infer_year(int(g["m"]), int(g["d"]), today)]
                elif g["dayonly"]:
                    days = [date(target_year, target_month, int(g["dd"]))]
                elif g["trange"]:
                    spec = (True, _hm(g["ah"], g["am"]), _hm(g["bh"], g["bm"]))
                elif g["tfrom"]:
                    spec = (True, _hm(g["fh"], g["fm"]), day_end)
                elif g["tuntil"]:
                    spec = (True, day_start, _hm(g["uh"], g["um"]))
                elif g["off"]:
                    spec = (False, None, None)
                elif g.get("pattern"):
                    s, e = patterns[g["pattern"]]
                    spec = (True, s, e)
                elif g["any"]:
                    spec = (True, None, None)
            except ValueError:
                result.warnings.append(f"「{m.group(0)}」は存在しない日付です")
                continue

            if days:
                if last and not pending:
                    last = []
                pending.extend(days)
                continue
            if spec is None:
                continue
            if spec[0] and spec[1] is not None and spec[2] <= spec[1]:
                result.warnings.append(f"「{m.group(0).strip()}」は終了時刻が開始時刻より前です")
                continue
            if pending:
                apply(pending, spec)
                last, pending = pending, []
            elif last:
                apply(last, spec)
            else:
                leading = spec

        if pending:
            if leading is None:
                result.warnings.append(
                    "時間の指定がない日は「終日OK」として登録しました: "
                    + "、".join(f"{d.month}/{d.day}" for d in pending)
                )
                leading = (True, None, None)
            apply(pending, leading)

    result.entries = sorted(found.values(), key=lambda e: e.day)
    return result


def _date_span(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("range")
    if (end - start).days > 62:
        raise ValueError("range too long")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_dates_only(text: str, today: date, target_year: int, target_month: int) -> list[date]:
    """「取消 11/3」などのコマンド用に日付だけを取り出す。"""
    res = parse_availability(text, today, target_year, target_month, {}, 0, 24 * 60)
    return [e.day for e in res.entries]
