"""Two independent OS processes exchanging events over pinned TLS on loopback.

Each process owns a separate temporary Store, a separate ledger identity and
its own LanServer/PeerRegistry when needed. The production code paths are
Store, Ledger, LanServer, PeerRegistry, pair and exchange; no protocol logic is
reimplemented here. The parent process only starts children, feeds them JSON
commands on stdin and reads JSON replies on stdout.

This is a loopback test on one machine. It is not a second physical PC, and it
does not open a LAN port (the server binds 127.0.0.1 only). All children are
terminated in tearDown, so no background service is left behind.
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from si_pet.storage import Store
from si_pet.sync_transport import LanServer, PeerRegistry, exchange, pair, write_json
from si_pet.sync_v1 import COUNTERS

LEGACY_SAVE = {
    "pats": 3, "snacks": 1, "focus_sessions": 2, "affinity": 16,
    "history": [{"at": "10-25 09:00", "text": "旧电脑上的手记"}],
}
LEGACY_SAVE_COUNTERS = {key: LEGACY_SAVE[key] for key in COUNTERS}
ZERO_COUNTERS = {key: 0 for key in COUNTERS}


def _counters(ledger):
    summary = ledger.summary()
    return {key: summary[key] for key in COUNTERS}


def _status(store, ledger):
    return {
        "device_id": ledger.device_id,
        "baselines": sum(1 for event in ledger.events.values()
                         if event["kind"] == "legacy_snapshot"),
        "events": len(ledger.events),
        "counters": _counters(ledger),
        "journal_rows": len(ledger.journal_rows()),
        "settings_exists": store.path.exists(),
    }


def _shared_command(command, request, store, ledger, lock):
    with lock:
        if command == "add":
            kind = request.get("kind")
            if kind == "focus_completed":
                store.record_sync(kind, {"minutes": int(request.get("minutes", 25))})
            elif kind in ("pat", "snack"):
                store.record_sync(kind)
            else:
                raise ValueError("unknown interaction kind")
        elif command == "log":
            text = request.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 500:
                raise ValueError("invalid journal text")
            store.log(text)
        return _status(store, ledger)


def _reply(ident, result=None, error=None):
    message = {"event": "reply", "id": ident, "ok": error is None}
    if error is None:
        message["result"] = result
    else:
        message["error"] = str(error)
    print(json.dumps(message), flush=True)


def _start_server(registry, receive, port):
    last_error = None
    for _ in range(30):
        try:
            return LanServer(registry, receive, host="127.0.0.1", port=port)
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise last_error or OSError("loopback server did not start")


def _run_host(arguments):
    root = Path(arguments.root)
    store = Store(root / "settings.json")
    if not arguments.reload:
        for key in COUNTERS:
            store[key] = LEGACY_SAVE[key]
        store["history"] = [dict(entry) for entry in LEGACY_SAVE["history"]]
        store.save()
    ledger = store.enable_sync_preview()
    lock = threading.Lock()
    state = {"last_import_added": 0}
    registry = PeerRegistry(root / "settings.lan", ledger.device_id, "旧电脑")

    def receive(data):
        with lock:
            added = store.import_sync_bundle(data)
            state["last_import_added"] = added
            return ledger.export()

    server = _start_server(registry, receive, arguments.port)

    def status():
        with lock:
            result = _status(store, ledger)
            result["last_import_added"] = state["last_import_added"]
            return result

    ready = status()
    ready.update(event="ready", role="host", port=server.port,
                 fingerprint=server.fingerprint,
                 invitation=server.invitation("127.0.0.1"))
    print(json.dumps(ready), flush=True)

    stop = False
    try:
        for raw in sys.stdin:
            try:
                request = json.loads(raw)
            except ValueError:
                continue
            ident = request.get("id")
            command = request.get("cmd")
            try:
                if command == "quit":
                    _reply(ident, status())
                    stop = True
                elif command in ("summary", "add", "log"):
                    result = _shared_command(command, request, store, ledger, lock)
                    with lock:
                        result["last_import_added"] = state["last_import_added"]
                    _reply(ident, result)
                else:
                    raise ValueError("unknown command")
            except Exception as exc:  # Report to the parent instead of dying silently.
                _reply(ident, error=exc)
            if stop:
                break
    finally:
        server.close()


def _run_guest(arguments):
    root = Path(arguments.root)
    store = Store(root / "settings.json")
    ledger = store.enable_sync_preview()
    peer_path = root / "peer.json"
    peer = json.loads(peer_path.read_text(encoding="utf-8")) if peer_path.exists() else None
    lock = threading.Lock()

    def status():
        with lock:
            result = _status(store, ledger)
            result["paired"] = peer is not None
            return result

    ready = status()
    ready.update(event="ready", role="guest")
    print(json.dumps(ready), flush=True)

    stop = False
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except ValueError:
            continue
        ident = request.get("id")
        command = request.get("cmd")
        try:
            if command == "quit":
                _reply(ident, status())
                stop = True
            elif command == "pair":
                with lock:
                    device_id = ledger.device_id
                peer = pair(request.get("invitation"), device_id,
                            request.get("name", "新电脑"))
                write_json(peer_path, peer)
                with lock:
                    result = _status(store, ledger)
                    result["peer_device_id"] = peer["device_id"]
                    result["fingerprint"] = peer["fingerprint"]
                _reply(ident, result)
            elif command == "exchange":
                with lock:
                    if peer is None:
                        raise ValueError("not paired")
                    bundle = ledger.export()
                    device_id = ledger.device_id
                data = exchange(peer, device_id, bundle)
                with lock:
                    added = store.import_sync_bundle(data)
                    result = _status(store, ledger)
                result["added"] = added
                _reply(ident, result)
            elif command in ("summary", "add", "log"):
                _reply(ident, _shared_command(command, request, store, ledger, lock))
            else:
                raise ValueError("unknown command")
        except Exception as exc:
            _reply(ident, error=exc)
        if stop:
            break


def _run_worker():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("host", "guest"), required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--reload", action="store_true")
    arguments = parser.parse_args(sys.argv[1:])
    if arguments.worker == "host":
        _run_host(arguments)
    else:
        _run_guest(arguments)


class _Process:
    """One real child process, addressed with JSON lines on stdin/stdout."""

    def __init__(self, root, role, port=0, reload=False):
        self.role = role
        self._id = 0
        self._lines = queue.Queue()
        self._unmatched = []
        self._log = []
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--worker", role, "--root", str(root), "--port", str(port)]
        if reload:
            command.append("--reload")
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=environment)
        threading.Thread(target=self._read_stdout, name=f"si-{role}-stdout",
                         daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"si-{role}-stderr",
                         daemon=True).start()

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._lines.put(json.loads(line))
                except ValueError:
                    self._log.append("stdout: " + line)
        except (ValueError, OSError):
            pass  # close() may shut the pipe while this reader is still waking.
        finally:
            self._lines.put(None)

    def _read_stderr(self):
        try:
            for line in self.process.stderr:
                line = line.strip()
                if line:
                    self._log.append("stderr: " + line)
        except (ValueError, OSError):
            pass

    def _failure(self, reason):
        return (f"{self.role} worker {reason} (exit={self.process.poll()}); "
                f"recent output: {self._log[-8:]}")

    def _next(self, timeout):
        try:
            message = self._lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            raise AssertionError(self._failure("timed out")) from None
        if message is None:
            raise AssertionError(self._failure("exited")) from None
        return message

    def wait(self, event, timeout=40):
        deadline = time.monotonic() + timeout
        while True:
            message = self._next(deadline - time.monotonic())
            if message.get("event") == event:
                return message
            self._unmatched.append(message)

    def call(self, command, timeout=40, **fields):
        self._id += 1
        ident = self._id
        request = dict(fields, cmd=command, id=ident)
        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AssertionError(self._failure(f"could not send {command}: {exc}")) from exc
        deadline = time.monotonic() + timeout
        while True:
            message = self._next(deadline - time.monotonic())
            if message.get("event") == "reply" and message.get("id") == ident:
                if not message.get("ok"):
                    raise AssertionError(f"{self.role} rejected {command}: "
                                         f"{message.get('error')}")
                return message.get("result")
            self._unmatched.append(message)

    def close(self):
        if self.process.poll() is None:
            try:
                self.call("quit", timeout=15)
            except Exception:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


def _free_loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TwoProcessLanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="si-lan-two-process-"))
        self.workers = []
        self.port = _free_loopback_port()

    def tearDown(self):
        for worker in reversed(self.workers):
            worker.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def start(self, role, root, port=0, reload=False):
        worker = _Process(root, role, port=port, reload=reload)
        self.workers.append(worker)
        return worker

    def test_blank_new_pc_pairs_with_legacy_pc_tls_and_restart_identity(self):
        host_root = self.root / "host"
        guest_root = self.root / "guest"

        host = self.start("host", host_root, port=self.port)
        host_ready = host.wait("ready")
        self.assertEqual(host_ready["role"], "host")
        self.assertEqual(host_ready["baselines"], 1)
        self.assertEqual(host_ready["counters"], LEGACY_SAVE_COUNTERS)
        self.assertEqual(host_ready["port"], self.port)
        invitation = json.loads(host_ready["invitation"])
        self.assertEqual((invitation["host"], invitation["port"]), ("127.0.0.1", self.port))

        guest = self.start("guest", guest_root)
        guest_ready = guest.wait("ready")
        self.assertEqual(guest_ready["role"], "guest")
        # A blank desktop install must persist an empty ledger, not a zero baseline.
        self.assertEqual(guest_ready["baselines"], 0)
        self.assertEqual(guest_ready["events"], 0)
        self.assertEqual(guest_ready["counters"], ZERO_COUNTERS)
        sidecar = guest_root / "settings.sync-v1.json"
        self.assertTrue(sidecar.is_file())
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(document["events"], [])
        self.assertEqual(document["device_id"], guest_ready["device_id"])
        self.assertNotEqual(guest_ready["device_id"], host_ready["device_id"])

        paired = guest.call("pair", invitation=host_ready["invitation"], name="新电脑")
        self.assertEqual(paired["peer_device_id"], host_ready["device_id"])
        self.assertEqual(paired["fingerprint"], host_ready["fingerprint"])
        self.assertNotEqual(paired["device_id"], host_ready["device_id"])

        guest.call("add", kind="pat")
        guest.call("log", text="新电脑上的第一条手记")
        host.call("add", kind="snack")
        host.call("log", text="旧电脑仍然记得")

        first = guest.call("exchange")
        host_after = host.call("summary")
        self.assertEqual(first["added"], 3)  # baseline + snack + host journal
        self.assertEqual(host_after["last_import_added"], 2)  # guest pat + guest journal
        self.assertEqual(first["counters"], host_after["counters"])
        self.assertEqual(first["counters"],
                         {"pats": 4, "snacks": 2, "focus_sessions": 2, "affinity": 20})
        self.assertEqual(first["journal_rows"], 3)
        self.assertEqual(first["baselines"], 1)

        second = guest.call("exchange")
        host_idle = host.call("summary")
        self.assertEqual(second["added"], 0)
        self.assertEqual(host_idle["last_import_added"], 0)
        self.assertEqual(second["counters"], first["counters"])
        self.assertEqual(host_idle["counters"], first["counters"])

        converged = first["counters"]
        host_identity = host_ready["device_id"]
        guest_identity = guest_ready["device_id"]
        host.close()
        guest.close()

        # A new OS process must reload the same device IDs, certificate pin and
        # authorization, then continue syncing without pairing again.
        host2 = self.start("host", host_root, port=self.port, reload=True)
        host2_ready = host2.wait("ready")
        guest2 = self.start("guest", guest_root, reload=True)
        guest2_ready = guest2.wait("ready")
        self.assertEqual(host2_ready["device_id"], host_identity)
        self.assertEqual(guest2_ready["device_id"], guest_identity)
        self.assertEqual(host2_ready["fingerprint"], host_ready["fingerprint"])
        self.assertEqual(host2_ready["counters"], converged)
        self.assertEqual(guest2_ready["counters"], converged)

        host2.call("add", kind="focus_completed", minutes=25)
        guest2.call("add", kind="snack")
        third = guest2.call("exchange")
        host3 = host2.call("summary")
        self.assertEqual(third["added"], 1)
        self.assertEqual(host3["last_import_added"], 1)
        self.assertEqual(third["counters"], host3["counters"])
        self.assertEqual(third["counters"],
                         {"pats": 4, "snacks": 3, "focus_sessions": 3, "affinity": 28})
        self.assertEqual(third["device_id"], guest_identity)
        self.assertEqual(host3["device_id"], host_identity)


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        _run_worker()
    else:
        unittest.main()
