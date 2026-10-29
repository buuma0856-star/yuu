import unittest
from datetime import date

from shiftbot.scheduler import Availability, StaffInfo, generate_schedule

PATTERNS = {"早番": (540, 840), "遅番": (840, 1320)}


class SchedulerTest(unittest.TestCase):
    def test_fills_and_reports_shortage(self):
        days = [date(2026, 11, 2), date(2026, 11, 3)]
        staff = [StaffInfo("a", "A"), StaffInfo("b", "B"), StaffInfo("c", "C")]
        av = {
            ("a", days[0]): Availability(True, None, None),
            ("b", days[0]): Availability(True, 540, 840),
            ("c", days[0]): Availability(True, 840, 1320),
            ("a", days[1]): Availability(False, None, None),
            ("b", days[1]): Availability(True, 540, 840),
        }
        res = generate_schedule(days, staff, av, PATTERNS, lambda d: {"早番": 1, "遅番": 1}, 5, 40)
        got = {(d, p): u for d, p, u in res.assignments}
        self.assertEqual(got[(days[0], "遅番")], "c")
        self.assertIn(got[(days[0], "早番")], ("a", "b"))
        self.assertEqual(got[(days[1], "早番")], "b")
        self.assertEqual([(s.day, s.pattern) for s in res.shortages], [(days[1], "遅番")])

    def test_prefers_less_flexible_staff(self):
        day = date(2026, 11, 2)
        staff = [StaffInfo("a", "A"), StaffInfo("b", "B"), StaffInfo("c", "C")]
        av = {
            ("a", day): Availability(True, None, None),
            ("b", day): Availability(True, 540, 840),
            ("c", day): Availability(True, None, None),
        }
        res = generate_schedule([day], staff, av, PATTERNS, lambda d: {"早番": 1, "遅番": 1}, 5, 40)
        got = {p: u for _, p, u in res.assignments}
        self.assertEqual(got["早番"], "b")  # 早番しか入れないBを優先
        self.assertEqual(res.shortages, [])

    def test_one_shift_per_day_and_weekly_limit(self):
        days = [date(2026, 11, d) for d in range(2, 9)]  # 月〜日
        staff = [StaffInfo("a", "A", max_days_per_week=3)]
        av = {("a", d): Availability(True, None, None) for d in days}
        res = generate_schedule(days, staff, av, PATTERNS, lambda d: {"早番": 1, "遅番": 1}, 5, 40)
        self.assertEqual(len({d for d, _, _ in res.assignments}), 3)
        self.assertEqual(len(res.assignments), 3)

    def test_fair_distribution(self):
        days = [date(2026, 11, d) for d in range(2, 6)]
        staff = [StaffInfo("a", "A"), StaffInfo("b", "B")]
        av = {(u, d): Availability(True, None, None) for u in "ab" for d in days}
        res = generate_schedule(days, staff, av, PATTERNS, lambda d: {"早番": 1}, 5, 40)
        self.assertEqual(res.hours["a"], res.hours["b"])

    def test_hours_limit(self):
        days = [date(2026, 11, d) for d in range(2, 9)]
        staff = [StaffInfo("a", "A")]
        av = {("a", d): Availability(True, None, None) for d in days}
        res = generate_schedule(days, staff, av, PATTERNS, lambda d: {"遅番": 1}, 7, 20)
        self.assertEqual(len(res.assignments), 2)  # 8時間 x 2 = 16 <= 20


if __name__ == "__main__":
    unittest.main()
