"""Multimodal chat dialog for the Si desktop pet.

Supports dragging/pasting images (up to 4) and an optional voice reply.
All network calls and voice synthesis run in worker threads; the Qt main
thread is never blocked.
"""
import os
import tempfile
import threading
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Signal, Qt, QEvent, QBuffer, QIODevice
from PySide6.QtGui import QFont, QClipboard, QImage, QKeyEvent
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QTextBrowser, QPushButton, QComboBox, QCompleter,
    QFormLayout, QApplication, QWidget)

from .conversation import (CUSTOM_DEFAULT_ENDPOINT, ConversationError, DEEPSEEK_DEFAULT_ENDPOINT,
    DEEPSEEK_DEFAULT_MODEL, DEFAULT_GENAI_ENDPOINT,
    DEFAULT_LOCAL_ENDPOINT, GENAI_DEFAULT_API_KEY, GENAI_DEFAULT_MODEL,
    GENAI_NO_MATCH_MESSAGE, LAN_DEFAULT_ENDPOINT, MODELS_EMPTY_MESSAGE,
    PROVIDERS, REMOTE_DEFAULT_ENDPOINT,
    endpoint_for_provider, fetch_models as fetch_models_request, filter_genai_models,
    local_endpoint, looks_like_gpt_model, request_reply,
    resolve_genai_model, validate_model_for_send)
from . import chat_backend, native
from .chat_backend import ChatSession, ChatError
from .theme import bind_theme, dialog_stylesheet
from .chat_widgets import build_config_drawer, render_transcript_html
from .voice import scaled_wav

try:
    import winsound
except ImportError:
    winsound = None

_MAX_IMAGES = 4
_ALLOWED_IMAGE_EXTS = frozenset(('.jpg', '.jpeg', '.png', '.gif', '.webp'))
DEFAULT_OLLAMA_ENDPOINT = 'http://127.0.0.1:11434/v1'
DEFAULT_OLLAMA_MODEL = 'si-qwen2.5-3b-lang-lora:latest'
DEFAULT_OLLAMA_VISION_MODEL = 'si-qwen2.5vl-3b-lora:latest'


def resolve_local_chat_model(provider: str, endpoint: str, model: str, has_images: bool) -> str:
    """Route Ollama image turns (local port 11434, LAN, or remote tunnel) to the vision LoRA."""
    if not has_images or provider not in ('local', 'lan', 'remote'):
        return model
    if provider != 'remote':
        try:
            port = urllib.parse.urlsplit(endpoint).port
        except ValueError:
            return model
        if port != 11434:
            return model
    if model.strip().lower() in ('si-qwen2.5-3b-lang-lora:latest', 'si-qwen2.5-3b-lang-lora'):
        return DEFAULT_OLLAMA_VISION_MODEL
    return model


class MultimodalDialog(QDialog):
    """Multimodal chat dialog: text + images + optional voice reply.

    Public API
    ----------
    render() -> None
        Re-render transcript from current session history (plain text only).
    clear_turns() -> None
        Clear conversation history on disk and in memory (slot).
    clear_images() -> None
        Clear any pending attached images (slot).
    fetch_models() -> None
        Query loopback service for available models in background (slot).
    submit() -> None
        Validate inputs and start worker thread for model reply and voice (slot).
    voice_mode -> bool
        Property getting/setting whether voice reply is requested.
    """

    # (generation, reply_text, error_text, voice_bytes_or_None)
    completed = Signal(int, str, str, object)
    # (token, models_list, error_text)
    models_ready = Signal(int, object, str)

    def __init__(self, ctrl, path, parent=None):
        super().__init__(parent)
        self.setFont(QFont('Microsoft YaHei UI', 10))
        self.ctrl = ctrl

        had_saved_config = Path(path).exists()
        self.session = chat_backend.ChatSession(path)
        if not had_saved_config:
            self.session.provider = 'local'
            self.session.endpoint = DEFAULT_OLLAMA_ENDPOINT
            self.session.model = DEFAULT_OLLAMA_MODEL

        self.provider = (
            self.session.provider if self.session.provider in PROVIDERS else 'local'
        )

        self.migration_notice = ''
        if not had_saved_config and self.provider == 'genai':
            self.session.model = GENAI_DEFAULT_MODEL
        elif self.provider == 'genai':
            try:
                resolve_genai_model(self.session.model)
            except ConversationError:
                self.session.model = GENAI_DEFAULT_MODEL
                self.migration_notice = (
                    '旧保存的 GenAI 模型不在已核验的国内开源型号中，窗口已改用 '
                    'DeepSeek-V4.1-Flash；原文件和历史记录未修改，也不会自动联网。'
                )
            else:
                self.session.model = self.session.model.strip()
        elif looks_like_gpt_model(self.session.model):
            self.session.model = ''
            self.migration_notice = (
                '旧保存的模型带有 GPT/OpenAI 命名，已从窗口清空；请选择国内开源模型，'
                '或填写自己部署的模型名称。原文件未修改。'
            )

        # Generation guard state for chat requests
        self.busy = False
        self.generation = 0
        self.active_generation = None
        self.pending_text = None

        # Model query state
        self.models_generation = 0
        self.model_busy = False
        self.models_request = None
        self.available_models = []

        # Pending image attachments
        self.pending_images: list[bytes] = []

        # Window configuration
        self.setWindowTitle('和祀聊聊 · 多模态聊聊')
        self.resize(660, 760)
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)

        hint = QLabel(
            '支持拖入或粘贴图片（最多 4 张）。支持本机模型（Ollama 可配视觉 LoRA）、DeepSeek 官方 API（纯文字）'
            '及自定义模型接口（如学校 API 网关）；对话记录只保存在本机，API Key 仅留内存不写盘。'
        )
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Service configuration form inside a collapsible drawer so messages dominate
        form = QFormLayout()
        self.service = QComboBox()
        self.service.addItem('本机模型（Ollama / 本地端口）', 'local')
        self.service.addItem('上科大 GenAI 反代', 'genai')
        self.service.addItem('DeepSeek 官方 API', 'deepseek')
        self.service.addItem('自定义模型接口（学校 API / 第三方）', 'custom')
        self.service.addItem('远程显卡主机（跨网 / 穿透 / 组网）', 'remote')
        self.service.setCurrentIndex({'local': 0, 'genai': 1, 'deepseek': 2, 'custom': 3, 'remote': 4, 'lan': 4}.get(self.provider, 0))

        self.endpoint = QLineEdit(self.session.endpoint)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(
            '请填写 DeepSeek API Key（sk-...）；只保留在内存' if self.provider == 'deepseek'
            else '普通本机可留空；只保留在内存'
        )
        self.api_key.setText(GENAI_DEFAULT_API_KEY if self.provider == 'genai' else '')

        self.model = QLineEdit(self.session.model)
        self.model.setPlaceholderText('模型名称；如 deepseek-chat 或本机已加载模型')
        self.model_completer = QCompleter([], self.model)
        self.model.setCompleter(self.model_completer)

        self.get_models = QPushButton('获取模型')
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.get_models)

        self.profile = QTextEdit()
        self.profile.setMaximumHeight(85)
        self.profile.setPlaceholderText(
            '可选：希望怎样称呼你、喜欢的话题、希望怎样陪伴。不必填写私人信息。'
        )
        self.profile.setPlainText(self.session.profile)

        self.endpoint_label = QLabel('接口地址')
        self.api_key_label = QLabel('API Key / 口令')
        form.addRow('连接方式', self.service)
        form.addRow(self.endpoint_label, self.endpoint)
        form.addRow(self.api_key_label, self.api_key)
        form.addRow('模型', model_row)
        form.addRow('管理员偏好', self.profile)
        drawer_box, self._drawer_summary, self._drawer_toggle, self._drawer = build_config_drawer(form, expanded=not had_saved_config)
        layout.addWidget(drawer_box)
        self._update_provider_labels(self.provider)

        # Transcript display area
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        layout.addWidget(self.transcript, 1)

        # Attachment status & thumbnail strip
        attach_row = QHBoxLayout()
        self.attach_label = QLabel('待发图片：0 张')
        self.clear_images_btn = QPushButton('清除图片')
        self.clear_images_btn.setObjectName('chip')
        self.clear_images_btn.setAutoDefault(False)
        self.clear_images_btn.setDefault(False)
        attach_row.addWidget(self.attach_label)
        attach_row.addStretch()
        attach_row.addWidget(self.clear_images_btn)
        layout.addLayout(attach_row)

        self.thumb_strip = QHBoxLayout()
        self.thumb_strip.setSpacing(6)
        self.thumb_host = QWidget()
        self.thumb_host.setLayout(self.thumb_strip)
        self.thumb_host.setVisible(False)
        layout.addWidget(self.thumb_host)

        # Input box
        self.input = QTextEdit()
        self.input.setMaximumHeight(100)
        self.input.setPlaceholderText('想说的话……（Ctrl+V 可粘贴图片）')
        layout.addWidget(self.input)

        # Status row
        self.status = QLabel(
            self.migration_notice
            or '不会自动获取模型或发送对话。口令只留在内存，不会读取其他软件的模型或口令。'
        )
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # Action row
        row = QHBoxLayout()
        self.clear_btn = QPushButton('清除对话')
        self.clear_btn.setAutoDefault(False)
        self.clear_btn.setDefault(False)

        self.voice_btn = QPushButton('语音同步')
        self.voice_btn.setCheckable(True)
        self.voice_btn.setChecked(False)
        self.voice_btn.setAutoDefault(False)
        self.voice_btn.setDefault(False)
        self.voice_btn.setToolTip('接入耳机且未运行本地游戏时自动开启语音+文本同步输出；也可手动切换。')
        self._voice_manual_override: bool | None = None
        self.voice_btn.clicked.connect(self._on_voice_btn_clicked)
        self._sync_auto_voice_button()

        self.send = QPushButton('发送')
        self.send.setObjectName('primary')

        row.addWidget(self.clear_btn)
        row.addWidget(self.voice_btn)
        row.addStretch()
        row.addWidget(self.send)
        layout.addLayout(row)

        self.model.textChanged.connect(self._update_drawer_summary)
        self._update_drawer_summary()
        bind_theme(self, self._apply_theme)

        # Signal connections
        self.completed.connect(self.finished_reply, Qt.ConnectionType.QueuedConnection)
        self.models_ready.connect(self.finished_models, Qt.ConnectionType.QueuedConnection)

        self.service.currentIndexChanged.connect(self.preset_changed)
        self.get_models.clicked.connect(self.fetch_models)
        self.endpoint.textEdited.connect(self.connection_edited)
        self.api_key.textEdited.connect(self.connection_edited)
        self.send.clicked.connect(self.submit)
        self.clear_btn.clicked.connect(self.clear_turns)
        self.clear_images_btn.clicked.connect(self.clear_images)

        # Event filter for Ctrl+V image paste
        self.input.installEventFilter(self)

        changed_sig = getattr(self.ctrl, 'changed', None)
        if changed_sig is not None and hasattr(changed_sig, 'connect'):
            try:
                changed_sig.connect(self.sync_from_ledger)
            except Exception:
                pass
        self.sync_from_ledger()
        self.render()

    # ---------------------------------------------------------------- properties

    @property
    def voice_mode(self) -> bool:
        """Whether the voice synthesis toggle is checked."""
        return self.voice_btn.isChecked()

    @voice_mode.setter
    def voice_mode(self, enabled: bool):
        self._voice_manual_override = bool(enabled)
        self.voice_btn.setChecked(bool(enabled))

    def _on_voice_btn_clicked(self, checked: bool):
        self._voice_manual_override = bool(checked)

    def _sync_auto_voice_button(self) -> None:
        """Sync voice toggle with headphone + local-game policy unless manually overridden."""
        store = getattr(self.ctrl, 'store', None)
        if store is None:
            return
        allowed, reason = native.voice_output_allowed(store)
        self.voice_btn.setToolTip(f'{reason}（点击可手动切换当前窗口语音）')
        if self._voice_manual_override is None:
            self.voice_btn.blockSignals(True)
            self.voice_btn.setChecked(allowed)
            self.voice_btn.blockSignals(False)

    def showEvent(self, event):
        self._sync_auto_voice_button()
        super().showEvent(event)

    def _voice_profile_id(self) -> str:
        """Return configured VoiceStudio profile ID from ctrl.store, or default."""
        store = getattr(self.ctrl, 'store', None)
        if store is not None:
            try:
                val = store['voice_profile_id']
                if isinstance(val, str) and val.strip():
                    return val.strip()
            except Exception:
                pass
        return chat_backend.VOICE_PROFILE_DEFAULT

    # ---------------------------------------------------------------- render

    def _apply_theme(self):
        self.setStyleSheet(dialog_stylesheet())
        if hasattr(self, 'transcript') and hasattr(self, 'session'):
            self.render()

    def _update_drawer_summary(self, _text=''):
        if not hasattr(self, '_drawer_summary'):
            return
        svc = self.service.currentText()
        mdl = self.model.text().strip() or '未填模型'
        self._drawer_summary.setText(f'当前连接：{svc} · {mdl}')

    def render(self):
        """Re-render transcript as plain-text-safe HTML bubbles with portrait."""
        html_doc = render_transcript_html(self.session.turns, portrait_mood='happy')
        if html_doc:
            self.transcript.setHtml(html_doc)
        else:
            self.transcript.setPlainText('')
        self.transcript.verticalScrollBar().setValue(
            self.transcript.verticalScrollBar().maximum()
        )

    def sync_from_ledger(self) -> int:
        """Pull any new cross-device chat_turn events from ctrl.store.sync_ledger."""
        store = getattr(self.ctrl, 'store', None)
        ledger = getattr(store, 'sync_ledger', None) if store is not None else None
        if ledger is None:
            return 0
        try:
            added = self.session.sync_with_ledger(ledger)
            if added > 0:
                self.render()
            return added
        except Exception:
            return 0

    # -------------------------------------------------------- image attachments

    def _update_attach_label(self):
        self.attach_label.setText(f'待发图片：{len(self.pending_images)} 张')
        if hasattr(self, 'thumb_strip') and hasattr(self, 'thumb_host'):
            while self.thumb_strip.count():
                item = self.thumb_strip.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            for raw in self.pending_images:
                img = QImage.fromData(raw)
                lbl = QLabel()
                lbl.setFixedSize(48, 48)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if not img.isNull():
                    from PySide6.QtGui import QPixmap
                    pix = QPixmap.fromImage(img).scaled(46, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    lbl.setPixmap(pix)
                else:
                    lbl.setText('图')
                self.thumb_strip.addWidget(lbl)
            self.thumb_strip.addStretch()
            self.thumb_host.setVisible(bool(self.pending_images))

    def _add_image_bytes(self, data: bytes) -> bool:
        if len(self.pending_images) >= _MAX_IMAGES:
            try:
                self.status.setText(f'最多只能附加 {_MAX_IMAGES} 张图片，请先清除部分图片。')
            except RuntimeError:
                pass
            return False
        self.pending_images.append(data)
        self._update_attach_label()
        return True

    def clear_images(self):
        """Clear all pending images (slot)."""
        self.pending_images.clear()
        self._update_attach_label()
        try:
            self.status.setText('图片已清除。')
        except RuntimeError:
            pass

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() and any(
            Path(u.toLocalFile()).suffix.lower() in _ALLOWED_IMAGE_EXTS
            for u in mime.urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() and any(
            Path(u.toLocalFile()).suffix.lower() in _ALLOWED_IMAGE_EXTS
            for u in mime.urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        added = 0
        for url in mime.urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() not in _ALLOWED_IMAGE_EXTS:
                continue
            if len(self.pending_images) >= _MAX_IMAGES:
                try:
                    self.status.setText(
                        f'已达到 {_MAX_IMAGES} 张图片上限，部分图片未添加。'
                    )
                except RuntimeError:
                    pass
                break
            try:
                data = path.read_bytes()
            except OSError:
                try:
                    self.status.setText(f'无法读取文件：{path.name}')
                except RuntimeError:
                    pass
                continue
            if self._add_image_bytes(data):
                added += 1
        if added:
            try:
                self.status.setText(f'已添加 {added} 张图片，共 {len(self.pending_images)} 张待发。')
            except RuntimeError:
                pass
        event.acceptProposedAction()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent):
                if (event.key() == Qt.Key.Key_V
                        and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    clipboard = QApplication.clipboard()
                    img = clipboard.image()
                    if not img.isNull():
                        buf = QBuffer()
                        buf.open(QIODevice.OpenMode.WriteOnly)
                        img.save(buf, 'PNG')
                        buf.close()
                        data = bytes(buf.data())
                        if data and self._add_image_bytes(data):
                            try:
                                self.status.setText(
                                    f'已粘贴图片，共 {len(self.pending_images)} 张待发。'
                                )
                            except RuntimeError:
                                pass
                            return True  # Handled; do not insert raw characters
        return super().eventFilter(obj, event)

    # --------------------------------------------------------- provider/models

    def current_provider(self):
        provider = self.service.currentData()
        return provider if provider in PROVIDERS else 'local'

    def clear_model_completer(self):
        old = self.model_completer
        empty = QCompleter([], self.model)
        self.model.setCompleter(empty)
        self.model_completer = empty
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass

    def invalidate_models(self):
        """Abandon any pending model list without touching text."""
        self.models_generation += 1
        self.model_busy = False
        self.models_request = None
        self.available_models = []
        self.get_models.setEnabled(True)
        try:
            self.clear_model_completer()
        except (RuntimeError, TypeError):
            pass

    def _trigger_pet_pose(self, action: str):
        perform = getattr(self.ctrl, 'perform', None)
        if callable(perform) and not getattr(self.ctrl, 'focus_end', None):
            try:
                perform(action, announce=False)
            except Exception:
                pass

    def _update_provider_labels(self, provider):
        self._update_drawer_summary()
        if provider == 'deepseek':
            self.endpoint_label.setText('DeepSeek 接口')
            self.endpoint.setPlaceholderText('https://api.deepseek.com/v1')
            self.api_key_label.setText('DeepSeek API Key')
            self.api_key.setPlaceholderText('必填：DeepSeek API Key（sk-...），仅保留在内存')
        elif provider == 'custom':
            self.endpoint_label.setText('自定义接口地址')
            self.endpoint.setPlaceholderText('填写学校或第三方 OpenAI 兼容地址，如 https://.../v1')
            self.api_key_label.setText('API Key / 访问口令')
            self.api_key.setPlaceholderText('填写学校平台提供的 API Key / 口令（仅留内存；无需可留空）')
        elif provider in ('lan', 'remote'):
            self.endpoint_label.setText('远程主机地址')
            self.endpoint.setPlaceholderText('例如 https://xxx.trycloudflare.com/v1')
            self.api_key_label.setText('访问口令 / Key')
            self.api_key.setPlaceholderText('无鉴权可留空；若挂了反向代理鉴权请填写，仅留内存')
        elif provider == 'genai':
            self.endpoint_label.setText('本机接口')
            self.endpoint.setPlaceholderText('http://127.0.0.1:8000/v1')
            self.api_key_label.setText('反代访问口令')
            self.api_key.setPlaceholderText('默认 sk-shanghaitech-genai；只保留在内存')
        else:
            self.endpoint_label.setText('本机接口')
            self.endpoint.setPlaceholderText('http://127.0.0.1:11434/v1 或 http://127.0.0.1:1234/v1')
            self.api_key_label.setText('访问口令 / Key')
            self.api_key.setPlaceholderText('普通本机可留空；只保留在内存')

    def preset_changed(self, _index):
        provider = self.current_provider()
        self.provider = provider
        self.session.provider = provider
        self.migration_notice = ''
        pending = self.model_busy
        self.invalidate_models()
        self._update_provider_labels(provider)
        if provider == 'genai':
            endpoint = DEFAULT_GENAI_ENDPOINT
        elif provider == 'deepseek':
            endpoint = DEEPSEEK_DEFAULT_ENDPOINT
        elif provider == 'custom':
            endpoint = CUSTOM_DEFAULT_ENDPOINT
        elif provider == 'lan':
            endpoint = LAN_DEFAULT_ENDPOINT
        elif provider == 'remote':
            endpoint = REMOTE_DEFAULT_ENDPOINT
        else:
            endpoint = DEFAULT_OLLAMA_ENDPOINT
        self.endpoint.setText(endpoint)
        self.api_key.setText(GENAI_DEFAULT_API_KEY if provider == 'genai' else '')
        if provider == 'genai':
            self.model.setText(GENAI_DEFAULT_MODEL)
            self.session.model = GENAI_DEFAULT_MODEL
        elif provider == 'deepseek':
            self.model.setText(DEEPSEEK_DEFAULT_MODEL)
            self.session.model = DEEPSEEK_DEFAULT_MODEL
        elif provider == 'custom':
            self.model.clear()
            self.session.model = ''
        else:
            self.model.setText(DEFAULT_OLLAMA_MODEL)
            self.session.model = DEFAULT_OLLAMA_MODEL
        self.session.endpoint = endpoint
        if pending:
            self.status.setText('已切换服务，迟到的模型列表不会更新窗口。需要时请重新获取模型。')
        elif provider == 'genai':
            self.status.setText(
                '已切换到上科大 GenAI 反代，默认模型为 DeepSeek-V4.1-Flash。'
                '需要时点击“获取模型”，查看已配置的国内开源模型。'
            )
        elif provider == 'deepseek':
            self.status.setText(
                '已切换到 DeepSeek 官方 API（deepseek-chat）。请填写你的 DeepSeek API Key；'
                '仅支持纯文字（发图请切回本机或远程显卡模型）。'
            )
        elif provider == 'custom':
            self.status.setText(
                '已切换到自定义模型接口（学校 API / 第三方 OpenAI 兼容网关）。'
                '请填写接口地址、API Key（如有）与模型名；若所填模型支持多模态，可直接发图。'
            )
        elif provider in ('lan', 'remote'):
            self.status.setText(
                '已切换到远程显卡主机（文字 si-qwen2.5-3b-lang-lora，带图自动切视觉 LoRA）。'
                '支持不同局域网穿透 HTTPS 域名、异地组网（100.x.x.x）或跨网段地址。'
            )
        else:
            self.status.setText(
                '已切换到普通本机模型（默认 Ollama 11434 · 文字 si-qwen2.5-3b-lang-lora，'
                '带图自动切换视觉 LoRA）。可直接发送或点击“获取模型”。'
            )

    def connection_edited(self, _text=''):
        if self.model_busy or self.available_models:
            pending = self.model_busy
            self.invalidate_models()
            if pending:
                self.status.setText('连接设置已修改，已忽略上一次模型查询。')

    def fetch_models(self):
        """Start a background query for the configured service's model list."""
        if self.model_busy:
            return
        provider = self.current_provider()
        try:
            endpoint = endpoint_for_provider(provider, self.endpoint.text())
            api_key = self.api_key.text()
            if provider == 'deepseek' and not api_key.strip():
                raise ConversationError('使用 DeepSeek 官方 API 时，请先填写你的 DeepSeek API Key（sk-...）。')
        except (ConversationError, ChatError) as exc:
            self.status.setText(str(exc))
            return
        token = self.models_generation + 1
        self.models_generation = token
        self.model_busy = True
        self.provider = provider
        self.models_request = (provider, endpoint)
        self.available_models = []
        try:
            self.clear_model_completer()
        except (RuntimeError, TypeError):
            pass
        self.get_models.setEnabled(False)
        self.status.setText(
            '正在向 DeepSeek 官方接口获取模型列表；不会发送对话。'
            if provider == 'deepseek'
            else ('正在向自定义接口获取模型列表；不会发送对话。'
                  if provider == 'custom'
                  else ('正在向远程显卡主机获取模型列表；不会发送对话。'
                        if provider in ('lan', 'remote') else '正在向本机服务获取模型列表；不会发送对话。'))
        )

        def work():
            try:
                models = (
                    fetch_models_request(endpoint, api_key, provider=provider)
                    if provider in ('deepseek', 'custom', 'lan', 'remote')
                    else fetch_models_request(endpoint, api_key)
                )
                error = ''
                if provider == 'genai':
                    models = filter_genai_models(models)
                    if not models:
                        error = GENAI_NO_MATCH_MESSAGE
            except ConversationError as exc:
                models, error = [], str(exc)
                if provider == 'genai' and error == MODELS_EMPTY_MESSAGE:
                    error = GENAI_NO_MATCH_MESSAGE
            except Exception:
                models, error = [], '获取模型失败，请检查本机服务后重试。'
            try:
                self.models_ready.emit(token, models, error)
            except (RuntimeError, TypeError):
                pass

        try:
            threading.Thread(target=work, daemon=True, name='si-mm-models').start()
        except RuntimeError:
            self.model_busy = False
            self.models_request = None
            self.get_models.setEnabled(True)
            self.status.setText('无法启动模型查询线程，请重试。')

    def finished_models(self, token, models, error):
        try:
            self.apply_models(token, models, error)
        except Exception:
            if token == self.models_generation:
                self.model_busy = False
                self.models_request = None
                try:
                    self.get_models.setEnabled(True)
                except RuntimeError:
                    pass
                try:
                    self.status.setText('获取模型暂时未能完成，请重试。')
                except RuntimeError:
                    pass

    def apply_models(self, token, models, error):
        if token != self.models_generation:
            return
        self.model_busy = False
        self.models_request = None
        self.get_models.setEnabled(True)
        provider = self.current_provider()
        if error:
            self.status.setText(error)
            self._trigger_pet_pose('failed')
            return
        if not isinstance(models, list) or not models:
            self.status.setText(
                GENAI_NO_MATCH_MESSAGE if provider == 'genai'
                else '本机接口没有返回可用的模型名称。'
            )
            return
        if provider == 'genai':
            models = filter_genai_models(models)
            if not models:
                self.available_models = []
                try:
                    self.clear_model_completer()
                except (RuntimeError, TypeError):
                    pass
                self.status.setText(GENAI_NO_MATCH_MESSAGE)
                return
        self.available_models = list(models)
        if provider == 'genai':
            current = self.model.text().strip()
            for name in self.available_models:
                if name.casefold() == current.casefold():
                    if self.model.text() != name:
                        self.model.setText(name)
                    break
        old = self.model_completer
        completer = QCompleter(self.available_models, self.model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.model.setCompleter(completer)
        self.model_completer = completer
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        if provider == 'genai':
            self.status.setText(
                f'已获取 {len(self.available_models)} 个已核验的国内开源模型。'
                '请从模型框选择或手动填写；尚未发送对话。'
            )
        else:
            self.status.setText(
                f'已获取 {len(self.available_models)} 个模型。'
                '请从模型框选择或手动填写；尚未发送对话。'
            )
        if self.isVisible():
            self.model.setFocus(Qt.FocusReason.OtherFocusReason)
            completer.complete()

    # --------------------------------------------------------------- submit

    def submit(self):
        """Validate input/attachments and launch the worker thread (slot)."""
        if self.busy:
            return
        try:
            text = self.input.toPlainText()
            images_snapshot = list(self.pending_images)
            if not text.strip() and not images_snapshot:
                self.status.setText('请输入文字或附加图片后再发送。')
                return
            if text.strip() and len(text) > 2000:
                raise ConversationError('每次请输入 1 至 2000 字。')

            provider = self.current_provider()
            endpoint = endpoint_for_provider(provider, self.endpoint.text())
            if provider == 'deepseek' and images_snapshot:
                raise ConversationError(
                    'DeepSeek 官方 API 当前仅支持纯文字对话；请先点击“清除图片”，或切换回“本机模型”识图。'
                )
            model = validate_model_for_send(
                provider, self.model.text(),
                self.available_models if provider == 'genai' else (),
            )
            if self.model.text() != model:
                self.model.setText(model)
            profile_text = self.profile.toPlainText()
            if len(profile_text) > 2000:
                raise ConversationError('管理员偏好最多 2000 字。')
            api_key = self.api_key.text()
            if provider == 'deepseek' and not api_key.strip():
                raise ConversationError('使用 DeepSeek 官方 API 时，请先填写你的 DeepSeek API Key（sk-...）。')
            self.provider = provider
            self.session.provider = provider
            self.session.endpoint = endpoint
            self.session.model = model
            self.session.profile = profile_text
            self.session.save()
        except (ConversationError, ChatError) as exc:
            self.status.setText(str(exc))
            return
        except Exception:
            self.status.setText('对话暂时未能完成，请重试。')
            return

        self._sync_auto_voice_button()
        store_ref = getattr(self.ctrl, 'store', None)
        extra_cfg = ''
        if store_ref is not None:
            try:
                extra_cfg = store_ref['voice_blocked_games']
            except Exception:
                extra_cfg = ''
        if store_ref is not None and native.running_blocked_game(extra_names=extra_cfg):
            voice_requested = False
        else:
            voice_requested = self.voice_mode
        voice_profile_id = self._voice_profile_id()
        effective_text = text.strip() if text.strip() else '（图片）'

        self.busy = True
        self.send.setEnabled(False)
        self.active_generation = self.generation
        self.pending_text = effective_text
        self.status.setText('正在等祀回应；你可以继续使用桌宠。')
        generation = self.generation

        def work():
            # a. Prepare image attachments
            image_urls: list[str] = []
            for img_bytes in images_snapshot:
                try:
                    url = chat_backend.prepare_image_attachment(img_bytes)
                    image_urls.append(url)
                except (ConversationError, ChatError) as exc:
                    err = str(exc) if str(exc) else '图片处理失败，请检查格式后重试。'
                    try:
                        self.completed.emit(generation, '', err, None)
                    except (RuntimeError, TypeError):
                        pass
                    return
                except Exception:
                    try:
                        self.completed.emit(generation, '', '图片处理失败，请检查格式后重试。', None)
                    except (RuntimeError, TypeError):
                        pass
                    return

            # b. Build messages
            try:
                messages = self.session.messages(effective_text, image_urls)
            except (ConversationError, ChatError) as exc:
                try:
                    self.completed.emit(generation, '', str(exc), None)
                except (RuntimeError, TypeError):
                    pass
                return
            except Exception:
                try:
                    self.completed.emit(generation, '', '对话暂时未能完成，请重试。', None)
                except (RuntimeError, TypeError):
                    pass
                return

            # c. Request reply
            request_model = resolve_local_chat_model(
                provider, endpoint, model, bool(image_urls)
            )
            try:
                reply = (
                    request_reply(endpoint, request_model, messages, api_key, provider=provider)
                    if provider in ('deepseek', 'custom', 'lan', 'remote')
                    else request_reply(endpoint, request_model, messages, api_key)
                )
                error = ''
            except ConversationError as exc:
                reply, error = '', str(exc)
            except Exception:
                reply, error = '', '对话暂时未能完成，请重试。'

            if error:
                try:
                    self.completed.emit(generation, '', error, None)
                except (RuntimeError, TypeError):
                    pass
                return

            # d. Voice synthesis (non-blocking degradation)
            voice_bytes = None
            if voice_requested:
                try:
                    voice_bytes = chat_backend.synthesize_voice(
                        reply, profile_id=voice_profile_id
                    )
                except Exception:
                    voice_bytes = None

            try:
                self.completed.emit(generation, reply, '', voice_bytes)
            except (RuntimeError, TypeError):
                pass

        try:
            threading.Thread(target=work, daemon=True, name='si-mm-chat').start()
        except RuntimeError:
            self.busy = False
            self.active_generation = None
            self.pending_text = None
            self.send.setEnabled(True)
            self.status.setText('无法启动对话线程，请重试。')

    # --------------------------------------------------------- reply handler

    def finished_reply(self, generation, reply, error, voice_bytes):
        """Queued slot: dispatch worker result to UI."""
        try:
            self.apply_reply(generation, reply, error, voice_bytes)
        except Exception:
            if self.active_generation == generation:
                self.active_generation = None
                self.pending_text = None
                self.busy = False
                try:
                    self.send.setEnabled(True)
                except RuntimeError:
                    pass
            try:
                self.status.setText('对话暂时未能完成，请重试。')
            except RuntimeError:
                pass

    def apply_reply(self, generation, reply, error, voice_bytes):
        """Apply reply adhering strictly to generation guard."""
        if generation != self.active_generation:
            return
        effective_text, self.pending_text = self.pending_text, None
        self.active_generation = None
        self.busy = False
        self.send.setEnabled(True)

        if generation != self.generation:
            return

        if error:
            self.status.setText(error)
            self._trigger_pet_pose('failed')
            return
        if effective_text is None:
            return

        attachments = []
        for idx, img_bytes in enumerate(self.pending_images[:3], start=1):
            try:
                thumb = chat_backend.prepare_image_thumbnail(img_bytes)
            except Exception:
                thumb = ''
            attachments.append({
                'kind': 'image',
                'name': f'image-{idx}.jpg',
                'data_url': thumb,
            })
        if voice_bytes:
            attachments.append({
                'kind': 'voice',
                'name': 'reply.wav',
                'data_url': '',
            })

        event_id = ''
        store = getattr(self.ctrl, 'store', None)
        record_fn = getattr(store, 'record_chat_turn', None) if store is not None else None
        if callable(record_fn):
            try:
                ev = record_fn(effective_text, reply, attachments)
                if isinstance(ev, dict):
                    event_id = str(ev.get('event_id', ''))
            except Exception:
                pass

        try:
            self.session.commit(
                effective_text, reply, attachments=attachments, event_id=event_id
            )
        except (ConversationError, ChatError) as exc:
            self.status.setText(str(exc))
            return

        try:
            self.input.clear()
            self.pending_images.clear()
            self._update_attach_label()
        except RuntimeError:
            pass

        self.render()

        # Voice playback or status update (re-check game suppression right before playing)
        store_now = getattr(self.ctrl, 'store', None)
        extra_g = ''
        if store_now is not None:
            try:
                extra_g = store_now['voice_blocked_games']
            except Exception:
                extra_g = ''
        blocked_now = native.running_blocked_game(extra_names=extra_g, force_refresh=True) if store_now is not None else None
        if voice_bytes and winsound is not None and not blocked_now:
            try:
                self.status.setText('已保存，语音与文本同步输出中。')
            except RuntimeError:
                pass
            self._play_voice(voice_bytes)
        elif blocked_now and voice_bytes:
            try:
                self.status.setText(f'已保存；检测到游戏（{blocked_now}），已自动静音仅显示文本。')
            except RuntimeError:
                pass
        elif self.voice_mode and not error:
            try:
                self.status.setText('语音合成失败，以文字回复。')
            except RuntimeError:
                pass
        else:
            try:
                self.status.setText('已保存到本机。')
            except RuntimeError:
                pass

    def _play_voice(self, wav_bytes: bytes):
        """Play WAV audio asynchronously without blocking."""
        store = getattr(self.ctrl, 'store', None)
        vol = 65
        if store is not None:
            try:
                vol = int(float(store['voice_volume']))
            except Exception:
                vol = 65
        if vol <= 0 or winsound is None:
            return

        def _play():
            tmp_path = None
            scaled_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    f.write(wav_bytes)
                    tmp_path = Path(f.name)
                scaled_path = scaled_wav(tmp_path, vol)
                winsound.PlaySound(str(scaled_path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            except Exception:
                pass
            finally:
                for p in (scaled_path, tmp_path):
                    if p is not None:
                        try:
                            Path(p).unlink(missing_ok=True)
                        except OSError:
                            pass

        try:
            threading.Thread(target=_play, daemon=True, name='si-mm-voice').start()
        except RuntimeError:
            pass

    # ------------------------------------------------------- clear turns

    def clear_turns(self):
        """Clear conversation history (slot)."""
        previous = self.session.turns
        self.session.turns = []
        try:
            self.session.save()
        except (ConversationError, ChatError) as exc:
            self.session.turns = previous
            self.status.setText(str(exc))
            return
        except Exception:
            self.session.turns = previous
            self.status.setText('对话暂时未能清除，请重试。')
            return
        self.generation += 1
        self.render()
        self.status.setText('本机对话已清除，管理员偏好保留。')

    # --------------------------------------------------------- close event

    def closeEvent(self, event):
        self.generation += 1
        models_pending = self.model_busy
        self.invalidate_models()
        if self.busy:
            self.busy = False
            self.active_generation = None
            self.pending_text = None
            self.send.setEnabled(True)
            self.status.setText('已停止等待这次回复；结果不会写入记录。')
        elif models_pending:
            self.status.setText('已停止等待模型列表；迟到的结果不会更新窗口。')
        super().closeEvent(event)
