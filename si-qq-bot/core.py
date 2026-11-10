"""Reusable, local-only QQ private-chat core for the 祀 desktop companion.

The official Tencent QQ adapter owns all QQ I/O.  This module only decides
whether a private C2C text message is allowed, builds the model request, and
keeps a small atomic local state file.  It never opens a network connection on
construction and never sends a QQ message itself.

Delivery is a two-step transaction.  ``handle_private`` persists a restricted
pending record and returns a ``CoreReply`` carrying a delivery token.  The
adapter sends the text, then reports the real outcome through
``finalize_reply``.  Only a confirmed send is appended to future model context.
A failed or cancelled send is removed from pending but its message ID remains
deduplicated.  If the adapter's send attempt ends without a definite
acknowledgement (timeout, lost connection, or shutdown while the request was in
flight), it calls ``mark_delivery_unknown`` only after that attempt has fully
unwound.  This keeps the pending record, enables the explicit local recovery
path for the running process, and never treats the outcome as delivered or as
a definite failure.  Recovery cannot race a send that may still be running:
the adapter never starts another send for that token, and new chats fail
closed while the pending record exists.  If the process dies while a send
outcome is unknown, the persisted pending record is retained with the same
semantics.  If the adapter retained the token it can still resolve that record
after restart with ``finalize_reply``.  Otherwise the local UI can inspect
``local_status`` and explicitly call ``discard_uncertain_pending`` to drop only
that one unresolved record, keeping its message ID deduplicated and
already-delivered history intact.  Nothing received through QQ can trigger
recovery.  This is bounded inbound deduplication plus fail-closed delivery,
not an end-to-end exactly-once guarantee across process crashes.

For cross-process use, the application must hold
``acquire_data_directory_lease(data_dir)`` before constructing the core or
reading the state file, and release it only after every background writer has
stopped.  The lease is a non-blocking OS byte-range lock on a fixed-name file
in that directory.  It never waits, never deletes or rewrites the state or
lock file, and Windows releases it automatically when the process exits.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


import reply_settings

_REPLY_MODE_COMMANDS = {'/语音': 'voice', '/文字': 'text'}
_REPLY_MODE_PREFIXES = tuple(_REPLY_MODE_COMMANDS)
_MODE_VOICE_TEXT = (
    '已切换为语音回复：之后的私聊会尝试本机 VoiceStudio 声线 A；'
    '语音生成失败时会改发原文字。'
)
_MODE_TEXT_TEXT = '已切换为文字回复：之后的私聊只发送文字。'
_MODE_FAILED_TEXT = '本机无法保存回复方式设置；本次未更改，仍按原设置回复。'

__all__ = ['QQCore', 'CoreReply']


_ENDPOINT = 'http://127.0.0.1:8000/v1'
_MODEL = 'DeepSeek-V4.1-Flash'

_STATE_SCHEMA = 1
_STATE_FILENAME = 'si_qq_core_state.json'
_MAX_STATE_BYTES = 1_000_000
_MAX_APP_ID = 256
_MAX_OPENID = 256
_MAX_MESSAGE_ID = 256
_MAX_INPUT = 2000
# A complete model answer may be up to the request layer's bound.  The
# adapter splits it into QQ-sized messages; the core never truncates a reply
# merely to satisfy the old single-message 2000-character ceiling.
_MAX_REPLY = 12000
_MAX_TURNS = 12
_MAX_PROCESSED_IDS = 128
_MAX_TOKEN = 512
_MAX_PROFILE = 2000

_PAIR_CODE_LENGTH = 10
_PAIR_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_PAIR_TTL = 300.0
_PAIR_MAX_FAILURES = 5
_PAIR_FAILURE_WINDOW = 60.0
_PAIR_LOCK_SECONDS = 60.0
_MAX_RATE_ENTRIES = 128

_BOUND_TEXT = '绑定成功。现在可以直接和我聊聊了。'
_ERROR_TEXT = '本机模型暂时无法回应，请稍后再试。'

# Fixed diagnostic codes exposed to an optional local hook.  The hook only
# ever receives these constants: never message text, a profile, an account or
# message identifier, exception text, a response body, or authorization data.
_MODEL_PROCESSING = 'model_processing'
_MODEL_FAILED = 'model_failed'
_MODEL_FAILED_TIMEOUT = 'model_failed_timeout'
_MODEL_FAILED_CONNECTION = 'model_failed_connection'
_MODEL_FAILED_AUTHENTICATION = 'model_failed_authentication'
_MODEL_FAILED_SCHOOL_LOGIN = 'model_failed_school_login'
_MODEL_FAILED_BUSY = 'model_failed_busy'
_MODEL_FAILED_UNAVAILABLE = 'model_failed_unavailable'
_MODEL_RESPONSE_INVALID = 'model_response_invalid'
_MODEL_PREPARE_FAILED = 'model_prepare_failed'
_MODEL_FAILED_CONVERSATION = 'model_failed_conversation'
_MODEL_FAILED_VALUE = 'model_failed_value'
_MODEL_FAILED_TYPE = 'model_failed_type'
_MODEL_FAILED_LOOKUP = 'model_failed_lookup'
_MODEL_FAILED_ATTRIBUTE = 'model_failed_attribute'
_MODEL_FAILED_OS = 'model_failed_os'
_MODEL_FAILED_RUNTIME = 'model_failed_runtime'
_HTTP_STATUS_PATTERN = re.compile(r'本机模型返回状态 ([1-5][0-9]{2})，请检查服务与模型名称。\Z')
_HTTP_SPECIFIC_CODES = {n: f'model_http_{n}' for n in (400, 404, 500, 502)}

# Exact fixed messages the production ``request_reply`` wraps in its own
# ``ConversationError``.  Classification uses only exact equality against
# these constants; no exception text is ever logged, returned, or forwarded.
# Any other message (including arbitrary or sensitive text) stays generic.
_CONVERSATION_ERROR_CODES = {
    '本机反代访问口令有误。请检查“反代访问口令”后重试。':
        _MODEL_FAILED_AUTHENTICATION,
    '本机反代或学校登录暂不可用。请先完成学校登录，并确认反代服务正在运行。':
        _MODEL_FAILED_SCHOOL_LOGIN,
    '本机模型服务正忙，请稍后重试。':
        _MODEL_FAILED_BUSY,
    '本机模型未连接或等待超时。请确认服务已启动，再重试。':
        _MODEL_FAILED_UNAVAILABLE,
    '本机接口没有返回可显示的文字回复。':
        _MODEL_RESPONSE_INVALID,
}

_BIND_COMMAND = re.compile(r'绑定 ([A-Za-z0-9]{1,64})')
_BIDI_CONTROLS = ''.join(chr(code) for code in (
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
))
_DANGEROUS_TEXT = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f' + _BIDI_CONTROLS + r']'
)
_DANGEROUS_IDENTIFIER = re.compile(r'[\x00-\x1f\x7f-\x9f]')
_DECIMAL_DIGITS = re.compile(r'[0-9]+')


def _load_conversation():
    """Import the production si_pet conversation module without reading data.

    A normal source checkout is found relative to this file.  A frozen build is
    expected to bundle ``si_pet``; the direct import then works from the
    PyInstaller archive.  The executable-directory fallbacks cover onedir
    layouts where the package is next to the EXE instead of on sys.path.
    """
    try:
        from si_pet import conversation as module
        return module
    except ImportError:
        pass

    candidates = []
    core_dir = Path(__file__).resolve().parent
    for base in (core_dir, *core_dir.parents):
        candidates.append(base / 'si-desktop-pet' / 'src')
        candidates.append(base / 'src')
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend((exe_dir / '_internal', exe_dir))
        if getattr(sys, '_MEIPASS', None):
            candidates.append(Path(sys._MEIPASS))

    seen = set()
    for root in candidates:
        try:
            key = str(root.resolve()).casefold()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if not (root / 'si_pet' / 'conversation.py').is_file():
            continue
        sys.path.insert(0, str(root))
        try:
            from si_pet import conversation as module
            return module
        except ImportError:
            continue
    raise ImportError('si_pet.conversation is required by the QQ core')


_CONVERSATION = _load_conversation()


class _ConversationView:
    """Minimal object accepted by the production Conversation.messages method."""

    __slots__ = ('turns', 'profile')

    def __init__(self, turns, profile=''):
        self.turns = [dict(turn) for turn in turns]
        self.profile = profile


@dataclass(frozen=True, slots=True)
class CoreReply:
    text: str
    kind: str
    delivery_token: str | None = None
    segment: dict | None = None


def _valid_utf8(value: str) -> bool:
    try:
        value.encode('utf-8')
    except UnicodeError:
        return False
    return True


def _valid_identifier(value, limit):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and _DANGEROUS_IDENTIFIER.search(value) is None
        and _valid_utf8(value)
    )


def _valid_message_text(value, limit):
    if not isinstance(value, str):
        return False
    if not value or len(value) > limit:
        return False
    if _DANGEROUS_TEXT.search(value) is not None:
        return False
    return _valid_utf8(value)


def _canonical_decimal(value):
    """Return the digit string without leading zeros, or None if not decimal.

    Only ASCII digits are normalised.  Arbitrary string identifiers stay
    opaque, so no non-numeric or Unicode-digit identifier is ever turned into
    a different account number.
    """
    if not isinstance(value, str) or _DECIMAL_DIGITS.fullmatch(value) is None:
        return None
    return value.lstrip('0') or '0'


def _same_account(left, right):
    """Whether two identifiers denote the same QQ account.

    Exact string equality always matches.  For pure decimal QQ numbers an
    extra leading zero does not create a different account.
    """
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if left == right:
        return True
    left_digits = _canonical_decimal(left)
    right_digits = _canonical_decimal(right)
    return (left_digits is not None
            and right_digits is not None
            and left_digits == right_digits)


def _clean_reply(value):
    if not isinstance(value, str):
        return None
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    value = _DANGEROUS_TEXT.sub('', value).strip()
    if not value:
        return None
    if len(value) > _MAX_REPLY:
        value = value[:_MAX_REPLY].rstrip()
    if not value or not _valid_utf8(value):
        return None
    return value


def _conversation_error_class(module):
    """Return the production module's genuine ``ConversationError`` class.

    The class object itself is used for an identity check, never its name.
    A custom exception that merely reuses the name is a different class and
    is therefore not recognised.  Returns None when the module exposes no
    such class, in which case all messages stay generically classified.
    """
    try:
        holders = (module, getattr(module, 'Conversation', None))
    except Exception:
        return None
    for holder in holders:
        if holder is None:
            continue
        try:
            candidate = getattr(holder, 'ConversationError', None)
        except Exception:
            continue
        if isinstance(candidate, type) and issubclass(candidate, Exception):
            return candidate
    return None


def _model_failure_code(exc, conversation_error=None):
    """Return a fixed diagnostic code for a failed model request.

    Only the exact genuine ``ConversationError`` class is inspected, and only
    through its simple ``args`` tuple: exactly one argument of exactly ``str``
    type, equal to one of the fixed allowlisted messages.  A subclass, a
    lookalike class name, extra arguments, non-string arguments, or any other
    text maps to a fixed generic conversation category. Exact standard HTTP
    status strings map to fixed categories. Builtin exception types map only
    to allowlisted generic categories; their contents are never emitted.  The exception is never stringified;
    its text, response body, authorization data, and request details are never
    read beyond the exact allowlist comparison, logged, or forwarded.
    """
    try:
        if (isinstance(conversation_error, type)
                and issubclass(conversation_error, Exception)
                and type(exc) is conversation_error):
            args = getattr(exc, 'args', None)
            if (type(args) is tuple
                    and len(args) == 1
                    and type(args[0]) is str):
                code = _CONVERSATION_ERROR_CODES.get(args[0])
                if code is not None:
                    return code
                match = _HTTP_STATUS_PATTERN.fullmatch(args[0])
                if match:
                    status = int(match.group(1))
                    return _HTTP_SPECIFIC_CODES.get(status, f'model_http_{status // 100}xx')
            return _MODEL_FAILED_CONVERSATION
    except Exception:
        # Never let classification itself escape; fall through to generic.
        pass
    if isinstance(exc, TimeoutError):
        return _MODEL_FAILED_TIMEOUT
    if isinstance(exc, ConnectionError):
        return _MODEL_FAILED_CONNECTION
    # Exact built-in types only: no class name, exception text or data leaves
    # the process; custom subclasses remain generic.
    return {ValueError: _MODEL_FAILED_VALUE, TypeError: _MODEL_FAILED_TYPE,
            KeyError: _MODEL_FAILED_LOOKUP, IndexError: _MODEL_FAILED_LOOKUP,
            AttributeError: _MODEL_FAILED_ATTRIBUTE, OSError: _MODEL_FAILED_OS,
            RuntimeError: _MODEL_FAILED_RUNTIME}.get(type(exc), _MODEL_FAILED)


def _new_pair_code():
    return ''.join(secrets.choice(_PAIR_CODE_ALPHABET)
                   for _ in range(_PAIR_CODE_LENGTH))


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _atomic_write_json(path, payload):
    """Write one JSON document next to *path* and atomically replace it.

    A failed write leaves the previous target byte-for-byte untouched.  The
    temporary file is removed on failure; it never contains more than the
    already-sanitised state document.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


_LOCK_FILENAME = 'si_qq_core_state.lock'

_BUSY_LOCK_TEXT = (
    '另一个程序正在使用同一 QQ 私聊数据目录；'
    '请先关闭那个程序，再回到这里重试。'
)
_LOCK_ERROR_TEXT = (
    '无法锁定本机 QQ 私聊数据目录；'
    '请确认该目录存在且当前用户可读写，然后重试。'
)
_UNSUPPORTED_LOCK_TEXT = (
    '当前平台不支持跨进程数据目录锁；'
    '请勿同时运行多个使用同一 QQ 私聊数据目录的程序。'
)

# Per-directory state lock shared by every QQCore instance in this process.
# This is separate from the cross-process lease below: it makes threads of
# several cores pointed at one directory serialise their reads/writes.
_PROCESS_STATE_LOCKS = {}
_PROCESS_LEASES = {}
_PROCESS_LOCK_GUARD = threading.Lock()


class DataDirectoryBusyError(RuntimeError):
    """Another process owns the same QQ private-chat data directory."""


class DataDirectoryLockError(RuntimeError):
    """The local data directory could not be locked or unlocked."""


def _directory_key(path):
    text = os.fspath(path)
    try:
        text = os.path.realpath(text)
    except (OSError, ValueError):
        text = os.path.abspath(text)
    return os.path.normcase(text)


def _process_state_lock(path):
    key = _directory_key(path)
    with _PROCESS_LOCK_GUARD:
        lock = _PROCESS_STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_STATE_LOCKS[key] = lock
        return lock


def _lock_error_is_busy(exc):
    # ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION both mean another
    # process currently owns the lock file or the locked byte range.
    if getattr(exc, 'winerror', None) in (32, 33):
        return True
    busy = {errno.EACCES, errno.EAGAIN}
    for name in ('EDEADLK', 'EDEADLOCK'):
        value = getattr(errno, name, None)
        if value is not None:
            busy.add(value)
    return exc.errno in busy


def _acquire_platform_lock(fd):
    """Take one non-blocking exclusive byte lock on an already-open fd."""
    if os.name == 'nt':
        try:
            import msvcrt
        except ImportError:
            raise DataDirectoryLockError(_UNSUPPORTED_LOCK_TEXT) from None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            # Locking past end-of-file is explicitly allowed by _locking, so
            # neither an empty nor a pre-existing lock file is modified.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if _lock_error_is_busy(exc):
                raise DataDirectoryBusyError(_BUSY_LOCK_TEXT) from None
            raise DataDirectoryLockError(_LOCK_ERROR_TEXT) from exc
        return

    try:
        import fcntl
    except ImportError:
        raise DataDirectoryLockError(_UNSUPPORTED_LOCK_TEXT) from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise DataDirectoryBusyError(_BUSY_LOCK_TEXT) from None
        raise DataDirectoryLockError(_LOCK_ERROR_TEXT) from exc


def _release_platform_lock(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class DataDirectoryLease:
    """Exclusive, non-blocking, cross-process lease for one data directory.

    The OS lock is tied to an open handle, so Windows releases it when the
    process dies (including a crash).  Acquisition never waits, never writes
    to or deletes an existing state or lock file, and the lock file itself is
    intentionally left in place after release.  A second acquisition in the
    same process reuses the one OS handle and is reference counted, so UI and
    worker threads cannot deadlock against themselves.
    """

    def __init__(self, data_dir):
        try:
            directory = Path(data_dir)
        except (TypeError, ValueError):
            raise ValueError('invalid data directory') from None
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DataDirectoryLockError(_LOCK_ERROR_TEXT) from exc
        if not directory.is_dir():
            raise DataDirectoryLockError(_LOCK_ERROR_TEXT)

        self._key = _directory_key(directory)
        self._guard = threading.Lock()
        self._released = False
        self._entry = None

        with _PROCESS_LOCK_GUARD:
            entry = _PROCESS_LEASES.get(self._key)
            if entry is not None:
                entry['count'] += 1
                self._entry = entry
                return

            lock_path = directory / _LOCK_FILENAME
            flags = os.O_RDWR | os.O_CREAT
            # Never let a child process inherit the locked handle; otherwise
            # it could keep the directory locked after this program exits.
            flags |= getattr(os, 'O_NOINHERIT', 0)
            try:
                fd = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                if _lock_error_is_busy(exc):
                    raise DataDirectoryBusyError(_BUSY_LOCK_TEXT) from None
                raise DataDirectoryLockError(_LOCK_ERROR_TEXT) from exc
            try:
                _acquire_platform_lock(fd)
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            entry = {'fd': fd, 'count': 1}
            _PROCESS_LEASES[self._key] = entry
            self._entry = entry

    def release(self):
        """Drop this lease; the last holder unlocks and closes the handle."""
        with self._guard:
            if self._released:
                return
            self._released = True
            entry = self._entry
            self._entry = None
        if entry is None:
            return

        with _PROCESS_LOCK_GUARD:
            if entry['count'] > 1:
                entry['count'] -= 1
                return
            _PROCESS_LEASES.pop(self._key, None)
            fd = entry.get('fd')
            entry['fd'] = None
            try:
                if fd is not None:
                    try:
                        _release_platform_lock(fd)
                    finally:
                        os.close(fd)
            except OSError as exc:
                raise DataDirectoryLockError(_LOCK_ERROR_TEXT) from exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def acquire_data_directory_lease(data_dir):
    """Return an exclusive lease for *data_dir* or raise immediately."""
    return DataDirectoryLease(data_dir)


class QQCore:
    """Synchronous, thread-safe QQ private-chat state machine.

    The public surface is intentionally limited to the local UI and adapter
    operations below.  ``data_dir`` holds one fixed-name state file; no OpenID
    is ever used as a
    path component.  ``app_id`` is this machine's own QQ number (or, for older
    arbitrary-string tests, an opaque bot identifier) and must match the state
    file exactly or loading fails without touching it.

    This class serialises threads in one process (including several cores
    pointed at the same directory), but it does not itself take the OS lock.
    The application owner must hold ``acquire_data_directory_lease(data_dir)``
    from before the first state read until every core writer has exited.
    """

    def __init__(self, data_dir, app_id, request_reply=None):
        if not _valid_identifier(app_id, _MAX_APP_ID):
            raise ValueError('invalid QQ bot app_id')
        try:
            self.data_dir = Path(data_dir)
        except (TypeError, ValueError):
            raise ValueError('invalid data directory') from None
        if request_reply is not None and not callable(request_reply):
            raise TypeError('request_reply must be callable')

        self.app_id = app_id
        self._state_path = self.data_dir / _STATE_FILENAME
        self._request_reply = request_reply
        self._conversation = _CONVERSATION
        # Shared per-directory lock: two cores in this process that point at
        # the same data directory must not write its state concurrently.
        self._lock = _process_state_lock(self.data_dir)
        self._profile_lock = threading.Lock()
        self._profile = ''
        self._clock = None
        # Optional fixed-code diagnostic hook installed by the local adapter.
        self._diagnostic_callback = None
        self._reply_mode_callback = None

        self._admin_openid = None
        self._turns = []
        self._processed = []
        self._pending = None
        # True for a pending record loaded from disk at construction, or for
        # one whose send the adapter has explicitly reported as unknown.
        self._pending_recovery_required = False

        # Pairing material exists only in memory.
        self._pair_code = None
        self._pair_expiry = 0.0
        self._pair_rate = OrderedDict()

        self._load_state()

    # -- local pairing -------------------------------------------------

    def begin_pairing(self):
        """Return a short-lived one-time code for the local UI, or None.

        Once an administrator is bound this refuses to mint a takeover code.
        The existing state is never changed here.  Rebinding is deliberately
        not exposed through the QQ message path.
        """
        with self._lock:
            if self._admin_openid is not None:
                return None
            code = _new_pair_code()
            self._pair_code = code
            self._pair_expiry = self._now() + _PAIR_TTL
            return code

    def set_diagnostic_callback(self, callback):
        """Install an optional fixed-code diagnostic hook, or None to remove.

        The hook receives exactly one of this module's fixed diagnostic code
        strings (``model_processing``, ``model_failed``,
        ``model_failed_timeout``, ``model_failed_connection``,
        ``model_failed_authentication``, ``model_failed_school_login``,
        ``model_failed_busy``, ``model_failed_unavailable``, or
        ``model_response_invalid``).  It never receives message text, a
        profile, an account or message identifier, exception text, a response
        body, or authorization material.  The hook may run on the thread that
        called ``handle_private``; a hook that raises is ignored.
        """
        if callback is not None and not callable(callback):
            raise TypeError('diagnostic callback must be callable')
        self._diagnostic_callback = callback

    def set_reply_mode_callback(self, callback):
        """Install the optional local GUI hook for persisted mode changes.

        The hook receives exactly ``'text'`` or ``'voice'`` after a successful
        administrator command changed the settings file.  It runs on the
        thread that called ``handle_private``, so the GUI must only marshal the
        value to its own thread (for example with a queued Qt signal) and must
        never touch widgets here.  It never receives the administrator ID,
        message text, or message identifier; a hook that raises is ignored.
        """
        if callback is not None and not callable(callback):
            raise TypeError('reply mode callback must be callable')
        self._reply_mode_callback = callback

    def _mode_command(self, message_id, mode):
        """Persist one authorized ``/语音`` or ``/文字`` command atomically.

        The message ID is committed to the existing processed-ID list first,
        so a replayed event, a restart, or two cores serialized by the
        process-wide settings lock can neither repeat the effect nor send the
        confirmation twice.  Any persistence failure returns the fixed failure
        notice and leaves the previous setting and file untouched.
        """
        processed = self._append_processed(message_id)
        try:
            self._commit(self._admin_openid, self._turns, processed, None)
        except Exception:
            return CoreReply(_MODE_FAILED_TEXT, 'mode')

        data_dir = self.data_dir
        with reply_settings.mode_lock(data_dir):
            try:
                # A corrupt or unreadable settings file is preserved and the
                # command fails closed instead of replacing the user's data.
                reply_settings.read_mode(data_dir)
            except (OSError, ValueError, RecursionError):
                return CoreReply(_MODE_FAILED_TEXT, 'mode')
            try:
                reply_settings.write_mode(data_dir, mode)
            except (OSError, RuntimeError, ValueError):
                return CoreReply(_MODE_FAILED_TEXT, 'mode')

        self._notify_reply_mode(mode)
        return CoreReply(
            _MODE_VOICE_TEXT if mode == 'voice' else _MODE_TEXT_TEXT,
            'mode',
        )

    def _notify_reply_mode(self, mode):
        """Forward only the fixed mode value to the optional local GUI hook."""
        callback = self._reply_mode_callback
        if callback is None:
            return
        try:
            callback(mode)
        except Exception:
            pass

    # -- private message entry point ----------------------------------

    def handle_private(self, sender_openid, message_id, text):
        """Handle one C2C text message from the official adapter.

        Returns None for every ignored/rejected request, a ``CoreReply`` with
        kind ``bound`` or ``error`` (no delivery token), or a ``chat`` reply
        carrying a token that must be finalised after the real QQ send.  The
        bot's own account is silently rejected in every state, including an
        unbound one; decimal QQ numbers differing only in leading zeroes are
        the same account.
        """
        if not _valid_identifier(sender_openid, _MAX_OPENID):
            return None
        if _same_account(sender_openid, self.app_id):
            # The bot account itself is never an administrator and never gets
            # a binding, model request, or response, bound or not.
            return None
        if not _valid_identifier(message_id, _MAX_MESSAGE_ID):
            return None
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if not _valid_message_text(stripped, _MAX_INPUT):
            return None

        with self._lock:
            if self._admin_openid is None:
                return self._try_bind(sender_openid, message_id, text)

            if sender_openid != self._admin_openid:
                return None
            if message_id in self._processed:
                return None
            if self._pending is not None:
                # The adapter is expected to serialise one account.  An
                # overlapping request is refused rather than replacing the
                # delivery record that may already be on its way to QQ.
                return None
            if _BIND_COMMAND.fullmatch(text) is not None:
                # Binding commands never grant a second administrator and are
                # not sent to the model after pairing.
                return None

            command_mode = _REPLY_MODE_COMMANDS.get(stripped)
            if command_mode is not None:
                return self._mode_command(message_id, command_mode)
            if stripped.startswith(_REPLY_MODE_PREFIXES):
                return None

            return self._chat(message_id, stripped)

    def finalize_reply(self, token, delivered):
        """Resolve a chat delivery after the adapter's real QQ send attempt.

        ``delivered=True`` appends the turn only after this state write
        succeeds.  ``delivered=False`` keeps the message ID deduplicated but
        never records the unsent answer as conversation.  The method returns
        True only when a matching pending record was resolved and persisted.
        """
        if not _valid_identifier(token, _MAX_TOKEN):
            return False
        if not isinstance(delivered, bool):
            return False

        with self._lock:
            pending = self._pending
            if not isinstance(pending, dict):
                return False
            if not hmac.compare_digest(pending.get('token_hash', ''),
                                       _token_hash(token)):
                return False

            turns = self._turns
            if delivered:
                if pending.get('sender_openid') != self._admin_openid:
                    return False
                turns = (self._turns + [{
                    'user': pending['user'],
                    'assistant': pending['assistant'],
                }])[-_MAX_TURNS:]

            try:
                self._commit(self._admin_openid, turns, self._processed, None)
            except Exception:
                # The old file and in-memory pending record stay intact.  The
                # adapter may retry finalize; nothing is misreported.
                return False
            # Any pending record, including one loaded at construction, is now
            # resolved and no longer needs local recovery.
            self._pending_recovery_required = False
            return True

    def mark_delivery_unknown(self, token):
        """Record that the adapter's QQ send attempt ended without an answer.

        The adapter calls this only after its send attempt has fully unwound
        (timeout, lost connection, or cancellation), so no delivery task for
        this token can later report success.  The pending record is kept: the
        reply is not treated as delivered and not treated as a definite
        failure, its message ID stays deduplicated, and future chats stay
        closed until the local user either resolves it with the original token
        through ``finalize_reply`` or explicitly calls
        ``discard_uncertain_pending``.  This changes only process memory; the
        already-durable pending record already provides the same recovery
        after a restart.  It never sends, calls the model, or writes a file.
        """
        if not _valid_identifier(token, _MAX_TOKEN):
            return False
        with self._lock:
            pending = self._pending
            if not isinstance(pending, dict):
                return False
            if not hmac.compare_digest(pending.get('token_hash', ''),
                                       _token_hash(token)):
                return False
            self._pending_recovery_required = True
            return True

    # -- safe identity comparison for other local surfaces -----------

    def is_administrator(self, account_id):
        """Whether *account_id* is the currently bound administrator.

        This is a comparison-only surface for sibling features such as the
        group-chat ledger.  It reads the administrator identifier under the
        core lock and returns a plain boolean; it never reads, returns, or
        copies the profile, turns, pairing material, pending record, or state
        file.  An unbound core, an invalid identifier, and a non-matching
        identifier all return False.  Decimal QQ numbers differing only in
        leading zeroes compare as the same account, exactly as elsewhere.
        """
        if not _valid_identifier(account_id, _MAX_OPENID):
            return False
        with self._lock:
            administrator = self._admin_openid
        return (
            administrator is not None
            and _same_account(account_id, administrator)
        )

    # -- explicit local recovery --------------------------------------

    def local_status(self):
        """Return recovery state for the local management UI as booleans only.

        Deliberately contains no QQ number, message, pairing code, token, or
        secret.  ``pending_recovery_required`` is True when this object loaded
        an unresolved pending delivery from the existing state file at
        construction, or when the adapter reported that a send attempt ended
        with an unknown outcome.  A plain in-flight send is not yet recoverable
        because it may still be running.
        """
        with self._lock:
            return {
                'bound': self._admin_openid is not None,
                'pending_recovery_required': bool(
                    self._pending is not None
                    and self._pending_recovery_required
                ),
            }

    def discard_uncertain_pending(self):
        """Explicitly drop an unresolved delivery, atomically.

        Only the local UI may call this.  It returns False when there is no
        recovery state (including while a send this process started may still
        be running) or when persistence fails.  On success the pending record is
        removed while its message ID stays deduplicated and already-delivered
        turns are untouched; the unknown reply is never recorded as
        conversation and no message is sent.  A persistence failure returns
        False and leaves both the old file and in-memory state unchanged.
        """
        with self._lock:
            if (self._pending is None
                    or not self._pending_recovery_required):
                return False
            try:
                self._commit(self._admin_openid, self._turns,
                             self._processed, None)
            except Exception:
                # _commit only updates memory after the atomic replacement
                # succeeds, so the original bytes and pending stay in place.
                return False
            self._pending_recovery_required = False
            return True

    # -- binding -------------------------------------------------------

    def _try_bind(self, sender_openid, message_id, text):
        if _same_account(sender_openid, self.app_id):
            return None
        match = _BIND_COMMAND.fullmatch(text)
        if match is None:
            # While unbound, ordinary text must not create a session, call the
            # model, or produce a response.
            return None

        now = self._now()
        if not self._rate_allows(sender_openid, now):
            return None

        candidate = match.group(1)
        code = self._pair_code
        valid = (
            isinstance(code, str)
            and now < self._pair_expiry
            and hmac.compare_digest(candidate, code)
        )
        if not valid:
            if code is not None and now >= self._pair_expiry:
                self._pair_code = None
            self._rate_failure(sender_openid, now)
            return None

        processed = self._append_processed(message_id)
        try:
            self._commit(sender_openid, [], processed, None)
        except Exception:
            # Binding requires the administrator OpenID to be durable first.
            # Leave the code usable for a later retry; old bytes are intact.
            return None

        self._pair_code = None
        self._pair_expiry = 0.0
        self._pair_rate.clear()
        return CoreReply(_BOUND_TEXT, 'bound')

    # -- model conversation -------------------------------------------

    def _set_profile(self, profile):
        """Replace the optional local admin profile used for future turns.

        The profile is kept separate from QQ conversation state.  Callers are
        responsible for saving it locally; an in-flight model request keeps
        the profile snapshot it started with.
        """
        if not isinstance(profile, str) or len(profile) > _MAX_PROFILE:
            raise ValueError('invalid administrator profile')
        with self._profile_lock:
            self._profile = profile

    def _profile_snapshot(self):
        with self._profile_lock:
            return self._profile

    def _diagnostic(self, code):
        """Send one fixed code to the optional local hook; never raise."""
        callback = self._diagnostic_callback
        if callback is None:
            return
        try:
            callback(code)
        except Exception:
            pass

    def _chat(self, message_id, text):
        self._diagnostic(_MODEL_PROCESSING)
        try:
            messages = self._conversation.Conversation.messages(
                _ConversationView(self._turns, self._profile_snapshot()), text
            )
            api_key = getattr(self._conversation, 'GENAI_DEFAULT_API_KEY')
            requester = self._request_reply
            if requester is None:
                requester = self._conversation.request_reply
        except Exception:
            self._diagnostic(_MODEL_PREPARE_FAILED)
            return self._model_failure(message_id)
        try:
            from service_reply import respond
            raw_reply = respond(requester, (_ENDPOINT, _MODEL, messages, api_key), text, self.data_dir, private=True)
        except Exception as exc:
            # Only a fixed code is forwarded.  The known conversation error is
            # matched by exact class identity and exact allowlisted argument;
            # the exception text, response body, and authorization data are
            # never emitted.  Classification failure itself stays generic.
            try:
                failure_code = _model_failure_code(
                    exc, _conversation_error_class(self._conversation)
                )
            except Exception:
                failure_code = _MODEL_FAILED
            self._diagnostic(failure_code)
            return self._model_failure(message_id)

        reply = _clean_reply(raw_reply)
        if reply is None:
            self._diagnostic(_MODEL_RESPONSE_INVALID)
            return self._model_failure(message_id)

        processed = self._append_processed(message_id)
        token = secrets.token_urlsafe(32)
        pending = {
            'token_hash': _token_hash(token),
            'sender_openid': self._admin_openid,
            'message_id': message_id,
            'user': text,
            'assistant': reply,
            'created_at': self._now(),
        }
        try:
            self._commit(self._admin_openid, self._turns, processed, pending)
        except Exception:
            # No chat reply is returned unless its pending record is durable.
            # The old state file is untouched and no turn is added.
            return None
        # This pending belongs to the current process, not to a restart.
        self._pending_recovery_required = False
        return CoreReply(reply, 'chat', token, getattr(raw_reply, 'segment', None))

    def _model_failure(self, message_id):
        # Deduplicate the failed input without adding any assistant text.
        processed = self._append_processed(message_id)
        try:
            self._commit(self._admin_openid, self._turns, processed, None)
        except Exception:
            return None
        return CoreReply(_ERROR_TEXT, 'error')

    # -- bounded process memory ---------------------------------------

    def _append_processed(self, message_id):
        processed = [item for item in self._processed if item != message_id]
        processed.append(message_id)
        return processed[-_MAX_PROCESSED_IDS:]

    def _now(self):
        clock = self._clock if self._clock is not None else time.monotonic
        return float(clock())

    def _rate_allows(self, openid, now):
        entry = self._pair_rate.get(openid)
        if entry is None:
            return True
        window_start, failures, blocked_until = entry
        if now < blocked_until:
            return False
        if now - window_start >= _PAIR_FAILURE_WINDOW:
            entry[0] = now
            entry[1] = 0
            entry[2] = 0.0
            self._pair_rate.move_to_end(openid)
        return True

    def _rate_failure(self, openid, now):
        entry = self._pair_rate.get(openid)
        if entry is None or now - entry[0] >= _PAIR_FAILURE_WINDOW:
            entry = [now, 0, 0.0]
            self._pair_rate[openid] = entry
        entry[1] += 1
        if entry[1] >= _PAIR_MAX_FAILURES:
            entry[1] = 0
            entry[2] = now + _PAIR_LOCK_SECONDS
        self._pair_rate.move_to_end(openid)
        while len(self._pair_rate) > _MAX_RATE_ENTRIES:
            self._pair_rate.popitem(last=False)

    # -- state file ----------------------------------------------------

    def _commit(self, admin_openid, turns, processed, pending):
        turns_copy = [dict(turn) for turn in turns]
        processed_copy = list(processed)
        pending_copy = dict(pending) if pending is not None else None
        payload = {
            'schema': _STATE_SCHEMA,
            'app_id': self.app_id,
            'admin_openid': admin_openid,
            'turns': turns_copy,
            'processed_message_ids': processed_copy,
            'pending': pending_copy,
        }
        # The on-disk document must stay readable by ``_load_state`` forever.
        # With the 12-turn x (2000 + 12000) bounds this cannot trigger, but a
        # guard here means an unexpectedly large payload fails closed instead
        # of writing a file that the next construction would reject.
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')
        if len(encoded) > _MAX_STATE_BYTES:
            raise ValueError('QQ core state exceeds the local size bound')
        _atomic_write_json(self._state_path, payload)
        # Assignment happens only after the replacement succeeded.  The new
        # values were fully built before the write, so these plain assignments
        # cannot leave memory on the old state after a durable success.
        self._admin_openid = admin_openid
        self._turns = turns_copy
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
        except (OSError, ValueError, TypeError, UnicodeError):
            raise RuntimeError(
                'QQ core state cannot be read; the original file was kept'
            ) from None

        try:
            if not isinstance(data, dict):
                raise ValueError()
            if set(data) != {
                'schema', 'app_id', 'admin_openid', 'turns',
                'processed_message_ids', 'pending',
            }:
                raise ValueError()
            if data['schema'] != _STATE_SCHEMA:
                raise ValueError()
            if data['app_id'] != self.app_id:
                raise RuntimeError(
                    'QQ core state belongs to a different app_id; refusing to use it'
                )

            admin_openid = data['admin_openid']
            if admin_openid is not None and not _valid_identifier(
                    admin_openid, _MAX_OPENID):
                raise ValueError()

            turns = data['turns']
            if not isinstance(turns, list) or len(turns) > _MAX_TURNS:
                raise ValueError()
            clean_turns = []
            for turn in turns:
                if (not isinstance(turn, dict)
                        or set(turn) != {'user', 'assistant'}
                        or not _valid_message_text(turn['user'], _MAX_INPUT)
                        or not _valid_message_text(turn['assistant'], _MAX_REPLY)):
                    raise ValueError()
                clean_turns.append({
                    'user': turn['user'],
                    'assistant': turn['assistant'],
                })

            processed = data['processed_message_ids']
            if not isinstance(processed, list) or len(processed) > _MAX_PROCESSED_IDS:
                raise ValueError()
            if len(set(processed)) != len(processed):
                raise ValueError()
            if any(not _valid_identifier(item, _MAX_MESSAGE_ID)
                   for item in processed):
                raise ValueError()

            pending = data['pending']
            if pending is not None:
                if not isinstance(pending, dict) or set(pending) != {
                    'token_hash', 'sender_openid', 'message_id',
                    'user', 'assistant', 'created_at',
                }:
                    raise ValueError()
                if (not isinstance(pending['token_hash'], str)
                        or len(pending['token_hash']) != 64
                        or any(ch not in '0123456789abcdef'
                               for ch in pending['token_hash'])):
                    raise ValueError()
                if pending['sender_openid'] != admin_openid:
                    raise ValueError()
                if pending['message_id'] not in processed:
                    raise ValueError()
                if not _valid_message_text(pending['user'], _MAX_INPUT):
                    raise ValueError()
                if not _valid_message_text(pending['assistant'], _MAX_REPLY):
                    raise ValueError()
                created = pending['created_at']
                if (not isinstance(created, (int, float))
                        or isinstance(created, bool)
                        or not math.isfinite(created) or created < 0):
                    raise ValueError()

            if admin_openid is None:
                if clean_turns or processed or pending is not None:
                    raise ValueError()

            self._admin_openid = admin_openid
            self._turns = clean_turns
            self._processed = list(processed)
            self._pending = dict(pending) if pending is not None else None
            # Only a pending loaded here is unknown because of a restart.
            self._pending_recovery_required = pending is not None
        except RuntimeError:
            raise
        except (ValueError, TypeError, KeyError, IndexError):
            raise RuntimeError(
                'QQ core state is corrupt; the original file was kept'
            ) from None
