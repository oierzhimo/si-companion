"""Manual, development-only live check for the frozen GenAI chat path.

This module is reachable only through the hidden ``--check-genai-live``
argument.  It constructs the real :class:`ConversationDialog` without showing
it, drives the real model-list and send buttons with Qt's programmatic
``click()``, and waits for the dialog's queued signals.  It is not a physical
mouse/keyboard test and it does not validate the user's desktop or persona.

The probe deliberately uses only a fresh directory beside the explicitly
chosen report path.  It never opens the ordinary settings, conversation,
voice cache, school credentials, or proxy control code.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt

from . import conversation_ui
from .conversation import (
    Conversation,
    DEFAULT_GENAI_ENDPOINT,
    GENAI_ALLOWED_MODELS,
    GENAI_DEFAULT_API_KEY,
    GENAI_DEFAULT_MODEL,
)
from .conversation_ui import ConversationDialog

REPORT_KIND = 'genai-frozen-live-ui-probe-v1'
SYNTHETIC_PROMPT = '合成验收：管理员回来了，请用一句自然的话回应。'
DEFAULT_TOTAL_TIMEOUT = 180.0
MAX_ERROR_TEXT = 2000

EXIT_OK = 0
EXIT_FAILED = 2
EXIT_TIMEOUT = 3
EXIT_REFUSED = 4

_SECRET_PATTERN = re.compile(
    r'(?i)(?:bearer\s+[^\s,;]+|sk-[a-z0-9._-]+)'
)


class ProbeRefused(RuntimeError):
    """The requested report/artifact target already exists or is unsafe."""


class ProbeFailure(RuntimeError):
    """The explicit live probe could not complete its acceptance checks."""


def _exists(path: Path) -> bool:
    """True for files, directories, and broken symlinks alike."""
    return os.path.lexists(os.fspath(path))


def validate_report_path(report_path) -> Path:
    """Validate the explicit output path without creating any target."""
    path = Path(report_path)
    if not path.is_absolute():
        raise ProbeRefused('报告路径必须是绝对路径。')
    if not path.name or path.name in ('.', '..'):
        raise ProbeRefused('报告路径必须指向一个文件。')
    if _exists(path):
        raise ProbeRefused('报告目标已存在；验收拒绝覆盖任何已有路径。')
    parent = path.parent
    if _exists(parent) and not parent.is_dir():
        raise ProbeRefused('报告路径的父路径不是目录。')
    return path


def prepare_artifact_dir(report_path) -> Path:
    """Create a brand-new directory beside the report, never reusing one."""
    report = validate_report_path(report_path)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(
            prefix=report.stem + '.artifacts-', dir=report.parent))
    except OSError as exc:
        raise ProbeRefused('无法在报告路径旁创建独立验收目录。') from exc


def _write_new_json(path: Path, payload) -> None:
    """Write one JSON file with exclusive creation; never replace a target."""
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ProbeRefused('报告目标在验收期间出现；拒绝覆盖。') from exc
    except OSError as exc:
        raise ProbeRefused('无法新建报告文件。') from exc


def _redact(value, api_key='') -> str:
    """Keep short diagnostic text while removing credential-shaped values."""
    try:
        text = str(value)
    except Exception:
        text = '<unprintable>'
    if api_key:
        text = text.replace(api_key, '[redacted]')
    text = text.replace(GENAI_DEFAULT_API_KEY, '[redacted]')
    text = _SECRET_PATTERN.sub('[redacted]', text)
    return text[:MAX_ERROR_TEXT]


def _safe_status(dialog) -> str:
    try:
        return _redact(dialog.status.text(), dialog.api_key.text())
    except (RuntimeError, AttributeError, TypeError):
        return ''


def _base_report(report_path: Path, artifact_dir: Path | None, app) -> dict:
    return {
        'kind': REPORT_KIND,
        'ok': False,
        'status': 'partial',
        'explicit_invocation': True,
        'normal_startup_path': False,
        'synthetic_only': True,
        'synthetic_prompt': SYNTHETIC_PROMPT,
        'technical_roundtrip_verified': False,
        'physical_gui_verified': False,
        'api_key_in_memory_only': True,
        'real_user_data_read': False,
        'school_token_read': False,
        'voice_cache_read': False,
        'voice_studio_started': False,
        'global_hotkey_registered': False,
        'tray_created': False,
        'desktop_pet_created': False,
        'proxy_started_or_stopped': False,
        'real_network_allowed_by_explicit_flag': True,
        'frozen_executable': bool(getattr(sys, 'frozen', False)),
        'report_path': str(report_path),
        'artifact_dir': str(artifact_dir) if artifact_dir else None,
        'qt_platform': app.platformName() if app is not None else '',
        'window_shown': False,
        'default_provider': 'genai',
        'default_model': GENAI_DEFAULT_MODEL,
        'model': GENAI_DEFAULT_MODEL,
        'default_endpoint': DEFAULT_GENAI_ENDPOINT,
        'allowed_domestic_models': list(GENAI_ALLOWED_MODELS),
        'interaction': {
            'method': 'programmatic_qt_widget_click',
            'model_list_button_clicked_programmatically': False,
            'send_button_clicked_programmatically': False,
            'programmatic_qt_click_count': 0,
            'physical_mouse_or_keyboard_used': False,
            'physical_gui_clicked': False,
            'user_desktop_acceptance': False,
            'not_a_physical_gui_acceptance': True,
            'message_input_method': 'programmatic QTextEdit.setPlainText',
            'queued_qt_signals_wait_used': True,
        },
        'model_list': [],
        'model_list_only_allowed_domestic': False,
        'default_preset_preserved': False,
        'default_model_preserved': False,
        'model_fetch_request_count': 0,
        'reply_request_count': 0,
        'sent_model': '',
        'reply_nonempty': False,
        'conversation_path': None,
        'saved_in_isolated_artifact_dir': False,
        'reloaded_consistent': False,
        'api_key_in_report': False,
        'api_key_in_conversation_file': False,
        'authorization_header_recorded': False,
        'controller_say_records': [],
        'controller_say_only_recorded': True,
        'audio_playback_started': False,
        'error': None,
        'dialog_status': '',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': 0.0,
    }


class RecordingController:
    """The dialog's controller double: record only, never play or show."""

    def __init__(self):
        self.say_calls = []

    def say(self, text, force=False):
        self.say_calls.append({'text': text, 'force': bool(force)})


class _ProbeRun:
    def __init__(self, app, report, report_path, artifact_dir,
                 total_timeout, original_fetch, original_reply):
        self.app = app
        self.report = report
        self.report_path = report_path
        self.artifact_dir = artifact_dir
        self.conversation_path = artifact_dir / 'conversation.json'
        self.total_timeout = max(0.05, float(total_timeout))
        self.original_fetch = original_fetch
        self.original_reply = original_reply
        self.loop = QEventLoop()
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timed_out)
        self.dialog = None
        self.controller = RecordingController()
        self.finished = False
        self.status = 'failed'
        self.error_type = ''
        self.error_message = ''
        self.stage = 'starting'
        self.sent = []
        self.send_clicked = False
        self.fetch_calls = 0
        self.pending_models = None
        self.pending_completion = None
        self._cleaned = False

    def _safe_api_key(self) -> str:
        """Read the in-memory key without assuming the QDialog still exists."""
        dialog = self.dialog
        if dialog is None:
            return ''
        try:
            return dialog.api_key.text()
        except (RuntimeError, AttributeError, TypeError):
            return ''

    def _stop_and_quit(self):
        """Best-effort idempotent shutdown of the probe timer and event loop."""
        try:
            self.timer.stop()
        except (RuntimeError, TypeError, AttributeError):
            pass
        try:
            self.loop.quit()
        except (RuntimeError, TypeError, AttributeError):
            pass

    def fail(self, stage, message, error_type='ProbeFailure'):
        if self.finished:
            return
        self.finished = True
        self.status = 'failed'
        self.stage = stage
        self.error_type = error_type
        try:
            self.error_message = _redact(message, self._safe_api_key())
        except Exception:
            self.error_message = '验收未完成。'
        self._stop_and_quit()

    def _timed_out(self):
        # Never let an exception escape a Qt slot.  PySide routes unhandled
        # slot exceptions through the process-wide sys.excepthook, which the
        # desktop application replaces; a timeout must end the probe cleanly
        # instead of entering that global error UI.
        try:
            if self.finished:
                return
            self.finished = True
            self.status = 'timeout'
            self.error_type = 'ProbeTimeout'
            self.error_message = (
                f'验收在 {self.total_timeout:g} 秒总超时内未完成'
                f'（阶段：{self.stage}）。'
            )
        except Exception:
            pass
        finally:
            self._stop_and_quit()

    def _recorded_fetch(self, endpoint, api_key=''):
        self.fetch_calls += 1
        return self.original_fetch(endpoint, api_key)

    def _recorded_reply(self, endpoint, model, messages, api_key=''):
        # Record only non-secret protocol metadata.  The request still goes
        # through the production client unchanged.
        last_message = ''
        if isinstance(messages, list) and messages:
            candidate = messages[-1]
            if isinstance(candidate, dict) and isinstance(candidate.get('content'), str):
                last_message = candidate['content']
        self.sent.append({
            'endpoint': endpoint,
            'model': model,
            'last_user_message': last_message,
            'api_key_supplied_in_memory': bool(api_key),
        })
        return self.original_reply(endpoint, model, messages, api_key)

    def _install_recording(self):
        conversation_ui.fetch_models_request = self._recorded_fetch
        conversation_ui.request_reply = self._recorded_reply

    def _restore_recording(self):
        conversation_ui.fetch_models_request = self.original_fetch
        conversation_ui.request_reply = self.original_reply

    def _check_defaults(self):
        dialog = self.dialog
        if dialog.current_provider() != 'genai':
            raise ProbeFailure('新验收窗口没有停留在 GenAI 预设。')
        if dialog.endpoint.text().strip().rstrip('/') != DEFAULT_GENAI_ENDPOINT:
            raise ProbeFailure('验收窗口的 GenAI 本机接口不是固定默认值。')
        if (dialog.model.text().strip().casefold()
                != GENAI_DEFAULT_MODEL.casefold()):
            raise ProbeFailure('验收窗口没有使用 DeepSeek-V4.1-Flash 默认模型。')
        if dialog.provider != 'genai' or dialog.session.provider != 'genai':
            raise ProbeFailure('验收窗口的提供方默认状态被改变。')
        if (dialog.session.model.strip().casefold()
                != GENAI_DEFAULT_MODEL.casefold()):
            raise ProbeFailure('验收窗口保存的默认模型不是 DeepSeek-V4.1-Flash。')
        if dialog.api_key.text() != GENAI_DEFAULT_API_KEY:
            raise ProbeFailure('验收窗口的本机访问口令预设与正式默认不一致。')
        if dialog.busy or dialog.model_busy:
            raise ProbeFailure('新建验收窗口不应处于请求状态。')

    def start(self):
        self.timer.start(max(1, int(self.total_timeout * 1000)))
        self._install_recording()
        try:
            try:
                # Never show this window: Qt still creates the real dialog and
                # all widgets, and click() exercises their actual signal
                # handlers.
                self.dialog = ConversationDialog(
                    self.controller, self.conversation_path)
                self.report['conversation_path'] = str(self.conversation_path)
                self._check_defaults()
                self.dialog.models_ready.connect(
                    self._models_ready, Qt.ConnectionType.QueuedConnection)
                self.dialog.completed.connect(
                    self._reply_ready, Qt.ConnectionType.QueuedConnection)

                self.stage = 'model_list'
                self.report['interaction'][
                    'model_list_button_clicked_programmatically'] = True
                self.report['interaction']['programmatic_qt_click_count'] = 1
                self.dialog.get_models.click()
                if not self.dialog.model_busy:
                    raise ProbeFailure(
                        '模型列表按钮没有进入请求状态：' + _safe_status(self.dialog))
            except Exception as exc:
                self.fail(self.stage, str(exc), 'ProbeSetupError')
            if not self.finished:
                try:
                    self.loop.exec()
                except Exception as exc:
                    # A Qt-level error while waiting is a probe failure, never
                    # an exception that escapes into the application's global
                    # excepthook.
                    self.fail(self.stage, str(exc), 'ProbeEventLoopError')
        finally:
            self._cleanup()

    def _models_ready(self, token, models, error):
        try:
            if self.finished:
                return
            self.pending_models = (token, models, error)
            self.stage = 'model_list_apply'
            QTimer.singleShot(0, self._apply_models_when_ready)
        except Exception as exc:
            self.fail(self.stage or 'model_list', str(exc), type(exc).__name__)

    def _apply_models_when_ready(self):
        try:
            self._apply_models_when_ready_impl()
        except Exception as exc:
            self.fail(self.stage or 'model_list', str(exc), type(exc).__name__)

    def _apply_models_when_ready_impl(self):
        if self.finished or self.dialog is None:
            return
        try:
            if self.dialog.model_busy:
                QTimer.singleShot(10, self._apply_models_when_ready)
                return
        except RuntimeError:
            self.fail('model_list', '验收窗口已不可用。', 'RuntimeError')
            return
        token, models, error = self.pending_models or (None, [], '')
        if token != self.dialog.models_generation:
            self.fail('model_list', '模型列表结果属于旧请求，验收不接受。',
                      'StaleModelList')
            return
        self.report['model_fetch_request_count'] = self.fetch_calls
        if error:
            self.fail('model_list', str(error), 'ModelListError')
            return
        if not isinstance(models, list) or not models \
                or not all(isinstance(name, str) and name.strip() for name in models):
            self.fail('model_list', '模型列表为空或格式异常。', 'ModelListError')
            return
        normalized = [name.strip().casefold() for name in models]
        allowed = {name.casefold() for name in GENAI_ALLOWED_MODELS}
        if len(normalized) != len(set(normalized)):
            self.fail('model_list', '模型列表包含重复项，验收不接受。',
                      'ModelListError')
            return
        if set(normalized) != allowed:
            self.fail(
                'model_list',
                '模型列表不是恰好两个已核验国内开源模型：' + '、'.join(models),
                'ModelListPolicyError',
            )
            return
        available = []
        try:
            available = [name.strip().casefold()
                         for name in self.dialog.available_models]
            if (self.dialog.model.text().strip().casefold()
                    != GENAI_DEFAULT_MODEL.casefold()):
                raise ProbeFailure('获取模型后默认 DeepSeek 模型被切换。')
            if self.dialog.current_provider() != 'genai':
                raise ProbeFailure('获取模型后 GenAI 预设被切换。')
            if (self.dialog.endpoint.text().strip().rstrip('/')
                    != DEFAULT_GENAI_ENDPOINT):
                raise ProbeFailure('获取模型后本机接口被改写。')
        except Exception as exc:
            self.fail('model_list', str(exc), 'DefaultChanged')
            return
        if available != normalized:
            self.fail('model_list', '窗口中的模型列表与已核验列表不一致。',
                      'ModelListPolicyError')
            return
        self.report['model_list'] = list(models)
        self.report['model_list_only_allowed_domestic'] = True

        self.stage = 'fixed_message_send'
        try:
            self.dialog.input.setPlainText(SYNTHETIC_PROMPT)
        except RuntimeError as exc:
            self.fail('fixed_message_send', str(exc), 'RuntimeError')
            return
        self.report['interaction'][
            'send_button_clicked_programmatically'] = True
        self.report['interaction']['programmatic_qt_click_count'] = 2
        self.send_clicked = True
        try:
            self.dialog.send.click()
            if not self.dialog.busy:
                raise ProbeFailure(
                    '发送按钮没有进入请求状态：' + _safe_status(self.dialog))
        except Exception as exc:
            self.fail('fixed_message_send', str(exc), 'SendNotStarted')
            return
        self.stage = 'model_reply'

    def _reply_ready(self, generation, reply, error):
        try:
            if self.finished:
                return
            self.pending_completion = (generation, reply, error)
            self.stage = 'reply_apply'
            QTimer.singleShot(0, self._apply_reply_when_ready)
        except Exception as exc:
            self.fail(self.stage or 'reply_apply', str(exc), type(exc).__name__)

    def _apply_reply_when_ready(self):
        try:
            self._apply_reply_when_ready_impl()
        except Exception as exc:
            self.fail(self.stage or 'reply_apply', str(exc), type(exc).__name__)

    def _apply_reply_when_ready_impl(self):
        if self.finished or self.dialog is None:
            return
        try:
            if self.dialog.busy:
                QTimer.singleShot(10, self._apply_reply_when_ready)
                return
        except RuntimeError:
            self.fail('reply_apply', '验收窗口已不可用。', 'RuntimeError')
            return
        generation, reply, error = self.pending_completion or (None, '', '')
        if generation != self.dialog.generation:
            self.fail('reply_apply', '回复属于已被放弃的请求，验收不接受。',
                      'StaleReply')
            return
        if error:
            self.fail('reply_apply', str(error), 'ReplyError')
            return
        if not isinstance(reply, str) or not reply.strip():
            self.fail('reply_apply', '真实服务返回了空回复。', 'EmptyReply')
            return
        self._validate_success(reply)

    def _validate_success(self, reply):
        dialog = self.dialog
        self.stage = 'verification'
        try:
            if len(self.sent) != 1:
                raise ProbeFailure('实际发送请求次数不是一次。')
            sent = self.sent[0]
            if sent['endpoint'].strip().rstrip('/') != DEFAULT_GENAI_ENDPOINT:
                raise ProbeFailure('实际请求没有发送到固定 GenAI 本机接口。')
            if sent['model'].strip().casefold() != GENAI_DEFAULT_MODEL.casefold():
                raise ProbeFailure('实际请求模型不是 DeepSeek-V4.1-Flash。')
            if sent['last_user_message'] != SYNTHETIC_PROMPT:
                raise ProbeFailure('实际请求没有包含固定合成消息。')
            if sent['api_key_supplied_in_memory'] is not True:
                raise ProbeFailure('验收窗口没有使用内存中的本机访问口令。')
            if len(dialog.session.turns) != 1:
                raise ProbeFailure('保存的对话轮数不是一轮。')
            turn = dialog.session.turns[0]
            if turn.get('user') != SYNTHETIC_PROMPT:
                raise ProbeFailure('保存的合成消息与固定文本不一致。')
            if not isinstance(turn.get('assistant'), str) or not turn['assistant'].strip():
                raise ProbeFailure('保存的模型回复为空。')
            if reply.strip() != turn['assistant']:
                raise ProbeFailure('queued signal 的回复与保存内容不一致。')
            if (dialog.current_provider() != 'genai'
                    or dialog.session.provider != 'genai'):
                raise ProbeFailure('真实发送后 GenAI 预设被切换。')
            if (dialog.endpoint.text().strip().rstrip('/')
                    != DEFAULT_GENAI_ENDPOINT):
                raise ProbeFailure('真实发送后 GenAI 本机接口被改写。')
            if (dialog.model.text().strip().casefold()
                    != GENAI_DEFAULT_MODEL.casefold()):
                raise ProbeFailure('真实发送后默认 DeepSeek 模型被切换。')
            if (dialog.session.model.strip().casefold()
                    != GENAI_DEFAULT_MODEL.casefold()):
                raise ProbeFailure('实际发送的模型不是 DeepSeek-V4.1-Flash。')

            conversation_path = Path(dialog.session.path)
            if conversation_path.resolve().parent != self.artifact_dir.resolve():
                raise ProbeFailure('对话没有保存在独立验收目录中。')
            raw = conversation_path.read_text(encoding='utf-8')
            if GENAI_DEFAULT_API_KEY in raw:
                raise ProbeFailure('对话文件中出现了本机访问口令。')
            if 'authorization' in raw.casefold():
                raise ProbeFailure('对话文件中出现了 Authorization 字段。')
            reloaded = Conversation(conversation_path)
            if reloaded.turns != dialog.session.turns:
                raise ProbeFailure('重新读取的对话内容不一致。')
            if reloaded.provider != 'genai' or reloaded.endpoint != DEFAULT_GENAI_ENDPOINT:
                raise ProbeFailure('重新读取的预设或接口不一致。')
            if (reloaded.model.strip().casefold()
                    != GENAI_DEFAULT_MODEL.casefold()):
                raise ProbeFailure('重新读取的模型不是 DeepSeek-V4.1-Flash。')
            if not dialog.send.isEnabled() or dialog.input.toPlainText() != '':
                raise ProbeFailure('回复完成后发送按钮或输入框状态不正确。')
            if any(call['force'] is not True for call in self.controller.say_calls):
                raise ProbeFailure('Controller stub 的 say 记录格式异常。')
        except ProbeFailure as exc:
            self.fail('verification', str(exc), 'VerificationError')
            return
        except Exception as exc:
            self.fail('verification', str(exc), type(exc).__name__)
            return

        self.report.update({
            'ok': True,
            'status': 'ok',
            'technical_roundtrip_verified': True,
            'physical_gui_verified': False,
            'model_list': list(self.report['model_list']),
            'model_list_only_allowed_domestic': True,
            'default_preset_preserved': True,
            'default_model_preserved': True,
            'model_fetch_request_count': self.fetch_calls,
            'reply_request_count': len(self.sent),
            'sent_model': sent['model'],
            'reply_nonempty': True,
            'conversation_path': str(conversation_path),
            'saved_in_isolated_artifact_dir': True,
            'reloaded_consistent': True,
            'api_key_in_report': False,
            'api_key_in_conversation_file': False,
            'authorization_header_recorded': False,
            'controller_say_records': [
                {'text': _redact(call['text'], dialog.api_key.text()),
                 'force': call['force']}
                for call in self.controller.say_calls
            ],
            'controller_say_only_recorded': True,
            'audio_playback_started': False,
            'turn': {
                'user': SYNTHETIC_PROMPT,
                'assistant': _redact(turn['assistant'], dialog.api_key.text()),
            },
            'error': None,
            'dialog_status': _safe_status(dialog),
        })
        # Only claim success after the report is complete; if building it
        # failed, the guard around this call records a real failure instead.
        self.finished = True
        self.status = 'ok'
        self.stage = 'done'
        self._stop_and_quit()

    def _detach_dialog(self, dialog):
        """Detach every probe/dialog callback before the dialog is destroyed."""
        for signal, slot in (
                (dialog.models_ready, self._models_ready),
                (dialog.completed, self._reply_ready)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        # Retract the dialog's own in-flight state first, so closing it is not
        # blocked by a request that will never be consumed.
        for name in ('abandon_pending_reply', 'invalidate_models'):
            try:
                getattr(dialog, name)()
            except (RuntimeError, TypeError, AttributeError):
                pass
        # Disconnect the dialog's internal queued slots as well.  A worker
        # thread that returns after the total timeout then cannot re-enter a
        # closing window; its emit degrades to a caught RuntimeError.
        for signal in (dialog.models_ready, dialog.completed):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        try:
            dialog.close()
        except (RuntimeError, TypeError):
            pass

    def _destroy_dialog(self, dialog):
        """Delete the probe-only dialog on the main thread, deterministically.

        The real dialog never has a parent and is never shown.  Leaving it to
        the cyclic garbage collector risks destroying a QWidget from a worker
        thread (the late reply closure holds the dialog) or after the
        QApplication is gone.  Deleting it here, while QApplication is alive,
        keeps teardown on the GUI thread; any worker that emits afterwards
        hits PySide's catchable ``RuntimeError: Internal C++ object already
        deleted``, which the dialog's worker already ignores.
        """
        try:
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete)
        except (RuntimeError, TypeError, AttributeError):
            pass

    def _cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self._stop_and_quit()
        dialog = self.dialog
        # Read the key and status while the dialog is still guaranteed alive;
        # afterwards the report must never touch the destroyed C++ object.
        api_key = self._safe_api_key()
        dialog_status = _safe_status(dialog) if dialog is not None else ''
        teardown_error = None
        try:
            if dialog is not None:
                try:
                    self._detach_dialog(dialog)
                finally:
                    self.dialog = None
                    self._destroy_dialog(dialog)
        except Exception as exc:
            # Teardown must never prevent the sanitized report from being
            # written, and a failure here is reported rather than raised.
            teardown_error = exc
        finally:
            # Never leave the live module-level request functions pointing at
            # the probe's recording wrappers, even if teardown failed.
            self._restore_recording()
        if teardown_error is not None and self.status == 'ok':
            self.status = 'failed'
            self.error_type = 'CleanupError'
            self.error_message = _redact(teardown_error, api_key)
        report = self.report
        report['status'] = self.status
        report['ok'] = self.status == 'ok'
        report['model_fetch_request_count'] = self.fetch_calls
        report['reply_request_count'] = len(self.sent)
        report['interaction']['programmatic_qt_click_count'] = (
            2 if self.send_clicked else
            (1 if report['interaction'][
                'model_list_button_clicked_programmatically'] else 0)
        )
        report['dialog_status'] = dialog_status
        report['controller_say_records'] = [
            {'text': _redact(call['text'], api_key),
             'force': call['force']}
            for call in self.controller.say_calls
        ]
        if self.status != 'ok':
            report['error'] = {
                'type': self.error_type or 'ProbeFailure',
                'message': self.error_message or '验收未完成。',
                'stage': self.stage,
            }
            if self.sent:
                report['sent_model'] = _redact(self.sent[0].get('model', ''))
            try:
                conversation_exists = self.conversation_path.exists()
            except OSError:
                conversation_exists = False
            report['conversation_path'] = (
                str(self.conversation_path) if conversation_exists else None)
        report['elapsed_seconds'] = round(
            time.monotonic() - getattr(
                self, 'started_monotonic', time.monotonic()), 3)


def _emit_json(payload) -> None:
    """Best-effort structured output for a target that could not be written."""
    stream = getattr(sys, 'stderr', None) or getattr(sys, 'stdout', None)
    if stream is None:
        return
    try:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
    except (OSError, ValueError, AttributeError):
        pass


def run_frozen_genai_probe(app, report_path, *,
                           total_timeout=DEFAULT_TOTAL_TIMEOUT) -> int:
    """Run one explicitly requested live UI check and write one new report.

    The caller must have constructed ``QApplication``.  No existing report or
    artifact directory is ever overwritten.  The returned exit code is 0 only
    when every technical and persistence check passed.
    """
    started = time.monotonic()
    try:
        report_path = validate_report_path(report_path)
    except ProbeRefused as exc:
        _emit_json({
            'kind': REPORT_KIND,
            'ok': False,
            'status': 'refused',
            'error': {'type': type(exc).__name__, 'message': str(exc)},
        })
        return EXIT_REFUSED

    try:
        artifact_dir = prepare_artifact_dir(report_path)
    except ProbeRefused as exc:
        _emit_json({
            'kind': REPORT_KIND,
            'ok': False,
            'status': 'refused',
            'report_path': str(report_path),
            'error': {'type': type(exc).__name__, 'message': str(exc)},
        })
        return EXIT_REFUSED

    report = _base_report(report_path, artifact_dir, app)
    runner = _ProbeRun(
        app, report, report_path, artifact_dir, total_timeout,
        conversation_ui.fetch_models_request, conversation_ui.request_reply,
    )
    runner.started_monotonic = started
    try:
        runner.start()
    except Exception as exc:
        # The runner handles expected failures itself; keep a last-resort
        # sanitized report if a Qt-level error escaped.
        runner.fail('internal', str(exc), type(exc).__name__)
        runner._restore_recording()
        runner.report['status'] = 'failed'
        runner.report['ok'] = False
        runner.report['error'] = {
            'type': type(exc).__name__,
            'message': _redact(exc, ''),
            'stage': 'internal',
        }
        runner.report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report = runner.report
    else:
        report = runner.report

    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    try:
        _write_new_json(report_path, report)
    except ProbeRefused as exc:
        _emit_json({
            'kind': REPORT_KIND,
            'ok': False,
            'status': 'refused',
            'report_path': str(report_path),
            'artifact_dir': str(artifact_dir),
            'error': {'type': type(exc).__name__, 'message': str(exc)},
        })
        return EXIT_REFUSED

    if report.get('status') == 'ok':
        return EXIT_OK
    if report.get('status') == 'timeout':
        return EXIT_TIMEOUT
    return EXIT_FAILED
