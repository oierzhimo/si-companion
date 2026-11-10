"""Headless checks for the v18 APNG Pillow decoder and Qt preview player."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The player test must never create a visible window or depend on the user's GUI.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tools.show_v13_motion_preview import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    V13MotionPlayer,
    decode_apng,
    main,
)

SOURCE = ROOT / "qa" / "v13-motion-preview-v18.png"
PORTRAIT = ROOT / "art-source" / "v1.3" / "si-standing-candidate-v18.png"
SCRIPT = ROOT / "tools" / "show_v13_motion_preview.py"
CANVAS = (CANVAS_WIDTH, CANVAS_HEIGHT)
EXPECTED_FRAMES = 192
EXPECTED_DURATION_MS = 8000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rgba_bytes(image: QImage) -> bytes:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(rgba.constBits())[: rgba.sizeInBytes()]


def _alpha_bytes(image: QImage) -> bytes:
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    return bytes(alpha.constBits())[: alpha.sizeInBytes()]


def _changed_pixels(first: Image.Image, second: Image.Image, threshold: int = 0) -> int:
    """Count pixels whose largest RGBA channel difference exceeds threshold."""
    red, green, blue, alpha = ImageChops.difference(first, second).split()
    largest = ImageChops.lighter(ImageChops.lighter(red, green), ImageChops.lighter(blue, alpha))
    return sum(largest.histogram()[threshold + 1 :])


def _first_opaque_pixel(image: Image.Image) -> tuple[int, int]:
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 255 if value == 255 else 0)
    index = mask.tobytes().find(b"\xff")
    if index < 0:
        raise AssertionError("reference frame has no fully opaque pixel")
    return index % image.width, index // image.width


def _read_png_frame(path: Path, index: int = 0) -> Image.Image:
    with Image.open(path) as image:
        image.seek(index)
        return image.convert("RGBA").copy()


class V13MotionPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_hash_before = _sha256(SOURCE)
        cls.portrait_hash_before = _sha256(PORTRAIT)
        cls.clip = decode_apng(SOURCE)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.clip.release()
        finally:
            if _sha256(SOURCE) != cls.source_hash_before:
                raise AssertionError("a player test modified the read-only v18 APNG")
            if _sha256(PORTRAIT) != cls.portrait_hash_before:
                raise AssertionError("a player test modified the read-only v18 portrait")

    def test_apng_decodes_192_frames_at_real_durations(self) -> None:
        with Image.open(SOURCE) as image:
            self.assertEqual(image.n_frames, EXPECTED_FRAMES)
            expected_durations = []
            for index in range(image.n_frames):
                image.seek(index)
                duration = image.info.get("duration")
                self.assertIsNotNone(duration, f"source APNG frame {index} has no duration")
                expected_durations.append(int(round(duration)))

        self.assertEqual(self.clip.frame_count, EXPECTED_FRAMES)
        self.assertEqual(self.clip.durations_ms, expected_durations)
        self.assertEqual(set(self.clip.durations_ms), {41, 42})
        self.assertEqual(sum(self.clip.durations_ms), EXPECTED_DURATION_MS)

        for index, frame in enumerate(self.clip.frames):
            self.assertFalse(frame.isNull(), f"Qt frame {index} is null")
            self.assertEqual((frame.width(), frame.height()), CANVAS)
            self.assertTrue(frame.hasAlphaChannel(), f"Qt frame {index} lost its alpha channel")

        # The first QImage must be the actual RGBA APNG frame, not a static or
        # re-coloured approximation.  This compares every byte in the frame.
        with Image.open(SOURCE) as image:
            expected_first = image.convert("RGBA").copy()
        self.assertEqual(_rgba_bytes(self.clip.frames[0]), expected_first.tobytes())

    def test_qt_frames_preserve_transparency_and_show_motion(self) -> None:
        first = self.clip.frames[0]
        middle = self.clip.frames[EXPECTED_FRAMES // 2]
        self.assertNotEqual(
            _rgba_bytes(first),
            _rgba_bytes(middle),
            "Qt frames 0 and 96 are identical; the APNG was presented as a still",
        )
        self.assertGreater(
            sum(left != right for left, right in zip(_rgba_bytes(first), _rgba_bytes(middle))),
            1000,
            "the two Qt frames differ too little to represent the standing motion",
        )

        alpha = _alpha_bytes(first)
        self.assertEqual(len(alpha), CANVAS_WIDTH * CANVAS_HEIGHT)
        self.assertEqual(min(alpha), 0, "the transparent APNG background was lost")
        self.assertEqual(max(alpha), 255, "the opaque character interior was lost")
        self.assertGreater(sum(1 for value in alpha if value), 20_000)
        for x, y in (
            (0, 0),
            (CANVAS_WIDTH - 1, 0),
            (0, CANVAS_HEIGHT - 1),
            (CANVAS_WIDTH - 1, CANVAS_HEIGHT - 1),
        ):
            self.assertEqual(first.pixelColor(x, y).alpha(), 0)
            self.assertEqual(middle.pixelColor(x, y).alpha(), 0)

        # Compare a later frame as well, so only frame 0 is not accidentally right.
        with Image.open(SOURCE) as image:
            image.seek(EXPECTED_FRAMES // 2)
            expected_middle = image.convert("RGBA").copy()
        self.assertEqual(_rgba_bytes(middle), expected_middle.tobytes())

    def test_offscreen_screenshot_writes_the_requested_real_frame(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v13-motion-player-") as folder:
            output = Path(folder) / "v13-motion-frame-096-review.png"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "offscreen"
            environment["PYTHONUTF8"] = "1"
            command = [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(SOURCE),
                "--screenshot",
                str(output),
                "--frame-index",
                str(EXPECTED_FRAMES // 2),
            ]
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            self.assertEqual(
                completed.returncode,
                0,
                "offscreen screenshot failed\n"
                f"command: {' '.join(command)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}",
            )
            self.assertTrue(output.is_file(), "screenshot mode did not write its target")
            self.assertEqual(_sha256(SOURCE), self.source_hash_before)

            with Image.open(output) as screenshot:
                self.assertEqual(screenshot.format, "PNG")
                self.assertEqual(screenshot.size, CANVAS)
                actual = screenshot.convert("RGBA").copy()

        expected = _read_png_frame(SOURCE, EXPECTED_FRAMES // 2)
        first = _read_png_frame(SOURCE, 0)
        self.assertEqual(actual.size, CANVAS)
        self.assertEqual(actual.getchannel("A").getextrema(), (0, 255))
        self.assertEqual(actual.getpixel((0, 0))[3], 0)

        # QImage's PNG writer may round a semi-transparent RGB value by at most
        # one unit; alpha must be exact.  Fully opaque pixels must be exact too.
        difference = ImageChops.difference(actual, expected)
        self.assertLessEqual(
            max(high for _low, high in difference.getextrema()),
            1,
            "screenshot pixels are not the decoded requested frame",
        )
        self.assertEqual(
            actual.getchannel("A").tobytes(),
            expected.getchannel("A").tobytes(),
            "screenshot alpha differs from the requested APNG frame",
        )
        opaque_point = _first_opaque_pixel(expected)
        self.assertEqual(actual.getpixel(opaque_point), expected.getpixel(opaque_point))

        self.assertGreater(
            _changed_pixels(actual, first),
            100,
            "screenshot is indistinguishable from frame 0; --frame-index was ignored",
        )
        self.assertEqual(
            _changed_pixels(actual, expected, threshold=1),
            0,
            "screenshot has more than one-unit channel differences from frame 96",
        )

    def test_screenshot_refuses_to_overwrite_source_or_portrait(self) -> None:
        source_hash = _sha256(SOURCE)
        portrait_hash = _sha256(PORTRAIT)
        for target in (SOURCE, PORTRAIT):
            with self.subTest(target=target.name):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "--source",
                            str(SOURCE),
                            "--screenshot",
                            str(target),
                            "--frame-index",
                            "0",
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertIn("refusing to overwrite", stderr.getvalue())
                self.assertTrue(target.is_file())
        self.assertEqual(_sha256(SOURCE), source_hash)
        self.assertEqual(_sha256(PORTRAIT), portrait_hash)

    def test_window_is_not_topmost_and_releases_resources_on_close(self) -> None:
        application = QApplication.instance()
        if application is None:
            application = QApplication([])
        if application.platformName() != "offscreen":
            self.skipTest("this lifecycle check requires the offscreen Qt platform")

        window = V13MotionPlayer(self.clip)
        try:
            self.assertFalse(
                bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint),
                "preview window must not be always-on-top by default",
            )
            self.assertIn("待审", window.windowTitle())
            self.assertIn("未获用户确认", window.windowTitle())
            self.assertEqual(window.width(), CANVAS_WIDTH)
            self.assertEqual(window.height(), CANVAS_HEIGHT)
            self.assertEqual(window.frame_count, EXPECTED_FRAMES)
            self.assertIsNotNone(window.timer)
            self.assertTrue(window.timer.isSingleShot())
            self.assertEqual(window.timer.timerType(), Qt.TimerType.PreciseTimer)

            window.show()
            application.processEvents()
            self.assertTrue(window.isVisible())
            self.assertIsNotNone(window.timer)
            self.assertTrue(window.timer.isActive())
            self.assertIn(window.timer.interval(), (41, 42))
            self.assertIsNotNone(window.current_image)

            window.close()
            application.processEvents()
            self.assertFalse(window.isVisible())
            self.assertIsNone(window.timer)
            self.assertEqual(window.frames, [])
            self.assertEqual(window.durations, [])
            self.assertIsNone(window.current_image)
        finally:
            window.release_resources()


if __name__ == "__main__":
    unittest.main(verbosity=2)
