"""希望シフトから自動でシフト表を作る。

方針:
  1. 人が集まりにくい枠 (候補者が少ない枠) から先に埋める
  2. 候補者のうち、その月の勤務時間がまだ少ない人を優先して公平にする
     (同じなら、その日ほかの枠に入れない人を優先)
  3. 週の勤務日数・勤務時間の上限、1日の勤務回数の上限を守る
"""

from __future__ import annotations

import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Availability:
    available: bool
    start: int | None  # None = 終日OK
    end: int | None

    def covers(self, start: int, end: int) -> bool:
        if not self.available:
            return False
        if self.start is None:
            return True
        return self.start <= start and end <= self.end


@dataclass
class StaffInfo:
    user_id: str
    name: str
    max_days_per_week: int | None = None


@dataclass
class Shortage:
    day: date
    pattern: str
    needed: int
    filled: int


@dataclass
class ScheduleResult:
    assignments: list[tuple[date, str, str]] = field(default_factory=list)  # (日付, 枠, user_id)
    shortages: list[Shortage] = field(default_factory=list)
    hours: dict[str, float] = field(default_factory=dict)


def month_days(year: int, month: int) -> list[date]:
    return [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def generate_schedule(
    days: list[date],
    staff: list[StaffInfo],
    availability: dict[tuple[str, date], Availability],
    patterns: dict[str, tuple[int, int]],
    requirements_for,
    max_days_per_week: int,
    max_hours_per_week: float,
    max_shifts_per_day: int = 1,
    seed: int = 0,
) -> ScheduleResult:
    rng = random.Random(seed)
    tiebreak = {s.user_id: rng.random() for s in staff}
    staff_by_id = {s.user_id: s for s in staff}

    slots: list[tuple[date, str, int, list[str]]] = []
    for day in days:
        for pattern, needed in requirements_for(day).items():
            if needed <= 0 or pattern not in patterns:
                continue
            start, end = patterns[pattern]
            candidates = [
                s.user_id
                for s in staff
                if (av := availability.get((s.user_id, day))) and av.covers(start, end)
            ]
            slots.append((day, pattern, needed, candidates))

    # その日に入れる枠の数 (少ない人＝融通がきかない人を優先して入れる)
    options: dict[tuple[str, date], int] = defaultdict(int)
    for day, _, _, candidates in slots:
        for uid in candidates:
            options[(uid, day)] += 1

    # 余裕のない枠から先に割り当てる
    slots.sort(key=lambda s: (len(s[3]) - s[2], s[0], s[1]))

    total_min: dict[str, int] = defaultdict(int)
    week_min: dict[tuple[str, tuple[int, int]], int] = defaultdict(int)
    week_days: dict[tuple[str, tuple[int, int]], set[date]] = defaultdict(set)
    day_slots: dict[tuple[str, date], list[tuple[int, int]]] = defaultdict(list)

    result = ScheduleResult()
    for day, pattern, needed, candidates in slots:
        start, end = patterns[pattern]
        length = end - start
        week = tuple(day.isocalendar()[:2])

        def ok(uid: str) -> bool:
            taken = day_slots[(uid, day)]
            if len(taken) >= max_shifts_per_day:
                return False
            if any(s < end and start < e for s, e in taken):
                return False
            limit_days = staff_by_id[uid].max_days_per_week or max_days_per_week
            if day not in week_days[(uid, week)] and len(week_days[(uid, week)]) >= limit_days:
                return False
            return week_min[(uid, week)] + length <= max_hours_per_week * 60

        chosen = sorted(
            (uid for uid in candidates if ok(uid)),
            key=lambda uid: (total_min[uid], options[(uid, day)], tiebreak[uid]),
        )[:needed]
        for uid in chosen:
            result.assignments.append((day, pattern, uid))
            total_min[uid] += length
            week_min[(uid, week)] += length
            week_days[(uid, week)].add(day)
            day_slots[(uid, day)].append((start, end))
        if len(chosen) < needed:
            result.shortages.append(Shortage(day, pattern, needed, len(chosen)))

    order = {name: i for i, name in enumerate(patterns)}
    result.assignments.sort(key=lambda a: (a[0], order.get(a[1], 99), staff_by_id[a[2]].name))
    result.shortages.sort(key=lambda s: (s.day, order.get(s.pattern, 99)))
    result.hours = {s.user_id: total_min[s.user_id] / 60 for s in staff}
    return result
