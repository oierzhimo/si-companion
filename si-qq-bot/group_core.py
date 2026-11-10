"""Group-only, explicit-mention conversation core for the 祀 companion.

This module is deliberately separate from :mod:`core`.  A group reply is only
ever produced after the official adapter has already verified an array ``at``
segment whose ``data.qq`` equals the verified bot account; this core repeats
the safety checks but never interprets free text as a mention.

Privacy model
-------------
The group ledger is a *separate* file, keyed by a SHA-256 **local grouping
key** computed from ``(group_id, sender_id)``.  It stores only the confirmed
mentioned turns for that exact pair.  It never receives the private
administrator profile, private C:/C2C turns, pairing material, or any other
private-chat state.  Group and sender numbers are not written as ledger keys,
paths, model messages, status strings, or logs; the verified bot id is stored
only as a plain value so a ledger cannot be reused by a different bot.  The
pair and deduplication hashes are local grouping keys, not anonymisation: QQ
number spaces are small enough that someone holding the state file can still
infer identities by guessing and hashing candidate pairs.  A caller may supply
``is_admin(sender_id)``; the expected implementation is
:meth:`core.QQCore.is_administrator`, a comparison-only method that does not
expose the private profile or turns.

Durability
----------
Inbound deduplication is persisted *before* the model is called.  A model
answer is persisted as a restricted pending record before the adapter sends
it.  Only a confirmed ``send_group_msg`` acknowledgement appends a turn to the
pair's context.  A rejected send is never claimed delivered.  A malformed ack,
timeout, or shutdown cancellation leaves the pending record unresolved and
fail-closed; nothing is automatically retried.  After a restart the pending
record is reloaded and blocked until a local user explicitly discards it with
:meth:`GroupChat.discard_uncertain_group_pending`.

The application owner must hold ``core.acquire_data_directory_lease(data_dir)``
from before this object is constructed until its last writer has stopped.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
from collections import OrderedDict
from pathlib import Path

import core as _private_core


__all__ = ['GroupChat', 'GROUP_ERROR_TEXT', 'GROUP_SYSTEM_ADMIN',
           'GROUP_SYSTEM_MEMBER']

_ENDPOINT = _private_core._ENDPOINT
_MODEL = _private_core._MODEL

_STATE_SCHEMA = 1
_STATE_FILENAME = 'si_qq_group_state.json'
_MAX_STATE_BYTES = 1_000_000
_MAX_BOT_ID = 256
_MAX_GROUP_ID = 256
_MAX_SENDER_ID = 256
_MAX_MESSAGE_ID = 256
_MAX_INPUT = 1000
# The complete model answer matches the request layer's bound.  The adapter
# splits it into QQ-sized group messages; the ledger stores the whole answer
# only after every chunk has been acknowledged.
_MAX_REPLY = 12000
_MAX_TURNS = 3
_MAX_PAIRS = 32
_MAX_PROCESSED_IDS = 256
_MAX_TOKEN = 512

_COOLDOWN_SECONDS = 1.5
_MAX_COOLDOWN_ENTRIES = 128

# Fixed natural fallback.  It intentionally contains no model, HTTP, storage,
# account, group, message, or exception detail.  It is safe to show in a group.
GROUP_ERROR_TEXT = '我这会儿有点没反应过来，等会儿再 @ 我试试吧。'

# This SYSTEM message is appended after the shared PERSONA so the group
# setting overrides any familiar-administrator assumption in the private-chat
# persona.  It deliberately contains no identifier or private detail.
_GROUP_SYSTEM_BASE = (
    '你是祀，现在正在一个 QQ 群聊中回应一条明确 @ 你的消息。群聊不是私聊，'
    '群内所有人都能看到你的回复。群聊是公开场合，无法获知或透露私聊中的'
    '偏好、内容与历史；不要把管理员偏好、私聊内容、私人记忆，或任何不是'
    '本群可见的信息带进这里；不要假装记得本群没有出现过的内容。'
    '只根据本群中你与当前发言者之间已确认的对话来回应；不确定的信息要自然'
    '说明，不要编造，也不要声称看过图片、音频或其他未提供的媒体内容。'
)
GROUP_SYSTEM_MEMBER = _GROUP_SYSTEM_BASE + (
    '当前发言者是群成员，不是你私下熟悉的管理员。你可以自然、友好、热情地'
    '和对方交流，也可以有自己的看法、幽默感和不同意见，不必像客服一样冷淡'
    '或一味顺从；但不要假设你知道对方的私人信息，不要使用只属于管理员的'
    '称呼、偏好或经历，也不要因为对方是群成员就疏远、敷衍或拒绝交流。'
)
GROUP_SYSTEM_ADMIN = _GROUP_SYSTEM_BASE + (
    '当前发言者是管理员本人，但这仍是公开群聊；不要用私聊中的称呼、偏好或'
    '经历作答，也不要把私聊内容带进群聊。'
)

_HEX = set('0123456789abcdef')
_DEDUP_DOMAIN = b'si-qq-group-dedup\x00'
_PAIR_DOMAIN = b'si-qq-group-pair\x00'


def _valid_hash(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in _HEX for char in value)
    )


def _pair_key(group_id, sender_id):
    """Opaque stable key for one group/sender pair; contains no account ID."""
    digest = hashlib.sha256()
    digest.update(_PAIR_DOMAIN)
    digest.update(group_id.encode('utf-8'))
    digest.update(b'\x00')
    digest.update(sender_id.encode('utf-8'))
    return digest.hexdigest()


def _dedup_key(group_id, message_id):
    """Opaque deduplication key for one group/message pair."""
    digest = hashlib.sha256()
    digest.update(_DEDUP_DOMAIN)
    digest.update(group_id.encode('utf-8'))
    digest.update(b'\x00')
    digest.update(message_id.encode('utf-8'))
    return digest.hexdigest()


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _valid_input(value):
    return _private_core._valid_message_text(value, _MAX_INPUT)


def _valid_reply(value):
    return _private_core._valid_message_text(value, _MAX_REPLY)


def _clean_reply(value):
    if not isinstance(value, str):
        return None
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    value = _private_core._DANGEROUS_TEXT.sub('', value).strip()
    if not value:
        return None
    if len(value) > _MAX_REPLY:
        value = value[:_MAX_REPLY].rstrip()
    if not value or not _private_core._valid_utf8(value):
        return None
    return value


class GroupChat:
    """Thread-safe, atomic group-mention ledger and model request builder.

    ``bot_id`` is the verified bot account.  ``request_reply`` defaults to the
    production loopback DeepSeek requester and may be replaced by tests.
    ``is_admin`` is an optional comparison-only callable; when it is absent or
    raises, every sender is treated as a group member, which is the privacy
    safe default.
    """

    def __init__(self, data_dir, bot_id, request_reply=None, is_admin=None):
        if not _private_core._valid_identifier(bot_id, _MAX_BOT_ID):
            raise ValueError('invalid QQ bot id')
        try:
            self.data_dir = Path(data_dir)
        except (TypeError, ValueError):
            raise ValueError('invalid data directory') from None
        if request_reply is not None and not callable(request_reply):
            raise TypeError('request_reply must be callable')
        if is_admin is not None and not callable(is_admin):
            raise TypeError('is_admin must be callable if provided')

        self.bot_id = bot_id
        self._state_path = self.data_dir / _STATE_FILENAME
        self._request_reply = request_reply
        self._is_admin = is_admin
        self._conversation = _private_core._CONVERSATION
        # Serialize users of this group ledger independently of the private
        # ledger. QQCore currently holds its own lock across a model call;
        # sharing that lock would also block the Qt group's status refresh
        # for the whole request. The shared process-wide lease still protects
        # the data directory against other application processes.
        self._lock = _private_core._process_state_lock(self._state_path)
        self._clock = None
        self._diagnostic_callback = None

        self._pairs = OrderedDict()
        self._processed = []
        self._pending = None
        self._pending_recovery_required = False

        # Per-(group, sender) cooldown.  Bounded and never persisted.
        self._cooldown = OrderedDict()
        # Only one model job may run at a time even if a caller misbehaves.
        self._model_busy = False

        self._load_state()

    # -- local diagnostics --------------------------------------------

    def set_diagnostic_callback(self, callback):
        """Install a fixed-code diagnostic hook, or None to remove it.

        Only the same fixed codes used by the private core are emitted.  The
        hook never receives message text, an identifier, a token, or
        exception text.
        """
        if callback is not None and not callable(callback):
            raise TypeError('diagnostic callback must be callable')
        self._diagnostic_callback = callback

    def _diagnostic(self, code):
        callback = self._diagnostic_callback
        if callback is None:
            return
        try:
            callback(code)
        except Exception:
            pass

    # -- public group entry point -------------------------------------

    def handle_group(self, group_id, sender_id, message_id, text):
        """Handle one already-verified group mention.

        The adapter has already checked that the group message is ``normal``,
        that an array ``at`` segment addresses the verified bot, and that the
        message contains useful text.  This method re-validates all of that
        cheaply, persists deduplication before touching the model, and returns
        None for every ignored request.  A successful model answer returns a
        ``CoreReply`` with kind ``group_chat`` and a delivery token; a model or
        response failure returns the fixed natural ``group_error`` notice with
        no token and no context change.
        """
        if not _private_core._valid_identifier(group_id, _MAX_GROUP_ID):
            return None
        if not _private_core._valid_identifier(sender_id, _MAX_SENDER_ID):
            return None
        if _private_core._same_account(sender_id, self.bot_id):
            return None
        if not _private_core._valid_identifier(message_id, _MAX_MESSAGE_ID):
            return None
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if not _valid_input(stripped):
            return None

        pair = _pair_key(group_id, sender_id)
        dedup = _dedup_key(group_id, message_id)
        now = self._now()

        with self._lock:
            if dedup in self._processed:
                return None
            if self._pending is not None:
                # One unresolved group delivery fails the whole group path
                # closed.  It is never silently replaced or retried.
                return None
            if self._model_busy:
                return None
            if not self._cooldown_allows(pair, now):
                return None
            # Persist inbound deduplication before any model or send work.
            processed = self._append_processed(dedup)
            try:
                self._commit(self._pairs, processed, self._pending)
            except Exception:
                return None
            self._model_busy = True
            self._touch_cooldown(pair, now)
            turns = [
                dict(turn)
                for turn in self._pairs.get(pair, {}).get('turns', ())
            ]

        try:
            return self._request_group_reply(
                pair, sender_id, stripped, turns, dedup
            )
        finally:
            with self._lock:
                self._model_busy = False

    def _request_group_reply(self, pair, sender_id, text, turns, dedup):
        is_admin = False
        if self._is_admin is not None:
            try:
                is_admin = bool(self._is_admin(sender_id))
            except Exception:
                # Privacy-safe default: an unverifiable administrator claim is
                # treated as an ordinary group member.
                is_admin = False

        self._diagnostic(_private_core._MODEL_PROCESSING)
        try:
            messages = self._build_messages(turns, text, is_admin)
            api_key = getattr(self._conversation, 'GENAI_DEFAULT_API_KEY')
            requester = self._request_reply
            if requester is None:
                requester = self._conversation.request_reply
        except Exception:
            self._diagnostic(_private_core._MODEL_PREPARE_FAILED)
            return _private_core.CoreReply(GROUP_ERROR_TEXT, 'group_error')

        try:
            from service_reply import respond
            raw_reply = respond(requester, (_ENDPOINT, _MODEL, messages, api_key), text, self.data_dir)
        except Exception as exc:
            try:
                failure_code = _private_core._model_failure_code(
                    exc,
                    _private_core._conversation_error_class(
                        self._conversation
                    ),
                )
            except Exception:
                failure_code = _private_core._MODEL_FAILED
            self._diagnostic(failure_code)
            return _private_core.CoreReply(GROUP_ERROR_TEXT, 'group_error')

        reply = _clean_reply(raw_reply)
        if reply is None:
            self._diagnostic(_private_core._MODEL_RESPONSE_INVALID)
            return _private_core.CoreReply(GROUP_ERROR_TEXT, 'group_error')

        token = secrets.token_urlsafe(32)
        pending = {
            'token_hash': _token_hash(token),
            'pair_key': pair,
            'dedup_key': dedup,
            'user': text,
            'assistant': reply,
            'created_at': self._now(),
        }
        with self._lock:
            if self._pending is not None:
                return None
            try:
                self._commit(self._pairs, self._processed, pending)
            except Exception:
                # No reply is returned unless its pending record is durable.
                return None
            self._pending_recovery_required = False
        return _private_core.CoreReply(reply, 'group_chat', token, getattr(raw_reply, 'segment', None))

    def _build_messages(self, turns, text, is_admin):
        persona = getattr(self._conversation, 'PERSONA')
        if not isinstance(persona, str) or not persona:
            raise ValueError('missing persona')
        system = GROUP_SYSTEM_ADMIN if is_admin else GROUP_SYSTEM_MEMBER
        messages = [
            {'role': 'system', 'content': persona},
            {'role': 'system', 'content': system},
        ]
        for turn in turns:
            messages.append({'role': 'user', 'content': turn['user']})
            messages.append(
                {'role': 'assistant', 'content': turn['assistant']}
            )
        messages.append({'role': 'user', 'content': text})
        return messages

    # -- delivery transaction -----------------------------------------

    def finalize_group(self, token, delivered):
        """Resolve a group delivery after the adapter's real QQ send attempt.

        True is returned only when a matching pending record was resolved and
        the atomic state write succeeded.  A confirmed delivery appends that
        one pair's turn; a definite rejection removes the pending record
        without adding context.  The inbound message stays deduplicated in
        both cases.
        """
        if not _private_core._valid_identifier(token, _MAX_TOKEN):
            return False
        if not isinstance(delivered, bool):
            return False

        with self._lock:
            pending = self._pending
            if not isinstance(pending, dict):
                return False
            if not hmac.compare_digest(
                    pending.get('token_hash', ''), _token_hash(token)):
                return False

            pairs = self._pairs
            if delivered:
                pair = pending.get('pair_key')
                if not _valid_hash(pair):
                    return False
                if (not _valid_input(pending.get('user'))
                        or not _valid_reply(pending.get('assistant'))):
                    return False
                previous = pairs.get(pair, {})
                turns = [
                    dict(turn)
                    for turn in previous.get('turns', ())
                ]
                turns = (turns + [{
                    'user': pending['user'],
                    'assistant': pending['assistant'],
                }])[-_MAX_TURNS:]
                pairs = OrderedDict(pairs)
                pairs[pair] = {'turns': turns}
                pairs.move_to_end(pair)
                while len(pairs) > _MAX_PAIRS:
                    pairs.popitem(last=False)

            try:
                self._commit(pairs, self._processed, None)
            except Exception:
                # Memory, old bytes, and the pending record all stay intact.
                return False
            self._pending_recovery_required = False
            return True

    def mark_group_delivery_unknown(self, token):
        """Keep an unresolved pending record after a timeout/cancellation.

        The adapter calls this only after the send attempt has fully unwound.
        It changes process memory only; the durable pending record already has
        the same recovery semantics after a restart.  It never sends, calls
        the model, retries, or writes a file.
        """
        if not _private_core._valid_identifier(token, _MAX_TOKEN):
            return False
        with self._lock:
            pending = self._pending
            if not isinstance(pending, dict):
                return False
            if not hmac.compare_digest(
                    pending.get('token_hash', ''), _token_hash(token)):
                return False
            self._pending_recovery_required = True
            return True

    # Compatibility alias so the adapter's generic unknown marker can be
    # reused without special-casing.  It has exactly the same semantics.
    def mark_delivery_unknown(self, token):
        return self.mark_group_delivery_unknown(token)

    # -- explicit local recovery --------------------------------------

    def local_status(self):
        """Return recovery state for the local UI as booleans only.

        Contains no account number, group number, pair key, message, token, or
        secret.
        """
        with self._lock:
            return {
                'pending_recovery_required': bool(
                    self._pending is not None
                    and self._pending_recovery_required
                ),
            }

    def discard_uncertain_group_pending(self):
        """Explicitly drop the unresolved group delivery, atomically.

        Only a local user action may call this.  It returns False when there
        is no recoverable pending record or when the atomic write fails.  On
        success the pending record is removed while its inbound message stays
        deduplicated and confirmed pair turns are untouched.  Nothing is
        replayed or sent.
        """
        with self._lock:
            if (self._pending is None
                    or not self._pending_recovery_required):
                return False
            try:
                self._commit(self._pairs, self._processed, None)
            except Exception:
                return False
            self._pending_recovery_required = False
            return True

    # -- bounded in-process caches ------------------------------------

    def _now(self):
        clock = self._clock if self._clock is not None else time.monotonic
        return float(clock())

    def _cooldown_allows(self, pair, now):
        entry = self._cooldown.get(pair)
        if entry is None:
            return True
        return now - entry >= _COOLDOWN_SECONDS

    def _touch_cooldown(self, pair, now):
        self._cooldown[pair] = now
        self._cooldown.move_to_end(pair)
        while len(self._cooldown) > _MAX_COOLDOWN_ENTRIES:
            self._cooldown.popitem(last=False)

    def _append_processed(self, dedup):
        processed = [item for item in self._processed if item != dedup]
        processed.append(dedup)
        return processed[-_MAX_PROCESSED_IDS:]

    # -- state file ----------------------------------------------------

    def _state_payload(self, pairs, processed, pending):
        return {
            'schema': _STATE_SCHEMA,
            'bot_id': self.bot_id,
            'pairs': {
                key: {'turns': [dict(turn) for turn in entry['turns']]}
                for key, entry in pairs.items()
            },
            'processed_message_ids': list(processed),
            'pending': dict(pending) if pending is not None else None,
        }

    def _commit(self, pairs, processed, pending):
        pairs_copy = OrderedDict(
            (key, {'turns': [dict(turn) for turn in entry['turns']]})
            for key, entry in pairs.items()
        )
        processed_copy = list(processed)
        pending_copy = dict(pending) if pending is not None else None
        # The ledger's fixed turn/pair caps permit more than 1 MB in the worst
        # case (many 12000-character answers across 32 pairs).  Evict only the
        # oldest pair contexts until the encoded document fits the existing
        # bound.  Deduplicated message hashes and the pending record are never
        # evicted, no record is migrated, and the current reply is never
        # trimmed.  With ordinary answers this loop does not run.
        while True:
            payload = self._state_payload(
                pairs_copy, processed_copy, pending_copy
            )
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(',', ':')
            ).encode('utf-8')
            if len(encoded) <= _MAX_STATE_BYTES:
                break
            if not pairs_copy:
                raise ValueError('group state exceeds the local size bound')
            pairs_copy.popitem(last=False)
        _private_core._atomic_write_json(self._state_path, payload)
        self._pairs = pairs_copy
        self._processed = processed_copy
        self._pending = pending_copy

    def _load_state(self):
        path = self._state_path
        try:
            if not path.exists():
                return
            if not path.is_file() or path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError()
            raw = path.read_bytes()
            if len(raw) > _MAX_STATE_BYTES:
                raise ValueError()
            data = json.loads(raw.decode('utf-8'))
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            # Deeply nested JSON can raise RecursionError; a corrupt local
            # file must fail closed with its bytes untouched, not crash the
            # group surface.
            raise RuntimeError(
                'QQ group state cannot be read; the original file was kept'
            ) from None

        try:
            if not isinstance(data, dict):
                raise ValueError()
            if set(data) != {
                'schema', 'bot_id', 'pairs',
                'processed_message_ids', 'pending',
            }:
                raise ValueError()
            if data['schema'] != _STATE_SCHEMA:
                raise ValueError()
            if data['bot_id'] != self.bot_id:
                raise RuntimeError(
                    'QQ group state belongs to a different bot id; '
                    'refusing to use it'
                )

            raw_pairs = data['pairs']
            if not isinstance(raw_pairs, dict) or len(raw_pairs) > _MAX_PAIRS:
                raise ValueError()
            pairs = OrderedDict()
            for key, entry in raw_pairs.items():
                if not _valid_hash(key):
                    raise ValueError()
                if (not isinstance(entry, dict)
                        or set(entry) != {'turns'}):
                    raise ValueError()
                turns = entry['turns']
                if (not isinstance(turns, list)
                        or len(turns) > _MAX_TURNS):
                    raise ValueError()
                clean_turns = []
                for turn in turns:
                    if (not isinstance(turn, dict)
                            or set(turn) != {'user', 'assistant'}
                            or not _valid_input(turn['user'])
                            or not _valid_reply(turn['assistant'])):
                        raise ValueError()
                    clean_turns.append({
                        'user': turn['user'],
                        'assistant': turn['assistant'],
                    })
                pairs[key] = {'turns': clean_turns}

            processed = data['processed_message_ids']
            if (not isinstance(processed, list)
                    or len(processed) > _MAX_PROCESSED_IDS
                    or len(set(processed)) != len(processed)
                    or any(not _valid_hash(item) for item in processed)):
                raise ValueError()

            pending = data['pending']
            if pending is not None:
                if (not isinstance(pending, dict)
                        or set(pending) != {
                            'token_hash', 'pair_key', 'dedup_key',
                            'user', 'assistant', 'created_at',
                        }):
                    raise ValueError()
                if not _valid_hash(pending['token_hash']):
                    raise ValueError()
                if not _valid_hash(pending['pair_key']):
                    raise ValueError()
                if (not _valid_hash(pending['dedup_key'])
                        or pending['dedup_key'] not in processed):
                    raise ValueError()
                if not _valid_input(pending['user']):
                    raise ValueError()
                if not _valid_reply(pending['assistant']):
                    raise ValueError()
                created = pending['created_at']
                if (not isinstance(created, (int, float))
                        or isinstance(created, bool)
                        or not math.isfinite(created) or created < 0):
                    raise ValueError()

            self._pairs = pairs
            self._processed = list(processed)
            self._pending = dict(pending) if pending is not None else None
            # Only a pending loaded here is unknown because of a restart.
            self._pending_recovery_required = pending is not None
        except RuntimeError:
            raise
        except (ValueError, TypeError, KeyError, IndexError):
            raise RuntimeError(
                'QQ group state is corrupt; the original file was kept'
            ) from None
