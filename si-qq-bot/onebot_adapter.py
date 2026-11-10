"""Loopback-only OneBot v11 private-message bridge.

This module deliberately has no import-time network, file, or QQ side effects.
The owner must call :meth:`OneBotAdapter.start` explicitly.

Model calls run on daemon threads because cancelling an asyncio coroutine
cannot stop them.  ``stop`` therefore reports whether the adapter is really
quiescent; ``start`` refuses to run while any previous worker may still write
the core state file, and the UI must wait for ``is_quiescent`` instead of
announcing a safe stop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import reply_chunks
from pending_media import PendingGroupMedia
from media_support import use_media, make_resolver


_QQ_ID = re.compile(r"[0-9]{1,20}\Z")
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_TOKEN = re.compile(r"[^\x00-\x20\x7f]{1,512}\Z")
# Incoming text keeps its existing 2000-character limit.  A complete
# *outgoing* model answer has its own, larger bound and is split by
# ``reply_chunks`` before anything is sent; the incoming regex is deliberately
# not widened.
_TEXT_SEGMENT = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]{1,2000}\Z")
_OUTGOING_ANSWER = re.compile(
    r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]{1,12000}\Z"
)
_MAX_OUTGOING_REPLY = reply_chunks.MAX_REPLY_CHARS
_MAX_CHUNK_TEXT = reply_chunks.MAX_CHUNK_CHARS

# A model call runs in a daemon thread and cannot be stopped by cancelling its
# coroutine.  Shutdown therefore waits only for a short, bounded time; a still
# running worker keeps the adapter from being restarted (and from constructing
# another core that could write the same state file).
_STOP_JOIN_SECONDS = 1.5
_WORKER_POLL_SECONDS = 0.02

# Group work is deliberately decoupled from the private path.  Unmentioned
# group traffic is dropped by the reader before it can consume queue space,
# and the group queue itself is small so unrelated traffic cannot grow memory.
_GROUP_QUEUE_MAX = 16
_PRIVATE_EVENT_QUEUE_MAX = 64
_MAX_GROUP_TEXT = 1000

# Fixed synthetic prompt fragments for group messages that have no usable
# user text.  They contain no URL, file name, path, account number, or media
# payload; only the fact that the bot was mentioned and that media was not
# made available to the model.  The ledger therefore never stores the original
# media data.
_MENTION_ONLY_MARKER = '对方只 @ 了你，没有附加文字，让模型自然回应。'
_MEDIA_NOT_PROVIDED_MARKER = (
    '对方这条消息还包含图片、音频或其他媒体，但这些媒体没有提供给你；'
    '不要假装看过、听过或知道其中内容，也不要描述或猜测媒体内容。'
)
# Segment types that carry media or other unsupported rich content.  They are
# never interpreted and never copied into the prompt; only the fixed marker
# above is added.  Plain ``face``/emoji segments are not media and stay
# silently ignored like before.
_MEDIA_SEGMENT_TYPES = frozenset({
    'image', 'mface', 'record', 'audio', 'video', 'file', 'video_file',
    'flash_file', 'forward', 'node', 'json', 'xml',
})

# Fixed prefix for diagnostics emitted by the current group ledger.  Private
# diagnostics keep their existing text so private chat behaviour is unchanged;
# the prefix makes the two surfaces distinguishable in the local status line.
_GROUP_DIAGNOSTIC_PREFIX = '群聊：'


# Fixed status messages for the local UI.  Every message is a constant; no
# exception text, response body, authorization data, profile, account or
# message identifier, or message content is ever interpolated into one.
_MODEL_PROCESSING_TEXT = "已收到消息，正在请求本机模型。"
_MODEL_FAILED_TEXT = "本机模型请求失败。"
_MODEL_FAILED_TIMEOUT_TEXT = "本机模型请求超时。"
_MODEL_FAILED_CONNECTION_TEXT = "本机模型服务连接失败。"
_MODEL_FAILED_AUTHENTICATION_TEXT = "本机反代访问口令有误，请检查本机设置后重试。"
_MODEL_FAILED_SCHOOL_LOGIN_TEXT = "本机反代或学校登录暂不可用，请先完成学校登录。"
_MODEL_FAILED_BUSY_TEXT = "本机模型服务正忙，请稍后重试。"
_MODEL_FAILED_UNAVAILABLE_TEXT = "本机模型服务未连接或等待超时。"
_MODEL_RESPONSE_INVALID_TEXT = "本机模型返回了空或无效内容。"
_DELIVERY_REJECTED_TEXT = "QQ 未确认本次回复发送。"
_DELIVERY_UNKNOWN_TEXT = "QQ 回复结果不明；请在本机点击“恢复未确认回复”，不会自动重发。"
_DELIVERY_PARTIAL_TEXT = (
    "QQ 已确认发送部分回复；剩余内容未确认。"
    "请在本机点击“恢复未确认回复”，不会自动重发。"
)
_NOTICE_DELIVERY_UNKNOWN_TEXT = "QQ 未确认通知是否发出，请查看 QQ 私聊。"
_REPLY_ACKNOWLEDGED_TEXT = "本机模型回复已由 QQ 确认发送。"
_GROUP_REPLY_ACKNOWLEDGED_TEXT = "群聊回复已由 QQ 确认发送。"
_GROUP_DELIVERY_REJECTED_TEXT = "QQ 未确认本次群聊回复发送。"
_GROUP_DELIVERY_UNKNOWN_TEXT = (
    "QQ 群聊回复结果不明；请在本机点击“恢复群聊未确认回复”，不会自动重发。"
)
_GROUP_DELIVERY_PARTIAL_TEXT = (
    "QQ 已确认发送部分群聊回复；剩余内容未确认。"
    "请在本机点击“恢复群聊未确认回复”，不会自动重发。"
)
_GROUP_NOTICE_UNKNOWN_TEXT = "QQ 未确认群聊通知是否发出，请查看该群聊。"
_GROUP_STORAGE_ERROR_TEXT = (
    "本机未能保存本次群聊发送结果；暂时不能继续群聊。"
    "请检查本机存储后重启程序并使用“恢复群聊未确认回复”。"
)
_GROUP_UNAVAILABLE_TEXT = "群聊状态无法读取；本次仅保留私聊，不会猜测群聊内容。"

# Only these fixed codes from a core are forwarded.  Unknown values from any
# other core implementation are dropped rather than passed to the UI.
_CORE_DIAGNOSTIC_TEXT = {
    "model_processing": _MODEL_PROCESSING_TEXT,
    "model_failed": _MODEL_FAILED_TEXT,
    "model_failed_timeout": _MODEL_FAILED_TIMEOUT_TEXT,
    "model_failed_connection": _MODEL_FAILED_CONNECTION_TEXT,
    "model_failed_authentication": _MODEL_FAILED_AUTHENTICATION_TEXT,
    "model_failed_school_login": _MODEL_FAILED_SCHOOL_LOGIN_TEXT,
    "model_failed_busy": _MODEL_FAILED_BUSY_TEXT,
    "model_failed_unavailable": _MODEL_FAILED_UNAVAILABLE_TEXT,
    "model_response_invalid": _MODEL_RESPONSE_INVALID_TEXT,
    "model_prepare_failed": "准备模型请求时出错。",
    "model_failed_conversation": "本机模型接口返回未分类错误。",
    "model_failed_value": "模型请求发生数据值异常。",
    "model_failed_type": "模型请求发生数据类型异常。",
    "model_failed_lookup": "模型请求发生数据读取异常。",
    "model_failed_attribute": "模型请求缺少必要组件。",
    "model_failed_os": "模型请求遇到系统资源或连接异常。",
    "model_failed_runtime": "模型请求遇到运行错误。",
    "model_http_400": "模型服务返回 HTTP 400（请求有误）。",
    "model_http_404": "模型服务返回 HTTP 404（接口或模型未找到）。",
    "model_http_500": "模型服务返回 HTTP 500（服务内部错误）。",
    "model_http_502": "模型服务返回 HTTP 502（上游服务异常）。",
    "model_http_1xx": "模型服务返回异常 HTTP 1xx 状态。",
    "model_http_2xx": "模型服务返回异常 HTTP 2xx 状态。",
    "model_http_3xx": "模型服务返回异常 HTTP 3xx 状态。",
    "model_http_4xx": "模型服务返回 HTTP 4xx 请求错误。",
    "model_http_5xx": "模型服务返回 HTTP 5xx 服务错误。",
}


def validate_ws_url(value: str) -> bool:
    """Accept only the exact unencrypted NapCat loopback endpoint."""
    if not isinstance(value, str) or len(value) > 256:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "ws"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 3001
            and parsed.path in ("", "/")
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and value in ("ws://127.0.0.1:3001", "ws://127.0.0.1:3001/")
        )
    except (ValueError, TypeError):
        return False


def _valid_token(value: str) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _account_id(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or _QQ_ID.fullmatch(value) is None:
        return None
    return value.lstrip("0") or "0"


def _plain_text(message) -> str | None:
    """Return text only when the OneBot message consists solely of text."""
    if isinstance(message, str):
        return message if _TEXT_SEGMENT.fullmatch(message) else None
    if not isinstance(message, list) or not message:
        return None
    pieces = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            return None
        data = segment.get("data")
        if not isinstance(data, dict) or set(data) != {"text"}:
            return None
        text = data["text"]
        if not isinstance(text, str) or not text:
            return None
        pieces.append(text)
    text = "".join(pieces)
    return text if _TEXT_SEGMENT.fullmatch(text.strip()) else None


def _group_message_text(message, bot_id: str) -> str | None:
    """Return the prompt text for an array message that explicitly @s *bot_id*.

    A string message is never a group mention: text such as ``@bot`` cannot
    fake the array ``at`` segment the task requires.  ``at`` segments for
    ``all`` or any other account do not count.  ``reply`` segments are
    stripped.

    A targeted message with no additional text is *not* dropped: it gets the
    fixed ``_MENTION_ONLY_MARKER`` so the model can react naturally.  If the
    message also carries image/audio/video/file/other unsupported rich
    content, the fixed ``_MEDIA_NOT_PROVIDED_MARKER`` is appended and the
    original URL/path/payload is never copied into the prompt or ledger.
    ``None`` is returned only when the bot was not mentioned, the array is
    unusable, or the combined prompt is outside the existing size bound.
    """
    if not isinstance(message, list) or not message:
        return None
    mentioned = False
    has_media = False
    pieces = []
    for segment in message:
        if not isinstance(segment, dict):
            # A malformed segment cannot prove the target or the content.
            return None
        segment_type = segment.get("type")
        data = segment.get("data")
        if segment_type == "at":
            if not isinstance(data, dict):
                continue
            target = _account_id(data.get("qq"))
            if target is not None and target == bot_id:
                mentioned = True
            continue
        if segment_type == "reply":
            # Quoted-message metadata is not conversation content.
            continue
        if segment_type == "text":
            if not isinstance(data, dict):
                return None
            text = data.get("text")
            if not isinstance(text, str):
                return None
            pieces.append(text)
            continue
        if segment_type in _MEDIA_SEGMENT_TYPES:
            # Remember only that unsupported media existed.  Its data (URL,
            # file name, path, base64, ...) is deliberately not read.
            has_media = True
        # Other segments (for example a plain emoji face) are ignored exactly
        # as before.
        continue
    if not mentioned:
        return None

    text = "".join(pieces).strip()
    if text and len(text) > _MAX_GROUP_TEXT:
        return None
    markers = []
    if not text:
        markers.append(_MENTION_ONLY_MARKER)
    if has_media:
        markers.append(_MEDIA_NOT_PROVIDED_MARKER)
    if text:
        suffix = "".join(" " + marker for marker in markers)
        if len(text) + len(suffix) > _MAX_GROUP_TEXT:
            # Keep the fixed marker (and therefore the explicit "media was not
            # provided" boundary) by trimming only the supplied text.  A
            # message that cannot fit even that is rejected rather than
            # silently treated as media-only.
            room = _MAX_GROUP_TEXT - len(suffix)
            if room <= 0:
                return None
            text = text[:room].rstrip()
            if not text:
                return None
        prompt = text + suffix
    else:
        prompt = " ".join(markers)
    if not prompt or len(prompt) > _MAX_GROUP_TEXT:
        return None
    if _TEXT_SEGMENT.fullmatch(prompt) is None:
        return None
    return prompt


def _success(packet) -> bool:
    return (
        isinstance(packet, dict)
        and packet.get("status") == "ok"
        and packet.get("retcode") == 0
        and "data" in packet
    )


def _delivery_outcome(packet):
    """Classify a OneBot send response without guessing from malformed data."""
    if not isinstance(packet, dict):
        return "unknown"
    status, code = packet.get("status"), packet.get("retcode")
    if type(code) is not int:
        return "unknown"
    if status == "failed" and code != 0:
        return "failed"
    if status == "ok" and code == 0:
        data = packet.get("data")
        if isinstance(data, dict):
            mid = data.get("message_id")
            if (type(mid) is int or
                    (type(mid) is str and 0 < len(mid) <= 128)):
                return "delivered"
    return "unknown"


def _valid_outgoing_answer(text) -> bool:
    """Validate one complete model answer before any chunk is sent."""
    if not isinstance(text, str) or not (0 < len(text) <= _MAX_OUTGOING_REPLY):
        return False
    if _OUTGOING_ANSWER.fullmatch(text) is None:
        return False
    try:
        text.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _valid_chunk(chunk) -> bool:
    """One QQ text segment: non-empty, at most 2000 safe characters."""
    if not isinstance(chunk, str) or not (0 < len(chunk) <= _MAX_CHUNK_TEXT):
        return False
    if _TEXT_SEGMENT.fullmatch(chunk) is None:
        return False
    try:
        chunk.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _split_outgoing(text):
    """Return bounded ordered chunks, or None for any invalid result."""
    try:
        chunks = reply_chunks.split_reply(text)
    except Exception:
        return None
    if (not chunks
            or len(chunks) > reply_chunks.MAX_CHUNKS
            or any(not _valid_chunk(chunk) for chunk in chunks)):
        return None
    return chunks


class OneBotAdapter:
    """Background OneBot v11 client.

    ``core_factory(bot_id)`` is called only after ``get_login_info`` confirms
    the local bot identity. It must return a QQCore configured for that ID.
    ``status_callback(code, short_message)`` is optional and always receives
    fixed, non-sensitive strings.  The model/delivery diagnostic codes are
    ``model_processing``, ``model_failed``, ``model_failed_timeout``,
    ``model_failed_connection``, ``model_failed_authentication``,
    ``model_failed_school_login``, ``model_failed_busy``,
    ``model_failed_unavailable``, ``model_response_invalid``,
    ``delivery_rejected``, ``delivery_unknown``, ``delivery_partial``, and
    ``reply_acknowledged``.  Group delivery uses the matching ``group_`` codes.
    ``send_pacing`` is a non-blocking pause (clamped to 2 seconds) between
    acknowledged chunks; it defaults to 0.25 s and may be set to 0 for tests.
    For offline tests ``connect_factory`` may be injected; production callers
    should leave it unset.
    """

    def __init__(self, ws_url, onebot_token, core_factory,
                 status_callback=None, connect_factory=None,
                 group_factory=None, group_enabled=None,
                 send_pacing=0.25):
        if not validate_ws_url(ws_url):
            raise ValueError("invalid local OneBot endpoint")
        if not _valid_token(onebot_token):
            raise ValueError("invalid OneBot token")
        if not callable(core_factory):
            raise TypeError("core_factory must be callable")
        if status_callback is not None and not callable(status_callback):
            raise TypeError("status_callback must be callable")
        if group_factory is not None and not callable(group_factory):
            raise TypeError("group_factory must be callable")
        if group_enabled is not None and not callable(group_enabled):
            raise TypeError("group_enabled must be callable")
        if isinstance(send_pacing, bool) or not isinstance(
                send_pacing, (int, float)):
            raise TypeError("send_pacing must be a number")
        pacing = float(send_pacing)
        if not math.isfinite(pacing) or pacing < 0:
            raise ValueError("send_pacing must be finite and non-negative")
        # A bounded, injectable pause between acknowledged chunks.  It runs
        # on the connection loop, never on the socket reader or the UI thread.
        self._send_pacing = min(pacing, 2.0)
        self._ws_url = ws_url
        self._token = onebot_token
        self._core_factory = core_factory
        self._status_callback = status_callback
        self._connect_factory = connect_factory
        # Optional group surface.  It is configured separately so existing
        # private-only callers (and test doubles) keep their exact constructor
        # contract.
        self._group_factory = group_factory
        self._group_enabled = group_enabled
        self._group_core = None
        self._pending_group_media = PendingGroupMedia()
        self._private_recent_media = {}
        self._queued_group_media = {}
        self._seen_group_mentions = OrderedDict()
        self._state_lock = threading.RLock()
        self._thread = None
        self._loop = None
        self._task = None
        self._stop_event = None
        self._running = False
        self._bot_id = None
        self._core = None
        # Incremented on every start/stop.  A diagnostic hook installed for an
        # older lifecycle captures this epoch and can never report after a
        # stop or reconnect, even if the same core object were reused.
        self._diagnostic_epoch = 0
        # Blocking core calls currently running on daemon threads.  They are
        # never cancelled, so shutdown must wait for this count to reach zero
        # before another core instance may be created.
        self._core_workers = 0
        self._status_queue = queue.Queue(maxsize=32)

    def clear_group_media(self):
        with self._state_lock:
            self._private_recent_media.clear()
            self._queued_group_media.clear()
            self._seen_group_mentions.clear()
            self._pending_group_media.clear()

    def configure_group(self, group_factory=None, group_enabled=None):
        """Attach the optional group surface before ``start``.

        ``group_factory(bot_id, private_core)`` must return a thread-safe group
        ledger or None.  ``group_enabled()`` is consulted for every group
        event, so a local UI switch takes effect without reconnecting.  The
        call is refused once a lifecycle is running; callers can simply
        construct the adapter with these keyword arguments instead.
        """
        if group_factory is not None and not callable(group_factory):
            raise TypeError("group_factory must be callable")
        if group_enabled is not None and not callable(group_enabled):
            raise TypeError("group_enabled must be callable")
        with self._state_lock:
            if self._running:
                return False
            self._group_factory = group_factory
            self._group_enabled = group_enabled
            return True

    def start(self) -> bool:
        """Start one background connection; return immediately.

        Refuses to start while a previous thread or any blocking core call is
        still alive.  Building a second core then could race the first one's
        state file.
        """
        with self._state_lock:
            if self._running:
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._core_workers:
                return False
            # A previous lifecycle may have left statuses in this same
            # adapter object's queue.  They belong to the old connection and
            # must never be replayed to a new owner.
            while True:
                try:
                    self._status_queue.get_nowait()
                except queue.Empty:
                    break
            self._running = True
            self.clear_group_media()
            self._diagnostic_epoch += 1
            self._bot_id = None
            self._core = None
            self._group_core = None
            self._thread = threading.Thread(
                target=self._thread_main, name="SiOneBotLoop", daemon=True
            )
            self._thread.start()
            return True

    def is_quiescent(self) -> bool:
        """Whether the background loop and every core worker have finished.

        Only then is it safe to create another core for the same state file.
        """
        with self._state_lock:
            thread = self._thread
            return (
                not self._running
                and (thread is None or not thread.is_alive())
                and self._core_workers == 0
            )

    def stop(self, timeout: float = _STOP_JOIN_SECONDS) -> bool:
        """Request shutdown and wait a bounded time.

        Returns True only when the connection thread and all blocking core
        calls have finished, so no task can still write the core state file.
        A model call inside ``handle_private`` cannot be cancelled; if it is
        still running after the bounded wait this returns False and the caller
        must keep the adapter and retry later instead of reconnecting.
        """
        self.clear_group_media()
        with self._state_lock:
            thread = self._thread
            loop = self._loop
            task = self._task
            self._running = False
            # Invalidate every diagnostic hook installed for the lifecycle
            # that is stopping, even if a worker thread outlives stop().
            self._diagnostic_epoch += 1
            if loop is not None and loop.is_running():
                try:
                    if task is not None and not task.done():
                        loop.call_soon_threadsafe(task.cancel)
                    else:
                        loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    # The loop closed between the check and the callback.
                    pass

        deadline = time.monotonic() + max(0.0, float(timeout))
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

        while True:
            with self._state_lock:
                alive = thread is not None and thread.is_alive()
                busy = self._core_workers != 0
                if not alive and not busy:
                    self._thread = None
                    self._loop = None
                    self._task = None
                    self._bot_id = None
                    self._core = None
                    self._group_core = None
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_WORKER_POLL_SECONDS)

    def verified_bot_id(self):
        """Return the verified bot number for explicit local UI display."""
        with self._state_lock:
            return self._bot_id

    def poll_status(self):
        """Return one queued ``(code, message)`` status, or None."""
        try:
            return self._status_queue.get_nowait()
        except queue.Empty:
            return None

    def _status(self, code, message):
        item = (code, message)
        try:
            self._status_queue.put_nowait(item)
        except queue.Full:
            try:
                self._status_queue.get_nowait()
                self._status_queue.put_nowait(item)
            except queue.Empty:
                pass
        callback = self._status_callback
        if callback is not None:
            try:
                callback(code, message)
            except Exception:
                pass

    def _install_core_diagnostics(self, core):
        """Attach the fixed-code diagnostic bridge when the core supports it."""
        setter = getattr(core, "set_diagnostic_callback", None)
        if not callable(setter):
            return
        with self._state_lock:
            epoch = self._diagnostic_epoch
        def bridge(code, core=core, epoch=epoch):
            self._core_diagnostic(core, code, epoch)

        try:
            setter(bridge)
        except Exception:
            pass

    def _core_diagnostic(self, core, code, epoch=None):
        """Forward one fixed core code on the loop thread, never raw data.

        The core hook runs on the blocking worker thread.  Scheduling the
        status on the connection loop keeps the existing status queue and UI
        callback on the same thread as every other adapter status.  If the
        loop is already gone the status is dropped rather than delivered from
        the worker thread.

        Both the current private core and the current group ledger are
        recognised.  Group diagnostics get a fixed ``群聊：`` prefix so the
        local status line never confuses them with private-chat status.
        A callback from a stopped or replaced core is rejected by identity and
        loop checks, including when a new lifecycle reuses this adapter.
        """
        if not isinstance(code, str):
            return
        message = _CORE_DIAGNOSTIC_TEXT.get(code)
        if message is None:
            return
        with self._state_lock:
            loop = self._loop
            if (not self._running or loop is None
                    or (epoch is not None
                        and epoch != self._diagnostic_epoch)):
                return
            if self._core is core:
                is_group = False
            elif self._group_core is core:
                is_group = True
            else:
                # Stop/reconnect clears both attributes, so an old core's
                # delayed callback can never become a current status.
                return
            if is_group:
                message = _GROUP_DIAGNOSTIC_PREFIX + message

        def deliver():
            with self._state_lock:
                current = (
                    self._running
                    and self._loop is loop
                    and (epoch is None
                         or epoch == self._diagnostic_epoch)
                    and (self._core is core or self._group_core is core)
                )
            if current:
                self._status(code, message)
        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._state_lock:
            self._loop = loop
            if not self._running:
                self._loop = None
                loop.close()
                return
            task = loop.create_task(self._run())
            self._task = task
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._status("connection_error", "连接中断，请检查本机 OneBot 设置。")
        finally:
            with self._state_lock:
                self._running = False
                self._bot_id = None
                self._core = None
                self._group_core = None
                self._task = None
                self._loop = None
            try:
                pending = asyncio.all_tasks(loop)
                for pending_task in pending:
                    pending_task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()
            with self._state_lock:
                busy = self._core_workers != 0
            if busy:
                # Do not claim a safe stop: a model call is still unwinding on
                # its worker thread and may still write the core state file.
                self._status(
                    "disconnected",
                    "本机连接已断开，正在等待本机模型调用结束；暂不能重新连接。",
                )
            else:
                self._status("disconnected", "本机连接已断开。")

    async def _run(self):
        try:
            connect_factory = self._connect_factory or _connect_local
            async with connect_factory(self._ws_url, self._token) as ws:
                pending_api = {}
                event_queue = asyncio.Queue(
                    maxsize=_PRIVATE_EVENT_QUEUE_MAX
                )
                group_queue = asyncio.Queue(maxsize=_GROUP_QUEUE_MAX)
                reader = asyncio.create_task(
                    self._reader(ws, pending_api, event_queue, group_queue)
                )
                group_worker = None
                try:
                    login = await self._api(ws, pending_api, "get_login_info", {})
                    if not _success(login) or not isinstance(login["data"], dict):
                        self._status("login_failed", "无法确认祀的 QQ 账号。")
                        return
                    bot_id = _account_id(login["data"].get("user_id"))
                    if bot_id is None:
                        self._status("login_failed", "无法确认祀的 QQ 账号。")
                        return
                    core = self._core_factory(bot_id)
                    if core is None:
                        self._status("core_failed", "本地聊天服务未能启动。")
                        return

                    group_core = None
                    if self._group_factory is not None:
                        try:
                            group_core = self._group_factory(bot_id, core)
                        except Exception:
                            group_core = None
                            self._status(
                                "group_unavailable", _GROUP_UNAVAILABLE_TEXT
                            )

                    with self._state_lock:
                        if not self._running:
                            return
                        self._bot_id = bot_id
                        self._core = core
                        self._group_core = group_core
                    # Install the hooks only after the identities are visible
                    # to ``_core_diagnostic``; otherwise an immediate diagnostic
                    # emitted during installation would be mistaken for a
                    # stale callback and silently dropped.
                    self._install_core_diagnostics(core)
                    if group_core is not None:
                        self._install_core_diagnostics(group_core)
                    if group_core is not None:
                        group_worker = asyncio.create_task(
                            self._group_worker(
                                ws, pending_api, group_queue, group_core
                            )
                        )
                    self._status("connected", "已识别 QQ 账号，可在本机生成绑定码。")
                    seen = set()
                    while True:
                        event = await event_queue.get()
                        if event is None:
                            break
                        await self._handle_event(ws, pending_api, event, seen)
                finally:
                    reader.cancel()
                    if group_worker is not None:
                        group_worker.cancel()
                    tasks = [reader]
                    if group_worker is not None:
                        tasks.append(group_worker)
                    await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status("connection_error", "本机连接失败，请检查 NapCat 配置。")

    async def _reader(self, ws, pending_api, event_queue, group_queue):
        try:
            async for raw in ws:
                if not isinstance(raw, str) or len(raw) > 1_000_000:
                    continue
                try:
                    packet = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(packet, dict):
                    continue
                echo = packet.get("echo")
                if isinstance(echo, str) and echo in pending_api:
                    future = pending_api.pop(echo)
                    if not future.done():
                        future.set_result(packet)
                    continue
                if packet.get('post_type') == 'notice' and packet.get('notice_type') == 'group_upload':
                    uploaded = packet.get('file')
                    if not isinstance(uploaded, dict):
                        continue
                    file_id = uploaded.get('id')
                    if not isinstance(file_id, str) or len(file_id)>4096:
                        continue
                    try:
                        upload_key = hashlib.sha256(file_id.encode('utf-8')).hexdigest()
                    except UnicodeError:
                        continue
                    packet = {
                        'post_type':'message', 'message_type':'group', 'sub_type':'normal',
                        'self_id':packet.get('self_id'), 'group_id':packet.get('group_id'),
                        'user_id':packet.get('user_id'),
                        'message_id':'upload-'+upload_key,
                        'message':[{'type':'file','data':dict(uploaded, file_id=file_id)}],
                    }
                if packet.get("post_type") != "message":
                    continue
                if packet.get("message_type") == "group":
                    with self._state_lock:
                        bot_id, group_core, running = self._bot_id, self._group_core, self._running
                    if not running or bot_id is None or group_core is None or not self._group_enabled_now():
                        self.clear_group_media()
                        continue
                    # Only bounded, copied attachment metadata is staged here.
                    # No downloads/model calls or state files for unmentioned traffic.
                    self._pending_group_media.offer(packet, bot_id)
                    details = self._group_event_details(packet)
                    if details is None:
                        continue
                    key = details[:3]
                    with self._state_lock:
                        if key in self._seen_group_mentions:
                            continue
                    batch = self._pending_group_media.take(details[0], details[1])
                    try:
                        group_queue.put_nowait(packet)
                    except asyncio.QueueFull:
                        self._pending_group_media.restore(details[0], details[1], batch)
                    else:
                        with self._state_lock:
                            self._queued_group_media[key] = batch
                            self._seen_group_mentions[key] = None
                            while len(self._seen_group_mentions)>256:
                                self._seen_group_mentions.popitem(last=False)
                    continue
                # Never block the socket reader on application work.  This
                # reader also resolves every API acknowledgement above; a
                # full private queue must not prevent a later send_group_msg
                # (or private send) acknowledgement from reaching its waiter.
                # Keep the existing bounded queue and drop excess application
                # events rather than turning a confirmed send into a timeout.
                try:
                    event_queue.put_nowait(packet)
                except asyncio.QueueFull:
                    pass
        finally:
            # Never block shutdown on a full event queue.
            try:
                event_queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    event_queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    async def _api(self, ws, pending_api, action, params):
        echo = "si-" + secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        pending_api[echo] = future
        try:
            await ws.send(json.dumps({
                "action": action,
                "params": params,
                "echo": echo,
            }, ensure_ascii=False, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout=12.0)
        finally:
            pending_api.pop(echo, None)

    async def _handle_event(self, ws, pending_api, event, seen):
        if (event.get("post_type") != "message"
                or event.get("message_type") != "private"
                or event.get("sub_type") != "friend"):
            return
        with self._state_lock:
            bot_id = self._bot_id
            core = self._core
            running = self._running
        if not running or core is None or bot_id is None:
            return
        if _account_id(event.get("self_id")) != bot_id:
            return
        sender_id = _account_id(event.get("user_id"))
        if sender_id is None or sender_id == bot_id:
            return
        raw_message_id = event.get("message_id")
        if isinstance(raw_message_id, bool) or not isinstance(raw_message_id, (int, str)):
            return
        message_id = str(raw_message_id)
        if _MESSAGE_ID.fullmatch(message_id) is None or message_id in seen:
            return
        text = _plain_text(event.get("message"))
        media = []
        if text is None and isinstance(event.get('message'), list):
            # Only the already bound administrator can provide private media.
            try:
                authorized = core.is_administrator(sender_id)
            except Exception:
                authorized = False
            if not authorized:
                return
            staged = PendingGroupMedia(max_pairs=1, max_items_per_pair=1)
            synthetic = dict(event, message_type='group', sub_type='normal', group_id='1')
            if staged.offer(synthetic, bot_id):
                media = staged.take('1', sender_id)
                self._private_recent_media={sender_id:(time.monotonic(),media)}
                pieces = [s.get('data',{}).get('text','') for s in event['message']
                          if isinstance(s,dict) and s.get('type')=='text' and isinstance(s.get('data'),dict)]
                if not all(isinstance(p,str) for p in pieces):
                    return
                text = ''.join(pieces).strip() or '对方发来了附件，请根据本轮实际提供的内容自然回应。'
                if not _TEXT_SEGMENT.fullmatch(text):
                    return
        if text is None or not text.strip():
            return
        if not media and re.search(r'这[个是张份]|刚才|附件|文件|图[片里中]|概[括述]|总结',text):
            recent=self._private_recent_media.get(sender_id)
            if (recent and time.monotonic()-recent[0]<600
                    and core.is_administrator(sender_id)):
                media=recent[1]
        self._private_recent_media={key:value for key,value in self._private_recent_media.items()
                                    if time.monotonic()-value[0]<600}
        seen.add(message_id)
        if len(seen) > 4096:
            seen.clear()
            seen.add(message_id)
        try:
            async def invoke_media(action, params):
                return await self._api(ws, pending_api, action, params)
            resolver = make_resolver(asyncio.get_running_loop(), invoke_media, sender_id=sender_id)
            def call_private():
                with use_media(media, resolver):
                    return core.handle_private(sender_id, message_id, text)
            reply = await self._call_core(
                call_private,
                on_abandon=self._abandon_unsent(core),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if reply is None:
            return
        is_chat = getattr(reply, "kind", None) == "chat"
        if not isinstance(getattr(reply, "text", None), str):
            if is_chat:
                self._status("model_response_invalid", _MODEL_RESPONSE_INVALID_TEXT)
                await self._finalize_core(
                    core, getattr(reply, "delivery_token", None), False
                )
            return
        reply_text = reply.text
        if is_chat:
            if not _valid_outgoing_answer(reply_text):
                self._status("model_response_invalid", _MODEL_RESPONSE_INVALID_TEXT)
                await self._finalize_core(core, reply.delivery_token, False)
                return
            chunks = _split_outgoing(reply_text)
            if chunks is None:
                self._status("model_response_invalid", _MODEL_RESPONSE_INVALID_TEXT)
                await self._finalize_core(core, reply.delivery_token, False)
                return
        else:
            # Binding/error notices stay short, fixed, single sends.
            if not _valid_chunk(reply_text):
                return
            chunks = [reply_text]
        await self._deliver_private_chunks(
            ws, pending_api, core, reply, sender_id, chunks, is_chat
        )

    async def _deliver_private_chunks(self, ws, pending_api, core, reply,
                                      sender_id, chunks, is_chat):
        """Send *chunks* in order and finalize only after every ACK.

        A definite rejection of the first chunk (nothing delivered) finalizes
        the pending as not delivered, exactly as before.  Any later rejection,
        timeout, malformed ACK, or cancellation leaves the durable pending
        record recoverable and unknown: no chunk is retried or resent, and the
        whole answer is never marked delivered or written to history.
        """
        token = getattr(reply, "delivery_token", None)
        delivered_count = 0
        segment = getattr(reply, 'segment', None)
        if segment is not None:
            chunks = [reply.text]
        total = len(chunks)
        for index, chunk in enumerate(chunks):
            try:
                ack = await self._api(ws, pending_api, "send_private_msg", {
                    "user_id": sender_id,
                    "message": [segment] if segment is not None else [{"type": "text", "data": {"text": chunk}}],
                })
            except asyncio.CancelledError:
                # The request may already be on the wire; never resend it and
                # do not guess.  With earlier chunks already acknowledged the
                # reply is partially sent, so keep the pending recoverable.
                if is_chat:
                    self._mark_unknown(core, token)
                    if delivered_count:
                        self._status("delivery_partial", _DELIVERY_PARTIAL_TEXT)
                raise
            except Exception:
                # Timeout or lost connection is an unknown outcome.  Never
                # resend or finalize it as a known failure.
                if is_chat:
                    self._mark_unknown(core, token)
                if delivered_count:
                    self._status("delivery_partial", _DELIVERY_PARTIAL_TEXT)
                else:
                    self._status(
                        "delivery_unknown",
                        _DELIVERY_UNKNOWN_TEXT if is_chat
                        else _NOTICE_DELIVERY_UNKNOWN_TEXT,
                    )
                return
            outcome = _delivery_outcome(ack)
            if outcome == "delivered":
                delivered_count += 1
                if index < total - 1:
                    try:
                        await asyncio.sleep(self._send_pacing)
                    except asyncio.CancelledError:
                        if is_chat:
                            self._mark_unknown(core, token)
                            if delivered_count:
                                self._status(
                                    "delivery_partial", _DELIVERY_PARTIAL_TEXT
                                )
                        raise
                continue
            if outcome == "failed" and delivered_count == 0:
                # The first send was definitely rejected and nothing reached
                # QQ; this is the only path that finalizes a chat as False.
                self._status("delivery_rejected", _DELIVERY_REJECTED_TEXT)
                if is_chat:
                    try:
                        await self._finalize_core(core, token, False)
                    except asyncio.CancelledError:
                        self._mark_unknown(core, token)
                        raise
                if segment is not None:
                    await self._media_rejected_notice(ws, pending_api, sender_id)
                return
            # A malformed ACK, or a rejection/unknown outcome after an earlier
            # chunk succeeded: the final state of the whole reply is unknown.
            if is_chat:
                self._mark_unknown(core, token)
            if delivered_count:
                self._status("delivery_partial", _DELIVERY_PARTIAL_TEXT)
            else:
                self._status(
                    "delivery_unknown",
                    _DELIVERY_UNKNOWN_TEXT if is_chat
                    else _NOTICE_DELIVERY_UNKNOWN_TEXT,
                )
            return
        if is_chat:
            try:
                saved = await self._finalize_core(core, token, True)
            except asyncio.CancelledError:
                # Every chunk was acknowledged, but the local transaction may
                # not have committed.  The durable pending record is kept and
                # flagged; nothing is resent or guessed.
                self._mark_unknown(core, token)
                raise
            if saved:
                self._status("reply_acknowledged", _REPLY_ACKNOWLEDGED_TEXT)

    # -- group mention path -------------------------------------------

    def _group_enabled_now(self):
        callback = self._group_enabled
        if callback is None:
            return True
        try:
            return bool(callback())
        except Exception:
            # Fail closed: an unreadable local switch never enables replies.
            return False

    def _group_event_details(self, event):
        """Return validated group tuple, or None for every ignored event.

        This is the single target check used by the reader before anything is
        queued and again by the worker before any model call.  It requires a
        ``group``/``normal`` event from a non-self sender, addressed to the
        verified bot by an array ``at`` segment.  A target-only mention and a
        target mention with media are valid; their prompt receives the fixed
        synthetic markers from :func:`_group_message_text`.
        """
        if not isinstance(event, dict):
            return None
        with self._state_lock:
            bot_id = self._bot_id
            group_core = self._group_core
            running = self._running
        if not running or bot_id is None or group_core is None:
            return None
        if not self._group_enabled_now():
            return None
        if event.get("post_type") != "message":
            return None
        if event.get("message_type") != "group":
            return None
        if event.get("sub_type") != "normal":
            return None
        if _account_id(event.get("self_id")) != bot_id:
            return None
        group_id = _account_id(event.get("group_id"))
        if group_id is None:
            return None
        sender_id = _account_id(event.get("user_id"))
        if sender_id is None or sender_id == bot_id:
            return None
        raw_message_id = event.get("message_id")
        if isinstance(raw_message_id, bool) or not isinstance(
                raw_message_id, (int, str)):
            return None
        message_id = str(raw_message_id)
        if _MESSAGE_ID.fullmatch(message_id) is None:
            return None
        text = _group_message_text(event.get("message"), bot_id)
        if text is None:
            return None
        return group_id, sender_id, message_id, text

    async def _group_worker(self, ws, pending_api, group_queue, group_core):
        """Consume the small group queue one model job at a time."""
        while True:
            event = await group_queue.get()
            if event is None:
                break
            try:
                await self._handle_group_event(
                    ws, pending_api, event, group_core
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A malformed event or unexpected core failure is contained to
                # this group item; private chat and later group items continue.
                continue

    async def _handle_group_event(self, ws, pending_api, event, group_core):
        details = self._group_event_details(event)
        if details is None:
            # A switch-off can invalidate a previously queued mention.
            if not self._group_enabled_now():
                self.clear_group_media()
            return
        group_id, sender_id, message_id, text = details
        with self._state_lock:
            media = self._queued_group_media.pop(details[:3], [])
            if (not self._running
                    or self._core is None
                    or self._group_core is not group_core):
                return
        try:
            if media:
                text = text.replace(_MEDIA_NOT_PROVIDED_MARKER, '').strip()
                text += ' [本轮含同一发送者本次或稍早的附件，仅按实际提供的信息回应。]'
                text = text[:_MAX_GROUP_TEXT]
            async def invoke_media(action, params):
                return await self._api(ws, pending_api, action, params)
            resolver = make_resolver(asyncio.get_running_loop(), invoke_media,
                                     group_id=group_id, sender_id=sender_id)
            def call_group():
                with use_media(media, resolver):
                    return group_core.handle_group(group_id, sender_id, message_id, text)
            reply = await self._call_core(
                call_group,
                on_abandon=self._abandon_unsent_group(group_core),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if reply is None:
            return
        is_chat = getattr(reply, "kind", None) == "group_chat"
        reply_text = getattr(reply, "text", None)
        if is_chat:
            if not _valid_outgoing_answer(reply_text):
                self._status(
                    "model_response_invalid", _MODEL_RESPONSE_INVALID_TEXT
                )
                await self._finalize_group(
                    group_core, getattr(reply, "delivery_token", None), False
                )
                return
            chunks = _split_outgoing(reply_text)
            if chunks is None:
                self._status(
                    "model_response_invalid", _MODEL_RESPONSE_INVALID_TEXT
                )
                await self._finalize_group(
                    group_core, getattr(reply, "delivery_token", None), False
                )
                return
        else:
            # The natural group error notice stays a short single send.
            if not _valid_chunk(reply_text):
                return
            chunks = [reply_text]
        await self._deliver_group_chunks(
            ws, pending_api, group_core, reply,
            group_id, sender_id, chunks, is_chat,
        )

    async def _deliver_group_chunks(self, ws, pending_api, group_core, reply,
                                    group_id, sender_id, chunks, is_chat):
        """Send group chunks in order; the sender is @-ed on the first only."""
        token = getattr(reply, "delivery_token", None)
        delivered_count = 0
        segment = getattr(reply, 'segment', None)
        if segment is not None:
            chunks = [reply.text]
        total = len(chunks)
        for index, chunk in enumerate(chunks):
            message = []
            if index == 0:
                message.append({"type": "at", "data": {"qq": sender_id}})
            # Native @ segment followed by a visible separator for QQ clients.
            # This transport space is not part of the stored model answer.
            wire_text = (' ' if index == 0 else '') + chunk
            message.append({"type": "text", "data": {"text": " " if segment is not None else wire_text}})
            if segment is not None:
                message.append(segment)
            try:
                ack = await self._api(ws, pending_api, "send_group_msg", {
                    "group_id": group_id,
                    "message": message,
                })
            except asyncio.CancelledError:
                # Never resend a chunk whose request may already be on the
                # wire.  Earlier acknowledged chunks make this a partial send.
                if is_chat:
                    self._mark_unknown(group_core, token)
                    if delivered_count:
                        self._status(
                            "group_delivery_partial",
                            _GROUP_DELIVERY_PARTIAL_TEXT,
                        )
                raise
            except Exception:
                if is_chat:
                    self._mark_unknown(group_core, token)
                if delivered_count:
                    self._status(
                        "group_delivery_partial",
                        _GROUP_DELIVERY_PARTIAL_TEXT,
                    )
                else:
                    self._status(
                        "group_delivery_unknown",
                        _GROUP_DELIVERY_UNKNOWN_TEXT if is_chat
                        else _GROUP_NOTICE_UNKNOWN_TEXT,
                    )
                return
            outcome = _delivery_outcome(ack)
            if outcome == "delivered":
                delivered_count += 1
                if index < total - 1:
                    try:
                        await asyncio.sleep(self._send_pacing)
                    except asyncio.CancelledError:
                        if is_chat:
                            self._mark_unknown(group_core, token)
                            if delivered_count:
                                self._status(
                                    "group_delivery_partial",
                                    _GROUP_DELIVERY_PARTIAL_TEXT,
                                )
                        raise
                continue
            if outcome == "failed" and delivered_count == 0:
                self._status(
                    "group_delivery_rejected", _GROUP_DELIVERY_REJECTED_TEXT
                )
                if is_chat:
                    try:
                        await self._finalize_group(group_core, token, False)
                    except asyncio.CancelledError:
                        self._mark_unknown(group_core, token)
                        raise
                if segment is not None:
                    await self._media_rejected_notice(
                        ws, pending_api, sender_id, group_id=group_id)
                return
            if is_chat:
                self._mark_unknown(group_core, token)
            if delivered_count:
                self._status(
                    "group_delivery_partial", _GROUP_DELIVERY_PARTIAL_TEXT
                )
            else:
                self._status(
                    "group_delivery_unknown",
                    _GROUP_DELIVERY_UNKNOWN_TEXT if is_chat
                    else _GROUP_NOTICE_UNKNOWN_TEXT,
                )
            return
        if is_chat:
            saved = await self._finalize_group(group_core, token, True)
            if saved:
                self._status(
                    "group_reply_acknowledged", _GROUP_REPLY_ACKNOWLEDGED_TEXT
                )

    async def _finalize_group(self, group_core, token, delivered):
        if token is None:
            self._status("group_storage_error", _GROUP_STORAGE_ERROR_TEXT)
            return False
        for attempt in range(3):
            try:
                saved = await self._call_core(
                    group_core.finalize_group, token, delivered
                )
            except asyncio.CancelledError:
                # The QQ outcome is already fixed, but this process may be
                # stopping before the local transaction commits.  The durable
                # pending record is kept; flag it for the explicit local
                # recovery path without writing, resending, or guessing.
                if token is not None:
                    try:
                        group_core.mark_group_delivery_unknown(token)
                    except Exception:
                        pass
                raise
            except Exception:
                saved = False
            if saved:
                return True
            if attempt < 2:
                await asyncio.sleep(0.05)
        # The QQ outcome is fixed but the local transaction did not commit.
        # Never claim success; the durable pending record stays fail-closed
        # and the local UI offers explicit recovery.  Marking it unknown is a
        # memory-only flag: it changes no bytes and never retries the send.
        if token is not None:
            try:
                group_core.mark_group_delivery_unknown(token)
            except Exception:
                pass
        self._status("group_storage_error", _GROUP_STORAGE_ERROR_TEXT)
        return False

    def _abandon_unsent_group(self, group_core):
        """Cleanup for a group model call abandoned before any send."""
        def cleanup(result):
            token = getattr(result, "delivery_token", None)
            if token and getattr(result, "kind", None) == "group_chat":
                try:
                    resolved = group_core.finalize_group(token, False)
                except Exception:
                    resolved = False
                if not resolved:
                    # The send never happened, but the durable pending record
                    # could not be cleared.  Keep it visible to the explicit
                    # local recovery path rather than leaving it silently
                    # blocking the group with no UI action.
                    try:
                        group_core.mark_group_delivery_unknown(token)
                    except Exception:
                        pass

        return cleanup

    # -- blocking core calls ------------------------------------------

    async def _call_core(self, function, *args, on_abandon=None):
        """Run one blocking core call on a daemon thread and await it.

        The thread cannot be cancelled.  ``on_abandon`` runs exactly once,
        after the call has returned, if the awaiting coroutine is cancelled
        first.  The worker count stays non-zero until that cleanup and the
        call are completely finished, so shutdown never reports quiescence
        while a thread may still write the state file.
        """
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        state = {
            "done": False,
            "result": None,
            "error": None,
            "abandoned": False,
            "resolved": False,
        }
        with self._state_lock:
            self._core_workers += 1

        def deliver(result, error):
            def apply():
                if waiter.done():
                    return
                if error is not None:
                    waiter.set_exception(error)
                else:
                    waiter.set_result(result)

            try:
                loop.call_soon_threadsafe(apply)
            except RuntimeError:
                # The event loop closed during shutdown; the result is simply
                # dropped.  Any unknown delivery keeps its persisted pending.
                pass

        def run():
            result = None
            error = None
            abandoned_here = False
            try:
                try:
                    result = function(*args)
                except BaseException as exc:  # noqa: BLE001 - reported below
                    error = exc
                with self._state_lock:
                    state["done"] = True
                    state["result"] = result
                    state["error"] = error
                    if state["abandoned"] and not state["resolved"]:
                        state["resolved"] = True
                        abandoned_here = True
                if abandoned_here and on_abandon is not None:
                    try:
                        on_abandon(result)
                    except Exception:
                        pass
            finally:
                with self._state_lock:
                    self._core_workers -= 1
                if not abandoned_here:
                    deliver(result, error)

        try:
            threading.Thread(
                target=run, name="SiQQCoreCall", daemon=True
            ).start()
        except Exception:
            with self._state_lock:
                self._core_workers -= 1
            raise

        try:
            return await waiter
        except asyncio.CancelledError:
            # The model call is still running (or just finished).  It never
            # reached the QQ send step, so when it returns the pending record
            # must be resolved as not delivered, not left unknown.
            result = None
            finalize_here = False
            with self._state_lock:
                state["abandoned"] = True
                if state["done"] and not state["resolved"]:
                    state["resolved"] = True
                    finalize_here = True
                    result = state["result"]
            if finalize_here and on_abandon is not None:
                try:
                    on_abandon(result)
                except Exception:
                    pass
            raise

    def _abandon_unsent(self, core):
        """Return the cleanup used when a model call is abandoned at shutdown.

        No send happened for this reply, so resolving the durable pending as
        not delivered is exact and does not count as an unknown outcome.
        """
        def cleanup(result):
            token = getattr(result, "delivery_token", None)
            if token and getattr(result, "kind", None) == "chat":
                try:
                    core.finalize_reply(token, False)
                except Exception:
                    pass

        return cleanup

    async def _media_rejected_notice(self, ws, pending_api, sender_id, group_id=None):
        """One text notice after a definite media rejection, never after timeout.

        This is separate from the failed answer: no media retry and no
        successful conversation turn is fabricated. A lost notice ACK is not
        retried either.
        """
        message = []
        if group_id is not None:
            message.append({'type': 'at', 'data': {'qq': sender_id}})
        message.append({'type': 'text', 'data': {'text':
            (' ' if group_id is not None else '') +
            '文件已在本机准备好，但 QQ 拒绝了媒体上传，这次没发出去。'}})
        action = 'send_private_msg' if group_id is None else 'send_group_msg'
        params = {'user_id': sender_id} if group_id is None else {'group_id': group_id}
        params['message'] = message
        try:
            await self._api(ws, pending_api, action, params)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _finalize_core(self, core, token, delivered):
        if token is None:
            self._status("storage_error", "本机无法保存本次发送结果。")
            return False
        for attempt in range(3):
            try:
                saved = await self._call_core(
                    core.finalize_reply, token, delivered
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                saved = False
            if saved:
                return True
            if attempt < 2:
                await asyncio.sleep(0.05)
        # The QQ outcome is fixed but the local transaction did not commit.
        # Never claim success; the durable pending record stays fail-closed
        # and is the explicit recovery path after a local restart.
        self._status(
            "storage_error",
            "本机未能保存本次发送结果；暂时不能继续私聊。"
            "请检查本机存储后重启程序并使用“恢复未确认回复”。",
        )
        return False

    def _mark_unknown(self, core, token):
        if token is None:
            return
        marker = getattr(core, "mark_delivery_unknown", None)
        if not callable(marker):
            return
        try:
            marker(token)
        except Exception:
            pass


def _connect_local(url, token):
    """Build a websockets 15 client that cannot follow a redirect."""
    from websockets.asyncio.client import connect

    class NoRedirectClient(connect):
        def process_redirect(self, exc):
            return exc

    return NoRedirectClient(
        url,
        additional_headers={"Authorization": "Bearer " + token},
        proxy=None,
        open_timeout=8,
        close_timeout=1,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_000_000,
    )
