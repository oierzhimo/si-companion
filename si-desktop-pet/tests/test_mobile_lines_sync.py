"""Guard against mobile dialogue drifting from the Windows line table."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from si_pet.companion import LINES

ROOT = Path(__file__).resolve().parents[1]
MOBILE_LINES = (
    ROOT / 'mobile/android/app/src/main/assets/lines.json',
    ROOT / 'mobile/harmony/entry/src/main/resources/rawfile/lines.json',
)


class MobileLinesSyncTests(unittest.TestCase):
    def test_each_mobile_table_matches_windows_lines(self):
        for path in MOBILE_LINES:
            with self.subTest(path=str(path)):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(list(data), list(LINES), path)
                for kind, lines in LINES.items():
                    self.assertEqual(data[kind], lines, f'{path}: {kind}')

    def test_mobile_tables_are_identical(self):
        first, second = (path.read_bytes() for path in MOBILE_LINES)
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
