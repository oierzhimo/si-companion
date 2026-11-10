"""Bounded, thread-safe, in-memory buffer for group media awaiting an @ mention.

The buffer exists so that non-text events (images, stickers, files and any
future non-text segment type) can be received silently and later attached to a
real @ mention from the *same* sender in the *same* group.  Nothing here talks
to the network, reads files, renders pictures, logs or calls a model; the only
imports are from the standard library.

Public surface
--------------
``PendingGroupMedia``
    ``offer(event, bot_id) -> bool``
    ``take(group_id, sender_id) -> PendingMediaBatch`` (a ``list`` of
    ``{"message_id": str, "segments": [ ... ]}`` items in arrival order)
    ``restore(group_id, sender_id, batch) -> int``
    ``clear() -> None``
    ``stats() -> dict`` with integer counters only

``PendingMediaBatch``
    A ``list`` subclass.  It carries private, opaque restore reservations so
    that ``restore`` can put items back with their original arrival sequence
    and original expiry.  It must be passed back as-is; a plain ``list`` is
    rejected by ``restore`` because it cannot carry that information.

All public methods are serialised by one ``threading.RLock``.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field

__all__ = ["PendingGroupMedia", "PendingMediaBatch"]

# Segment types that carry no attachment / are not media.
_EXCLUDED_SEGMENT_TYPES = frozenset(("text", "at", "reply"))

_MAX_DECIMAL_CHARS = 32
_MAX_DECIMAL_INT_BITS = 128
_MAX_MESSAGE_ID_CHARS = 128
_MAX_SEGMENT_TYPE_CHARS = 64
_MAX_JSON_KEY_CHARS = 128
_MAX_JSON_INT_BITS = 512


class _InvalidJson(Exception):
    """Internal: a value is not bounded, ordinary JSON."""


def _canon_decimal(value):
    """Canonicalise a group/user/bot id or return ``None`` if invalid.

    Accepted inputs are non-negative ``int`` (never ``bool``) or ASCII decimal
    strings of 1..32 characters.  Leading zeroes are stripped consistently so
    ``"000222"``, ``"222"`` and ``222`` are the same identifier.
    """

    if type(value) is int:
        if value < 0 or value.bit_length() > _MAX_DECIMAL_INT_BITS:
            return None
        text = str(value)
        if len(text) > _MAX_DECIMAL_CHARS:
            return None
        return text
    if type(value) is str:
        if not (1 <= len(value) <= _MAX_DECIMAL_CHARS):
            return None
        for ch in value:
            if not ("0" <= ch <= "9"):
                return None
        stripped = value.lstrip("0")
        return stripped if stripped else "0"
    return None


def _canon_message_id(value):
    """Canonicalise a message id or return ``None`` if invalid.

    ``int`` (never ``bool``) or a 1..128 character printable-ASCII string with
    no surrounding whitespace.  Digit-only strings are leading-zero
    canonicalised to match their integer form.
    """

    if type(value) is int:
        if value.bit_length() > _MAX_JSON_INT_BITS:
            return None
        text = str(value)
        if len(text) > _MAX_MESSAGE_ID_CHARS:
            return None
        return text
    if type(value) is str:
        if not (1 <= len(value) <= _MAX_MESSAGE_ID_CHARS):
            return None
        for ch in value:
            code = ord(ch)
            if code < 0x20 or code > 0x7E:
                return None
        if value != value.strip():
            return None
        if all("0" <= ch <= "9" for ch in value):
            stripped = value.lstrip("0")
            return stripped if stripped else "0"
        return value
    return None


class _SanitizeState:
    """Per-segment node/character budget used while copying JSON."""

    __slots__ = ("nodes", "max_nodes", "chars", "max_chars")

    def __init__(self, max_nodes, max_chars):
        self.nodes = 0
        self.max_nodes = max_nodes
        self.chars = 0
        self.max_chars = max_chars


def _sanitize_json(value, depth, max_depth, state, max_string):
    """Return a fresh, plain-JSON copy of ``value`` or raise ``_InvalidJson``.

    Only exact ``dict`` / ``list`` / ``str`` / ``int`` / ``float`` / ``bool``
    / ``None`` types are accepted, so subclasses, tuples, bytes, sets, custom
    objects and other non-JSON types are rejected.  Depth, node count, string
    length and non-finite numbers are bounded; a reference cycle is rejected
    when its depth exceeds ``max_depth``.
    """

    state.nodes += 1
    if state.nodes > state.max_nodes:
        raise _InvalidJson()

    kind = type(value)
    if value is None:
        return None
    if kind is bool:
        return value
    if kind is int:
        if value.bit_length() > _MAX_JSON_INT_BITS:
            raise _InvalidJson()
        return value
    if kind is float:
        if not math.isfinite(value):
            raise _InvalidJson()
        return value
    if kind is str:
        if len(value) > max_string:
            raise _InvalidJson()
        state.chars += len(value)
        if state.chars > state.max_chars:
            raise _InvalidJson()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise _InvalidJson() from None
        return value
    if kind is list:
        if depth >= max_depth:
            raise _InvalidJson()
        copy = []
        for entry in value:
            copy.append(
                _sanitize_json(entry, depth + 1, max_depth, state, max_string)
            )
        return copy
    if kind is dict:
        if depth >= max_depth:
            raise _InvalidJson()
        copy = {}
        for key, entry in value.items():
            if type(key) is not str:
                raise _InvalidJson()
            if len(key) > _MAX_JSON_KEY_CHARS or len(key) > max_string:
                raise _InvalidJson()
            try:
                key.encode("utf-8")
            except UnicodeEncodeError:
                raise _InvalidJson() from None
            copy[key] = _sanitize_json(
                entry, depth + 1, max_depth, state, max_string
            )
        return copy
    raise _InvalidJson()


def _serialized_size(value):
    """UTF-8 byte length of a compact JSON serialisation (no NaN/Infinity)."""

    blob = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return len(blob.encode("utf-8"))


def _clone_json(value):
    """Deep-copy a plain JSON structure without any validation."""

    kind = type(value)
    if kind is dict:
        return {key: _clone_json(entry) for key, entry in value.items()}
    if kind is list:
        return [_clone_json(entry) for entry in value]
    return value


def _item_serialized_size(message_id, segments):
    """Byte size of the exact stored item shape (used for byte accounting)."""

    return _serialized_size(
        {"message_id": message_id, "segments": list(segments)}
    )


@dataclass
class _Item:
    seq: int
    expiry: float
    message_id: str
    segments: tuple
    size: int
    group: str
    sender: str


@dataclass
class _Pair:
    items: list = field(default_factory=list)


@dataclass
class _Reservation:
    owner: object
    gen: int
    group: str
    sender: str
    seq: int
    expiry: float
    message_id: str
    segments: tuple
    size: int
    restored: bool = False


class PendingMediaBatch(list):
    """A list of saved media items plus opaque restore reservations.

    Elements are exactly ``{"message_id": str, "segments": [dict, ...]}`` in
    arrival order.  The list contents are copies: mutating them is safe and has
    no effect on the buffer.  Pass the *same object* to
    ``PendingGroupMedia.restore`` (optionally via ``batch.copy()``); a plain
    list, slice or JSON round-trip loses the reservation metadata.
    """

    __slots__ = ("_reservations", "_pair_key")

    def __init__(self, items=(), reservations=(), key=None):
        list.__init__(self, items)
        self._reservations = tuple(reservations)
        self._pair_key = key

    def copy(self):
        """Return an equivalent batch that shares the same reservations."""

        return PendingMediaBatch(
            list(self), self._reservations, self._pair_key
        )

    @property
    def pair(self):
        """The ``(group, sender)`` pair this batch was taken for, if known."""

        return self._pair_key

    @property
    def restorable(self):
        """How many items still carry an unused restore reservation."""

        count = 0
        for reservation in self._reservations:
            if isinstance(reservation, _Reservation) and not reservation.restored:
                count += 1
        return count


def _item_seq(item):
    return item.seq


def _reservation_order(reservation):
    if not isinstance(reservation, _Reservation):
        return 0
    if type(reservation.seq) is int:
        return reservation.seq
    return 0


class PendingGroupMedia:
    """Bounded, thread-safe staging area for unmentioned group media."""

    def __init__(
        self,
        *,
        ttl_seconds=600,
        max_pairs=64,
        max_items_per_pair=16,
        max_total_bytes=2 * 1024 * 1024,
        clock=time.monotonic,
        max_segments=64,
        max_segment_bytes=64 * 1024,
        max_string_chars=8192,
        max_json_depth=8,
        max_json_nodes=2048,
    ):
        self._ttl = _positive_duration(ttl_seconds, "ttl_seconds")
        self._max_pairs = _positive_int(max_pairs, "max_pairs")
        self._max_items_per_pair = _positive_int(
            max_items_per_pair, "max_items_per_pair"
        )
        self._max_total_bytes = _positive_int(
            max_total_bytes, "max_total_bytes"
        )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._max_segments = _positive_int(max_segments, "max_segments")
        self._max_segment_bytes = _positive_int(
            max_segment_bytes, "max_segment_bytes"
        )
        self._max_string_chars = _positive_int(
            max_string_chars, "max_string_chars"
        )
        self._max_json_depth = _positive_int(max_json_depth, "max_json_depth")
        self._max_json_nodes = _positive_int(max_json_nodes, "max_json_nodes")

        self._max_dedup_entries = max(
            1, self._max_pairs * self._max_items_per_pair
        )

        self._instance = object()
        self._lock = threading.RLock()
        self._pairs = {}  # (group, sender) -> _Pair
        self._dedup = {}  # (group, sender, message_id) -> expiry
        self._generation = 0
        self._seq = 0
        self._total_bytes = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def offer(self, event, bot_id):
        """Validate ``event`` and silently stage its non-text segments.

        Returns ``True`` only when a new, valid, unexpired item was accepted.
        Returns ``False`` for anything malformed, for duplicates
        (group + sender + message id within the TTL), for messages with no
        eligible segment, and for messages too large to be stored whole.
        """

        with self._lock:
            now = self._clock()
            self._purge_locked(now)

            header = self._parse_event(event, bot_id)
            if header is None:
                return False
            group, sender, message_id, raw_segments = header

            dedup_key = (group, sender, message_id)
            if dedup_key in self._dedup:
                return False
            # Also deduplicate against items still held, in case the bounded
            # dedup registry had to evict this key under a flood.
            pair = self._pairs.get((group, sender))
            if pair is not None:
                for existing in pair.items:
                    if existing.message_id == message_id:
                        return False

            segments, _segment_bytes = self._sanitize_segments(raw_segments)
            if segments is None:
                return False

            size = _item_serialized_size(message_id, segments)
            if size > self._max_total_bytes:
                return False

            self._insert_locked(
                group, sender, message_id, segments, size, now
            )
            self._remember_dedup_locked(dedup_key, now)
            self._enforce_limits_locked()
            return True

    def take(self, group_id, sender_id):
        """Atomically remove and return all media staged for one pair.

        The returned object is a :class:`PendingMediaBatch` (a ``list``) of
        items in arrival order.  Nothing from any other group or sender is
        ever returned.  The reader should call this at the moment it accepts
        a validated real @ mention, not later in worker processing.
        """

        group = _canon_decimal(group_id)
        sender = _canon_decimal(sender_id)
        if group is None or sender is None:
            return PendingMediaBatch()

        key = (group, sender)
        with self._lock:
            now = self._clock()
            self._purge_locked(now)

            pair = self._pairs.pop(key, None)
            if pair is None or not pair.items:
                return PendingMediaBatch(key=key)

            items = sorted(pair.items, key=_item_seq)
            self._total_bytes -= sum(item.size for item in items)
            if self._total_bytes < 0:
                self._total_bytes = 0

            public_items = [
                {
                    "message_id": item.message_id,
                    "segments": [_clone_json(seg) for seg in item.segments],
                }
                for item in items
            ]
            reservations = tuple(
                _Reservation(
                    owner=self._instance,
                    gen=self._generation,
                    group=group,
                    sender=sender,
                    seq=item.seq,
                    expiry=item.expiry,
                    message_id=item.message_id,
                    segments=item.segments,
                    size=item.size,
                )
                for item in items
            )
            return PendingMediaBatch(public_items, reservations, key)

    def restore(self, group_id, sender_id, items):
        """Put a previously taken batch back with its original order/expiry.

        Only the :class:`PendingMediaBatch` object returned by :meth:`take`
        (or a ``.copy()`` of it) is accepted.  Expired reservations, stale
        reservations from before :meth:`clear`, and already restored
        reservations are skipped.  Original expiry is never extended; the
        normal bounds (items per pair, total bytes, pair count) are applied
        afterwards by evicting oldest items first.

        Returns the number of items that were re-inserted (some may be
        immediately evicted again by the bounds, and restored items are older
        than any already-held item, so they are the first eviction candidates).
        """

        group = _canon_decimal(group_id)
        sender = _canon_decimal(sender_id)
        if group is None or sender is None:
            raise ValueError("group_id and sender_id must be decimal ids")
        if not isinstance(items, PendingMediaBatch):
            raise TypeError(
                "restore() requires the PendingMediaBatch returned by take()"
            )

        key = (group, sender)
        with self._lock:
            now = self._clock()
            self._purge_locked(now)

            batch_key = items._pair_key
            if batch_key is not None:
                try:
                    batch_key = tuple(batch_key)
                except TypeError:
                    raise ValueError("malformed batch pair key") from None
                if batch_key != key:
                    raise ValueError(
                        "batch was taken for a different group/sender pair"
                    )

            reservations = items._reservations
            if type(reservations) is not tuple and type(reservations) is not list:
                return 0
            if not reservations:
                return 0

            pair = self._pairs.get(key)
            restored = 0
            for reservation in sorted(reservations, key=_reservation_order):
                if not isinstance(reservation, _Reservation):
                    continue
                if reservation.restored:
                    continue
                if reservation.owner is not self._instance:
                    continue
                if reservation.gen != self._generation:
                    continue
                if (reservation.group, reservation.sender) != key:
                    continue

                seq = reservation.seq
                if type(seq) is not int or seq <= 0 or seq > self._seq:
                    continue
                expiry = reservation.expiry
                if type(expiry) not in (int, float) or type(expiry) is bool:
                    continue
                try:
                    finite = math.isfinite(expiry)
                except (TypeError, OverflowError):
                    finite = False
                if not finite or now >= expiry:
                    continue
                # Never allow a crafted reservation to extend the lifetime.
                expiry = min(float(expiry), now + self._ttl)

                message_id = _canon_message_id(reservation.message_id)
                if message_id is None:
                    continue

                stored = reservation.segments
                if type(stored) is not tuple and type(stored) is not list:
                    continue
                segments, _segment_bytes = self._sanitize_segments(list(stored))
                if segments is None:
                    continue
                size = _item_serialized_size(message_id, segments)
                if size > self._max_total_bytes:
                    continue
                if pair is not None and any(
                    existing.message_id == message_id
                    for existing in pair.items
                ):
                    continue

                if pair is None:
                    pair = _Pair()
                    self._pairs[key] = pair
                pair.items.append(
                    _Item(
                        seq=seq,
                        expiry=expiry,
                        message_id=message_id,
                        segments=segments,
                        size=size,
                        group=group,
                        sender=sender,
                    )
                )
                self._total_bytes += size
                reservation.restored = True
                restored += 1

            if pair is not None:
                pair.items.sort(key=_item_seq)
                self._enforce_limits_locked()
                if not pair.items and self._pairs.get(key) is pair:
                    del self._pairs[key]
            return restored

    def clear(self):
        """Drop every staged item, dedup entry and outstanding reservation.

        Call on group switch-off, adapter stop and reconnect.  Any batch taken
        before ``clear`` can no longer be restored.
        """

        with self._lock:
            self._pairs.clear()
            self._dedup.clear()
            self._total_bytes = 0
            self._generation += 1

    def stats(self):
        """Return counts and byte totals only - never ids or content."""

        with self._lock:
            now = self._clock()
            self._purge_locked(now)
            item_count = 0
            byte_count = 0
            for pair in self._pairs.values():
                item_count += len(pair.items)
                byte_count += sum(item.size for item in pair.items)
            self._total_bytes = byte_count
            return {
                "pairs": len(self._pairs),
                "items": item_count,
                "bytes": byte_count,
                "dedup_keys": len(self._dedup),
            }

    # ------------------------------------------------------------------
    # validation helpers
    # ------------------------------------------------------------------

    def _parse_event(self, event, bot_id):
        """Return ``(group, sender, message_id, segments)`` or ``None``."""

        if type(event) is not dict:
            return None
        if not _is_exact_string(event.get("post_type"), "message"):
            return None
        if not _is_exact_string(event.get("message_type"), "group"):
            return None
        if not _is_exact_string(event.get("sub_type"), "normal"):
            return None

        bot = _canon_decimal(bot_id)
        if bot is None:
            return None
        self_id = _canon_decimal(event.get("self_id"))
        if self_id is None or self_id != bot:
            return None

        user_id = _canon_decimal(event.get("user_id"))
        group_id = _canon_decimal(event.get("group_id"))
        if user_id is None or group_id is None:
            return None
        if user_id == self_id:
            return None

        message_id = _canon_message_id(event.get("message_id"))
        if message_id is None:
            return None

        message = event.get("message")
        if type(message) is not list:
            return None
        if len(message) > self._max_segments:
            return None

        return group_id, user_id, message_id, message

    def _sanitize_segments(self, raw_segments):
        """Copy eligible segments, returning ``(tuple_of_dicts, bytes)``.

        Returns ``(None, 0)`` when the message is malformed, has no eligible
        segment, or cannot be stored whole under the byte limits.
        """

        cleaned = []
        total = 0
        for raw in raw_segments:
            if type(raw) is not dict:
                return None, 0
            seg_type = raw.get("type")
            if type(seg_type) is not str:
                return None, 0
            if not (1 <= len(seg_type) <= _MAX_SEGMENT_TYPE_CHARS):
                return None, 0
            for ch in seg_type:
                if not (0x21 <= ord(ch) <= 0x7E):
                    return None, 0
            if seg_type.lower() in _EXCLUDED_SEGMENT_TYPES:
                continue

            data = raw.get("data", {})
            if data is None:
                data = {}
            if type(data) is not dict:
                return None, 0

            state = _SanitizeState(
                self._max_json_nodes, self._max_string_chars
            )
            try:
                clean_data = _sanitize_json(
                    data,
                    0,
                    self._max_json_depth,
                    state,
                    self._max_string_chars,
                )
                segment_bytes = _serialized_size(clean_data)
            except (
                _InvalidJson,
                TypeError,
                ValueError,
                UnicodeError,
                RecursionError,
            ):
                return None, 0

            if segment_bytes > self._max_segment_bytes:
                return None, 0
            total += segment_bytes
            if total > self._max_total_bytes:
                return None, 0
            cleaned.append({"type": seg_type, "data": clean_data})

        if not cleaned:
            return None, 0
        return tuple(cleaned), total

    # ------------------------------------------------------------------
    # internal state helpers (all called with the lock held)
    # ------------------------------------------------------------------

    def _insert_locked(self, group, sender, message_id, segments, size, now):
        self._seq += 1
        item = _Item(
            seq=self._seq,
            expiry=now + self._ttl,
            message_id=message_id,
            segments=segments,
            size=size,
            group=group,
            sender=sender,
        )
        key = (group, sender)
        pair = self._pairs.get(key)
        if pair is None:
            pair = _Pair()
            self._pairs[key] = pair
        pair.items.append(item)
        self._total_bytes += size

    def _remember_dedup_locked(self, dedup_key, now):
        self._dedup[dedup_key] = now + self._ttl
        while len(self._dedup) > self._max_dedup_entries:
            oldest = next(iter(self._dedup))
            del self._dedup[oldest]

    def _purge_locked(self, now):
        for key in list(self._pairs):
            pair = self._pairs[key]
            kept = [item for item in pair.items if item.expiry > now]
            if len(kept) != len(pair.items):
                expired = [item for item in pair.items if item.expiry <= now]
                self._total_bytes -= sum(item.size for item in expired)
                pair.items = kept
            if not pair.items:
                del self._pairs[key]

        if self._dedup:
            stale = [
                dedup_key
                for dedup_key, expiry in self._dedup.items()
                if expiry <= now
            ]
            for dedup_key in stale:
                del self._dedup[dedup_key]

        if self._total_bytes < 0:
            self._total_bytes = 0

    def _enforce_limits_locked(self):
        # Per-pair item cap: drop that pair's oldest items first.
        for pair in self._pairs.values():
            if len(pair.items) > self._max_items_per_pair:
                pair.items.sort(key=_item_seq)
                excess = len(pair.items) - self._max_items_per_pair
                removed = pair.items[:excess]
                del pair.items[:excess]
                self._total_bytes -= sum(item.size for item in removed)

        # Global byte cap: evict the oldest item across all pairs.
        while self._total_bytes > self._max_total_bytes:
            victim_key = None
            victim_item = None
            for key, pair in self._pairs.items():
                if not pair.items:
                    continue
                oldest = min(pair.items, key=_item_seq)
                if victim_item is None or oldest.seq < victim_item.seq:
                    victim_item = oldest
                    victim_key = key
            if victim_item is None:
                self._total_bytes = 0
                break
            pair = self._pairs[victim_key]
            pair.items = [
                item for item in pair.items if item is not victim_item
            ]
            self._total_bytes -= victim_item.size
            if not pair.items:
                del self._pairs[victim_key]

        # Pair cap: evict the least recently active pair.
        while len(self._pairs) > self._max_pairs:
            victim_key = min(
                self._pairs,
                key=lambda k: max(
                    (item.seq for item in self._pairs[k].items), default=-1
                ),
            )
            pair = self._pairs.pop(victim_key)
            self._total_bytes -= sum(item.size for item in pair.items)

        if self._total_bytes < 0:
            self._total_bytes = 0


def _is_exact_string(value, expected):
    return type(value) is str and value == expected


def _positive_int(value, name):
    if type(value) is bool or not isinstance(value, int) or value < 1:
        raise ValueError("%s must be a positive integer" % name)
    return int(value)


def _positive_duration(value, name):
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError("%s must be a positive finite number" % name)
    try:
        finite = math.isfinite(value)
    except (TypeError, OverflowError):
        finite = False
    if not finite or value <= 0:
        raise ValueError("%s must be a positive finite number" % name)
    return float(value)
