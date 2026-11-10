import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from si_pet.companion import Companion, LINES, CONTACT_OPTIONS


class CompanionTests(unittest.TestCase):
    def test_each_occasion_has_enough_distinct_lines(self):
        for occasion, lines in LINES.items():
            self.assertGreaterEqual(len(lines), 5, occasion)
            self.assertEqual(len(lines), len(set(lines)), occasion)
        for _, occasion in CONTACT_OPTIONS:
            self.assertIn(occasion, LINES)

    def test_full_rounds_do_not_repeat_or_carry_over(self):
        for occasion, lines in LINES.items():
            companion = Companion(random.Random(7))
            rounds = [
                [companion.choose(occasion) for _ in range(len(lines))]
                for _ in range(3)
            ]
            for round_ in rounds:
                self.assertEqual(set(round_), set(lines), occasion)
            for previous, following in zip(rounds, rounds[1:]):
                self.assertNotEqual(previous[-1], following[0], occasion)

    def test_occasions_rotate_independently(self):
        companion = Companion(random.Random(11))
        pat = LINES['pat']
        seen = [companion.choose('pat') for _ in range(len(pat) - 1)]
        companion.choose('feed')
        last_pat = companion.choose('pat')
        self.assertNotIn(last_pat, seen)
        self.assertEqual(set(seen + [last_pat]), set(pat))

    def test_lines_fit_voice_and_avoid_unsupported_claims(self):
        all_lines = [line for lines in LINES.values() for line in lines]
        self.assertEqual(len(all_lines), len(set(all_lines)))
        claimed = ('我看见你的屏幕', '我知道你正在', '我监听到', '检测到你的')
        for line in all_lines:
            self.assertLessEqual(len(line), 120, line)
            self.assertNotIn('\n', line)
            for phrase in claimed:
                self.assertNotIn(phrase, line)
        admin_lines = [line for line in all_lines if '管理员' in line]
        self.assertTrue(admin_lines)
        self.assertLess(len(admin_lines), len(all_lines) / 2)


if __name__ == '__main__':
    unittest.main()
