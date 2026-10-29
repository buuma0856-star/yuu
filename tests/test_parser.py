import unittest
from datetime import date

from shiftbot.parser import parse_availability

PATTERNS = {"早番": (540, 840), "遅番": (840, 1320)}
TODAY = date(2026, 10, 20)


def parse(text):
    return parse_availability(text, TODAY, 2026, 11, PATTERNS, 540, 1320)


def simple(res):
    return [(e.day, e.available, e.start, e.end) for e in res.entries]


class ParserTest(unittest.TestCase):
    def test_time_range_and_off(self):
        res = parse("11/3 9-17\n11月5日 休み")
        self.assertEqual(simple(res), [
            (date(2026, 11, 3), True, 540, 1020),
            (date(2026, 11, 5), False, None, None),
        ])

    def test_fullwidth_and_colon_times(self):
        res = parse("１１／３（火）　９：３０～１７：００")
        self.assertEqual(simple(res), [(date(2026, 11, 3), True, 570, 1020)])

    def test_japanese_times(self):
        res = parse("11/4 9時から17時半")
        self.assertEqual(simple(res), [(date(2026, 11, 4), True, 540, 1050)])

    def test_multiple_dates_share_spec(self):
        res = parse("11/7, 11/8 終日")
        self.assertEqual(simple(res), [
            (date(2026, 11, 7), True, None, None),
            (date(2026, 11, 8), True, None, None),
        ])

    def test_date_range_with_pattern(self):
        res = parse("11/10〜11/12 早番")
        self.assertEqual([e.day.day for e in res.entries], [10, 11, 12])
        self.assertTrue(all((e.start, e.end) == (540, 840) for e in res.entries))

    def test_day_only_uses_target_month(self):
        res = parse("15日 18時から\n20日 15時まで")
        self.assertEqual(simple(res), [
            (date(2026, 11, 15), True, 1080, 1320),
            (date(2026, 11, 20), True, 540, 900),
        ])

    def test_two_patterns_merge(self):
        res = parse("11/9 早番 遅番")
        self.assertEqual(simple(res), [(date(2026, 11, 9), True, 540, 1320)])

    def test_several_entries_on_one_line(self):
        res = parse("11/3 ○ 11/4 × 11/5 10-15")
        self.assertEqual(simple(res), [
            (date(2026, 11, 3), True, None, None),
            (date(2026, 11, 4), False, None, None),
            (date(2026, 11, 5), True, 600, 900),
        ])

    def test_spec_before_dates(self):
        res = parse("休み: 11/2, 11/6")
        self.assertEqual([(e.day.day, e.available) for e in res.entries], [(2, False), (6, False)])

    def test_dates_without_spec_default_to_any(self):
        res = parse("11/3 11/4")
        self.assertEqual(len(res.entries), 2)
        self.assertTrue(all(e.available and e.start is None for e in res.entries))
        self.assertTrue(res.warnings)

    def test_not_impossible_word(self):
        res = parse("11/3 出勤不可")
        self.assertFalse(res.entries[0].available)

    def test_year_rollover(self):
        res = parse_availability("1/5 終日", date(2026, 12, 20), 2027, 1, PATTERNS, 540, 1320)
        self.assertEqual(res.entries[0].day, date(2027, 1, 5))

    def test_invalid_date(self):
        res = parse("11/31 9-17")
        self.assertEqual(res.entries, [])
        self.assertTrue(res.warnings)

    def test_plain_chat_has_no_entries(self):
        self.assertEqual(parse("了解です！よろしくお願いします").entries, [])


if __name__ == "__main__":
    unittest.main()
