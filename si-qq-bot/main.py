"""Local control window for chatting with Si through a separate QQ account."""
from __future__ import annotations

import os
import json
import queue
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QPlainTextEdit, QVBoxLayout, QScrollArea, QWidget,
)


ROOT = Path(__file__).resolve().parent
WEBSOCKETS_SITE = ROOT / ".venv" / "Lib" / "site-packages"
PROFILE_MAX_CHARS = 2000
PROFILE_MAX_BYTES = 16 * 1024
DESKTOP_CONVERSATION_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_ADMIN_PROFILE = (
    "称呼我为管理员；我们是已经相识、信任的伙伴。"
    "相处自然、亲切但克制，不机械傲娇、强行撒娇或反复称呼。"
    "可以把我明确告诉你的偏好用于陪伴，不要臆测我没有说明的个人信息。"
    "我使用电脑时注意力很重要，请尽量不遮挡屏幕，也不要主动打扰。"
)
PROFILE_CORRUPT_TEXT = (
    "管理员偏好文件已损坏或格式无效；为保护你的资料，本次不会连接，"
    "也不会改用默认偏好。原文件已保留；请修复该文件，或删除它后重新打开"
    "程序以改用默认偏好。"
)
PROFILE_UNREADABLE_TEXT = (
    "管理员偏好文件存在但无法读取（权限或磁盘问题）；为保护你的资料，"
    "本次不会连接，也不会改用默认偏好。原文件未被修改；请检查文件权限后"
    "重试，或删除该文件后重新打开程序以改用默认偏好。"
)
APP_TITLE = "小祀 · QQ 陪伴"
APP_VERSION_TEXT = "0.2.5 中文绘图 · 本地模型调度"

GROUP_SWITCH_FILENAME = "group_reply_switch.json"
GROUP_SWITCH_MAX_BYTES = 1024
# A missing file means the local user has never turned group replies off, so
# the explicitly requested default (enabled) applies.  An existing file that
# is corrupt or unreadable is different: it must fail closed.
GROUP_SWITCH_DEFAULT = True
GROUP_SWITCH_SAVE_ERROR_TEXT = (
    "无法保存群聊 @ 回复开关；原文件已保留。请检查本机数据目录是否可写。"
)
GROUP_SWITCH_CORRUPT_TEXT = (
    "群聊 @ 回复设置文件已损坏或格式无效；为安全起见，本次已关闭群聊 @ 回复，"
    "且不会改写原文件。私聊功能不受影响。如需重新设置，请点击“重置群聊开关设置”。"
)
GROUP_SWITCH_UNREADABLE_TEXT = (
    "群聊 @ 回复设置文件存在但无法读取；为安全起见，本次已关闭群聊 @ 回复，"
    "且不会改写原文件。私聊功能不受影响。请检查文件权限，"
    "或点击“重置群聊开关设置”。"
)
GROUP_SWITCH_CHANGED_TEXT = (
    "群聊 @ 回复设置已被其他程序修改；为避免覆盖，本次未保存，原文件保持不变。"
    "请重新打开本窗口以载入最新设置。"
)
GROUP_SWITCH_RESET_TEXT = (
    "群聊 @ 回复设置文件已损坏或无法读取。重置会用“关闭群聊 @ 回复”的"
    "有效设置覆盖该文件，原内容将无法恢复；不会影响私聊。\n\n"
    "是否重置群聊开关设置？"
)
GROUP_RECOVERY_TEXT = (
    "上一条群聊回复的投递结果不明，既不能当作已送达，也不能当作确定失败。"
    "继续后会丢弃那条未确认的群聊回复，不会重发，也无法判断腾讯侧是否已送达；"
    "之后被 @ 时仍可继续回应。\n\n是否恢复群聊未确认回复？"
)
if WEBSOCKETS_SITE.is_dir():
    sys.path.insert(0, str(WEBSOCKETS_SITE))

from core import (  # noqa: E402
    DataDirectoryBusyError, DataDirectoryLockError, QQCore,
    _atomic_write_json, acquire_data_directory_lease,
)
from group_core import GroupChat  # noqa: E402
from model_connection import (  # noqa: E402
    CUSTOM_ENDPOINT_PLACEHOLDER, CUSTOM_MODEL_PLACEHOLDER, CUSTOM_PROVIDER,
    GEMINI_PROVIDER, GEMINI_ENDPOINT, GEMINI_MODEL,
    ERROR_CONFIG_CORRUPT_TEXT, ERROR_CONFIG_UNREADABLE_TEXT,
    LOCAL_DEFAULT_ENDPOINT, LOCAL_DEFAULT_MODEL, LOCAL_PROVIDER,
    MODEL_CONNECTION_FILENAME, ModelConnection, ModelConnectionError,
    PROVIDERS, PROVIDER_LABELS, SCHOOL_API_KEY, SCHOOL_ENDPOINT, SCHOOL_MODEL,
    SCHOOL_PROVIDER, fixed_message, test_connection, test_reply,
)
from onebot_adapter import OneBotAdapter, validate_ws_url  # noqa: E402


MODEL_CONNECTION_APPLIED_TEXT = (
    "模型连接已更新；之后的私聊与群聊回复将使用该设置。"
)
MODEL_CONNECTION_REQUIRED_TEXT = (
    "模型连接设置不可用；请先在“模型连接”中应用有效设置后再连接。"
)
MODEL_CONNECTION_UNAVAILABLE_TEXT = (
    "模型连接设置不可用；请重新打开“模型连接”并应用有效设置。"
)
MODEL_CONNECTION_APPLY_FAILED_TEXT = (
    "模型连接设置未能应用；请检查填写内容后重试。"
)
MODEL_TEST_RUNNING_TEXT = "正在测试连接…（只读取服务状态，不发送聊天内容）"
MODEL_REPLY_TEST_RUNNING_TEXT = "正在测试回复…（只发送固定测试语：请只回复：连接成功）"
MODEL_DIALOG_NO_ACTIVE_TEXT = (
    "当前没有可用的模型连接设置；请填写并显式点击“应用”后才会生效。"
)
MODEL_TEST_FAILED_TEXT = "模型连接测试未能完成；请稍后重试。"


class _UiEvents(QObject):
    # Every signal carries both the adapter object and the non-reusable
    # lifecycle generation that produced it.  Qt may deliver a queued signal
    # long after the adapter was replaced (or even reused); receivers must
    # ignore anything that is not the current pair.
    status = Signal(object, object, str, str)
    core_status = Signal(object, object, bool, bool)
    stopped = Signal(object, object)
    stop_pending = Signal(object, object)


class SiQQWindow(QDialog):
    """A deliberately small UI; it never stores credentials or chat text."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumWidth(510)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self._adapter = None
        self._adapter_generation = None
        self._generation_seq = 0
        self._core = None
        self._stop_thread = None
        self._stopping_adapter = None
        self._connected = False
        self._bound = False
        self._recovery = False
        # True while the modal recovery confirmation is on the stack.  It is
        # an explicit programmatic interlock, not merely a button state: the
        # dialog runs a nested Qt event loop, so _connect/_recover/close and
        # queued slots can all re-enter while the old core is captured.
        self._recovering = False
        self._close_requested = False
        self._status_watch_adapter = None
        self._status_watch_generation = None
        # (thread, stop_event) pairs for every status-watch thread that can
        # still emit a queued Qt signal.  closeEvent/shutdown join these
        # before the QObject is allowed to be destroyed.
        self._watch_threads = []
        self._data_lease = None
        self._profile_guard = threading.Lock()
        self._profile_value = DEFAULT_ADMIN_PROFILE
        # False means "a saved profile may exist but has not been read yet"
        # (for example because another process held the directory lease).
        # It is deliberately different from an empty or default profile.
        self._profile_known = False
        self._profile_load_error = None
        self._shutting_down = False
        self._profile_dialog = None
        self._profile_editor = None
        # Value the open editor was populated from.  _save_profile re-reads
        # the file first and refuses if the on-disk value no longer matches
        # this baseline, so a stale editor cannot silently replace a newer
        # saved preference.
        self._profile_editor_base = None
        # Group support is optional and entirely separate from the private
        # ledger.  The switch defaults to enabled when no saved file exists,
        # exactly as the local user requested.
        self._group_core = None
        self._group_reply_enabled = GROUP_SWITCH_DEFAULT
        self._group_switch_saving = False
        # None means the persisted setting was valid (or absent).  "corrupt"
        # and "unreadable" both force the in-memory switch closed and keep the
        # original bytes until an explicit local reset.
        self._group_switch_load_error = None
        self._group_switch_file_present = False
        self._group_switch_loaded_value = None
        self._group_switch_last_save_error = GROUP_SWITCH_SAVE_ERROR_TEXT
        # The shared model connection is loaded without creating the data
        # directory.  A missing file keeps the documented school preset; an
        # existing corrupt/unreadable file has no active snapshot and blocks a
        # new connection until the user explicitly applies a valid setting.
        self._model_connection = None
        self._model_dialog = None
        self._events = _UiEvents(self)
        self._events.status.connect(self._on_status)
        self._events.core_status.connect(self._on_core_status)
        self._events.stopped.connect(self._on_stopped)
        self._events.stop_pending.connect(self._on_stop_pending)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("contentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.content_scroll.setWidget(content)
        outer.addWidget(self.content_scroll)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        heading = QLabel(APP_TITLE)
        heading.setObjectName("appHeading")
        heading.setStyleSheet("font-size: 19px; font-weight: 650; color: #26364a;")
        layout.addWidget(heading)

        self.version_label = QLabel(APP_VERSION_TEXT)
        self.version_label.setObjectName("versionLabel")
        self.version_label.setStyleSheet("color: #657286; font-size: 12px;")
        layout.addWidget(self.version_label)

        self.intro_label = QLabel(
            "在 NapCat 用专用 QQ 账号完成扫码登录并启动本机 OneBot 后，在这里点击“连接”。\n"
            "私聊只回应已绑定管理员；群聊只在被明确 @ 时回应。私聊与群聊的对话记录分别独立保存。"
        )
        self.intro_label.setObjectName("introLabel")
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet("color: #566579;")
        layout.addWidget(self.intro_label)

        self.endpoint = QLineEdit("ws://127.0.0.1:3001/")
        self.endpoint.setObjectName("onebotEndpoint")
        self.endpoint.setAccessibleName("本机 OneBot 地址")
        self.endpoint.setPlaceholderText("ws://127.0.0.1:3001/")
        layout.addWidget(QLabel("本机连接地址"))
        layout.addWidget(self.endpoint)

        self.token = QLineEdit()
        self.token.setObjectName("onebotToken")
        self.token.setAccessibleName("OneBot 连接令牌")
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("输入 NapCat 的 OneBot Token")
        layout.addWidget(QLabel("OneBot Token（不是 QQ 密码或 WebUI 密码）"))
        layout.addWidget(self.token)

        actions = QHBoxLayout()
        self.connect_button = QPushButton("连接")
        self.connect_button.setObjectName("connectButton")
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.setObjectName("disconnectButton")
        self.disconnect_button.setEnabled(False)
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        layout.addLayout(actions)

        self.status_label = QLabel("未连接。不会在点击“连接”前访问 QQ 或模型。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.account_label = QLabel("祀的 QQ 账号：尚未识别")
        self.account_label.setObjectName("accountLabel")
        layout.addWidget(self.account_label)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #d8dee8;")
        layout.addWidget(line)

        bind_row = QHBoxLayout()
        self.bind_button = QPushButton("生成绑定码")
        self.bind_button.setObjectName("bindButton")
        self.bind_button.setEnabled(False)
        self.bind_label = QLabel("绑定后，只回应你的好友私聊。")
        self.bind_label.setObjectName("bindCodeLabel")
        bind_row.addWidget(self.bind_button)
        bind_row.addWidget(self.bind_label, 1)
        layout.addLayout(bind_row)

        self.recovery_button = QPushButton("恢复未确认回复")
        self.recovery_button.setObjectName("recoveryButton")
        self.recovery_button.setEnabled(False)
        layout.addWidget(self.recovery_button)

        self._load_initial_group_switch()
        self.group_switch = QCheckBox(
            "仅在群聊中被明确 @ 时回复（默认开启）"
        )
        self.group_switch.setObjectName("groupReplySwitch")
        self.group_switch.setAccessibleName("群聊 @ 回复开关")
        self.group_switch.setChecked(self._group_reply_enabled)
        self.group_switch.toggled.connect(self._on_group_switch_toggled)
        layout.addWidget(self.group_switch)
        media_hint = QLabel("群聊非文本附件先暂存，等同一发送者 @ 后处理；每群每人最多16条、保留10分钟。")
        media_hint.setWordWrap(True)
        media_hint.setStyleSheet("color: #657286; font-size: 12px;")
        layout.addWidget(media_hint)

        self.group_switch_status = QLabel("")
        self.group_switch_status.setObjectName("groupSwitchStatus")
        self.group_switch_status.setWordWrap(True)
        self.group_switch_status.setStyleSheet(
            "color: #566579; font-size: 12px;"
        )
        layout.addWidget(self.group_switch_status)

        self.group_switch_reset_button = QPushButton("重置群聊开关设置")
        self.group_switch_reset_button.setObjectName("groupSwitchResetButton")
        self.group_switch_reset_button.clicked.connect(
            self._reset_group_switch
        )
        layout.addWidget(self.group_switch_reset_button)
        self._refresh_group_switch_ui()

        self._load_initial_model_connection()
        self._model_connection.configure_companion_tools(enabled=True)
        model_row = QHBoxLayout()
        self.model_connection_button = QPushButton("模型连接…")
        self.model_connection_button.setObjectName("modelConnectionButton")
        self.model_connection_button.clicked.connect(
            self._open_model_connection
        )
        self.model_connection_status = QLabel("")
        self.model_connection_status.setObjectName("modelConnectionStatus")
        self.model_connection_status.setWordWrap(True)
        self.model_connection_status.setStyleSheet(
            "color: #566579; font-size: 12px;"
        )
        model_row.addWidget(self.model_connection_button)
        model_row.addWidget(self.model_connection_status, 1)
        layout.addLayout(model_row)
        self._refresh_model_connection_status()

        self.router_switch = QCheckBox("启用本地能力路由（本次运行）")
        self.router_switch.setObjectName("localModelRouterSwitch")
        self.router_switch.setToolTip("文字和图片默认共用 Qwen3.5；需要时可改用旧视觉模型。")
        self.router_switch.toggled.connect(self._set_local_router)
        layout.addWidget(self.router_switch)
        self.legacy_vision_switch = QCheckBox("图片改用旧视觉模型（切换可能较慢）")
        self.legacy_vision_switch.setObjectName("legacyVisionSwitch")
        self.legacy_vision_switch.setEnabled(False)
        self.legacy_vision_switch.toggled.connect(lambda value:self._set_local_router(self.router_switch.isChecked()))
        layout.addWidget(self.legacy_vision_switch)

        self.search_switch = QCheckBox("本次允许明确查询时联网搜索（Bing 仅接收查询词）")
        self.search_switch.setChecked(True)
        self.search_switch.toggled.connect(lambda value:
            self._model_connection.configure_companion_tools(enabled=True, search_enabled=value))
        layout.addWidget(self.search_switch)

        self.group_recovery_button = QPushButton("恢复群聊未确认回复")
        self.group_recovery_button.setObjectName("groupRecoveryButton")
        self.group_recovery_button.setEnabled(False)
        self.group_recovery_button.clicked.connect(self._recover_group)
        layout.addWidget(self.group_recovery_button)

        self._load_initial_profile()
        self.profile_button = QPushButton(self._profile_button_text())
        self.profile_button.setObjectName("profileButton")
        self.profile_button.clicked.connect(self._open_profile_editor)
        layout.addWidget(self.profile_button)

        self.privacy_label = QLabel(
            "QQ 消息经过腾讯 QQ 平台，回复使用“模型连接”中当前生效的服务。"
            "选择 Gemini 时，聊天内容经指定的 Vercel 服务交给 Google Gemini；"
            "选择学校预设时，使用上科大 GenAI 反代。"
            "需保持电脑、NapCat 和所选模型服务在线。QQ 对话与桌宠原有对话分开保存；"
            "私聊记录与群聊记录也分别独立保存，互不混用。\n"
            "管理员偏好为可选本机资料；只有你发送私聊时，它才会与当前对话一起发给模型。"
            "群聊会在内存中暂存非文本附件，仅在同一发送者 @ 祀 后解析并回复；"
            "关闭群聊或断开时清空暂存。普通文字未 @ 时丢弃。管理员偏好、私聊内容和私聊记录"
            "永远不会发送到群聊，也不会用于群聊回复。\n"
            "普通 QQ 号通过第三方 NapCat 接入，不保证与其他 QQ 登录数据隔离；"
            "请确认 NapCat 中登录的是专用账号。此窗口不会自动发起聊天。"
        )
        self.privacy_label.setObjectName("privacyLabel")
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setStyleSheet("color: #657286; font-size: 12px;")
        layout.addWidget(self.privacy_label)

        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.bind_button.clicked.connect(self._make_pair_code)
        self.recovery_button.clicked.connect(self._recover)

        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self.setStyleSheet("""
            QDialog { background: #f7f9fc; }
            QLineEdit { padding: 8px 10px; border: 1px solid #cbd4e1;
                        border-radius: 7px; background: white; }
            QPushButton { padding: 8px 14px; border: 0; border-radius: 7px;
                          background: #35a9ad; color: white; font-weight: 600; }
            QPushButton:disabled { background: #c5ced9; color: #f7f9fc; }
            QLabel#bindCodeLabel { color: #165f61; font-size: 16px; font-weight: 650; }
        """)
        screen = QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 800
        self.resize(560, min(740, max(360, available_height - 60)))

    def _data_directory(self):
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise RuntimeError("Windows 本机数据目录不可用。")
        return Path(root) / "SiCompanion" / "QQPrivateChat"

    def _profile_path(self):
        return self._data_directory() / "admin_profile.json"

    def _group_switch_path(self):
        # The group switch and the one group ledger live in the same leased
        # data directory as the private state.  Group numbers are never used
        # as path components.
        return self._data_directory() / GROUP_SWITCH_FILENAME

    def _read_group_switch_file(self):
        """Return ``(found, enabled)`` for the persisted group switch.

        A missing file means the local user has never saved a choice, so the
        explicitly requested default (enabled) applies.  An existing file that
        is not a regular bounded file, cannot be read, or does not match the
        exact schema raises instead: a corrupt setting must never be treated
        as permission to enable group replies.  This method never writes or
        modifies the original bytes.
        """
        path = self._group_switch_path()
        try:
            info = path.stat()
        except FileNotFoundError:
            return False, GROUP_SWITCH_DEFAULT
        if (not stat.S_ISREG(info.st_mode)
                or info.st_size > GROUP_SWITCH_MAX_BYTES):
            raise ValueError("invalid group switch file")
        # Read a bounded number of bytes rather than an unbounded text read:
        # the file could grow between stat() and read.
        raw = path.read_bytes()
        if len(raw) > GROUP_SWITCH_MAX_BYTES:
            raise ValueError("invalid group switch file")
        value = json.loads(raw.decode("utf-8"))
        if (not isinstance(value, dict)
                or set(value) != {"schema", "enabled"}
                or value.get("schema") != 1
                or type(value.get("enabled")) is not bool):
            raise ValueError("invalid group switch file")
        return True, value["enabled"]

    def _group_switch_error_text(self):
        if self._group_switch_load_error == "unreadable":
            return GROUP_SWITCH_UNREADABLE_TEXT
        return GROUP_SWITCH_CORRUPT_TEXT

    def _load_initial_group_switch(self):
        """Load the saved switch, failing closed on an existing bad file.

        A missing file keeps the explicitly authorized default (enabled).  A
        corrupt, invalid, or unreadable *existing* file disables group replies
        for this run and leaves the original bytes untouched.  Private chat is
        never blocked by this local group setting.
        """
        self._group_switch_load_error = None
        self._group_switch_file_present = False
        self._group_switch_loaded_value = None
        try:
            found, enabled = self._read_group_switch_file()
        except OSError:
            self._group_reply_enabled = False
            self._group_switch_load_error = "unreadable"
            return
        except (ValueError, TypeError, RecursionError):
            self._group_reply_enabled = False
            self._group_switch_load_error = "corrupt"
            return
        self._group_reply_enabled = bool(enabled)
        self._group_switch_file_present = bool(found)
        self._group_switch_loaded_value = bool(enabled) if found else None

    def _refresh_group_switch_ui(self):
        """Show the effective state and whether it is a saved or default one."""
        if self._group_switch_load_error is not None:
            self.group_switch.setEnabled(False)
            self.group_switch_reset_button.setEnabled(True)
            self.group_switch_reset_button.setVisible(True)
            self.group_switch_status.setText(self._group_switch_error_text())
            return
        self.group_switch.setEnabled(True)
        self.group_switch_reset_button.setEnabled(False)
        self.group_switch_reset_button.setVisible(False)
        if not self._group_switch_file_present:
            # The persisted default is visible even before the first choice.
            self.group_switch_status.setText(
                "群聊 @ 回复：默认开启（尚未保存选择）"
            )
        elif self._group_reply_enabled:
            self.group_switch_status.setText("群聊 @ 回复：已保存为开启")
        else:
            self.group_switch_status.setText("群聊 @ 回复：已保存为关闭")

    def _save_group_switch(self, enabled, *, recover=False):
        """Persist the switch atomically under the same data-directory lease.

        ``recover`` is only used by the explicit local reset confirmation.
        Without it, a missing, corrupt, unreadable, or externally changed file
        is never overwritten: the original bytes win and the caller gets a
        fixed local message.  A successfully written value becomes the new
        baseline for subsequent stale-write checks.
        """
        self._group_switch_last_save_error = GROUP_SWITCH_SAVE_ERROR_TEXT
        before_status = self.status_label.text()
        if not self._ensure_data_lease():
            after_status = self.status_label.text()
            self._group_switch_last_save_error = (
                after_status if after_status != before_status
                else GROUP_SWITCH_SAVE_ERROR_TEXT
            )
            return False
        if not recover:
            try:
                found, current = self._read_group_switch_file()
            except OSError:
                # The setting became unreadable after this window loaded it.
                # Preserve the bytes and fail closed: an unreadable saved
                # setting is never evidence that group replies are allowed.
                self._group_reply_enabled = False
                self._group_switch_load_error = "unreadable"
                self._group_switch_last_save_error = (
                    GROUP_SWITCH_UNREADABLE_TEXT
                )
                self._release_data_lease_if_idle()
                return False
            except (ValueError, TypeError, RecursionError):
                # Same rule for corrupt content: preserve bytes, disable the
                # feature, and offer only the explicit local reset.
                self._group_reply_enabled = False
                self._group_switch_load_error = "corrupt"
                self._group_switch_last_save_error = GROUP_SWITCH_CORRUPT_TEXT
                self._release_data_lease_if_idle()
                return False
            if (found != self._group_switch_file_present
                    or (found
                        and current != self._group_switch_loaded_value)):
                # Another program changed or removed the setting after this
                # window loaded it.  Do not silently overwrite unseen bytes.
                self._group_switch_last_save_error = (
                    GROUP_SWITCH_CHANGED_TEXT
                )
                self._release_data_lease_if_idle()
                return False
        try:
            _atomic_write_json(
                self._group_switch_path(),
                {"schema": 1, "enabled": bool(enabled)},
            )
        except (OSError, ValueError, RuntimeError):
            self._release_data_lease_if_idle()
            return False
        self._group_switch_load_error = None
        self._group_switch_file_present = True
        self._group_switch_loaded_value = bool(enabled)
        self._release_data_lease_if_idle()
        return True

    def _revert_group_switch_control(self):
        """Restore the visible checkbox without recursively saving."""
        self._group_switch_saving = True
        try:
            self.group_switch.setChecked(self._group_reply_enabled)
        finally:
            self._group_switch_saving = False

    def _on_group_switch_toggled(self, checked):
        if self._group_switch_saving:
            return
        enabled = bool(checked)
        if enabled == self._group_reply_enabled:
            return
        if self._group_switch_load_error is not None:
            # The saved file is unusable.  Never turn group replies on, and
            # never overwrite the original bytes without explicit recovery.
            self._revert_group_switch_control()
            self.status_label.setText(self._group_switch_error_text())
            self._refresh_group_switch_ui()
            return
        if self._save_group_switch(enabled):
            self._group_reply_enabled = enabled
            if not enabled and self._adapter is not None:
                clear = getattr(self._adapter, 'clear_group_media', None)
                if callable(clear):
                    clear()
            self.status_label.setText(
                "群聊 @ 回复已开启；只回应明确 @ 祀 的群聊消息。"
                if enabled else
                "群聊 @ 回复已关闭；不会再处理新的群聊消息。"
            )
            self._refresh_group_switch_ui()
            return
        self._revert_group_switch_control()
        self.status_label.setText(self._group_switch_last_save_error)
        self._refresh_group_switch_ui()

    def _reset_group_switch(self):
        """Explicitly replace a corrupt/unreadable switch file with a safe one.

        This is the only path that may overwrite unusable bytes.  It always
        writes the closed state; the user can then turn the switch on with a
        normal, validated save.
        """
        if self._group_switch_load_error is None or self._group_switch_saving:
            return
        if not self._ensure_data_lease():
            return
        try:
            answer = QMessageBox.question(
                self,
                "重置群聊开关设置",
                GROUP_SWITCH_RESET_TEXT,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        except Exception:
            self.status_label.setText(
                "群聊开关重置确认未能完成；原文件已保留，群聊 @ 回复保持关闭。"
            )
            self._release_data_lease_if_idle()
            return
        if answer != QMessageBox.Yes:
            self.status_label.setText(
                "未重置群聊开关设置；原文件已保留，群聊 @ 回复保持关闭。"
            )
            self._release_data_lease_if_idle()
            return
        if not self._save_group_switch(False, recover=True):
            self.status_label.setText(GROUP_SWITCH_SAVE_ERROR_TEXT)
            self._refresh_group_switch_ui()
            return
        self._group_reply_enabled = False
        self._revert_group_switch_control()
        self.status_label.setText(
            "群聊 @ 回复设置已重置为关闭；如需开启，请手动勾选。"
        )
        self._refresh_group_switch_ui()

    # -- model connection ---------------------------------------------

    def _model_connection_path(self):
        return self._data_directory() / MODEL_CONNECTION_FILENAME

    def _load_initial_model_connection(self):
        """Load the persisted selection without creating any directory."""
        path = None
        try:
            path = self._model_connection_path()
            self._model_connection = ModelConnection.load(path)
        except Exception:
            # Fail closed: no active endpoint is invented when the local
            # configuration cannot even be addressed.
            try:
                self._model_connection = (
                    ModelConnection.failed(path, 'corrupt')
                    if path is not None else None
                )
            except Exception:
                self._model_connection = None

    def _model_connection_error_text(self):
        connection = self._model_connection
        if connection is not None and connection.load_error == 'unreadable':
            return ERROR_CONFIG_UNREADABLE_TEXT
        return ERROR_CONFIG_CORRUPT_TEXT

    def _refresh_model_connection_status(self):
        connection = self._model_connection
        snapshot = connection.current() if connection is not None else None
        if snapshot is None:
            self.model_connection_status.setText(
                self._model_connection_error_text()
            )
            return
        label = PROVIDER_LABELS.get(snapshot.provider, snapshot.provider)
        text = f"模型连接：{label} · {snapshot.model}"
        if hasattr(self,'router_switch') and self.router_switch.isChecked():
            from model_router import RouterConfig,select_route
            route=select_route(RouterConfig(True),snapshot.provider,snapshot.endpoint,snapshot.model,[])
            if route.routed:
                legacy=hasattr(self,'legacy_vision_switch') and self.legacy_vision_switch.isChecked()
                text="本地分流：文字 Qwen3.5 · 图片 "+("Qwen2.5VL" if legacy else "Qwen3.5")
            else:
                text+="（分流仅用于本机 Ollama，当前未启用）"
        if snapshot.provider in (CUSTOM_PROVIDER, GEMINI_PROVIDER) and not snapshot.api_key:
            text += "（API Key 只保存在内存中，重启后需重新输入）"
        self.model_connection_status.setText(text)

    def _set_local_router(self, enabled):
        from model_router import RouterConfig
        if self._model_connection is not None:
            legacy=hasattr(self,'legacy_vision_switch') and self.legacy_vision_switch.isChecked()
            self._model_connection.configure_router(RouterConfig(enabled=bool(enabled),
                vision_model='si-qwen2.5vl:7b' if legacy else 'si-qwen3.5:9b'))
        if hasattr(self,'legacy_vision_switch'):
            self.legacy_vision_switch.setEnabled(bool(enabled))
        self._refresh_model_connection_status()

    def _apply_model_connection(self, provider, endpoint, model, api_key):
        """Explicitly validate, persist (provider/endpoint/model), then apply."""
        if self._shutting_down:
            return False, "程序正在关闭，未应用模型连接设置。"
        connection = self._model_connection
        if connection is None:
            return False, MODEL_CONNECTION_UNAVAILABLE_TEXT
        if not self._ensure_data_lease():
            return False, MODEL_CONNECTION_APPLY_FAILED_TEXT
        try:
            connection.apply(provider, endpoint, model, api_key)
        except ModelConnectionError as exc:
            return False, fixed_message(exc, MODEL_CONNECTION_APPLY_FAILED_TEXT)
        except Exception:
            return False, MODEL_CONNECTION_APPLY_FAILED_TEXT
        finally:
            self._release_data_lease_if_idle()
        # The key is only in the connection's in-memory snapshot; neither the
        # status text nor the persisted payload contains it.
        self.status_label.setText(MODEL_CONNECTION_APPLIED_TEXT)
        self._refresh_model_connection_status()
        return True, MODEL_CONNECTION_APPLIED_TEXT

    def _auto_connect_existing(self):
        if not self._ensure_data_lease():
            return
        try:
            from local_napcat import existing_connection
            configured = existing_connection(self._data_directory())
            self.endpoint.setText(configured.endpoint)
            self.token.setText(configured.token)
            self._connect()
        except Exception:
            self.status_label.setText("未能读取已绑定小祀的本机 NapCat 连接；请在窗口手动填写原 OneBot Token。")
        finally:
            self._release_data_lease_if_idle()

    def _open_model_connection(self, *, preselect_gemini=False):
        if self._shutting_down:
            return
        if self._model_dialog is not None:
            self._model_dialog.raise_()
            self._model_dialog.activateWindow()
            return
        if self._model_connection is None:
            self._load_initial_model_connection()
        dialog = ModelConnectionDialog(
            self, self._model_connection, self._apply_model_connection
        )
        self._model_dialog = dialog
        if preselect_gemini and dialog.provider_combo.currentData() != GEMINI_PROVIDER:
            dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData(GEMINI_PROVIDER))
        try:
            dialog.exec()
        finally:
            self._model_dialog = None
            self._refresh_model_connection_status()

    def _read_profile_file(self):
        """Read the optional profile without taking or releasing the lease.

        Returns ``(found, profile)``.  A missing file is not an error: it
        means the user never saved a profile, so the built-in default is the
        correct value.  An existing file that cannot be read or validated
        raises instead, because silently substituting the default would
        discard the user's saved preference.
        """
        path = self._profile_path()
        try:
            info = path.stat()
        except FileNotFoundError:
            return False, DEFAULT_ADMIN_PROFILE
        if not stat.S_ISREG(info.st_mode):
            raise OSError("profile path is not a regular file")
        if info.st_size > PROFILE_MAX_BYTES:
            raise ValueError("profile file too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        profile = value.get("profile") if isinstance(value, dict) else None
        if not isinstance(profile, str) or len(profile) > PROFILE_MAX_CHARS:
            raise ValueError("invalid profile file")
        return True, profile

    def _load_profile_under_lease(self):
        """Read the saved profile while the caller owns the directory lease.

        On failure the in-memory value is left untouched (never replaced by
        the default) and a recoverable error state is recorded.  ``_connect``
        uses the return value to refuse to connect.
        """
        try:
            _found, profile = self._read_profile_file()
        except (ValueError, RecursionError):
            self._profile_known = False
            self._profile_load_error = "corrupt"
            self.status_label.setText(PROFILE_CORRUPT_TEXT)
            return False
        except OSError:
            self._profile_known = False
            self._profile_load_error = "unreadable"
            self.status_label.setText(PROFILE_UNREADABLE_TEXT)
            return False
        with self._profile_guard:
            self._profile_value = profile
            self._profile_known = True
            self._profile_load_error = None
        return True

    def _load_initial_profile(self):
        parent = self._profile_path().parent
        try:
            info = parent.stat()
        except FileNotFoundError:
            # No persistent state exists yet; the default is the actual value
            # rather than a fallback for an unreadable file.
            self._profile_known = True
            return
        except OSError:
            self._profile_known = False
            self._profile_load_error = "unreadable"
            self.status_label.setText(PROFILE_UNREADABLE_TEXT)
            return
        if not stat.S_ISDIR(info.st_mode):
            self._profile_known = False
            self._profile_load_error = "unreadable"
            self.status_label.setText(PROFILE_UNREADABLE_TEXT)
            return
        if not self._ensure_data_lease():
            # Another program owns the directory.  Keep _profile_known False
            # so the UI never presents the default as the saved preference;
            # _connect re-reads the file after it acquires the same lease.
            return
        try:
            self._load_profile_under_lease()
        finally:
            self._release_data_lease_if_idle()

    def _profile_button_text(self):
        if self._profile_load_error:
            return "管理员偏好（无法读取，原文件已保留）"
        if not self._profile_known:
            return "管理员偏好（尚未读取，连接时重试）"
        if self._profile_value == DEFAULT_ADMIN_PROFILE:
            return "管理员偏好（默认，可修改）"
        return ("管理员偏好（已保存）" if self._profile_value
                else "管理员偏好（已清空）")

    def _open_profile_editor(self):
        if self._profile_dialog is not None:
            self._profile_dialog.raise_()
            self._profile_dialog.activateWindow()
            return
        # Always try to show the latest saved value.  Another process may
        # have saved a new preference since startup or while this window was
        # idle; if the lease is busy now, the editor falls back to the
        # in-memory value and _save_profile will re-check before writing.
        if self._ensure_data_lease():
            try:
                self._load_profile_under_lease()
            finally:
                self._release_data_lease_if_idle()
            self.profile_button.setText(self._profile_button_text())

        dialog = QDialog(self)
        dialog.setWindowTitle("祀 · 管理员偏好")
        dialog.setMinimumWidth(490)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "首次使用会带入你明确提出的交流偏好，不推断姓名、年龄等个人身份。"
            "你可以修改、清空，或从桌宠 conversation.json 只导入 profile 字段；"
            "不会导入聊天记录。资料最多 2000 字。\n"
            "资料只保存在本机；每次你发起私聊时会以普通用户资料形式附在对话中"
            "发送给本机上科大 GenAI 模型，不会覆盖祀的系统角色设定。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        editor = QPlainTextEdit(dialog)
        editor.setObjectName("adminProfileEditor")
        editor.setAccessibleName("管理员偏好资料")
        editor.setPlainText(self._profile_value)
        editor.setPlaceholderText("例如：称呼我为管理员；表达自然、亲切、克制。")
        editor.setMaximumBlockCount(200)
        editor.setMinimumHeight(150)
        layout.addWidget(editor)

        actions = QHBoxLayout()
        import_button = QPushButton("从桌宠偏好导入…")
        import_button.setObjectName("importProfileButton")
        save_button = QPushButton("保存到本机")
        save_button.setObjectName("saveProfileButton")
        close_button = QPushButton("关闭")
        actions.addWidget(import_button)
        actions.addStretch(1)
        actions.addWidget(save_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        import_button.clicked.connect(self._import_desktop_profile)
        save_button.clicked.connect(self._save_profile)
        close_button.clicked.connect(dialog.close)
        dialog.finished.connect(self._profile_dialog_closed)
        self._profile_dialog = dialog
        self._profile_editor = editor
        self._profile_editor_base = self._profile_value
        dialog.show()

    def _profile_dialog_closed(self, _result):
        self._profile_dialog = None
        self._profile_editor = None
        self._profile_editor_base = None

    def _import_desktop_profile(self):
        if self._profile_dialog is None or self._profile_editor is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self._profile_dialog,
            "选择桌宠对话设置文件",
            str(Path.home()),
            "JSON 文件 (conversation.json);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            source = Path(path)
            if source.stat().st_size > DESKTOP_CONVERSATION_MAX_BYTES:
                raise ValueError("file too large")
            value = json.loads(source.read_text(encoding="utf-8"))
            profile = value.get("profile") if isinstance(value, dict) else None
            if not isinstance(profile, str) or len(profile) > PROFILE_MAX_CHARS:
                raise ValueError("invalid profile")
        except (OSError, ValueError, json.JSONDecodeError):
            QMessageBox.information(
                self._profile_dialog,
                "无法导入偏好",
                "这个文件没有可用的管理员偏好，或文件格式无法读取。桌宠聊天记录不会被导入。",
            )
            return
        self._profile_editor.setPlainText(profile)

    def _save_profile(self):
        if self._profile_dialog is None or self._profile_editor is None:
            return
        profile = self._profile_editor.toPlainText()
        if len(profile) > PROFILE_MAX_CHARS:
            QMessageBox.information(
                self._profile_dialog, "偏好内容过长", "请将资料控制在 2000 字以内。"
            )
            return
        # The profile lives in the same local data directory.  Guard the
        # write with the same lease so another program cannot modify that
        # directory at the same time.
        if not self._ensure_data_lease():
            QMessageBox.information(
                self._profile_dialog, "数据目录暂时不可用",
                self.status_label.text(),
            )
            return
        # Re-read under the lease before writing.  If the saved value differs
        # from the value this editor was populated from, another program (or
        # a concurrent reload) changed the preference in the meantime; this
        # editor has not shown that value, so refuse rather than silently
        # replacing it.  A missing or unreadable/corrupt file also fails
        # closed: the user's original bytes are never overwritten unseen.
        base = self._profile_editor_base
        if not self._load_profile_under_lease():
            self.profile_button.setText(self._profile_button_text())
            self._release_data_lease_if_idle()
            QMessageBox.information(
                self._profile_dialog, "暂时无法保存",
                self.status_label.text()
                + "\n本次未保存，原文件未被修改；请稍后重试。",
            )
            return
        self.profile_button.setText(self._profile_button_text())
        if base is not None and self._profile_value != base:
            self._release_data_lease_if_idle()
            QMessageBox.information(
                self._profile_dialog, "已有更新的管理员偏好",
                "另一个程序已保存了新的管理员偏好，而这个编辑窗口未显示该内容。"
                "为避免覆盖，本次未保存。请关闭本窗口后重新打开偏好设置，"
                "确认最新内容后再保存。",
            )
            return
        try:
            path = self._profile_path()
            payload = json.dumps(
                {"schema": 1, "profile": profile}, ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(payload) > PROFILE_MAX_BYTES:
                raise ValueError("profile file too large")
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
        except (OSError, RuntimeError, ValueError):
            self._release_data_lease_if_idle()
            QMessageBox.information(
                self._profile_dialog,
                "保存失败",
                "无法保存本机偏好。原有文件已保留，请检查本机数据目录是否可写。",
            )
            return

        with self._profile_guard:
            self._profile_value = profile
            self._profile_known = True
            self._profile_load_error = None
            core = self._core
            if core is not None:
                core._set_profile(profile)
        self.profile_button.setText(self._profile_button_text())
        self.status_label.setText(
            "管理员偏好已保存在本机；你发送下一条私聊时会随对话发送给模型。"
        )
        self._release_data_lease_if_idle()
        self._profile_editor_base = profile
        self._profile_dialog.close()

    def _is_current(self, adapter, generation):
        """Whether *adapter*/*generation* is still the one owning this UI."""
        return (
            adapter is not None
            and adapter is self._adapter
            and generation == self._adapter_generation
        )

    def _offline_core_reports_pending(self):
        """Whether a retained, disconnected core still has unresolved state.

        Only a disconnected core can block a new connection this way; while
        an adapter is current, ``_connect`` is already refused.  An unreadable
        status is treated as pending (fail closed).  This is an in-memory read
        under the core's process lock and never writes.
        """
        if self._adapter is not None:
            return False
        core = self._core
        if core is None:
            return False
        try:
            return bool(
                core.local_status().get("pending_recovery_required")
            )
        except Exception:
            return True

    def _ensure_data_lease(self):
        """Take the cross-process data-directory lease without blocking Qt."""
        if self._data_lease is not None:
            return True
        try:
            self._data_lease = acquire_data_directory_lease(
                self._data_directory()
            )
            return True
        except (DataDirectoryBusyError, DataDirectoryLockError) as exc:
            # Actionable, non-modal: the UI stays responsive and the buttons
            # remain usable for a retry after the other program is closed.
            self.status_label.setText(str(exc))
            return False
        except (OSError, RuntimeError) as exc:
            self.status_label.setText(
                str(exc) or "无法访问本机 QQ 私聊数据目录，请检查后重试。"
            )
            return False

    def _release_data_lease(self):
        lease = self._data_lease
        self._data_lease = None
        if lease is None:
            return
        try:
            lease.release()
        except (DataDirectoryLockError, OSError):
            self.status_label.setText(
                "本机 QQ 数据目录锁未能正常释放；请关闭程序后重新打开。"
            )

    def _release_data_lease_if_idle(self):
        # A status watcher only reads local state, but it is still a Qt
        # emitter.  Keep the lease until it has really exited as well, so the
        # lease lifetime never ends before the last background emitter.
        if (self._adapter is not None or self._core is not None
                or self._group_core is not None):
            return
        stop_thread = self._stop_thread
        if stop_thread is not None and stop_thread.is_alive():
            return
        self._reap_status_watch_threads()
        if self._watch_threads:
            return
        self._release_data_lease()

    def _connect(self):
        # A final shutdown may keep the window responsive while it waits for
        # an unwinding model call.  No new adapter (and no new lease or core)
        # may be started during that window.
        if self._shutting_down or self._adapter is not None:
            return
        # The recovery confirmation dialog runs a nested event loop.  Even
        # though its buttons are disabled, a queued slot or a programmatic
        # caller could reach this method, construct a second core, and make
        # the retained core's later write disagree with the new core's
        # in-memory state.  Refuse every connection attempt until the dialog
        # has returned and its recovery really succeeded.
        if self._recovering:
            self.status_label.setText(
                "正在确认未确认回复的处理；完成前不会发起连接。"
            )
            return
        # A retained, quiescent core with an unresolved offline pending owns
        # the durable state.  Connecting would construct a second core for
        # the same state file.  Check both the UI flag and the core itself so
        # a stale flag cannot reopen the race.
        if self._recovery or self._offline_core_reports_pending():
            self.status_label.setText(
                "上一条 QQ 回复的投递结果仍不明；请先完成“恢复未确认回复”。"
                "未确认回复仍被保留，在恢复成功前不会建立新连接，也不会重发。"
            )
            return
        # A corrupt or unreadable model configuration has no active snapshot.
        # Never fall back to the school preset or the old hard-coded core
        # endpoint: the user must explicitly apply a valid connection first.
        connection = self._model_connection
        if connection is None or connection.current() is None:
            self.status_label.setText(MODEL_CONNECTION_REQUIRED_TEXT)
            return
        url = self.endpoint.text().strip()
        token = self.token.text()
        if not validate_ws_url(url):
            self.status_label.setText("只允许连接 ws://127.0.0.1:3001/ 本机地址。")
            return
        if not token or any(ord(ch) <= 32 or ord(ch) == 127 for ch in token):
            self.status_label.setText("请填写有效的 OneBot Token。")
            return
        try:
            self._data_directory()
        except RuntimeError as exc:
            self.status_label.setText(str(exc))
            return
        # The lease is acquired before the adapter can construct a core or
        # read the state file, and is held until every writer is quiescent.
        if not self._ensure_data_lease():
            return
        # The profile may have been unreadable at startup (another process
        # held the lease, the file was temporarily inaccessible, or this
        # window started before a saved profile was written).  Now that the
        # directory is locked, re-read it before any adapter/core can be
        # built.  An existing but invalid file blocks the connection rather
        # than silently falling back to the default profile.
        if not self._load_profile_under_lease():
            self.profile_button.setText(self._profile_button_text())
            self._release_data_lease_if_idle()
            return
        self.profile_button.setText(self._profile_button_text())

        # A retained, quiescent group ledger has no writer while disconnected.
        # Dropping it here lets the next lifecycle construct exactly one group
        # ledger; any durable unresolved pending record is reloaded from the
        # same file and remains blocked until explicit local recovery.
        self._group_core = None
        self._generation_seq += 1
        generation = self._generation_seq
        adapter_holder = {}

        def core_factory(bot_id):
            # Called on the adapter's background thread.  A delayed call from
            # an older lifecycle must neither create a second core nor assign
            # it to this window.
            if not self._is_current(adapter_holder.get("adapter"), generation):
                return None
            return self._create_core_for_bot(bot_id)

        def group_factory(bot_id, private_core):
            # A separate, pair-keyed group ledger.  It receives only the safe
            # comparison method from the existing private core, never its
            # profile, turns, pending state, or state file.
            if not self._is_current(adapter_holder.get("adapter"), generation):
                return None
            if (self._recovering or self._shutting_down or self._recovery
                    or self._core is not private_core):
                return None
            if self._model_connection is None:
                return None
            try:
                group = GroupChat(
                    self._data_directory(), bot_id,
                    request_reply=self._model_connection,
                    is_admin=getattr(
                        private_core, "is_administrator", None
                    ),
                )
            except Exception:
                return None
            self._group_core = group
            return group

        def status_callback(code, message):
            # Capture the adapter identity, not self._adapter: by delivery
            # time self._adapter may already be a newer connection.  Once
            # shutdown() has started, no thread may touch the Qt object.
            if self._shutting_down:
                return
            self._events.status.emit(
                adapter_holder.get("adapter"), generation, code, message
            )

        try:
            adapter = OneBotAdapter(
                url, token, core_factory, status_callback=status_callback,
            )
        except (TypeError, ValueError):
            self._release_data_lease_if_idle()
            self.status_label.setText("本机连接参数无效，未启动连接。")
            return
        adapter_holder["adapter"] = adapter
        # Test doubles and older private-only adapters may not expose the
        # optional group surface; in that case private chat remains exactly
        # as before.
        configure_group = getattr(adapter, "configure_group", None)
        if callable(configure_group):
            try:
                configure_group(
                    group_factory,
                    lambda: self._group_reply_enabled,
                )
            except (TypeError, ValueError, RuntimeError):
                pass
        self._adapter = adapter
        self._adapter_generation = generation
        self._stopping_adapter = None
        self.token.clear()
        self.token.setEnabled(False)
        self.endpoint.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self.status_label.setText("正在连接本机 OneBot…")
        if not adapter.start():
            # start() refuses while a previous worker may still write the
            # state file.  Only detach when the adapter is really quiescent;
            # otherwise keep the adapter identity and the lease so close and
            # shutdown wait for is_quiescent() instead of dropping the guard.
            quiescent = adapter.is_quiescent()
            if quiescent:
                self._adapter = None
                self._adapter_generation = None
                self._release_data_lease_if_idle()
            else:
                self._stopping_adapter = adapter
            self.token.setEnabled(quiescent)
            self.endpoint.setEnabled(quiescent)
            self.connect_button.setEnabled(quiescent)
            self.disconnect_button.setEnabled(not quiescent)
            self.recovery_button.setEnabled(
                quiescent and self._recovery and self._core is not None
            )
            self.status_label.setText(
                "上一条本机任务尚未结束，暂不能连接；请稍后重试。"
            )

    def _create_core_for_bot(self, bot_id):
        # Never install a new core while recovery owns the old one or while a
        # final shutdown is waiting for writers.  The adapter's core factory
        # runs on another thread, so this guard must be checked here as well
        # as in _connect.
        if self._recovering or self._shutting_down or self._recovery:
            return None
        if self._model_connection is None:
            return None
        # The same callable instance is injected into both ledgers.  It is
        # callable with the existing (endpoint, model, messages, api_key)
        # signature but ignores those legacy arguments in favour of one
        # immutable per-call snapshot of the user's active selection.
        core = QQCore(self._data_directory(), bot_id,
                      request_reply=self._model_connection)
        # Install the freshest profile and the new core atomically with
        # respect to an explicit profile save, so a save that lands between
        # lease acquisition and core construction is never lost.
        with self._profile_guard:
            core._set_profile(self._profile_value)
            self._core = core
        return core

    def _disconnect(self):
        # A stop started inside the recovery dialog's nested loop could let a
        # send unwind or retire the adapter/core that _recover captured.
        # Refuse it; the user can disconnect again after the dialog returns.
        if self._recovering:
            return
        adapter = self._adapter
        generation = self._adapter_generation
        if adapter is None or self._stop_thread is not None:
            return
        self.disconnect_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        # Stop the core-status watcher immediately; recovery must not be
        # offered while a send may still be unwinding.
        self._connected = False
        self._stopping_adapter = adapter
        self._stop_status_watch(join_timeout=0.0)
        self.status_label.setText(
            "正在断开；如有本机模型调用，会等它结束后再宣布安全停止。"
        )

        def stop_worker():
            # stop() is bounded and runs off the UI thread.  It returns False
            # while a model call is still unwinding inside a thread that
            # cancellation cannot stop.  After shutdown() the event loop may
            # already be gone, so never emit into this window's QObject then.
            stopped = adapter.stop()
            if self._shutting_down:
                return
            if stopped:
                self._events.stopped.emit(adapter, generation)
            else:
                self._events.stop_pending.emit(adapter, generation)

        self._stop_thread = threading.Thread(
            target=stop_worker, name="SiQQStop", daemon=True
        )
        self._stop_thread.start()

    def _on_stop_pending(self, adapter, generation):
        if not self._is_current(adapter, generation):
            return
        if self._recovering:
            # Do not change the stop/lease state that the recovery dialog is
            # validating against; the timer resumes normal handling later.
            return
        if self._stop_thread is None:
            return
        self._connected = False
        self._stop_status_watch(join_timeout=0.0)
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.status_label.setText(
            "本机模型调用仍在结束中；暂未安全停止，不能重新连接。"
            "窗口会保持响应并自动等待，请勿重复断开。"
        )

    def _on_stopped(self, adapter, generation):
        self._finish_stopped(adapter, generation)

    def _finish_stopped(self, adapter, generation):
        # Identity *and* generation: an adapter object reused for a newer
        # lifecycle must not let its old queued stop signal finish the new
        # connection.
        if not self._is_current(adapter, generation):
            return False
        # The recovery dialog may have delivered a queued ``stopped`` signal.
        # Do not retire the adapter or core that _recover is about to use;
        # _refresh resumes normal finalisation once the dialog has returned.
        if self._recovering:
            return False
        # ``stopped`` is emitted by the stop worker itself, and Qt may deliver
        # that queued slot while the worker is still returning from emit().
        # The regression test forces exactly this by blocking the worker just
        # after emit.  Until the emitting thread has really exited, neither
        # the adapter identity nor the lease may be retired and closeEvent
        # must keep ignoring the close.  The UI timer retries through
        # _refresh() once the thread is gone.
        stop_thread = self._stop_thread
        if stop_thread is not None and stop_thread.is_alive():
            return False
        # A bounded adapter.stop() may have returned True while a delayed
        # adapter callback or core worker is still live; the extra check keeps
        # this finalisation fail-closed even for callers other than _refresh.
        if not adapter.is_quiescent():
            return False
        core = self._core
        bound = False
        recovery = False
        if core is not None:
            try:
                state = core.local_status()
                bound = bool(state.get("bound"))
                recovery = bool(state.get("pending_recovery_required"))
            except Exception:
                bound = False
                # An unreadable status cannot prove that no unresolved
                # pending exists.  Keep the quiescent core and block a new
                # connection rather than guessing it is safe.
                recovery = True

        group_core = self._group_core
        group_recovery = False
        if group_core is not None:
            try:
                group_recovery = bool(
                    group_core.local_status().get(
                        "pending_recovery_required"
                    )
                )
            except Exception:
                # Treat an unreadable group status as unresolved; it is
                # retained and never silently discarded.
                group_recovery = True

        self._adapter = None
        self._adapter_generation = None
        self._stop_thread = None
        self._stopping_adapter = None
        self._connected = False
        self._bound = bound
        self._recovery = recovery
        self._stop_status_watch(join_timeout=0.0)
        self.account_label.setText("祀的 QQ 账号：尚未识别")
        if bound:
            self.bind_label.setText("已绑定管理员；仅回应此账号的好友私聊。")
        else:
            self.bind_label.setText("绑定后，只回应你的好友私聊。")
        self.bind_button.setEnabled(False)
        self.endpoint.setEnabled(True)
        self.token.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        if group_recovery:
            # The group ledger is quiescent and has no writer; keeping it
            # makes the explicit local recovery path available while
            # disconnected.  It does not block private reconnection: the next
            # lifecycle reloads the same durable pending and stays blocked.
            self._group_core = group_core
            self.group_recovery_button.setText(
                "恢复群聊未确认回复（已断开）"
            )
            self.group_recovery_button.setEnabled(True)
        else:
            self._group_core = None
            self.group_recovery_button.setText("恢复群聊未确认回复")
            self.group_recovery_button.setEnabled(False)

        if recovery:
            # Keep the quiescent core so recovery is available even while
            # disconnected.  No send is retried and no new writer exists.
            # Connecting now would build a second core for the same state
            # file; the unresolved pending must be resolved first.
            self._core = core
            self.connect_button.setEnabled(False)
            self.recovery_button.setEnabled(True)
            self.recovery_button.setText("恢复未确认回复（已断开）")
            text = (
                "本机连接已安全断开。上一条 QQ 回复结果不明；"
                "可在本机恢复，不会重发，也不会把它当作已送达或已失败。"
            )
            if group_recovery:
                text += (
                    "另有一条群聊回复结果不明，可在本机单独恢复；"
                    "不会自动重发。"
                )
            self.status_label.setText(text)
        else:
            self._core = None
            self.recovery_button.setText("恢复未确认回复")
            self.recovery_button.setEnabled(False)
            if group_recovery:
                self.status_label.setText(
                    "本机连接已安全断开。上一条群聊回复结果不明；"
                    "可在本机点击“恢复群聊未确认回复”，不会重发。"
                    "私聊仍可重新连接。"
                )
            else:
                self.status_label.setText(
                    "已安全断开本机连接，本机任务均已结束。"
                )
            # The adapter and every core worker are quiescent (stop returned
            # True or is_quiescent was verified), the stop thread is gone, and
            # no recovery core is retained.  A status watcher may still be
            # unwinding; _release_data_lease_if_idle keeps the lease until it
            # has exited too, and _refresh retries after it is reaped.
            self._release_data_lease_if_idle()
        if self._close_requested:
            # closeEvent decides when it is safe: it keeps the window alive
            # while a status watcher can still emit to its QObject.
            self.close()
        return True

    def _on_status(self, adapter, generation, code, message):
        # Adapter callbacks are constrained to static status strings.  The
        # adapter/generation check rejects both a replaced adapter and a
        # queued signal from an older lifecycle of a reused adapter object.
        if not self._is_current(adapter, generation):
            return
        if self._stopping_adapter is adapter:
            # A status queued before disconnect must not overwrite the stop
            # progress or mark this connection live again.
            return
        self.status_label.setText(message)
        if code == "connected":
            self._connected = True
        elif code in ("stopped", "disconnected", "connection_error",
                      "login_failed", "core_failed"):
            self._connected = False
            self._stop_status_watch(join_timeout=0.0)

    def _reap_status_watch_threads(self):
        """Drop watcher records whose threads have finished (UI thread only)."""
        self._watch_threads = [
            (thread, event) for thread, event in self._watch_threads
            if thread.is_alive()
        ]

    def _start_status_watch(self, adapter, generation, core):
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._watch_core_status,
            args=(adapter, generation, core, stop_event),
            name="SiQQStatusWatch", daemon=True,
        )
        self._watch_threads.append((thread, stop_event))
        thread.start()
        return thread, stop_event

    def _stop_status_watch(self, join_timeout=0.0):
        """Wake and (boundedly) join every thread that emits core_status.

        The stop event makes the normal 0.5 s sleep interruptible.  A watcher
        can be inside ``core.local_status()``; callers that must destroy the
        window use a real timeout and verify the result before doing so.
        """
        self._status_watch_adapter = None
        self._status_watch_generation = None
        for _thread, event in self._watch_threads:
            event.set()
        deadline = time.monotonic() + max(0.0, float(join_timeout))
        for thread, _event in list(self._watch_threads):
            if thread is threading.current_thread():
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self._reap_status_watch_threads()
        return not self._watch_threads

    def background_threads_idle(self):
        """Whether no thread that emits Qt signals is still running.

        The adapter is part of this check, not just the threads we started
        here: its connection thread invokes the status callback (a Qt emit)
        and its core workers can still write the state file.  Retaining the
        adapter therefore also retains the data-directory lease until
        ``is_quiescent()`` is true.
        """
        self._reap_status_watch_threads()
        stop_thread = self._stop_thread
        adapter = self._adapter
        return (
            not self._watch_threads
            and (stop_thread is None or not stop_thread.is_alive())
            and (adapter is None or adapter.is_quiescent())
        )

    def wait_for_background_threads(self, timeout=2.0):
        """Stop watchers and join the stop worker; return True only if idle.

        This is called after the Qt event loop has ended and by tests before
        deleting a window.  ``idle`` also requires the adapter's own thread and
        every core worker to have finished (``adapter.is_quiescent()``), so a
        bounded ``stop()`` that returned False keeps this returning False.
        It never deletes or invalidates the QObject itself; callers must not
        do so unless this returns True.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        self._stop_status_watch(join_timeout=timeout)
        stop_thread = self._stop_thread
        if (stop_thread is not None
                and stop_thread is not threading.current_thread()):
            stop_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        while (time.monotonic() < deadline
               and not self.background_threads_idle()):
            time.sleep(0.01)
        return self.background_threads_idle()

    def shutdown(self, timeout=2.0):
        """Stop every background emitter and report whether they all ended.

        Safe to call after the Qt loop has ended or from test cleanup.  The
        adapter identity and the data-directory lease are kept until the
        adapter's own thread is gone, every core worker has finished, and
        every status-watcher/stop thread has exited.  If this returns False a
        model call is still unwinding or a watcher is still blocked: the
        caller must keep this window, its QObject receivers, and the lease
        alive, pump Qt events, and retry.  It must not destroy the window
        merely because ``app.exec()`` returned.
        """
        self._shutting_down = True
        self._connected = False
        self._stop_status_watch(join_timeout=0.0)
        if self._adapter is not None and self._stop_thread is None:
            self._disconnect()
        if not self.wait_for_background_threads(timeout):
            # Deliberately keep the adapter, generation, core, watchers, stop
            # thread, and lease in place.  A late adapter callback must still
            # find a live QObject, and a core worker still unwinding must
            # still be covered by the lease.
            return False
        # Every emitter has really ended, so no late callback can use this
        # identity any more.  This is a final shutdown: a retained recovery
        # core has no more writes to make (a restart reloads the durable
        # pending record), so neither it nor the lease needs to be kept.
        self._adapter = None
        self._adapter_generation = None
        self._stopping_adapter = None
        self._stop_thread = None
        self._core = None
        self._group_core = None
        self._release_data_lease_if_idle()
        return True

    def _group_pending(self, group_core):
        """Read the group ledger's recovery flag; unreadable means pending."""
        try:
            return bool(
                group_core.local_status().get("pending_recovery_required")
            )
        except Exception:
            return True

    def _refresh_group_recovery(self):
        if self._recovering:
            return
        group_core = self._group_core
        if group_core is None:
            self.group_recovery_button.setEnabled(False)
            return
        pending = self._group_pending(group_core)
        self.group_recovery_button.setEnabled(
            pending and self._stop_thread is None
        )
        if pending:
            self.group_recovery_button.setText(
                "恢复群聊未确认回复（已断开）"
                if self._adapter is None
                else "恢复群聊未确认回复"
            )
        else:
            self.group_recovery_button.setText("恢复群聊未确认回复")

    def _refresh(self):
        # The timer also fires inside the modal recovery dialog's nested
        # event loop.  While recovery owns the retained core, do not finish a
        # stop, release the lease, or honour a deferred close from here.
        if self._recovering:
            return
        self._reap_status_watch_threads()
        self._refresh_group_recovery()
        adapter = self._adapter
        generation = self._adapter_generation
        if adapter is None:
            # With no adapter, the remaining reasons to keep the lease are a
            # stop worker/status watcher that may still emit, or a recovery
            # core.  _release_data_lease_if_idle checks all three and retries
            # on every timer tick once the last watcher has exited.
            if self.background_threads_idle():
                self._release_data_lease_if_idle()
            if (self._close_requested
                    and self.background_threads_idle()):
                self._close_requested = False
                self.close()
            return
        while True:
            item = adapter.poll_status()
            if item is None:
                break
            self._on_status(adapter, generation, *item)
        if self._stop_thread is not None:
            # A previous model call may still be unwinding.  Keep polling from
            # the UI timer so the window stays responsive; only a verified
            # quiescent adapter is allowed to finish the stop.
            if (not self._stop_thread.is_alive()
                    and adapter.is_quiescent()):
                self._finish_stopped(adapter, generation)
            return
        bot_id = adapter.verified_bot_id()
        if bot_id is not None:
            self.account_label.setText(f"祀的 QQ 账号：{bot_id}")
        core = self._core
        if core is None:
            return
        if self._connected and (
                self._status_watch_adapter is not adapter
                or self._status_watch_generation != generation):
            self._status_watch_adapter = adapter
            self._status_watch_generation = generation
            self._start_status_watch(adapter, generation, core)

    def _watch_core_status(self, adapter, generation, core, stop_event):
        while (not stop_event.is_set()
               and not self._shutting_down
               and self._is_current(adapter, generation)
               and self._stopping_adapter is not adapter
               and self._connected
               and self._core is core):
            state = None
            try:
                state = core.local_status()
            except Exception:
                state = None
            # Re-check immediately before emitting: a stop/close/shutdown may
            # have started while local_status() was running.  shutdown() joins
            # this thread before the QObject can be destroyed.
            if (state is not None
                    and not stop_event.is_set()
                    and not self._shutting_down
                    and self._is_current(adapter, generation)
                    and self._stopping_adapter is not adapter
                    and self._connected
                    and self._core is core):
                self._events.core_status.emit(
                    adapter, generation, bool(state.get("bound")),
                    bool(state.get("pending_recovery_required")),
                )
            stop_event.wait(0.5)

    def _on_core_status(self, adapter, generation, bound, recovery):
        if not self._is_current(adapter, generation):
            return
        if self._stopping_adapter is adapter:
            # Recovery must not be offered from a queued callback while the
            # send may still be unwinding.
            return
        self._bound = bound
        self._recovery = recovery
        if self._recovering:
            # Keep the state above (the post-dialog validation must notice a
            # concurrent resolution), but do not re-enable any control while
            # the recovery dialog is still on the stack.
            return
        if self._bound:
            self.bind_label.setText("已绑定管理员；仅回应此账号的好友私聊。")
            self.bind_button.setEnabled(False)
        else:
            self.bind_button.setEnabled(self._connected)
        self.recovery_button.setEnabled(self._recovery and self._connected)

    def _make_pair_code(self):
        if self._recovering:
            # Do not mutate the core that recovery captured from inside the
            # dialog's nested event loop.
            return
        core = self._core
        if not self._connected or core is None or self._bound:
            return
        code = core.begin_pairing()
        if not code:
            self.bind_label.setText("当前账号已绑定，不能从 QQ 聊天中更换管理员。")
            return
        self.bind_label.setText(f"{code}　（5 分钟内有效）")
        self.status_label.setText("请用自己的 QQ 给祀发送：绑定 " + code)

    def _sync_recovery_controls(self):
        """Restore the buttons after a recovery attempt.

        The offline branch also consults the retained core, so a stale UI
        flag can never offer a new connection while unresolved pending data
        still owns the state file.
        """
        if self._recovering or self._shutting_down:
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(False)
            self.recovery_button.setEnabled(False)
            self.bind_button.setEnabled(False)
            self.group_recovery_button.setEnabled(False)
            return
        # Group pending never blocks private reconnection, so this is only the
        # availability of the group-scoped local recovery action.
        self._refresh_group_recovery()
        if self._adapter is not None:
            self.connect_button.setEnabled(False)
            self.disconnect_button.setEnabled(self._stop_thread is None)
            self.recovery_button.setEnabled(
                self._recovery
                and self._connected
                and self._stop_thread is None
                and self._core is not None
            )
            self.bind_button.setEnabled(
                not self._bound
                and self._connected
                and self._stop_thread is None
                and self._stopping_adapter is None
            )
            return
        self.disconnect_button.setEnabled(False)
        self.bind_button.setEnabled(False)
        self.connect_button.setEnabled(
            not self._recovery and not self._offline_core_reports_pending()
        )
        self.recovery_button.setEnabled(
            self._recovery and self._core is not None
        )

    def _recover(self):
        # A final shutdown must not gain a new state write from the UI while
        # it is waiting for the existing writers to become quiescent.  A
        # pending stop could still let a send unwind, and a second recovery
        # must not run inside the modal dialog's nested event loop.
        if (self._shutting_down or self._recovering or self._close_requested
                or self._stop_thread is not None
                or self._stopping_adapter is not None):
            return
        core = self._core
        if core is None or not self._recovery:
            return
        # Local recovery writes the state file, so it needs the same exclusive
        # lease even if this window is currently disconnected.
        if not self._ensure_data_lease():
            return
        lease = self._data_lease
        adapter = self._adapter
        generation = self._adapter_generation
        connected = self._connected

        # The confirmation is modal but runs a nested Qt event loop.  Disable
        # the buttons for the user and set an explicit interlock for every
        # programmatic path (_connect, _recover, _disconnect, closeEvent,
        # _refresh, _finish_stopped, and the queued status slots).
        self._recovering = True
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.bind_button.setEnabled(False)
        self.group_recovery_button.setEnabled(False)
        try:
            try:
                answer = QMessageBox.question(
                    self,
                    "恢复未确认回复",
                    "上一条 QQ 回复的投递结果不明，既不能当作已送达，也不能当作确定失败。"
                    "继续后会丢弃那条未确认回复，不会重发，也无法判断腾讯侧是否已送达；"
                    "之后的聊天可继续。\n\n是否恢复？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
            except Exception:
                self.status_label.setText(
                    "恢复确认未能完成；未确认回复仍被保留，不会重发。"
                )
                return
            if answer != QMessageBox.Yes:
                # Cancel keeps the durable pending record, the retained core,
                # and its message-id deduplication.  Connecting stays refused.
                self.status_label.setText(
                    "未确认回复仍被保留，没有重发；恢复成功前不会建立新连接。"
                )
                return
            # The nested loop may have delivered queued signals or run a
            # programmatic _connect/_recover/_disconnect/close/shutdown.
            # Re-validate the exact lifecycle captured above before touching
            # the file.  Nothing may write through a core that is no longer
            # the current, leased one.
            if (self._shutting_down
                    or self._close_requested
                    or self._stop_thread is not None
                    or self._stopping_adapter is not None
                    or self._adapter is not adapter
                    or self._adapter_generation != generation
                    or self._core is not core
                    or self._data_lease is not lease
                    or self._connected != connected):
                self.status_label.setText(
                    "恢复未执行：窗口状态已变化；未确认回复仍被保留，不会重发。"
                )
                return
            # Re-read the pending record itself, not just the UI flag; it may
            # have been resolved by a delayed callback while the dialog was
            # open.  If it cannot be read, abort without writing: the
            # original durable record must win over an unverifiable state.
            try:
                still_pending = bool(
                    core.local_status().get("pending_recovery_required")
                )
            except Exception:
                self.status_label.setText(
                    "恢复未执行：无法确认未确认回复的状态；"
                    "原记录仍被保留，不会重发。"
                )
                return
            if not still_pending:
                self._recovery = False
                self.recovery_button.setText("恢复未确认回复")
                if adapter is None and self._core is core:
                    self._core = None
                self.status_label.setText(
                    "这条回复已由本机确认处理，无需再次恢复；没有重发。"
                )
                return
            try:
                discarded = bool(core.discard_uncertain_pending())
            except Exception:
                # _commit only updates memory after the atomic replacement
                # succeeds, so a raised error leaves the original bytes and
                # the pending record in place.
                discarded = False
            if discarded:
                self._recovery = False
                self.recovery_button.setText("恢复未确认回复")
                if adapter is None and self._core is core:
                    # The disconnected recovery core has no more writes to
                    # make.  The lease is released below once it is detached.
                    self._core = None
                if self._adapter is None:
                    self.status_label.setText(
                        "已清除未确认状态；没有重发那条回复。现在可以重新连接。"
                    )
                else:
                    self.status_label.setText(
                        "已清除未确认状态；没有重发那条回复，后续私聊可以继续。"
                    )
                return
            # Failure keeps both the durable record and the retained core so
            # the same recovery and deduplication can be retried.  It never
            # reports the QQ delivery as successful or failed.
            try:
                still_pending = bool(
                    core.local_status().get("pending_recovery_required")
                )
            except Exception:
                still_pending = True
            if still_pending:
                self.status_label.setText(
                    "恢复未完成，请稍后检查本机存储；未确认回复仍被保留。"
                )
                return
            self._recovery = False
            self.recovery_button.setText("恢复未确认回复")
            if adapter is None and self._core is core:
                self._core = None
            self.status_label.setText(
                "这条回复已由本机确认处理，无需再次恢复；没有重发。"
            )
        finally:
            self._recovering = False
            if adapter is None and not self._recovery:
                # Success, or a concurrent resolution: the disconnected
                # recovery core has no more writes.  _release_data_lease_if_idle
                # still checks the live adapter/core/watcher state.
                self._release_data_lease_if_idle()
            self._sync_recovery_controls()

    def _recover_group(self):
        # Same interlock as private recovery: the confirmation runs a nested
        # Qt event loop, so no connect/stop/close or second recovery may race
        # the group state write while the dialog is on the stack.
        if (self._shutting_down or self._recovering or self._close_requested
                or self._stop_thread is not None
                or self._stopping_adapter is not None):
            return
        group_core = self._group_core
        if group_core is None or not self._group_pending(group_core):
            return
        # The group ledger is a local write and shares the application's
        # exclusive data-directory lease, even while disconnected.
        if not self._ensure_data_lease():
            return
        lease = self._data_lease
        adapter = self._adapter
        generation = self._adapter_generation
        connected = self._connected

        self._recovering = True
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.bind_button.setEnabled(False)
        self.group_recovery_button.setEnabled(False)
        try:
            try:
                answer = QMessageBox.question(
                    self,
                    "恢复群聊未确认回复",
                    GROUP_RECOVERY_TEXT,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
            except Exception:
                self.status_label.setText(
                    "群聊恢复确认未能完成；未确认回复仍被保留，不会重发。"
                )
                return
            if answer != QMessageBox.Yes:
                # Cancel keeps the durable pending record and the retained
                # ledger.  The group path stays blocked, and nothing is sent.
                self.status_label.setText(
                    "未确认的群聊回复仍被保留，没有重发；"
                    "恢复成功前该群聊不会继续回复。"
                )
                return
            # The nested loop may have delivered queued signals or run a
            # programmatic connect/disconnect/close/shutdown.  Only the exact
            # lifecycle captured above may write the group state file.
            if (self._shutting_down
                    or self._close_requested
                    or self._stop_thread is not None
                    or self._stopping_adapter is not None
                    or self._adapter is not adapter
                    or self._adapter_generation != generation
                    or self._group_core is not group_core
                    or self._data_lease is not lease
                    or self._connected != connected):
                self.status_label.setText(
                    "群聊恢复未执行：窗口状态已变化；"
                    "未确认回复仍被保留，不会重发。"
                )
                return
            # Re-read the durable flag, not just the button state: a delayed
            # adapter callback may have resolved it while the dialog was open.
            try:
                still_pending = bool(
                    group_core.local_status().get(
                        "pending_recovery_required"
                    )
                )
            except Exception:
                self.status_label.setText(
                    "群聊恢复未执行：无法确认未确认回复的状态；"
                    "原记录仍被保留，不会重发。"
                )
                return
            if not still_pending:
                self._group_recovery_resolved(group_core, adapter)
                self.status_label.setText(
                    "这条群聊回复已由本机确认处理，无需再次恢复；没有重发。"
                )
                return
            try:
                discarded = bool(
                    group_core.discard_uncertain_group_pending()
                )
            except Exception:
                discarded = False
            if discarded:
                self._group_recovery_resolved(group_core, adapter)
                if adapter is None:
                    self.status_label.setText(
                        "已清除群聊未确认状态；没有重发那条回复。"
                        "现在可以重新连接。"
                    )
                else:
                    self.status_label.setText(
                        "已清除群聊未确认状态；没有重发那条回复，"
                        "该群聊再次明确 @ 时可以继续。"
                    )
                return
            # Failure keeps the durable record and its deduplication so the
            # same explicit recovery may be retried.  It never claims the
            # group send succeeded or failed.
            try:
                still_pending = bool(
                    group_core.local_status().get(
                        "pending_recovery_required"
                    )
                )
            except Exception:
                still_pending = True
            if still_pending:
                self.status_label.setText(
                    "群聊恢复未完成，请稍后检查本机存储；"
                    "未确认回复仍被保留。"
                )
                return
            self._group_recovery_resolved(group_core, adapter)
            self.status_label.setText(
                "这条群聊回复已由本机确认处理，无需再次恢复；没有重发。"
            )
        finally:
            self._recovering = False
            # A retained group ledger keeps the lease on its own; this only
            # releases it when every core and emitter is really gone.
            self._release_data_lease_if_idle()
            self._sync_recovery_controls()

    def _group_recovery_resolved(self, group_core, adapter):
        """Retire the group recovery control after its pending record is gone.

        Nothing is replayed.  A disconnected recovery ledger has no further
        writes and is detached; while connected the ledger remains the live
        one for that adapter.
        """
        self.group_recovery_button.setText("恢复群聊未确认回复")
        self.group_recovery_button.setEnabled(False)
        if adapter is None and self._group_core is group_core:
            self._group_core = None

    def closeEvent(self, event):
        if self._recovering:
            # The recovery confirmation is still on the stack (this slot can
            # be reached from its nested event loop).  Refuse the close
            # outright instead of deferring it, so no lease/core release can
            # race the pending state write.  The user may close afterwards.
            event.ignore()
            return
        if self._adapter is not None or not self.background_threads_idle():
            # Never announce a stop, destroy the window, or enable
            # reconnection while the adapter, a model call, the stop worker
            # that emitted ``stopped``, or a status watcher may still emit a
            # queued Qt signal or write state.  background_threads_idle()
            # checks the stop thread's is_alive() as well as is_quiescent().
            # The timer keeps polling and closes the window once every
            # background emitter has ended.
            self._close_requested = True
            if self._adapter is not None:
                self._disconnect()
            else:
                self._stop_status_watch(join_timeout=0.0)
            event.ignore()
            return
        self._close_requested = False
        event.accept()
        # No adapter or background emitter remains; releasing here also
        # covers an idle window that only holds a recovery-ready core.
        self._release_data_lease()


class ModelConnectionDialog(QDialog):
    """Compact editor for the shared private/group model connection.

    Testing runs on a worker thread and communicates only through a
    ``queue.Queue``.  The worker never touches a QObject, so closing this
    dialog or the application while a test is outstanding cannot emit into a
    destroyed receiver.  Editing and testing never persist or activate a
    setting; only the explicit Apply callback does that.
    """

    def __init__(self, parent, connection, apply_callback):
        super().__init__(parent)
        self.setWindowTitle("模型连接")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._connection = connection
        self._apply_callback = apply_callback
        self._populating = False
        self._closed = False
        self._test_running = False
        self._test_generation = 0
        self._test_queue = queue.Queue()
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(100)
        self._test_timer.timeout.connect(self._poll_test_result)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("modelProviderCombo")
        for provider in PROVIDERS:
            self.provider_combo.addItem(PROVIDER_LABELS[provider], provider)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setObjectName("modelEndpointEdit")
        self.endpoint_edit.setAccessibleName("模型接口地址")
        self.model_edit = QLineEdit()
        self.model_edit.setObjectName("modelModelEdit")
        self.model_edit.setAccessibleName("模型名称")
        self.key_edit = QLineEdit()
        self.key_edit.setObjectName("modelApiKeyEdit")
        self.key_edit.setAccessibleName("模型 API Key")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("留空表示不使用；只保存在内存中")
        form.addRow("连接预设", self.provider_combo)
        form.addRow("接口地址", self.endpoint_edit)
        form.addRow("模型名称", self.model_edit)
        form.addRow("API Key", self.key_edit)
        layout.addLayout(form)

        hint = QLabel(
            "Gemini 预设使用你指定的 Vercel 转发服务，聊天内容经该服务交给 Google Gemini。"
            "本机预设只允许 HTTP 回环地址；远程服务使用 HTTPS。\n"
            "只有“应用”会保存并启用设置，配置文件只包含预设、地址和模型名称；"
            "API Key 仅保留在内存中，重启后需重新输入。\n"
            "“测试连接”只读取服务状态。“测试回复”仅发送：请只回复：连接成功；"
            "两者均不读取聊天记录或管理员偏好。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #657286; font-size: 12px;")
        layout.addWidget(hint)

        self.status_label = QLabel("")
        self.status_label.setObjectName("modelStatusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.apply_button = QPushButton("应用")
        self.apply_button.setObjectName("modelApplyButton")
        self.test_button = QPushButton("测试连接")
        self.test_button.setObjectName("modelTestButton")
        self.reply_test_button = QPushButton("测试回复")
        self.reply_test_button.setObjectName("modelReplyTestButton")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("modelCloseButton")
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.reply_test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.provider_combo.currentIndexChanged.connect(
            self._on_provider_changed
        )
        self.apply_button.clicked.connect(self._apply)
        self.test_button.clicked.connect(self._start_test)
        self.reply_test_button.clicked.connect(lambda: self._start_test(reply_test=True))
        self.close_button.clicked.connect(self.reject)
        self._load_values()
        self.resize(520, 390)

    # -- values --------------------------------------------------------

    def _current_status_text(self):
        snapshot = (
            self._connection.current()
            if self._connection is not None else None
        )
        if snapshot is None:
            return MODEL_DIALOG_NO_ACTIVE_TEXT
        label = PROVIDER_LABELS.get(snapshot.provider, snapshot.provider)
        return f"当前生效：{label} · {snapshot.model}"

    def _update_placeholders(self):
        provider = self.provider_combo.currentData()
        if provider == SCHOOL_PROVIDER:
            self.endpoint_edit.setPlaceholderText(SCHOOL_ENDPOINT)
            self.model_edit.setPlaceholderText(SCHOOL_MODEL)
        elif provider == GEMINI_PROVIDER:
            self.endpoint_edit.setPlaceholderText(GEMINI_ENDPOINT)
            self.model_edit.setPlaceholderText(GEMINI_MODEL)
        elif provider == LOCAL_PROVIDER:
            self.endpoint_edit.setPlaceholderText(LOCAL_DEFAULT_ENDPOINT)
            self.model_edit.setPlaceholderText(LOCAL_DEFAULT_MODEL)
        else:
            self.endpoint_edit.setPlaceholderText(CUSTOM_ENDPOINT_PLACEHOLDER)
            self.model_edit.setPlaceholderText(CUSTOM_MODEL_PLACEHOLDER)

    def _load_values(self):
        snapshot = (
            self._connection.current()
            if self._connection is not None else None
        )
        if snapshot is None:
            provider = SCHOOL_PROVIDER
            endpoint = SCHOOL_ENDPOINT
            model = SCHOOL_MODEL
            api_key = SCHOOL_API_KEY
        else:
            provider = snapshot.provider
            endpoint = snapshot.endpoint
            model = snapshot.model
            api_key = snapshot.api_key
        self._populating = True
        try:
            index = self.provider_combo.findData(provider)
            self.provider_combo.setCurrentIndex(index if index >= 0 else 0)
            self.endpoint_edit.setText(endpoint)
            self.model_edit.setText(model)
            self.key_edit.setText(api_key)
        finally:
            self._populating = False
        self._update_placeholders()
        self.status_label.setText(self._current_status_text())

    def _on_provider_changed(self, _index):
        if self._populating:
            return
        provider = self.provider_combo.currentData()
        if provider == SCHOOL_PROVIDER:
            self.endpoint_edit.setText(SCHOOL_ENDPOINT)
            self.model_edit.setText(SCHOOL_MODEL)
            self.key_edit.setText(SCHOOL_API_KEY)
        elif provider == GEMINI_PROVIDER:
            self.endpoint_edit.setText(GEMINI_ENDPOINT)
            self.model_edit.setText(GEMINI_MODEL)
            self.key_edit.clear()
        elif provider == LOCAL_PROVIDER:
            self.endpoint_edit.setText(LOCAL_DEFAULT_ENDPOINT)
            self.model_edit.setText(LOCAL_DEFAULT_MODEL)
            self.key_edit.clear()
        else:
            # Do not silently reuse a school/local HTTP address for a custom
            # HTTPS selection.
            self.endpoint_edit.clear()
            self.model_edit.clear()
            self.key_edit.clear()
        self._update_placeholders()
        self.status_label.setText(
            self._current_status_text() + "\n修改后需点击“应用”才会生效。"
        )

    # -- explicit apply ------------------------------------------------

    def _apply(self):
        if self._closed:
            return
        provider = self.provider_combo.currentData()
        endpoint = self.endpoint_edit.text()
        model = self.model_edit.text()
        api_key = self.key_edit.text()
        try:
            ok, message = self._apply_callback(
                provider, endpoint, model, api_key
            )
        except Exception:
            ok, message = False, MODEL_CONNECTION_APPLY_FAILED_TEXT
        self.status_label.setText(
            message if isinstance(message, str) and message
            else MODEL_CONNECTION_APPLY_FAILED_TEXT
        )
        if ok:
            # The active snapshot now contains the key in memory only.
            self._update_placeholders()

    # -- off-thread test ------------------------------------------------

    def _start_test(self, *, reply_test=False):
        if self._closed or self._test_running:
            return
        provider = self.provider_combo.currentData()
        endpoint = self.endpoint_edit.text()
        model = self.model_edit.text()
        api_key = self.key_edit.text()
        self._test_running = True
        self._test_generation += 1
        generation = self._test_generation
        result_queue = queue.Queue()
        self._test_queue = result_queue
        self.apply_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.reply_test_button.setEnabled(False)
        for field in (self.provider_combo, self.endpoint_edit, self.model_edit, self.key_edit):
            field.setEnabled(False)
        self.status_label.setText(MODEL_REPLY_TEST_RUNNING_TEXT if reply_test else MODEL_TEST_RUNNING_TEXT)
        probe = test_reply if reply_test else test_connection

        def worker():
            # This closure intentionally captures no QObject.  A late result
            # is simply left in the queue if the dialog has gone away.
            try:
                ok, message = probe(
                    provider, endpoint, model, api_key
                )
            except Exception:
                ok, message = False, MODEL_TEST_FAILED_TEXT
            result_queue.put((generation, bool(ok), message))

        threading.Thread(
            target=worker, name="model-connection-test", daemon=True
        ).start()
        self._test_timer.start()

    def _poll_test_result(self):
        if self._closed:
            return
        try:
            generation, ok, message = self._test_queue.get_nowait()
        except queue.Empty:
            return
        if generation != self._test_generation:
            return
        self._test_timer.stop()
        self._test_running = False
        self.apply_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.reply_test_button.setEnabled(True)
        for field in (self.provider_combo, self.endpoint_edit, self.model_edit, self.key_edit):
            field.setEnabled(True)
        self.status_label.setText(
            message if isinstance(message, str) and message
            else MODEL_TEST_FAILED_TEXT
        )

    def _shutdown_test_poll(self):
        self._closed = True
        self._test_generation += 1
        self._test_running = False
        try:
            self._test_timer.stop()
        except RuntimeError:
            pass
        # Do not leave the typed key in a widget after the dialog is closed.
        try:
            self.key_edit.clear()
        except RuntimeError:
            pass

    def reject(self):
        self._shutdown_test_poll()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_test_poll()
        super().closeEvent(event)


def main():
    args = sys.argv[1:]
    smoke_path = None
    if len(args) == 2 and args[0] in ("--smoke-test", "--model-smoke-test"):
        smoke_path = Path(args[1])
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Si QQ 私聊")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = SiQQWindow()
    if '--legacy-vision' in args and smoke_path is None:
        window.legacy_vision_switch.setChecked(True)
    if '--local-router' in args and smoke_path is None:
        window.router_switch.setChecked(True)
    window.show()
    if '--connect-existing-napcat' in args and smoke_path is None:
        QTimer.singleShot(200, window._auto_connect_existing)
    preview = window
    if smoke_path is not None and args[0] == '--model-smoke-test':
        preview = ModelConnectionDialog(window, window._model_connection, window._apply_model_connection)
        preview.provider_combo.setCurrentIndex(preview.provider_combo.findData(LOCAL_PROVIDER))
        preview.show()
    if '--configure-gemini' in args and smoke_path is None:
        QTimer.singleShot(150, lambda: window._open_model_connection(preselect_gemini=True))
    smoke_result = {"saved": False}
    if smoke_path is not None:
        def save_preview():
            smoke_result["saved"] = preview.grab().save(str(smoke_path))
            app.quit()
        QTimer.singleShot(700, save_preview)
    result = app.exec()
    # Keep the window, its QObject receivers, and the data-directory lease
    # alive until every background emitter has really stopped.  shutdown()
    # returns False while a bounded adapter.stop() left a model call (or a
    # blocked watcher) unwinding; returning here would let Python/Qt destroy
    # the QObject or drop the lease while a daemon thread could still emit or
    # write state.  Pump the Qt queue so deferred closes and queued signals
    # are handled normally, and retry until the adapter itself is quiescent.
    # Only an OS/user forced kill bypasses this loop; that is not a safe
    # cleanup and is not claimed as one.
    while not window.shutdown(timeout=0.5):
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    if smoke_path is not None and not smoke_result["saved"]:
        return 3
    return result


if __name__ == "__main__":
    raise SystemExit(main())
