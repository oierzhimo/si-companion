"""Offline event exchange for the future multi-device companion.

This module does not connect to a network or change settings.json. It makes
the merge rules executable so desktop and mobile clients can share fixtures.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL = 1
MAX_BUNDLE_BYTES = 5_000_000
MAX_EVENTS = 10_000
DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
EVENT_KINDS = {"pat", "snack", "focus_completed", "journal", "legacy_snapshot", "pref_set"}
COUNTERS = ("pats", "snacks", "focus_sessions", "affinity")
SHARED_PREFERENCE_KEYS = ("preferred_name", "greeting_style")
# Harmony/JS clients read sequence and Lamport numbers as Numbers; values
# beyond this integer lose precision there even though Python accepts them.
JS_MAX_SAFE_INTEGER = 2**53 - 1


class SyncError(ValueError):
    """Invalid or conflicting event data; no partial merge is applied."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _nonnegative_int(value, label):
    if type(value) is not int or value < 0:
        raise SyncError(f"{label} must be a non-negative integer")
    return value


def validate_event(event):
    if not isinstance(event, dict) or set(event) != {
            "protocol", "event_id", "device_id", "sequence", "lamport",
            "kind", "payload", "occurred_at"}:
        raise SyncError("Invalid event fields")
    if type(event["protocol"]) is not int or event["protocol"] != PROTOCOL:
        raise SyncError("Unsupported event protocol")
    device = event["device_id"]
    if not isinstance(device, str) or not DEVICE_ID.fullmatch(device):
        raise SyncError("Invalid device ID")
    sequence = _nonnegative_int(event["sequence"], "sequence")
    if sequence == 0 or event["event_id"] != f"{device}:{sequence}":
        raise SyncError("Event ID does not match device sequence")
    _nonnegative_int(event["lamport"], "lamport")
    if not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS:
        raise SyncError("Unknown event kind")
    timestamp = event["occurred_at"]
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise SyncError("Invalid timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncError("Invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise SyncError("Timestamp needs an offset")
    payload = event["payload"]
    if not isinstance(payload, dict):
        raise SyncError("Invalid payload")
    kind = event["kind"]
    if kind in {"pat", "snack"} and payload:
        raise SyncError("Unexpected interaction payload")
    if kind == "focus_completed":
        if set(payload) != {"minutes"} or not 1 <= _nonnegative_int(payload["minutes"], "minutes") <= 120:
            raise SyncError("Invalid focus duration")
    if kind == "journal":
        if set(payload) != {"text"} or not isinstance(payload["text"], str) or not 1 <= len(payload["text"]) <= 500:
            raise SyncError("Invalid journal text")
    if kind == "legacy_snapshot":
        if set(payload) != set(COUNTERS) | {"history"}:
            raise SyncError("Invalid legacy snapshot")
        for key in COUNTERS:
            _nonnegative_int(payload[key], key)
        if not isinstance(payload["history"], list) or len(payload["history"]) > 100:
            raise SyncError("Invalid legacy history")
        for entry in payload["history"]:
            if (not isinstance(entry, dict) or set(entry) != {"at", "text"}
                    or not isinstance(entry["at"], str) or len(entry["at"]) > 40
                    or not isinstance(entry["text"], str) or len(entry["text"]) > 500):
                raise SyncError("Invalid legacy history entry")
    if kind == "pref_set":
        if (set(payload) != {"key", "value"} or not isinstance(payload["key"], str)
                or payload["key"] not in {"preferred_name", "greeting_style"}):
            raise SyncError("Unsupported shared preference")
        if not isinstance(payload["value"], str) or len(payload["value"]) > 80:
            raise SyncError("Invalid shared preference value")
    try:
        if len(canonical(event)) > 32_000:
            raise SyncError("Event too large")
    except (TypeError, ValueError) as exc:
        raise SyncError("Event is not JSON-compatible") from exc
    return event


def legacy_event(settings):
    """An exact copy of a legacy save gets the same migration event ID."""
    snapshot = {}
    for key in COUNTERS:
        value = settings.get(key, 0)
        if type(value) not in {int, float} or not float(value).is_integer() or value < 0:
            raise SyncError(f"Legacy {key} is not a whole non-negative number")
        snapshot[key] = int(value)
    history = settings.get("history", [])
    if not isinstance(history, list):
        history = []
    snapshot["history"] = [{"at": str(item.get("at", ""))[:40],
                            "text": str(item.get("text", ""))[:500]}
                           for item in history[-100:] if isinstance(item, dict)]
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    event = {"protocol": PROTOCOL, "event_id": f"legacy-{digest[:32]}:1",
             "device_id": f"legacy-{digest[:32]}", "sequence": 1,
             "lamport": 0, "kind": "legacy_snapshot", "payload": snapshot,
             "occurred_at": "1970-01-01T00:00:00Z"}
    return validate_event(event)


class Ledger:
    def __init__(self, path, device_id=None):
        self.path = Path(path)
        self.device_id = device_id or str(uuid.uuid4())
        if not DEVICE_ID.fullmatch(self.device_id):
            raise SyncError("Invalid local device ID")
        # LAN probes and the GUI can reach one Ledger from different threads.
        # A re-entrant lock keeps every read/write operation on one state.
        self._lock = threading.RLock()
        self._staged = None
        self.sequence = 0
        self.lamport = 0
        self.events = {}
        if self.path.exists():
            document = json.loads(self.path.read_text(encoding="utf-8"))
            self._load(document)

    def _load(self, document):
        if not isinstance(document, dict) or set(document) != {"protocol", "device_id", "sequence", "lamport", "events"}:
            raise SyncError("Invalid ledger fields")
        if (type(document["protocol"]) is not int or document["protocol"] != PROTOCOL
                or not isinstance(document["device_id"], str)
                or not DEVICE_ID.fullmatch(document["device_id"])):
            raise SyncError("Unsupported ledger")
        self.device_id = document["device_id"]
        self.sequence = _nonnegative_int(document["sequence"], "sequence")
        self.lamport = _nonnegative_int(document["lamport"], "lamport")
        if not isinstance(document["events"], list) or len(document["events"]) > MAX_EVENTS:
            raise SyncError("Invalid ledger events")
        for event in document["events"]:
            validate_event(event)
            old = self.events.get(event["event_id"])
            if old is not None and old != event:
                raise SyncError("Conflicting event ID in ledger")
            self.events[event["event_id"]] = event
        baselines = {event["event_id"] for event in self.events.values()
                     if event["kind"] == "legacy_snapshot"}
        if len(baselines) > 1:
            raise SyncError("Conflicting legacy baselines in ledger")
        local_max = max((event["sequence"] for event in self.events.values()
                         if event["device_id"] == self.device_id), default=0)
        if self.sequence < local_max:
            raise SyncError("Ledger sequence went backwards")
        self.lamport = max(self.lamport, *(event["lamport"] for event in self.events.values())) if self.events else self.lamport

    def save(self):
        with self._lock:
            # _commit stages a candidate state so a failed write cannot leak
            # into memory; direct callers still persist the live state.
            events, sequence, lamport = (self.events, self.sequence, self.lamport) \
                if self._staged is None else self._staged
            self.path.parent.mkdir(parents=True, exist_ok=True)
            document = {"protocol": PROTOCOL, "device_id": self.device_id,
                        "sequence": sequence, "lamport": lamport,
                        "events": [events[key] for key in sorted(events)]}
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_bytes(canonical(document))
            os.replace(temp, self.path)

    def _commit(self, events, sequence, lamport):
        """Persist a validated candidate state, then publish it in memory.

        ``save`` is still invoked through ``self`` so tests and integrations
        that wrap or mock it keep working. Any failure (temp write, os.replace,
        or a mocked save) leaves the previous events, sequence and lamport
        untouched and is re-raised.
        """
        with self._lock:
            self._staged = (events, sequence, lamport)
            try:
                self.save()
            except BaseException:
                self._staged = None
                raise
            self.events, self.sequence, self.lamport = events, sequence, lamport
            self._staged = None

    def seed_legacy(self, settings):
        with self._lock:
            if self.events:
                raise SyncError("Migration requires an empty ledger")
            event = legacy_event(settings)
            self._commit({event["event_id"]: event}, self.sequence, self.lamport)
            return event["event_id"]

    def add(self, kind, payload=None, occurred_at=None):
        with self._lock:
            if kind == "legacy_snapshot":
                raise SyncError("Use seed_legacy for migration")
            if len(self.events) >= MAX_EVENTS:
                raise SyncError("Ledger full")
            sequence = self.sequence + 1
            lamport = self.lamport + 1
            event = {"protocol": PROTOCOL, "event_id": f"{self.device_id}:{sequence}",
                     "device_id": self.device_id, "sequence": sequence,
                     "lamport": lamport, "kind": kind, "payload": payload or {},
                     "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat()}
            validate_event(event)
            next_events = dict(self.events)
            next_events[event["event_id"]] = event
            self._commit(next_events, sequence, lamport)
            return event

    def add_preferences_batch(self, items, *, occurred_at=None):
        """Append a batch of ``pref_set`` events as one atomic transaction.

        ``items`` must be a non-empty ordered sequence of ``(key, value)``
        pairs. Keys must be unique within the batch and one of the shared
        preference keys. Values may be the empty string, but must be valid
        UTF-8 strings no longer than the protocol limit.

        Sequence and Lamport numbers follow the caller's order and every
        generated event is passed through :func:`validate_event`. Validation,
        identifier reservation, capacity/bundle checks and the write all
        happen under this ledger's re-entrant lock; ``_commit`` is called
        exactly once, so a failure leaves memory, ``export``/``summary`` and
        the existing ledger bytes untouched. This adds no crash-recovery or
        retry semantics beyond what ``_commit`` already provides.
        """
        with self._lock:
            if (isinstance(items, (str, bytes, bytearray, Mapping))
                    or not isinstance(items, Sequence)):
                raise SyncError("Preference batch must be an ordered sequence of pairs")
            batch = list(items)
            if not batch:
                raise SyncError("Preference batch must not be empty")
            if len(batch) > len(SHARED_PREFERENCE_KEYS):
                raise SyncError("Preference batch exceeds the supported keys")
            preferences = []
            seen = set()
            for item in batch:
                if (isinstance(item, (str, bytes, bytearray, Mapping))
                        or not isinstance(item, Sequence) or len(item) != 2):
                    raise SyncError("Each preference must be a (key, value) pair")
                key, value = item[0], item[1]
                if not isinstance(key, str):
                    raise SyncError("Preference key must be a string")
                if key not in SHARED_PREFERENCE_KEYS:
                    raise SyncError("Unsupported shared preference")
                if key in seen:
                    raise SyncError("Duplicate preference key")
                seen.add(key)
                if not isinstance(value, str):
                    raise SyncError("Invalid shared preference value")
                if len(value) > 80:
                    raise SyncError("Invalid shared preference value")
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise SyncError("Preference value is not valid Unicode") from exc
                preferences.append((key, value))
            timestamp = (datetime.now(timezone.utc).isoformat()
                         if occurred_at is None else occurred_at)
            count = len(preferences)
            next_sequence = self.sequence + count
            next_lamport = self.lamport + count
            # Interop guard only; existing methods and the protocol format are
            # intentionally left unchanged.
            if (next_sequence > JS_MAX_SAFE_INTEGER
                    or next_lamport > JS_MAX_SAFE_INTEGER):
                raise SyncError("Preference batch exceeds the safe integer range")
            if len(self.events) + count > MAX_EVENTS:
                raise SyncError("Ledger full")
            new_events = []
            for index, (key, value) in enumerate(preferences, start=1):
                event = {"protocol": PROTOCOL,
                         "event_id": f"{self.device_id}:{self.sequence + index}",
                         "device_id": self.device_id,
                         "sequence": self.sequence + index,
                         "lamport": self.lamport + index,
                         "kind": "pref_set",
                         "payload": {"key": key, "value": value},
                         "occurred_at": timestamp}
                validate_event(event)
                new_events.append(event)
            candidate = dict(self.events)
            for event in new_events:
                candidate[event["event_id"]] = event
            # Mirror export() for the candidate state so a successful batch can
            # always be exported afterwards.
            body = {"protocol": PROTOCOL, "source_device": self.device_id,
                    "events": [candidate[key] for key in sorted(candidate)]}
            body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
            if len(canonical(body)) > MAX_BUNDLE_BYTES:
                raise SyncError("Bundle too large; export fewer events")
            self._commit(candidate, next_sequence, next_lamport)
            return copy.deepcopy(new_events)

    def export(self):
        with self._lock:
            body = {"protocol": PROTOCOL, "source_device": self.device_id,
                    "events": [self.events[key] for key in sorted(self.events)]}
            body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
            result = canonical(body)
            if len(result) > MAX_BUNDLE_BYTES:
                raise SyncError("Bundle too large; export fewer events")
            return result

    def import_bundle(self, data):
        with self._lock:
            if not isinstance(data, bytes) or len(data) > MAX_BUNDLE_BYTES:
                raise SyncError("Invalid bundle size")
            try:
                bundle = json.loads(data)
            except (ValueError, UnicodeDecodeError) as exc:
                raise SyncError("Invalid bundle JSON") from exc
            if not isinstance(bundle, dict) or set(bundle) != {"protocol", "source_device", "events", "sha256"}:
                raise SyncError("Invalid bundle fields")
            if type(bundle["protocol"]) is not int or bundle["protocol"] != PROTOCOL:
                raise SyncError("Unsupported bundle protocol")
            if not isinstance(bundle["source_device"], str) or not DEVICE_ID.fullmatch(bundle["source_device"]):
                raise SyncError("Invalid bundle source")
            checksum = bundle.pop("sha256")
            if not isinstance(checksum, str) or checksum != hashlib.sha256(canonical(bundle)).hexdigest():
                raise SyncError("Bundle checksum mismatch")
            incoming = bundle["events"]
            if not isinstance(incoming, list) or len(incoming) > MAX_EVENTS:
                raise SyncError("Invalid event batch")
            additions = {}
            for event in incoming:
                validate_event(event)
                key = event["event_id"]
                old = additions.get(key, self.events.get(key))
                if old is not None and old != event:
                    raise SyncError(f"Conflicting event ID: {key}")
                if key not in self.events:
                    additions[key] = event
            baselines = {event["event_id"] for event in (*self.events.values(), *additions.values())
                         if event["kind"] == "legacy_snapshot"}
            if len(baselines) > 1:
                raise SyncError("Conflicting legacy baselines; manual migration required")
            if len(self.events) + len(additions) > MAX_EVENTS:
                raise SyncError("Ledger full")
            if additions:
                next_events = dict(self.events)
                next_events.update(additions)
                next_lamport = max(self.lamport, *(event["lamport"] for event in incoming)) + 1
                self._commit(next_events, self.sequence, next_lamport)
            return len(additions)

    def summary(self):
        with self._lock:
            counts = {key: 0 for key in COUNTERS}
            journal = {}
            preferences = {}
            for event in self.events.values():
                kind, payload = event["kind"], event["payload"]
                if kind == "legacy_snapshot":
                    for key in COUNTERS:
                        counts[key] += payload[key]
                    for index, item in enumerate(payload["history"]):
                        journal[f'{event["event_id"]}/{index}'] = item
                elif kind == "pat":
                    counts["pats"] += 1; counts["affinity"] += 1
                elif kind == "snack":
                    counts["snacks"] += 1; counts["affinity"] += 3
                elif kind == "focus_completed":
                    counts["focus_sessions"] += 1; counts["affinity"] += 5
                elif kind == "journal":
                    journal[event["event_id"]] = {"at": event["occurred_at"], "text": payload["text"]}
                elif kind == "pref_set":
                    key = payload["key"]
                    candidate = (event["lamport"], event["device_id"], event["event_id"], payload["value"])
                    if key not in preferences or candidate > preferences[key]:
                        preferences[key] = candidate
            return {**counts, "journal": journal,
                    "preferences": {key: candidate[3] for key, candidate in preferences.items()}}

    def journal_rows(self):
        """Deterministic display projection; timestamps never decide counters."""
        with self._lock:
            rows=[]
            for event in self.events.values():
                if event['kind']=='legacy_snapshot':
                    for index,entry in enumerate(event['payload']['history']):
                        rows.append(((0,index,event['event_id']),dict(entry)))
                elif event['kind']=='journal':
                    instant=datetime.fromisoformat(event['occurred_at'].replace('Z','+00:00'))
                    order=instant.astimezone(timezone.utc).isoformat()
                    rows.append(((1,order,event['event_id']),{
                        'at':instant.astimezone().strftime('%m-%d %H:%M'),
                        'text':event['payload']['text']}))
            return [entry for _,entry in sorted(rows,key=lambda pair:pair[0])][-100:]
