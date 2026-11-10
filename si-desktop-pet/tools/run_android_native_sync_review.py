"""Isolated PC service for the Android native-TLS sync review.

Run this on the PC while the phone connects through either:
    --invite-host <this PC's private LAN IPv4>   (physical_lan_candidate)
    adb reverse tcp:<port> tcp:<port>            (usb_loopback_candidate, default)

A successful exchange is only candidate evidence; this script does not claim
physical-LAN acceptance until the phone path is actually verified. If the LAN
attempt fails, write 127.0.0.1 to invite-host.txt in the QA directory: the same
running service switches only the advertised host (never the listen host) and
keeps working through adb reverse, without restarting or deleting evidence.
The test syncs two explicitly marked journal events to the phone and does not
change existing pat/snack/focus/affinity totals. The fixed qa/ directory is a
throwaway test artifact and must not be shipped with the product.

The QA directory is fixed under qa/ as android-native-sync-vN, where N is the
--round test round 1..9 (default 1). The round only selects that directory
name under the fixed qa root; this script never accepts an arbitrary file path.
An existing round directory is always refused, never deleted, reused, or
overwritten, so earlier rounds keep their evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True  # Do not leave bytecode in the application tree.
sys.path.insert(0, str(ROOT / "src"))

from si_pet.sync_transport import LanServer, PeerRegistry, local_address
from si_pet.sync_v1 import COUNTERS, Ledger, canonical

# These paths are module level so helpers can read them; main() recomputes all
# of them from --round before any use. The round only picks the fixed
# qa/android-native-sync-vN directory name (N in 1..9), never an arbitrary path.
QA_DIR = ROOT / "qa" / "android-native-sync-v1"
READY_PATH = QA_DIR / "ready.json"
STATUS_PATH = QA_DIR / "status.json"
INVITATION_PATH = QA_DIR / "invitation.txt"
STOP_MARKER = QA_DIR / "stop"
REFRESH_MARKER = QA_DIR / "refresh-invitation"
ADD_MARKER = QA_DIR / "add-pc-journal"
INVITE_HOST_MARKER = QA_DIR / "invite-host.txt"

DEVICE_ID = "pc-android-qa-" + uuid.uuid4().hex  # Fresh per run (46 chars) so QA never reuses event IDs.
DEVICE_NAME = "PC-Si-QA"
LABEL = "android-native-sync-v1"
DEFAULT_INVITE_HOST = "127.0.0.1"  # adb reverse forwards the phone's loopback here.
LISTEN_ALL = "0.0.0.0"  # A private-LAN invitation must accept LAN peers.
INVITATION_SECONDS = 180
DEFAULT_TIMEOUT = 900
MAX_RUNTIME = 1800
TICK_SECONDS = 0.2
FIRST_JOURNAL = "【同步验证】电脑到手机的记录交换检查。"
SECOND_JOURNAL = "【同步验证】电脑端补充记录交换检查；此事件不改变互动累计。"


def _atomic_write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _write_json(path, value):
    _atomic_write(path, canonical(value))


def _write_text(path, text):
    _atomic_write(path, text.encode("utf-8"))


def _consume_marker(path):
    """Remove only one exact marker file owned by this QA directory."""
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _is_loopback(host):
    return ipaddress.IPv4Address(host).is_loopback


def _validate_invite_host(value):
    """Accept loopback, or a private LAN IPv4; never public.

    local_address(value) validates the given address itself (10/8, 172.16/12,
    192.168/16, 127/8) and returns it unchanged; it does not detect this
    host's address, and host selection stays with the operator.
    """
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise ValueError("invite host must be one IPv4 address") from exc
    try:
        return local_address(str(address))
    except ValueError as exc:
        raise ValueError(f"invite host {address} must be a private LAN address") from exc


def _invite_host_argument(value):
    try:
        return _validate_invite_host(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _read_invite_host_marker():
    text = INVITE_HOST_MARKER.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("invite-host.txt must contain exactly one IPv4 address")
    return _validate_invite_host(lines[0])


def _transport_candidate(advertised_host):
    # Candidate wording only; the phone path is verified separately.
    return ("usb_loopback_candidate" if _is_loopback(advertised_host)
            else "physical_lan_candidate")


def _new_qa_dir():
    if QA_DIR.exists():
        print(f"refusing existing QA directory: {QA_DIR}", file=sys.stderr)
        return False
    try:
        QA_DIR.parent.mkdir(parents=True, exist_ok=True)
        QA_DIR.mkdir()
    except OSError as exc:
        print(f"cannot create QA directory: {exc}", file=sys.stderr)
        return False
    return True


def _public_summary(ledger):
    # Ledger.summary() contains journal bodies; publish counters and a count only.
    summary = ledger.summary()
    return {**{key: summary[key] for key in COUNTERS},
            "journal_entries": len(summary["journal"]),
            "preferences": summary["preferences"]}


def _export_summary(ledger):
    data = ledger.export()
    events = json.loads(data)["events"]  # Event total is derived from the export.
    return data, {"sha256": hashlib.sha256(data).hexdigest(),
                  "bytes": len(data), "event_count": len(events)}


def _status(port, registry, ledger, ledger_lock, state, closed):
    with ledger_lock:
        summary = _public_summary(ledger)
        _data, exported = _export_summary(ledger)
        imports = [dict(item) for item in state["imports"]]
        exchange_count = state["exchange_count"]
    with registry.lock:
        paired_peers = len(registry.peers)
    return {
        "label": LABEL,
        "port": port,
        "listen_host": state["listen_host"],
        "advertised_host": state["advertised_host"],
        "transport_candidate": _transport_candidate(state["advertised_host"]),
        "paired_peers": paired_peers,
        "exchange_count": exchange_count,
        "imports": imports,
        "ledger_summary": summary,
        "event_count": exported["event_count"],
        "export": exported,
        "closed": closed,
    }


def _exchange_callback(ledger, ledger_lock, state):
    def exchange(body):
        # LanServer invokes this on its worker thread. The same RLock also
        # protects main-loop journal writes and status reads.
        with ledger_lock:
            newly_imported = ledger.import_bundle(body)
            export_data, exported = _export_summary(ledger)
            summary = ledger.summary()
            state["exchange_count"] += 1
            state["imports"].append({
                "at": datetime.now(timezone.utc).isoformat(),
                "newly_imported": newly_imported,
                "event_count": exported["event_count"],
                "export_sha256": exported["sha256"],
                "counts": {key: summary[key] for key in COUNTERS},
            })
            return export_data
    return exchange


def _refresh_invitation(server, advertised_host):
    # Contains a temporary pairing token: written only to the QA directory.
    _write_text(INVITATION_PATH,
                server.invitation(advertised_host, INVITATION_SECONDS))


def _round_number(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("round must be an integer") from exc
    if not 1 <= number <= 9:
        raise argparse.ArgumentTypeError("round must be between 1 and 9")
    return number


def _timeout(value):
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be an integer") from exc
    if not 1 <= seconds <= MAX_RUNTIME:
        raise argparse.ArgumentTypeError(f"timeout must be between 1 and {MAX_RUNTIME}")
    return seconds


def main(argv=None):
    global LABEL, QA_DIR, READY_PATH, STATUS_PATH, INVITATION_PATH
    global STOP_MARKER, REFRESH_MARKER, ADD_MARKER, INVITE_HOST_MARKER
    parser = argparse.ArgumentParser(description="Android native-TLS sync QA service")
    parser.add_argument("--round", type=_round_number, default=1,
                        help="test round 1-9; selects qa/android-native-sync-vN "
                             "(default 1). An existing round directory is refused.")
    parser.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT,
                        help=f"seconds before shutdown (1-{MAX_RUNTIME}, default {DEFAULT_TIMEOUT})")
    parser.add_argument("--invite-host", type=_invite_host_argument,
                        default=DEFAULT_INVITE_HOST,
                        help="private IPv4 advertised in the invitation; loopback "
                             "(default) keeps adb reverse, a LAN IPv4 also listens "
                             "on 0.0.0.0")
    args = parser.parse_args(argv)
    # The round selects a fixed directory name under qa/; it is not a path
    # argument, so no arbitrary location can be read, written, or reused.
    LABEL = f"android-native-sync-v{args.round}"
    QA_DIR = ROOT / "qa" / LABEL
    READY_PATH = QA_DIR / "ready.json"
    STATUS_PATH = QA_DIR / "status.json"
    INVITATION_PATH = QA_DIR / "invitation.txt"
    STOP_MARKER = QA_DIR / "stop"
    REFRESH_MARKER = QA_DIR / "refresh-invitation"
    ADD_MARKER = QA_DIR / "add-pc-journal"
    INVITE_HOST_MARKER = QA_DIR / "invite-host.txt"
    if not _new_qa_dir():
        return 2

    advertised_host = args.invite_host
    listen_host = (DEFAULT_INVITE_HOST if _is_loopback(advertised_host)
                   else LISTEN_ALL)
    ledger_lock = threading.RLock()
    ledger = Ledger(QA_DIR / "ledger.json", DEVICE_ID)  # Fresh QA ledger only.
    with ledger_lock:
        ledger.add("journal", {"text": FIRST_JOURNAL})
    registry = PeerRegistry(QA_DIR, DEVICE_ID, DEVICE_NAME)
    state = {"exchange_count": 0, "imports": [],
             "listen_host": listen_host, "advertised_host": advertised_host}

    server = LanServer(registry, _exchange_callback(ledger, ledger_lock, state),
                       host=listen_host, port=0)
    try:
        _refresh_invitation(server, advertised_host)
        initial = _status(server.port, registry, ledger, ledger_lock, state, closed=False)
        _write_json(READY_PATH, initial)
        _write_json(STATUS_PATH, initial)
        print(f"ready port={server.port} dir={QA_DIR}", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if _consume_marker(STOP_MARKER):
                break
            if INVITE_HOST_MARKER.exists():
                try:
                    candidate = _read_invite_host_marker()
                    # The marker changes advertisement only; the listener keeps
                    # its startup host, so a LAN address needs a LAN listener.
                    if not _is_loopback(candidate) and _is_loopback(listen_host):
                        raise ValueError("LAN advertisement needs a LAN listen host")
                except (OSError, ValueError) as exc:
                    if state.get("invite_host_error") != str(exc):
                        state["invite_host_error"] = str(exc)
                        print(f"rejected invite-host.txt: {exc}",
                              file=sys.stderr, flush=True)
                else:
                    state.pop("invite_host_error", None)
                    advertised_host = candidate
                    state["advertised_host"] = candidate
                    _refresh_invitation(server, advertised_host)
                    _consume_marker(INVITE_HOST_MARKER)
                    print(f"advertised={candidate} listen={listen_host}", flush=True)
            else:
                state.pop("invite_host_error", None)
            if _consume_marker(REFRESH_MARKER):
                _refresh_invitation(server, advertised_host)
            if _consume_marker(ADD_MARKER):
                with ledger_lock:
                    ledger.add("journal", {"text": SECOND_JOURNAL})
            _write_json(STATUS_PATH,
                        _status(server.port, registry, ledger, ledger_lock, state, closed=False))
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        pass  # Ctrl-C takes the same cleanup path as timeout and stop.
    finally:
        try:
            server.close()
        finally:
            _write_json(STATUS_PATH,
                        _status(server.port, registry, ledger, ledger_lock, state, closed=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
