"""Isolated cross-process tests for the QQ data-directory lease.

Every path is a fresh temporary directory.  Child processes run this project's
lock code only; they never touch QQ, the model, credentials, or real user data.
"""
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import (  # noqa: E402
    DataDirectoryBusyError, acquire_data_directory_lease,
)


_HOLDER = r"""
import sys
sys.path.insert(0, sys.argv[2])
from core import acquire_data_directory_lease
lease = acquire_data_directory_lease(sys.argv[1])
print("locked", flush=True)
try:
    sys.stdin.read()
finally:
    lease.release()
print("released", flush=True)
"""

_PROBE = r"""
import sys
sys.path.insert(0, sys.argv[2])
from core import DataDirectoryBusyError, acquire_data_directory_lease
try:
    lease = acquire_data_directory_lease(sys.argv[1])
except DataDirectoryBusyError:
    print("busy")
else:
    lease.release()
    print("acquired")
"""


class DataDirectoryLockTests(unittest.TestCase):
    def setUp(self):
        self._temps = []
        self._children = []

    def tearDown(self):
        # Children must be gone before Windows can remove their lock files.
        for child in self._children:
            self.stop_child(child)
        for temp in self._temps:
            temp.cleanup()

    def make_dir(self):
        temp = tempfile.TemporaryDirectory()
        self._temps.append(temp)
        return Path(temp.name)

    # -- helpers -------------------------------------------------------

    def probe(self, directory):
        result = subprocess.run(
            [sys.executable, "-c", _PROBE, str(directory), str(ROOT)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"probe failed: {result.stdout!r} {result.stderr!r}",
        )
        return result.stdout.strip()

    def spawn_holder(self, directory):
        child = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(directory), str(ROOT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace",
        )
        self._children.append(child)
        self.addCleanup(self.stop_child, child)
        lines = queue.Queue()
        threading.Thread(
            target=lambda: lines.put(child.stdout.readline()), daemon=True
        ).start()
        try:
            line = lines.get(timeout=30)
        except queue.Empty:
            self.fail("holder child did not start in time")
        if not line.startswith("locked"):
            error = ""
            if child.poll() is not None:
                error = child.stderr.read()
            self.fail(f"holder child failed: {line!r} {error!r}")
        return child

    def stop_child(self, child):
        try:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
        for stream in (child.stdout, child.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # pragma: no cover - defensive only
                pass

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    # -- tests ---------------------------------------------------------

    def test_second_process_is_rejected_and_files_are_not_damaged(self):
        directory = self.make_dir()
        lock_path = directory / "si_qq_core_state.lock"
        state_path = directory / "si_qq_core_state.json"
        lock_bytes = b"pre-existing-lock-bytes"
        state_bytes = b'{"sentinel":true}'
        lock_path.write_bytes(lock_bytes)
        state_path.write_bytes(state_bytes)

        lease = acquire_data_directory_lease(directory)
        try:
            self.assertEqual(self.probe(directory), "busy")
        finally:
            lease.release()

        # The lock file is neither deleted nor rewritten, and the state file
        # was not touched merely by taking and dropping the lease.
        self.assertTrue(lock_path.is_file())
        self.assertEqual(lock_path.read_bytes(), lock_bytes)
        self.assertEqual(state_path.read_bytes(), state_bytes)
        self.assertEqual(self.probe(directory), "acquired")

    def test_lock_is_released_when_holder_process_is_killed(self):
        directory = self.make_dir()
        child = self.spawn_holder(directory)
        self.assertEqual(self.probe(directory), "busy")

        child.kill()
        child.wait(timeout=10)

        def can_acquire():
            lease = None
            try:
                lease = acquire_data_directory_lease(directory)
                return True
            except DataDirectoryBusyError:
                return False
            finally:
                if lease is not None:
                    lease.release()

        self.assertTrue(
            self.wait_for(can_acquire),
            "the OS did not release the lease after the holder was killed",
        )
        self.assertEqual(self.probe(directory), "acquired")

    def test_different_directories_are_independent(self):
        held = self.make_dir()
        other = self.make_dir()
        child = self.spawn_holder(held)
        try:
            self.assertEqual(self.probe(held), "busy")
            lease = acquire_data_directory_lease(other)
            try:
                self.assertEqual(self.probe(other), "busy")
            finally:
                lease.release()
            self.assertEqual(self.probe(other), "acquired")
            self.assertEqual(self.probe(held), "busy")
        finally:
            self.stop_child(child)

        self.assertTrue(self.wait_for(
            lambda: self.probe(held) == "acquired"
        ))

    def test_same_process_leases_are_reference_counted(self):
        directory = self.make_dir()
        first = acquire_data_directory_lease(directory)
        second = acquire_data_directory_lease(directory)
        try:
            first.release()
            # The process still owns the directory through the second lease.
            self.assertEqual(self.probe(directory), "busy")
        finally:
            second.release()
        self.assertEqual(self.probe(directory), "acquired")
        first.release()  # idempotent
        self.assertEqual(self.probe(directory), "acquired")


if __name__ == "__main__":
    unittest.main()
