"""End-to-end review checks for the v18 standing motion preview."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "render_v13_motion_preview.py"
SOURCE = ROOT / "art-source" / "v1.3" / "si-standing-candidate-v18.png"

CANVAS = (384, 416)
EXPECTED_FRAMES = 192
EXPECTED_DURATION_MS = 8000

# Output geometry of qa/v13-motion-keyframes-v18.png.  It is a four-panel
# contact sheet at the actual desktop-pet canvas size.
PANEL_X0 = 24
PANEL_GAP = 22
PANEL_Y = 124
PANEL_W, PANEL_H = CANVAS

FOOT_BAND_PX = 24
MAX_FOOT_DRIFT_PX = 1.5
MAX_FOOT_BASELINE_SPREAD_PX = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class V13MotionPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_hash_before = _sha256(SOURCE)
        cls._temporary = tempfile.TemporaryDirectory(prefix="v13-motion-preview-")
        cls.addClassCleanup(cls._temporary.cleanup)
        directory = Path(cls._temporary.name)
        cls.preview = directory / "v13-motion-preview-v18.png"
        cls.keyframes = directory / "v13-motion-keyframes-v18.png"

        command = [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(SOURCE),
            "--output",
            str(cls.preview),
            "--keyframes",
            str(cls.keyframes),
        ]
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "renderer failed\n"
                f"command: {' '.join(command)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        cls.render_stdout = completed.stdout
        cls.source_hash_after = _sha256(SOURCE)

    def test_preview_is_desktop_size_and_transparent(self) -> None:
        with Image.open(self.preview) as image:
            self.assertEqual(image.size, CANVAS)
            image.seek(0)
            frame = image.convert("RGBA")
        alpha = frame.getchannel("A")
        low, high = alpha.getextrema()
        self.assertEqual(low, 0, "APNG background must contain transparent pixels")
        self.assertEqual(high, 255, "the character must retain opaque interior pixels")
        for corner in (
            (0, 0),
            (CANVAS[0] - 1, 0),
            (0, CANVAS[1] - 1),
            (CANVAS[0] - 1, CANVAS[1] - 1),
        ):
            self.assertEqual(alpha.getpixel(corner), 0, f"corner is not transparent: {corner}")
        visible_pixels = sum(alpha.histogram()[1:])
        self.assertGreater(visible_pixels, 20_000, "preview appears empty or mostly missing")

    def test_apng_has_full_loop_and_exact_frame_timing(self) -> None:
        with Image.open(self.preview) as image:
            self.assertEqual(image.n_frames, EXPECTED_FRAMES)
            self.assertEqual(image.info.get("loop"), 0)
            durations = []
            for index in range(image.n_frames):
                image.seek(index)
                duration = image.info.get("duration")
                self.assertIsNotNone(duration, f"frame {index} has no duration")
                durations.append(int(round(duration)))
            image.seek(0)
            first = image.convert("RGBA").copy()
            image.seek(EXPECTED_FRAMES // 2)
            middle = image.convert("RGBA").copy()

        self.assertEqual(sum(durations), EXPECTED_DURATION_MS)
        self.assertTrue(
            all(duration in (41, 42) for duration in durations),
            f"unexpected APNG frame durations: {sorted(set(durations))}",
        )
        self.assertIsNotNone(
            ImageChops.difference(first, middle).getbbox(),
            "the loop does not contain visible motion",
        )

    def test_keyframe_sheet_has_four_distinct_poses(self) -> None:
        with Image.open(self.keyframes) as image:
            self.assertGreaterEqual(image.width, PANEL_W * 4)
            self.assertGreaterEqual(image.height, PANEL_H)
            sheet = image.convert("RGB")

        panels = []
        for index in range(4):
            left = PANEL_X0 + index * (PANEL_W + PANEL_GAP)
            box = (left, PANEL_Y, left + PANEL_W, PANEL_Y + PANEL_H)
            self.assertLessEqual(box[2], sheet.width)
            self.assertLessEqual(box[3], sheet.height)
            panels.append(sheet.crop(box))

        for left_index in range(4):
            for right_index in range(left_index + 1, 4):
                difference = ImageChops.difference(
                    panels[left_index], panels[right_index]
                )
                self.assertIsNotNone(
                    difference.getbbox(),
                    f"keyframes {left_index} and {right_index} are identical",
                )
                changed = sum(difference.convert("L").histogram()[8:])
                self.assertGreater(
                    changed,
                    50,
                    f"keyframes {left_index} and {right_index} differ too little",
                )

    def test_feet_stay_anchored_across_the_loop(self) -> None:
        baselines = []
        centroids = []
        with Image.open(self.preview) as image:
            for index in range(image.n_frames):
                image.seek(index)
                alpha = image.convert("RGBA").getchannel("A")
                bbox = alpha.getbbox()
                self.assertIsNotNone(bbox, f"frame {index} is empty")
                assert bbox is not None
                baselines.append(bbox[3])
                top = max(bbox[1], bbox[3] - FOOT_BAND_PX)
                band = alpha.crop((0, top, CANVAS[0], bbox[3]))
                pixels = band.load()
                total = 0
                weighted_x = 0
                for y in range(band.height):
                    for x in range(CANVAS[0]):
                        value = pixels[x, y]
                        if value:
                            total += value
                            weighted_x += value * x
                self.assertGreater(total, 0, f"frame {index} has no foot-band pixels")
                centroids.append(weighted_x / total)

        self.assertLessEqual(
            max(baselines) - min(baselines),
            MAX_FOOT_BASELINE_SPREAD_PX,
            "the sole line moved vertically",
        )
        drift = max(abs(value - centroids[0]) for value in centroids)
        self.assertLessEqual(
            drift,
            MAX_FOOT_DRIFT_PX,
            f"lower-body alpha centroid drifted by {drift:.2f}px; feet left their anchor",
        )

    def test_complete_character_never_touches_canvas_edges(self) -> None:
        coverage = []
        with Image.open(self.preview) as image:
            for index in range(image.n_frames):
                image.seek(index)
                alpha = image.convert("RGBA").getchannel("A")
                bbox = alpha.getbbox()
                self.assertIsNotNone(bbox, f"frame {index} is empty")
                assert bbox is not None
                left, top, right, bottom = bbox
                self.assertGreaterEqual(left, 2, f"frame {index} is clipped on the left")
                self.assertGreaterEqual(top, 2, f"frame {index} is clipped at the top")
                self.assertLessEqual(right, CANVAS[0] - 2, f"frame {index} is clipped on the right")
                self.assertLessEqual(bottom, CANVAS[1] - 2, f"frame {index} is clipped at the bottom")
                coverage.append(sum(alpha.histogram()[1:]))

        ratio = max(coverage) / min(coverage)
        self.assertLess(
            ratio,
            1.08,
            "visible alpha coverage changed too much; the full character may be cropped",
        )

    def test_source_candidate_is_not_modified(self) -> None:
        self.assertEqual(
            self.source_hash_before,
            self.source_hash_after,
            "the renderer modified the read-only v18 source image",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
