"""Regression tests for autonomous walking on window ledges.

These tests drive the real ``Controller.update`` and ``Physics.step`` against
one fixed offscreen screen and one fixed, simulated window segment.  They do
not emulate a random real desktop or validate native window enumeration; the
window platform is injected through ``si_pet.controller.native.window_platforms``.

Run from the project root:

    $env:QT_QPA_PLATFORM = 'offscreen'
    python -m unittest discover -s tests -p test_window_ledge_walk.py -v
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtWidgets import QApplication

from si_pet.controller import Controller
from si_pet.physics import Platform
from si_pet.storage import Store

# One QApplication for the whole test process.  Qt does not support destroying
# the application object and creating a second one later in the same process.
_QT_APP = None


def qt_application():
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


WINDOW_KEY = 'window:4242:0'
SCALE = 1.0
SPEED = 65.0
DT = 1 / 60


class FakeSprites:
    """Small stand-in for Sprites: update() only needs state names and a mask."""

    def __init__(self):
        names = (
            'idle', 'jumping', 'walk-right', 'walk-left', 'sitting',
            'sleeping', 'waving', 'running', 'review', 'special',
        )
        self.frames = {name: [0] for name in names}
        self.durations = {name: [100] for name in names}
        self._mask = QRegion(0, 0, round(192 * SCALE), round(208 * SCALE))

    def mask(self, state, index, scale):
        return QRegion(self._mask)

    def index(self, state, elapsed_ms):
        return 0


class WindowLedgeWalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_application()

    def setUp(self):
        qt_application()
        screen = QGuiApplication.primaryScreen()
        self.assertIsNotNone(screen, 'offscreen Qt did not expose a screen')
        self.screen = screen.availableGeometry()
        self.tmp = tempfile.TemporaryDirectory(prefix='window-ledge-')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'settings.json')
        self.store['scale'] = SCALE
        self.store['speed'] = SPEED
        self.store['gravity'] = 1250.0
        self.store['window_edges'] = True
        self.store['wander'] = True
        self.store['manual'] = False
        self.store['bottom_offset'] = 0
        self.platform = self.make_platform(110)
        with patch(
            'si_pet.controller.native.window_platforms',
            return_value=[self.platform],
        ):
            self.ctrl = Controller(self.store, FakeSprites())
        # Keep simulated updates from writing the settings file.
        self.ctrl.last_save = time.monotonic() + 3600

    # -- fixtures -----------------------------------------------------------

    def make_platform(self, width):
        margin = 96  # _walk_margin for the fixed scale=1 store
        walk_left = self.screen.left() + margin + 8
        walk_right = self.screen.right() - margin - 8
        available = max(0, walk_right - walk_left)
        width = max(0, min(int(width), available))
        left = walk_left + (available - width) // 2
        top = self.screen.top() + max(220, self.screen.height() // 2)
        return Platform(WINDOW_KEY, left, left + width, top, origin_x=float(left))

    def use_window(self, width):
        """Replace the injected platform with one of the requested width."""
        platform = self.make_platform(width)
        self.ctrl.physics.set_platforms([platform])
        return platform

    def place_on(self, platform, x=None, vx=0.0):
        body = self.ctrl.physics.body
        if x is None:
            x = (platform.left + platform.right) / 2
        body.x = x
        body.y = platform.top
        body.vx = vx
        body.vy = 0
        body.support = platform.key
        body.dragging = False

    def arm_walk(self, direction=1):
        ctrl = self.ctrl
        ctrl.action = 'walk'
        ctrl.direction = direction
        ctrl.override_until = 0
        ctrl.decision_at = time.monotonic() + 3600
        ctrl.sleeping = False
        ctrl.focus_end = None
        ctrl.store['manual'] = False
        ctrl.keys.clear()

    def simulate(self, seconds, dt=DT):
        steps = max(1, round(seconds / dt))
        frames = []
        previous_vx = self.ctrl.physics.body.vx
        previous_direction = self.ctrl.direction
        turns = 0
        for _ in range(steps):
            self.ctrl.update(dt)
            body = self.ctrl.physics.body
            if self.ctrl.direction != previous_direction:
                turns += 1
                previous_direction = self.ctrl.direction
            frames.append({
                'x': body.x,
                'y': body.y,
                'vx': body.vx,
                'support': body.support,
                'direction': self.ctrl.direction,
                'action': self.ctrl.action,
                'state': self.ctrl.state,
                'dv': abs(body.vx - previous_vx),
                'turns': turns,
            })
            previous_vx = body.vx
        return frames

    def assert_window_held(self, frames, platform):
        for index, frame in enumerate(frames):
            self.assertEqual(
                frame['support'], platform.key,
                f'frame {index}: pet lost the window ledge at x={frame["x"]}',
            )
            self.assertGreaterEqual(frame['x'], platform.left - .5,
                                    f'frame {index}: crossed the left segment edge')
            self.assertLessEqual(frame['x'], platform.right + .5,
                                 f'frame {index}: crossed the right segment edge')

    # -- autonomous window walking -----------------------------------------

    def test_auto_walk_turns_on_window_ledge_without_falling(self):
        platform = self.use_window(110)
        self.place_on(platform)
        self.arm_walk(direction=1)
        frames = self.simulate(3.0)

        self.assert_window_held(frames, platform)
        self.assertGreaterEqual(frames[-1]['turns'], 1,
                                'the pet never turned around on a short ledge')
        self.assertLessEqual(frames[-1]['turns'], 4,
                             'the pet flipped direction too many times')
        max_step_change = max(frame['dv'] for frame in frames)
        self.assertLessEqual(
            max_step_change, SPEED * .35 + 4,
            f'velocity jumped by {max_step_change:.1f} px/s in one frame',
        )
        max_step_move = max(
            abs(frames[index]['x'] - frames[index - 1]['x'])
            for index in range(1, len(frames))
        )
        self.assertLess(max_step_move, SPEED * DT * 2 + 1,
                        'the pet teleported along the ledge')

    def test_high_speed_walk_stays_on_a_wide_window(self):
        self.store['speed'] = 220.0
        platform = self.use_window(360)
        self.place_on(platform)
        self.arm_walk(direction=-1)
        frames = self.simulate(3.0)

        self.assert_window_held(frames, platform)
        self.assertGreaterEqual(frames[-1]['turns'], 1)
        self.assertLessEqual(frames[-1]['turns'], 8)
        self.assertLessEqual(max(abs(frame['vx']) for frame in frames), 221.0)
        max_step_change = max(frame['dv'] for frame in frames)
        self.assertLessEqual(
            max_step_change, 220.0 * .35 + 4,
            f'velocity jumped by {max_step_change:.1f} px/s at speed 220',
        )
        max_step_move = max(
            abs(frames[index]['x'] - frames[index - 1]['x'])
            for index in range(1, len(frames))
        )
        self.assertLess(max_step_move, 220.0 * DT * 2 + 1,
                        'the pet teleported along the ledge at high speed')

    def test_narrow_ledge_settles_to_idle_without_repeated_turns(self):
        platform = self.use_window(40)
        self.place_on(platform, vx=40.0)
        self.arm_walk(direction=1)
        before = time.monotonic()
        self.simulate(DT)
        self.assertEqual(self.ctrl.action, 'idle')
        self.assertGreaterEqual(self.ctrl.decision_at, before + 4)
        self.assertGreater(self.ctrl.physics.body.vx, 0,
                           'a narrow ledge must not hard-stop the pet')
        self.assertLess(self.ctrl.physics.body.vx, 40)
        frames = self.simulate(1.0)

        self.assert_window_held(frames, platform)
        self.assertEqual(frames[-1]['turns'], 0,
                         'the pet paced back and forth on a too-narrow ledge')
        self.assertEqual(frames[-1]['state'], 'idle')
        self.assertLess(abs(frames[-1]['vx']), 12)
        self.assertLessEqual(max(frame['dv'] for frame in frames), 40 * .2 + 2)

    def test_narrow_ledge_steers_back_from_the_edge(self):
        platform = self.use_window(40)
        self.place_on(platform, x=platform.right - 2, vx=40.0)
        self.arm_walk(direction=1)
        frames = self.simulate(1.0)

        self.assert_window_held(frames, platform)
        self.assertEqual(frames[-1]['turns'], 1)
        self.assertEqual(frames[-1]['direction'], -1)
        self.assertEqual(self.ctrl.action, 'idle')
        self.assertLess(abs(self.ctrl.physics.body.vx), 12)
        self.assertLess(self.ctrl.physics.body.x, platform.right - 2,
                        'the pet stayed at the unsafe edge instead of stepping in')

    def test_high_speed_narrow_ledge_is_not_walked(self):
        self.store['speed'] = 220.0
        platform = self.use_window(90)
        self.place_on(platform, vx=180.0)
        self.arm_walk(direction=1)
        start_x = self.ctrl.physics.body.x
        frames = self.simulate(1.0)

        self.assert_window_held(frames, platform)
        self.assertEqual(frames[-1]['turns'], 0)
        self.assertEqual(self.ctrl.action, 'idle')
        self.assertLess(abs(frames[-1]['vx']), 12)
        # Braking is smooth but the body must not wander far on a short ledge.
        self.assertLess(abs(frames[-1]['x'] - start_x), 30)

    def test_window_segment_key_change_keeps_guiding(self):
        platform = self.use_window(200)
        self.place_on(platform, x=platform.right - 20)
        self.arm_walk(direction=1)
        self.simulate(0.1)

        # An occluder can remove the left part of a window ledge and rename the
        # visible segment.  Physics.set_platforms follows the new key; the
        # controller must turn at the new segment's edge, not the stale one.
        moved = Platform(
            'window:4242:60', platform.left + 60, platform.right, platform.top,
            origin_x=platform.origin_x,
        )
        self.ctrl.physics.set_platforms([moved])
        self.assertEqual(self.ctrl.physics.body.support, moved.key)
        frames = self.simulate(1.0)

        self.assert_window_held(frames, moved)
        self.assertEqual(frames[-1]['turns'], 1)
        self.assertEqual(frames[-1]['direction'], -1)

    # -- unchanged manual drag/jump/floor behaviour -------------------------

    def test_manual_walk_can_still_leave_the_window_edge(self):
        platform = self.use_window(160)
        self.place_on(platform, x=platform.right - 1)
        self.ctrl.store['manual'] = True
        self.ctrl.keys = {'right'}
        self.ctrl.action = 'idle'
        self.ctrl.direction = 1
        self.ctrl.override_until = 0
        self.ctrl.decision_at = time.monotonic() + 3600

        frames = self.simulate(0.8)
        body = self.ctrl.physics.body

        self.assertEqual(self.ctrl.direction, 1)
        self.assertGreater(body.x, platform.right,
                           'manual right key did not step off the ledge')
        self.assertIsNone(body.support, 'the pet should be falling after the step-off')
        self.assertGreater(body.y, platform.top)
        self.assertTrue(any(frame['support'] is None for frame in frames))

    def test_screen_floor_edge_keeps_original_turn(self):
        floor = next(
            platform for platform in self.ctrl.physics.platforms
            if platform.key.startswith('floor:')
        )
        margin = self.ctrl._walk_margin(self.ctrl.scenes[0])
        start_x = self.screen.right() - margin
        self.place_on(floor, x=start_x)
        self.arm_walk(direction=1)
        frames = self.simulate(1.0)

        self.assertEqual(self.ctrl.direction, -1)
        for index, frame in enumerate(frames):
            self.assertEqual(frame['support'], floor.key,
                             f'frame {index}: fell off the screen floor')
            self.assertLessEqual(frame['x'], start_x + .5)
        self.assertLess(frames[-1]['x'], start_x)

    def test_drag_suspends_window_edge_guidance(self):
        platform = self.use_window(160)
        self.place_on(platform, x=platform.right - .5)
        self.arm_walk(direction=1)
        body = self.ctrl.physics.body
        body.dragging = True
        start = (body.x, body.y, body.support)

        frames = self.simulate(0.2)

        self.assertEqual((body.x, body.y, body.support), start)
        self.assertEqual(self.ctrl.direction, 1)
        self.assertTrue(all(frame['state'] == 'jumping' for frame in frames))

    def test_jump_from_window_ledge_still_lands(self):
        platform = self.use_window(160)
        self.place_on(platform)
        self.ctrl.perform('jump', announce=False)

        body = self.ctrl.physics.body
        self.assertIsNone(body.support)
        self.assertLess(body.vy, 0)
        self.assertEqual(self.ctrl.action, 'jump')
        frames = self.simulate(1.0)

        self.assertLess(min(frame['y'] for frame in frames), platform.top - 40,
                        'the jump did not leave the ledge')
        self.assertEqual(body.support, platform.key,
                         'the pet did not land back on the window ledge')
        self.assertAlmostEqual(body.y, platform.top, places=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
