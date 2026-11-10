"""Optional free-chat dialog. No network call runs until the user acts."""
import threading
from pathlib import Path
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QTextEdit, QTextBrowser, QPushButton,
                              QFormLayout)
from .conversation import (Conversation, ConversationError, DEFAULT_GENAI_ENDPOINT,
                           DEFAULT_LOCAL_ENDPOINT, GENAI_DEFAULT_API_KEY,
                           GENAI_DEFAULT_MODEL, GENAI_NO_MATCH_MESSAGE,
                           MODELS_EMPTY_MESSAGE, PROVIDERS,
                           fetch_models as fetch_models_request, filter_genai_models,
                           local_endpoint, looks_like_gpt_model, request_reply,
                           resolve_genai_model, validate_model_for_send)

# A quiet request is acted on only when the whole message is one of these
# exact, affirmative phrasings.  This is intentionally a closed list: no
# substring, prefix or fuzzy match may enter the local action path.  Each
# entry is a complete sentence; the everyday "吧"/"一会"/"一会儿" variants
# ask for the same quiet action rather than a different one.
QUIET_COMMANDS = (
    '安静陪我',
    '先安静陪我',
    '安静陪我吧',
    '先安静陪我吧',
    '安静陪我一会',
    '先安静陪我一会',
    '安静陪我一会吧',
    '先安静陪我一会吧',
    '安静陪我一会儿',
    '先安静陪我一会儿',
    '安静陪我一会儿吧',
    '先安静陪我一会儿吧',
)
# Ordinary spaces (including full-width and tab) may be sprinkled through the
# phrase.  At most one trailing sentence period or exclamation mark is
# ignored; "。。", "！？" or any question mark stays ambiguous.
_QUIET_SPACES = frozenset(chr(code) for code in (0x20, 0x09, 0x3000, 0xA0))
_QUIET_ENDINGS = '。.．！!'


def is_quiet_command(text):
    """Return True only for a wholly explicit, affirmative quiet request.

    The whole message must, after ordinary spaces and at most one trailing
    period/exclamation mark are ignored, equal one entry from the closed
    command set.  Negated ("不要安静陪我"), questioning ("安静陪我吗？"),
    qualified phrases, additional requests ("安静陪我然后帮我开程序") and
    multiple sentences therefore never match and are handled as ordinary chat.
    """
    if not isinstance(text, str):
        return False
    candidate = ''.join(ch for ch in text.strip() if ch not in _QUIET_SPACES)
    if candidate and candidate[-1] in _QUIET_ENDINGS:
        candidate = candidate[:-1]
    return candidate in QUIET_COMMANDS


class ConversationDialog(QDialog):
    completed = Signal(int, str, str)
    models_ready = Signal(int, object, str)

    def __init__(self, ctrl, path, parent=None):
        super().__init__(parent)
        self.setFont(QFont('Microsoft YaHei UI', 10))
        self.ctrl = ctrl
        had_saved_config = Path(path).exists()
        self.session = Conversation(path)
        if not had_saved_config:
            # A brand-new conversation starts on the ShanghaiTech GenAI proxy
            # preset.  Existing records keep their saved provider and settings.
            self.session.provider = 'genai'
            self.session.endpoint = DEFAULT_GENAI_ENDPOINT
        self.provider = self.session.provider if self.session.provider in PROVIDERS else 'local'
        # Window-only migration.  Nothing here saves or rewrites the existing
        # file: an old GPT choice is retired for this window, while the original
        # record stays on disk as historical evidence until the user acts.
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
                # An already-allowed saved name stays as saved in this window;
                # only invalid GPT/unlisted choices are replaced.
                self.session.model = self.session.model.strip()
        elif looks_like_gpt_model(self.session.model):
            self.session.model = ''
            self.migration_notice = (
                '旧保存的模型带有 GPT/OpenAI 命名，已从窗口清空；请选择国内开源模型，'
                '或填写自己部署的模型名称。原文件未修改。'
            )
        self.busy = False
        self.generation = 0
        # Generation of the request that currently owns the busy state.  A late
        # reply from an older generation is ignored outright: it must neither
        # write history nor re-enable Send while a newer request is pending.
        self.active_generation = None
        self.pending_text = None
        # Model discovery has its own generation and busy flag.  Closing,
        # switching services or editing the connection invalidates it so a
        # late list cannot touch a newer window/model choice.
        self.models_generation = 0
        self.model_busy = False
        self.models_request = None
        self.available_models = []
        self.setWindowTitle('和祀聊聊 · 自由聊聊')
        self.resize(640, 700)
        layout = QVBoxLayout(self)
        hint = QLabel('普通本机模型只连接本机。上科大 GenAI 反代会把本次文字、偏好与最近 12 轮经本机反代送到学校模型；'
                      '对话记录只保存在本机，不参与设备同步，也不代表本机服务本身离线。')
        hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout()
        self.service = QComboBox()
        self.service.addItem('普通本机模型', 'local')
        self.service.addItem('上科大 GenAI 反代', 'genai')
        self.service.setCurrentIndex(1 if self.provider == 'genai' else 0)
        self.endpoint = QLineEdit(self.session.endpoint)
        self.model = QLineEdit(self.session.model)
        self.model.setPlaceholderText('服务中已加载的模型名称；不要猜测')
        self.model_completer = QCompleter([], self.model)
        self.model.setCompleter(self.model_completer)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('普通本机可留空；只保留在内存')
        self.api_key.setText(GENAI_DEFAULT_API_KEY if self.provider == 'genai' else '')
        self.get_models = QPushButton('获取模型')
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1); model_row.addWidget(self.get_models)
        self.profile = QTextEdit(); self.profile.setMaximumHeight(85)
        self.profile.setPlaceholderText('可选：希望怎样称呼你、喜欢的话题、希望怎样陪伴。不必填写私人信息。')
        self.profile.setPlainText(self.session.profile)
        form.addRow('服务预设', self.service)
        form.addRow('本机接口', self.endpoint)
        form.addRow('反代访问口令', self.api_key)
        form.addRow('模型', model_row)
        form.addRow('管理员偏好', self.profile); layout.addLayout(form)
        self.transcript = QTextBrowser(); layout.addWidget(self.transcript, 1)
        self.input = QTextEdit(); self.input.setMaximumHeight(100)
        self.input.setPlaceholderText('想说的话……'); layout.addWidget(self.input)
        self.status = QLabel(
            self.migration_notice
            or '不会自动获取模型或发送对话。口令只留在内存，不会读取其他软件的模型或口令。'
        )
        self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        self.clear = QPushButton('清除对话'); self.clear.clicked.connect(self.clear_turns)
        self.quiet_button = QPushButton('安静陪我')
        # Never let Enter in the input field trigger a quiet action by becoming
        # the dialog's implicit default button; the explicit click still works.
        self.quiet_button.setAutoDefault(False)
        self.quiet_button.setDefault(False)
        self.quiet_button.setToolTip('立即在本机进入安静陪伴：靠边坐下、停止自主走动，不会联网。')
        self.quiet_button.clicked.connect(self.quiet_companion)
        self.send = QPushButton('发送'); self.send.clicked.connect(self.submit)
        row.addWidget(self.clear); row.addWidget(self.quiet_button)
        row.addStretch(); row.addWidget(self.send); layout.addLayout(row)
        self.completed.connect(self.finished_reply, Qt.ConnectionType.QueuedConnection)
        self.models_ready.connect(self.finished_models, Qt.ConnectionType.QueuedConnection)
        self.service.currentIndexChanged.connect(self.preset_changed)
        self.get_models.clicked.connect(self.fetch_models)
        self.endpoint.textEdited.connect(self.connection_edited)
        self.api_key.textEdited.connect(self.connection_edited)
        self.render()

    def render(self):
        # Plain text prevents model-provided HTML, links or remote images executing.
        self.transcript.setPlainText('\n\n'.join('管理员：' + t['user'] + '\n祀：' + t['assistant'] for t in self.session.turns))
        self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())

    def current_provider(self):
        provider = self.service.currentData()
        return provider if provider in PROVIDERS else 'local'

    def clear_model_completer(self):
        old = self.model_completer
        empty = QCompleter([], self.model)
        self.model.setCompleter(empty)
        self.model_completer = empty
        if old is not None:
            try: old.deleteLater()
            except RuntimeError: pass

    def invalidate_models(self):
        """Abandon any pending/previous model list without touching text."""
        self.models_generation += 1
        self.model_busy = False
        self.models_request = None
        self.available_models = []
        self.get_models.setEnabled(True)
        try: self.clear_model_completer()
        except (RuntimeError, TypeError): pass

    def preset_changed(self, _index):
        provider = self.current_provider()
        self.provider = provider
        self.session.provider = provider
        self.migration_notice = ''
        pending = self.model_busy
        self.invalidate_models()
        endpoint = DEFAULT_GENAI_ENDPOINT if provider == 'genai' else DEFAULT_LOCAL_ENDPOINT
        self.endpoint.setText(endpoint)
        self.api_key.setText(GENAI_DEFAULT_API_KEY if provider == 'genai' else '')
        if provider == 'genai':
            # Switching to GenAI always starts from the verified domestic
            # default; an old GPT/unknown name never survives the switch.
            self.model.setText(GENAI_DEFAULT_MODEL)
            self.session.model = GENAI_DEFAULT_MODEL
        else:
            self.model.clear()
            self.session.model = ''
        self.session.endpoint = endpoint
        if pending:
            self.status.setText('已切换服务，迟到的模型列表不会更新窗口。需要时请重新获取模型。')
        elif provider == 'genai':
            self.status.setText(
                '已切换到上科大 GenAI 反代，默认模型为 DeepSeek-V4.1-Flash。'
                '需要时点击“获取模型”，查看已配置的国内开源模型。'
            )
        else:
            self.status.setText(
                '已切换到普通本机模型。请填写自己部署的国内开源模型名称；不会连接学校服务。'
            )

    def connection_edited(self, _text=''):
        if self.model_busy or self.available_models:
            pending = self.model_busy
            self.invalidate_models()
            if pending:
                self.status.setText('连接设置已修改，已忽略上一次模型查询。')

    def fetch_models(self):
        if self.model_busy:
            return
        try:
            endpoint = local_endpoint(self.endpoint.text())
            api_key = self.api_key.text()
        except ConversationError as exc:
            self.status.setText(str(exc)); return
        token = self.models_generation + 1
        self.models_generation = token
        self.model_busy = True
        provider = self.current_provider()
        self.provider = provider
        self.models_request = (provider, endpoint)
        self.available_models = []
        try: self.clear_model_completer()
        except (RuntimeError, TypeError): pass
        self.get_models.setEnabled(False)
        self.status.setText('正在向本机服务获取模型列表；不会发送对话。')

        def work():
            try:
                models, error = fetch_models_request(endpoint, api_key), ''
                if not error and provider == 'genai':
                    # fetch_models stays a generic OpenAI-compatible protocol
                    # function.  The GenAI allow-list is applied here, in the
                    # dialog, and an empty result is never replaced by GPT.
                    models = filter_genai_models(models)
                    if not models:
                        error = GENAI_NO_MATCH_MESSAGE
            except ConversationError as exc:
                models, error = [], str(exc)
                if provider == 'genai' and error == MODELS_EMPTY_MESSAGE:
                    # An empty list and a list of only non-allowed models get
                    # the same friendly domestic-model notice, never GPT.
                    error = GENAI_NO_MATCH_MESSAGE
            except Exception: models, error = [], '获取模型失败，请检查本机服务后重试。'
            try: self.models_ready.emit(token, models, error)
            except (RuntimeError, TypeError): pass
        try:
            threading.Thread(target=work, daemon=True, name='si-models').start()
        except RuntimeError:
            self.model_busy = False; self.models_request = None
            self.get_models.setEnabled(True)
            self.status.setText('无法启动模型查询线程，请重试。')

    def finished_models(self, token, models, error):
        try:
            self.apply_models(token, models, error)
        except Exception:
            if token == self.models_generation:
                self.model_busy = False; self.models_request = None
                try: self.get_models.setEnabled(True)
                except RuntimeError: pass
                try: self.status.setText('获取模型暂时未能完成，请重试。')
                except RuntimeError: pass

    def apply_models(self, token, models, error):
        if token != self.models_generation:
            # Closed, switched service, or replaced by a newer query.  Do not
            # touch the model field, completion popup or status.
            return
        self.model_busy = False
        self.models_request = None
        self.get_models.setEnabled(True)
        provider = self.current_provider()
        if error:
            self.status.setText(error); return
        if not isinstance(models, list) or not models:
            self.status.setText(
                GENAI_NO_MATCH_MESSAGE if provider == 'genai'
                else '本机接口没有返回可用的模型名称。'
            ); return
        if provider == 'genai':
            # Defence in depth: even a directly delivered/stale list cannot put
            # GPT or an unverified name into the GenAI completion popup.
            models = filter_genai_models(models)
            if not models:
                self.available_models = []
                try: self.clear_model_completer()
                except (RuntimeError, TypeError): pass
                self.status.setText(GENAI_NO_MATCH_MESSAGE); return
        self.available_models = list(models)
        if provider == 'genai':
            # If the current choice matches a service-returned verified model,
            # display the service's own canonical spelling.  Unmatched manual
            # text is left alone and will be refused by submit().
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
            try: old.deleteLater()
            except RuntimeError: pass
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

    def clear_turns(self):
        previous = self.session.turns
        self.session.turns = []
        try:
            self.session.save()
        except ConversationError as exc:
            self.session.turns = previous; self.status.setText(str(exc)); return
        except Exception:
            # A slot must not let an unexpected error escape into Qt, and a
            # failed save must not leave the in-memory history cleared.
            self.session.turns = previous; self.status.setText('对话暂时未能清除，请重试。'); return
        self.generation += 1
        self.render(); self.status.setText('本机对话已清除，管理员偏好保留。')

    def quiet_companion(self):
        """Explicit button: enter quiet companionship without any request."""
        self.enter_quiet()

    def enter_quiet(self, source_text=None):
        """Apply the existing local quiet-dock state, never a model reply.

        ``Controller.contact('quiet')`` already sets quiet_mode, docks at the
        screen edge, sits down and emits its single reply/speech.  This dialog
        only asks for that action: it adds no second ``say`` call and never
        touches the network.  ``source_text`` is the exact command the user
        submitted, so only that text is cleared; a button click leaves drafts.
        Once the action succeeds, a reply still in flight is abandoned so a
        late network result cannot speak or reopen a finished conversation.
        """
        try:
            if source_text is not None and self.input.toPlainText() == source_text:
                self.input.clear()
        except RuntimeError:
            pass
        contact = getattr(self.ctrl, 'contact', None)
        if not callable(contact):
            try: self.status.setText('当前没有可用的桌宠，未能进入安静陪伴。')
            except RuntimeError: pass
            return False
        try:
            contact('quiet')
        except Exception:
            # A Qt slot must not let an unexpected error escape into Qt.  The
            # action did not succeed, so an already-issued chat request keeps
            # running and its reply may still be committed normally.
            try: self.status.setText('安静陪伴暂时未能生效，请从桌宠菜单再试。')
            except RuntimeError: pass
            return False
        # The local action has succeeded, so any reply issued before it is now
        # stale: it must neither write history nor speak nor reopen Send over a
        # newer interaction.  A quiet request is an answer in itself, so this
        # invalidation happens only after contact('quiet') returned without
        # error; failure returned above and left the request untouched.
        abandoned = self.abandon_pending_reply()
        try:
            if abandoned:
                self.status.setText(
                    '已安静陪你：她到屏幕边缘坐下，停止自主走动。这是本机操作，没有联网。'
                    '之前等待的回复已作废，不会再写入记录或说话。'
                )
            else:
                self.status.setText(
                    '已安静陪你：她到屏幕边缘坐下，停止自主走动。这是本机操作，没有联网。'
                )
        except RuntimeError:
            pass
        return True

    def abandon_pending_reply(self):
        """Forget an in-flight chat reply and release its busy state.

        The worker thread itself cannot be cancelled; its result is simply
        ignored by the generation guard in ``apply_reply``.  Returns True when
        there was a pending request to abandon.
        """
        if not self.busy and self.active_generation is None:
            return False
        self.generation += 1
        self.active_generation = None
        self.pending_text = None
        self.busy = False
        try: self.send.setEnabled(True)
        except RuntimeError: pass
        return True

    def submit(self):
        try:
            typed_text = self.input.toPlainText()
        except RuntimeError:
            return
        # A wholly explicit quiet request is a local action.  It is handled
        # before the busy guard and never reaches endpoint/model validation or
        # a worker thread; ambiguous or negated text falls through as chat.
        if is_quiet_command(typed_text):
            self.enter_quiet(typed_text)
            return
        if self.busy: return
        try:
            text = self.input.toPlainText()
            endpoint = local_endpoint(self.endpoint.text())
            provider = self.current_provider()
            # Enforce the model policy before saving any new configuration or
            # starting the worker thread.  Completion popups are only a hint;
            # a manually typed GPT/unknown GenAI name must be refused here.
            model = validate_model_for_send(
                provider, self.model.text(),
                self.available_models if provider == 'genai' else (),
            )
            if self.model.text() != model:
                self.model.setText(model)
            profile = self.profile.toPlainText()
            if len(profile) > 2000: raise ConversationError('管理员偏好最多 2000 字。')
            api_key = self.api_key.text()
            self.provider = provider
            self.session.provider = provider
            self.session.endpoint, self.session.model, self.session.profile = endpoint, model, profile
            messages = self.session.messages(text)
            self.session.save()
        except ConversationError as exc:
            self.status.setText(str(exc)); return
        except Exception:
            # Same rule as above: report instead of aborting the desktop app.
            self.status.setText('对话暂时未能完成，请重试。'); return
        self.busy = True; self.send.setEnabled(False)
        self.active_generation = self.generation
        self.pending_text = text
        self.status.setText('正在等祀回应；你可以继续使用桌宠。')
        generation = self.generation

        def work():
            try: reply, error = request_reply(endpoint, model, messages, api_key), ''
            except ConversationError as exc: reply, error = '', str(exc)
            except Exception: reply, error = '', '对话暂时未能完成，请重试。'
            try: self.completed.emit(generation, reply, error)
            except (RuntimeError, TypeError): pass
        try:
            threading.Thread(target=work, daemon=True, name='si-local-chat').start()
        except RuntimeError:
            self.busy = False; self.active_generation = None; self.pending_text = None
            self.send.setEnabled(True)
            self.status.setText('无法启动对话线程，请重试。')

    def finished_reply(self, generation, reply, error):
        # Queued signal slot.  Never let a Python error escape into Qt: depending
        # on the Qt/PySide build an exception raised from a slot can terminate
        # the whole process instead of being reported like a normal failure.
        try:
            self.apply_reply(generation, reply, error)
        except Exception:
            if self.active_generation == generation:
                self.active_generation = None; self.pending_text = None
                self.busy = False
                try: self.send.setEnabled(True)
                except RuntimeError: pass
            try: self.status.setText('对话暂时未能完成，请重试。')
            except RuntimeError: pass

    def apply_reply(self, generation, reply, error):
        if generation != self.active_generation:
            # Reply from a request abandoned by closing (or superseded before
            # it could finish).  It must not touch the UI of a newer request.
            return
        text, self.pending_text = self.pending_text, None
        self.active_generation = None
        self.busy = False; self.send.setEnabled(True)
        if generation != self.generation:
            return
        if error:
            self.status.setText(error); return
        if text is None: return
        try: self.session.commit(text, reply)
        except ConversationError as exc: self.status.setText(str(exc)); return
        if self.input.toPlainText() == text: self.input.clear()
        self.render(); self.status.setText('已保存到本机。')
        # Long replies stay in the dialog; avoid a large obstructive desktop bubble.
        if len(reply) <= 160: self.ctrl.say(reply, True)

    def closeEvent(self, event):
        # A late network result must not speak or recreate a cleared/closed turn.
        self.generation += 1
        models_pending = self.model_busy
        self.invalidate_models()
        if self.busy:
            # urllib cannot cancel an in-flight request, so the request keeps
            # running; its result is abandoned.  Reopening the dialog must be
            # usable at once instead of showing a disabled Send until the slow
            # request returns (possibly 90 s later).
            self.busy = False
            self.active_generation = None
            self.pending_text = None
            self.send.setEnabled(True)
            self.status.setText('已停止等待这次回复；结果不会写入记录。')
        elif models_pending:
            self.status.setText('已停止等待模型列表；迟到的结果不会更新窗口。')
        super().closeEvent(event)
