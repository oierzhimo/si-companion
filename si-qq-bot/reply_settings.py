"""Dedicated, non-sensitive QQ reply-mode settings file.

The file lives beside the existing QQ state and only ever holds::

    {"schema": 1, "reply_mode": "text" | "voice"}

No token, message, profile, account, or audio data is ever written here.  Both
the QQ core (the administrator's ``/语音``/``/文字`` commands) and the local GUI
use these helpers, so they share one parser, one atomic writer, and one
per-directory in-process lock.  Cross-process serialization stays with the
existing data-directory lease that the application already holds.

Reads fail closed: a missing file means "never chosen" and yields the built-in
text default, while an existing file that cannot be read or validated raises
``OSError``/``ValueError`` so callers can preserve it instead of overwriting it.
Writes replace the file atomically (temporary file + flush + fsync + rename)
and clean up the temporary file on any failure.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path


FILENAME = "qq_reply_settings.json"
SCHEMA = 1
MAX_BYTES = 4096
MODES = ("text", "voice")
DEFAULT_MODE = "text"

_LOCK_GUARD = threading.Lock()
_LOCKS = {}


def _directory_key(data_dir):
    try:
        return os.path.normcase(os.path.abspath(os.fspath(data_dir)))
    except (TypeError, ValueError):
        return str(data_dir)


def mode_lock(data_dir):
    """Return the process-wide lock for one data directory.

    The GUI (save from the settings control) and the QQ core (administrator
    command) take this lock around their read-check-write sequence so one
    cannot silently overwrite a newer value written by the other.
    """
    key = _directory_key(data_dir)
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def path_for(data_dir):
    return Path(data_dir) / FILENAME


def read_mode(data_dir):
    """Return ``(found, mode)``; raise ``OSError``/``ValueError`` on bad data."""
    path = path_for(data_dir)
    try:
        info = path.stat()
    except FileNotFoundError:
        return False, DEFAULT_MODE
    if not stat.S_ISREG(info.st_mode):
        raise OSError("reply mode path is not a regular file")
    if info.st_size > MAX_BYTES:
        raise ValueError("reply mode file too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema", "reply_mode"}:
        raise ValueError("invalid reply mode file")
    if type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise ValueError("invalid reply mode schema")
    mode = value["reply_mode"]
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("invalid reply mode")
    return True, mode


def write_mode(data_dir, mode):
    """Atomically persist *mode*; raise on any failure."""
    if mode not in MODES:
        raise ValueError("invalid reply mode")
    path = path_for(data_dir)
    payload = json.dumps(
        {"schema": SCHEMA, "reply_mode": mode},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("reply mode file too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
