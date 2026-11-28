import math
import time
import html
from PySide6.QtCore import Qt,QTimer,QUrl,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QLinearGradient,QFont,QDesktopServices
from PySide6.QtWidgets import (QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,
    QGridLayout,QStackedWidget,QSlider,QCheckBox,QComboBox,QScrollArea,QTextBrowser,
    QProgressBar,QFileDialog,QMessageBox,QButtonGroup,QLineEdit,QMenu,QApplication)
from .storage import app_root,data_root
from .companion import CONTACT_OPTIONS
from .genai_status_widget import GenAIStatusWidget
from .theme import bind_theme,panel_stylesheet,menu_stylesheet,theme_colors
from . import native,__version__

STYLE=panel_stylesheet(False)

def label(text,kind=None,wrap=False):
    w=QLabel(text)
    if kind:w.setObjectName(kind)
    w.setWordWrap(wrap)
    return w

def button(text,fn,primary=False,name=None):
    w=QPushButton(text)
    if primary:w.setObjectName('primary')
    elif name:w.setObjectName(name)
    w.clicked.connect(fn)
    return w

def card():
    w=QFrame();w.setObjectName('card');return w

class Preview(QWidget):
    def __init__(self,ctrl):
        super().__init__();self.ctrl=ctrl;self.setMinimumSize(310,350)
        self.timer=QTimer(self);self.timer.timeout.connect(self.update);self.timer.start(80)
    def frame_layout(self,state,index):
        # Widget geometry is logical, whereas a high-DPI pixmap's size is physical.
        # Centre the visible figure (not its transparent canvas) in the picture area.
        bounds=QRectF(self.ctrl.sprites.mask(state,index,1).boundingRect())
        area=QRectF(16,48,max(1,self.width()-32),max(1,self.height()-96))
        scale=min(area.width()/max(1,bounds.width()),area.height()/max(1,bounds.height()))
        pix=self.ctrl.sprites.pixmap(state,index,scale,self.devicePixelRatioF())
        position=QPointF(area.center().x()-bounds.center().x()*scale,area.center().y()-bounds.center().y()*scale)
        visible=QRectF(position.x()+bounds.x()*scale,position.y()+bounds.y()*scale,bounds.width()*scale,bounds.height()*scale)
        return pix,position,visible,area
    def _portrait_mood(self,state):
        if state in ('sleeping',):return 'sleepy'
        if state in ('waving','running','jumping'):return 'happy'
        if state in ('failed',):return 'troubled'
        if state in ('review',):return 'thinking'
        return 'neutral'
    def paintEvent(self,event):
        colors=theme_colors()
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g=QLinearGradient(0,0,self.width(),self.height());g.setColorAt(0,QColor(colors['preview_grad0']));g.setColorAt(1,QColor(colors['preview_grad1']))
        p.setBrush(g);p.setPen(QColor(colors['border_soft']));p.drawRoundedRect(QRectF(.5,.5,self.width()-1,self.height()-1),20,20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*colors['preview_glow']));p.drawEllipse(self.width()-190,-65,260,260);p.drawEllipse(-85,self.height()-145,230,230)
        state,index=self.ctrl.animation()
        pix,position,_,_=self.frame_layout(state,index)
        p.drawPixmap(position,pix)
        # Small corner bust portrait for readable expression at any window size
        if hasattr(self.ctrl.sprites,'portrait'):
            bust=self.ctrl.sprites.portrait(self._portrait_mood(state),56)
            if not bust.isNull():
                bx,by=self.width()-70,self.height()-70
                p.setBrush(QColor(colors['card_bg']));p.setPen(QColor(colors['gold_accent']))
                p.drawRoundedRect(bx-3,by-3,62,62,12,12)
                p.drawPixmap(bx,by,56,56,bust)
        p.setPen(QColor(colors['preview_text']));p.setFont(QFont('Segoe UI',9));p.drawText(21,30,'SI  /  ENDFIELD COMPANION')
        p.setFont(QFont('Microsoft YaHei UI',9));p.drawText(21,self.height()-18,'随心所欲 · 也会为你停留')

class Panel(QWidget):
    def __init__(self,ctrl,pet,voice=None):
        super().__init__();self.ctrl=ctrl;self.pet=pet;self.voice=voice;self.setObjectName('panel')
        self.setWindowTitle('祀 · 桌面伙伴');self.setWindowIcon(ctrl.sprites.icon())
        self.setMinimumSize(900,680);self.resize(1020,760)
        outer=QHBoxLayout(self);outer.setSpacing(0);outer.setContentsMargins(0,0,0,0)
        side=QFrame();side.setObjectName('sidebar');side.setFixedWidth(216)
        sl=QVBoxLayout(side);sl.setContentsMargins(22,28,22,24);sl.setSpacing(7)
        brand_row=QHBoxLayout();brand_row.setSpacing(10)
        seal=QLabel('祀');seal.setObjectName('seal');seal.setFixedSize(44,44);seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_row.addWidget(seal)
        brand_meta=QVBoxLayout();brand_meta.setSpacing(1)
        brand_meta.addWidget(label('祀','subhead'));brand_meta.addWidget(label('桌 面 伙 伴','eyebrow'))
        brand_row.addLayout(brand_meta);brand_row.addStretch()
        sl.addLayout(brand_row);sl.addSpacing(30)
        self.stack=QStackedWidget();self.nav=QButtonGroup(self);self.nav.setExclusive(True)
        for i,text in enumerate(['相伴此刻','行为与显示','陪伴手记','关于祀']):
            b=QPushButton(text);b.setObjectName('nav');b.setCheckable(True)
            self.nav.addButton(b,i);sl.addWidget(b);b.clicked.connect(lambda checked=False,n=i:self.stack.setCurrentIndex(n))
        self.nav.button(0).setChecked(True);sl.addStretch()
        sl.addWidget(button('找回桌宠',pet.recover));sl.addSpacing(12)
        sl.addWidget(label('本地陪伴 · 存档随行','muted'));sl.addWidget(label(f'Windows · {__version__} 正式版','muted',True))
        outer.addWidget(side)
        container=QWidget();cl=QVBoxLayout(container);cl.setContentsMargins(32,26,32,22)
        cl.addWidget(self.stack);outer.addWidget(container,1)
        self.stack.addWidget(self.home());self.stack.addWidget(self.settings());self.stack.addWidget(self.journal());self.stack.addWidget(self.about())
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1000)
        ctrl.changed.connect(self.refresh);bind_theme(self,self.apply_theme);self.refresh()

    def apply_theme(self):
        self.setStyleSheet(panel_stylesheet())
        self._history_html=None
        if hasattr(self,'contact_menu'):self.contact_menu.setStyleSheet(menu_stylesheet())
        if hasattr(self,'history'):self.refresh()
        if hasattr(self,'preview'):self.preview.update()

    def heading(self,title,subtitle):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,0,0,10);l.setSpacing(6)
        l.addWidget(label(title,'headline'));l.addWidget(label(subtitle,'muted',True));return w

    def home(self):
        page=QWidget();lay=QVBoxLayout(page);lay.setContentsMargins(0,0,0,0);lay.setSpacing(16)
        header=QHBoxLayout();header.addWidget(self.heading('管理员，今天也一起吧。','不必时时说话。你认真做事，她自在停留。'),1)
        header.addWidget(button('唤到身边',self.pet.recover,True));lay.addLayout(header)
        middle=QHBoxLayout();middle.setSpacing(20);self.preview=Preview(self.ctrl);middle.addWidget(self.preview,1)
        right=QVBoxLayout();right.setSpacing(10)

        status_card=card();scl=QVBoxLayout(status_card);scl.setContentsMargins(16,12,16,12);scl.setSpacing(6)
        srow=QHBoxLayout();srow.addWidget(label('她的此刻','subhead'));srow.addStretch()
        self.mood_badge=label('心情平稳','muted');srow.addWidget(self.mood_badge)
        scl.addLayout(srow)
        self.state_text=label('正在看看四周','muted');scl.addWidget(self.state_text)
        self.focus_button=button('陪我专注 · 25 分钟',lambda:self.ctrl.focus(),True);scl.addWidget(self.focus_button)
        self.focus_text=label('专注时，安静陪在旁边。','muted',True);scl.addWidget(self.focus_text)
        right.addWidget(status_card)

        chat_card=card();ccl=QVBoxLayout(chat_card);ccl.setContentsMargins(16,12,16,12);ccl.setSpacing(8)
        ccl.addWidget(label('和祀聊聊','subhead'))
        ccl.addWidget(label('支持本机模型、DeepSeek 官方与自定义学校接口','muted',True))
        chat_row=QHBoxLayout();chat_row.setSpacing(8)
        chat_row.addWidget(button('自由聊聊（文字）',self.open_conversation,name='actionTile'),1)
        chat_row.addWidget(button('多模态聊聊（图文）',self.open_multimodal_chat,name='actionTile'),1)
        ccl.addLayout(chat_row)
        right.addWidget(chat_card)

        act_card=card();acl=QVBoxLayout(act_card);acl.setContentsMargins(16,12,16,12);acl.setSpacing(8)
        acl.addWidget(label('日常互动与姿态','subhead'))
        grid=QGridLayout();grid.setSpacing(8)
        actions=[
            ('散散步',lambda:self.ctrl.perform('walk')),
            ('坐一会儿',lambda:self.ctrl.perform('sit')),
            ('打个盹',lambda:self.ctrl.perform('sleep')),
            ('摸摸头',self.ctrl.pat),
            ('送一份点心',self.ctrl.feed),
            ('靠边坐好',self.ctrl.dock),
        ]
        for idx,(title,fn) in enumerate(actions):
            grid.addWidget(button(title,fn,name='actionTile'),idx//3,idx%3)
        acl.addLayout(grid)
        contact=button('和祀说一句（本地话题）',lambda:None)
        self.contact_menu=QMenu(contact);self.contact_menu.setStyleSheet(menu_stylesheet())
        for title,topic in CONTACT_OPTIONS:
            self.contact_menu.addAction(title,lambda checked=False,t=topic:self.ctrl.contact(t))
        contact.setMenu(self.contact_menu);acl.addWidget(contact)
        self.contact_reply_text=label('选一个话题，她会认真听。','muted',True)
        acl.addWidget(self.contact_reply_text)
        self.ctrl.contact_reply.connect(self.contact_reply_text.setText)
        right.addWidget(act_card)

        right.addStretch()
        middle.addLayout(right,1);lay.addLayout(middle,1)
        stats=QHBoxLayout();stats.setSpacing(12);self.stat_labels=[]
        for title in ['亲密度','一起专注','相伴时间']:
            c=card();l=QVBoxLayout(c);l.setContentsMargins(18,12,18,12);l.addWidget(label(title,'muted'))
            value=label('0','subhead');l.addWidget(value);self.stat_labels.append(value);stats.addWidget(c)
        lay.addLayout(stats)
        lay.addWidget(label('拖动她到任意位置，松开会自然下落。右键有分组动作菜单；双击可以轻轻跳起。','muted',True))
        return page

    def open_conversation(self):
        from .conversation_ui import ConversationDialog
        from .conversation import ConversationError
        try:
            if not hasattr(self,'conversation_dialog'):
                self.conversation_dialog=ConversationDialog(self.ctrl,self.ctrl.store.path.parent/'conversation.json',self)
            self.conversation_dialog.show();self.conversation_dialog.raise_();self.conversation_dialog.activateWindow()
        except ConversationError as exc:
            QMessageBox.warning(self,'对话暂不可用',str(exc))

    def open_multimodal_chat(self):
        from .chat_ui import MultimodalDialog
        from .conversation import ConversationError
        try:
            if not hasattr(self, 'multimodal_dialog'):
                self.multimodal_dialog = MultimodalDialog(
                    self.ctrl,
                    self.ctrl.store.path.parent / 'multimodal_conversation.json',
                    self
                )
            self.multimodal_dialog.show()
            self.multimodal_dialog.raise_()
            self.multimodal_dialog.activateWindow()
        except ConversationError as exc:
            QMessageBox.warning(self, '多模态聊聊暂不可用', str(exc))

    def settings(self):
        body=QWidget();l=QVBoxLayout(body);l.setContentsMargins(0,0,12,0);l.setSpacing(14)
        l.addWidget(self.heading('由你决定，她的节奏。','设置即时生效，并随便携文件夹保存。'))
        self.checks={};self.sliders={};self.value_labels={}

        # 1. 外观与显示大小（带 S / M / L 预设与实时尺寸显示）
        appear=card();al=QVBoxLayout(appear);al.setContentsMargins(18,14,18,14);al.setSpacing(10)
        al.addWidget(label('外观与显示','subhead'))
        row=QHBoxLayout();row.addWidget(label('显示大小（桌宠画布尺寸）'))
        row.addStretch();v=label('','muted');row.addWidget(v);al.addLayout(row)
        scale_slider=QSlider(Qt.Orientation.Horizontal);scale_slider.setRange(75,300)
        scale_slider.setValue(round(self.ctrl.store['scale']*100))
        scale_slider.valueChanged.connect(lambda val:self.setting('scale',val/100))
        self.sliders['scale']=scale_slider;self.value_labels['scale']=v;al.addWidget(scale_slider)
        preset_row=QHBoxLayout();preset_row.setSpacing(8)
        for ptitle,pval in [('小 · S (1.0×)',1.0),('标准 · M (2.0×)',2.0),('大 · L (3.0×)',3.0)]:
            pb=button(ptitle,lambda checked=False,s=pval:self.sliders['scale'].setValue(round(s*100)),name='chip')
            preset_row.addWidget(pb)
        preset_row.addStretch();al.addLayout(preset_row)
        for title,key in [('选中轮廓','outline'),('脚下轻影','shadow')]:
            cb=QCheckBox(title);cb.setChecked(self.ctrl.store[key])
            cb.toggled.connect(lambda value,k=key:self.setting(k,value));self.checks[key]=cb;al.addWidget(cb)
        l.addWidget(appear)

        # 2. 物理与动作参数（带清晰单位与说明）
        phys=card();pl=QVBoxLayout(phys);pl.setContentsMargins(18,14,18,14);pl.setSpacing(10)
        pl.addWidget(label('动作与物理参数','subhead'))
        for title,key,lo,hi,desc in [
            ('散步速度','speed',15,180,'桌宠在桌面和窗口边缘行走时的水平速度（像素/秒）'),
            ('重力强度','gravity',100,2500,'拖动松手或跳跃下落时的重力加速度（像素/秒²）'),
            ('下边界微调','bottom_offset',0,160,'抬高落脚基准线以适配底部工具栏（像素 px）'),
        ]:
            srow=QHBoxLayout();srow.addWidget(label(title));srow.addStretch()
            sv=label('','muted');srow.addWidget(sv);pl.addLayout(srow)
            sl=QSlider(Qt.Orientation.Horizontal);sl.setRange(lo,hi);sl.setValue(round(self.ctrl.store[key]))
            sl.valueChanged.connect(lambda val,k=key:self.setting(k,val))
            self.sliders[key]=sl;self.value_labels[key]=sv;pl.addWidget(sl)
            pl.addWidget(label(desc,'muted',True))
        l.addWidget(phys)

        # 3. 桌面行为与交互模式（分组陈列）
        behav=card();bl=QVBoxLayout(behav);bl.setContentsMargins(18,14,18,14);bl.setSpacing(4)
        bl.addWidget(label('自主行为与互动','subhead'))
        for title,key in [
            ('自动散步与随机小动作','wander'),
            ('眼神跟随鼠标','cursor_follow'),
            ('显示陪伴短句气泡','bubbles'),
            ('安静陪伴：停在桌边，降低存在感','quiet_mode'),
            ('专注时自动靠边','auto_dock'),
        ]:
            cb=QCheckBox(title);cb.setChecked(self.ctrl.store[key]);cb.toggled.connect(lambda value,k=key:self.setting(k,value));self.checks[key]=cb;bl.addWidget(cb)
        bl.addSpacing(8);bl.addWidget(label('窗口与系统集成','subhead'))
        click_label='鼠标穿透（从顶部菜单栏图标找回）' if native.MACOS else '鼠标穿透（从托盘找回；快捷键 Ctrl+Alt+S）'
        start_label='登录 macOS 时启动' if native.MACOS else ('登录 Windows 时启动' if native.WINDOWS else '开机登录时启动')
        for title,key in [
            ('可站在普通窗口顶边','window_edges'),
            ('全屏应用时自动隐藏，退出全屏后恢复','hide_fullscreen'),
            ('始终在其他窗口上方','topmost'),
            (click_label,'clickthrough'),
            ('手动控制（选中桌宠后使用方向键）','manual'),
            (start_label,'auto_start'),
        ]:
            cb=QCheckBox(title);cb.setChecked(self.ctrl.store[key]);cb.toggled.connect(lambda value,k=key:self.setting(k,value));self.checks[key]=cb;bl.addWidget(cb)
        l.addWidget(behav)

        voice_card=card();vl=QVBoxLayout(voice_card);vl.setContentsMargins(18,14,18,14)
        vl.addWidget(label('VoiceStudio · 智能语音与动作声','subhead'))
        voice_auto=QCheckBox('智能语音：接入耳机且未打开 Apex / 彩六等本地游戏时默认同时输出语音与文本')
        voice_auto.setChecked(self.ctrl.store['voice_auto_headphone']);voice_auto.toggled.connect(lambda value:self.setting('voice_auto_headphone',value))
        self.checks['voice_auto_headphone']=voice_auto;vl.addWidget(voice_auto)
        voice_toggle=QCheckBox('未插耳机时也强制外放语音（运行本地游戏时仍自动静音避让）')
        voice_toggle.setChecked(self.ctrl.store['voice_enabled']);voice_toggle.toggled.connect(lambda value:self.setting('voice_enabled',value))
        self.checks['voice_enabled']=voice_toggle;vl.addWidget(voice_toggle)
        self.voice_env_label=label(native.voice_output_allowed(self.ctrl.store)[1],'muted',True);vl.addWidget(self.voice_env_label)
        vl.addWidget(label('支持拟声词（哼、咳咳、噗嗤等）与动作音效（执笔落墨、尾巴盖戳、推过热茶、敲桌面等）；连接失败时继续显示文字，绝不切换为其他声线。','muted',True))
        profile_row=QHBoxLayout();profile_row.addWidget(label('声线 ID'))
        self.voice_profile=QLineEdit(str(self.ctrl.store['voice_profile_id']))
        self.voice_profile.editingFinished.connect(lambda:self.setting('voice_profile_id',self.voice_profile.text().strip()))
        profile_row.addWidget(self.voice_profile,1);vl.addLayout(profile_row)
        games_row=QHBoxLayout();games_row.addWidget(label('附加避让进程'))
        self.voice_games=QLineEdit(str(self.ctrl.store['voice_blocked_games']))
        self.voice_games.setPlaceholderText('已内置 Apex / 彩六 / CS2 / 瓦罗兰特 / 永劫等；可填额外 exe（逗号分隔）')
        self.voice_games.editingFinished.connect(lambda:self.setting('voice_blocked_games',self.voice_games.text().strip()))
        games_row.addWidget(self.voice_games,1);vl.addLayout(games_row)
        volume_row=QHBoxLayout();volume_row.addWidget(label('语音音量'))
        volume=QSlider(Qt.Orientation.Horizontal);volume.setRange(0,100);volume.setValue(round(self.ctrl.store['voice_volume']))
        volume.valueChanged.connect(lambda value:self.setting('voice_volume',value))
        volume_row.addWidget(volume,1);vl.addLayout(volume_row)
        audition=button('试听一句（含拟声词与动作声）',lambda:self.voice.speak('（祀执笔在宣纸上轻轻一划，推过一杯热茶）||哼，管理员，咳咳……我在呢。',force_audition=True) if self.voice else None)
        audition.setEnabled(self.voice is not None);vl.addWidget(audition)
        connect=button('连接本机 VoiceStudio 引擎',self.voice.start_backend if self.voice else lambda:None)
        connect.setEnabled(self.voice is not None);vl.addWidget(connect)
        self.voice_status=label('已开启耳机智能同步语音，接入耳机后自动就绪。','muted',True);vl.addWidget(self.voice_status)
        if self.voice:self.voice.status.connect(self.voice_status.setText)
        l.addWidget(voice_card)
        self.genai_status=GenAIStatusWidget()
        l.addWidget(self.genai_status)
        perf=card();pfl=QHBoxLayout(perf);pfl.setContentsMargins(18,14,18,14)
        pfl.addWidget(label('帧率上限'));fps=QComboBox();fps.addItems(['15','30','60']);fps.setCurrentText(str(int(self.ctrl.store['fps'])));fps.currentTextChanged.connect(lambda t:self.setting('fps',int(t)));pfl.addWidget(fps)
        pfl.addSpacing(20);pfl.addWidget(label('专注时长（分钟）'));minutes=QComboBox();minutes.addItems(['5','15','25','45','60']);minutes.setCurrentText(str(int(self.ctrl.store['focus_minutes'])));minutes.currentTextChanged.connect(lambda t:self.setting('focus_minutes',int(t)));pfl.addWidget(minutes);pfl.addStretch();l.addWidget(perf)
        l.addWidget(label('手动模式：← → 移动，↑ ↓ 切换动作，空格跳跃，Esc 返回自动。按键仅在桌宠获得焦点时响应。','muted',True))
        l.addStretch();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(body);return scroll

    def journal(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,0,0,0);l.setSpacing(15)
        l.addWidget(self.heading('一些小事，留在这里。','亲密度和相伴记录保存在本机，不需要账号。'))
        self.mood=QProgressBar();self.mood.setRange(0,100);self.mood.setTextVisible(False)
        l.addWidget(label('此刻心情','subhead'));l.addWidget(self.mood)
        self.history=QTextBrowser();self.history.setOpenExternalLinks(False);l.addWidget(self.history,1)
        row=QHBoxLayout();row.addWidget(button('打开存档文件夹',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_root())))))
        row.addWidget(button('导出陪伴存档',self.export_save));l.addLayout(row)
        if self.ctrl.store.sync_ledger is not None:
            l.addWidget(label('同步预览 · 仅由你手动交换事件包；不会同步桌宠位置与本机设置。','muted',True))
            sync_row=QHBoxLayout()
            sync_row.addWidget(button('导出同步事件包',self.export_sync))
            sync_row.addWidget(button('导入同步事件包',self.import_sync))
            l.addLayout(sync_row)
            l.addWidget(button('局域网配对与同步',self.open_lan_sync))
        return w

    def about(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,0,0,0);l.setSpacing(16)
        l.addWidget(self.heading('祀 / SI',f'桌面很小，也装得下片刻自在 · v{__version__}'))
        text=QTextBrowser();text.setOpenExternalLinks(True)
        text.setHtml(f'''<h2>独立的桌面伙伴（{html.escape(__version__)}）</h2><p>一款支持 Windows 与 Mac mini（Apple Silicon / Intel）的绿色便携跨端桌宠，并配套 iOS（PWA + SwiftUI）、Android 与 HarmonyOS 客户端。使用本地 3D 离线渲染动作帧组与表情头像，自动适配系统深浅色与水墨金玉视觉风格，基础陪伴完全离线运行；可选联网对话支持本机 Ollama、DeepSeek 官方 API 及自定义学校/第三方 OpenAI 兼容接口，也可搭配本机 VoiceStudio 合成语音。</p><h3>陪伴方式</h3><p>对管理员采用熟悉、放松而克制的互动。摸头、点心、专注计时、五个话题短句及日常动态反馈均由本地状态与预设驱动。安静陪伴与靠边坐好可在工作时减少打扰。</p><h3>适用范围</h3><p>Windows 10 / 11（64 位）、macOS 13+（Mac mini M 系列 / Intel），以及 iPhone / iPad（Safari 添加到主屏幕独立 PWA 或 SwiftUI 工程）。Windows 上支持普通窗口顶边停留与多显示器适配；macOS 使用 <code>~/Library/Application Support/SiDesktopPet</code> 保存便携与打包存档，iOS 可接入实时同步节点或直连 API。</p><h3>署名与许可</h3><p>行为设计参考 <a href="https://github.com/isHarryh/Ark-Pets">Ark-Pets</a>。代码：GPL-3.0。图形框架：PySide6 / Qt，LGPL-3.0。角色“祀”及《明日方舟：终末地》相关权利属于原权利人；3D 源模型仅供本地离线光栅化展示与非商业同人交流，不含商业授权或二次配布权利。</p>''')
        l.addWidget(text,1);l.addWidget(button('查看项目文件',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_root())))))
        return w

    def setting(self,key,value):
        if key=='quiet_mode':self.ctrl.set_quiet_mode(value);return
        if key=='auto_start':
            try:native.set_startup(value)
            except OSError as exc:QMessageBox.warning(self,'未能修改启动设置',str(exc));return
        if key=='scale':self.ctrl.sprites.cache.clear();self.ctrl.sprites.mask_cache.clear();self.ctrl.sprites.outline_cache.clear()
        self.ctrl.store[key]=value;self.ctrl.store.save()
        if key in {'voice_enabled','voice_auto_headphone'} and not self.ctrl.store['voice_enabled'] and not self.ctrl.store['voice_auto_headphone'] and self.voice:
            self.voice.stop()
        elif key in {'voice_enabled','voice_auto_headphone'} and self.voice and self.voice.effective_enabled(force_refresh=True):
            self.voice.start_backend()
        if key in {'scale','window_edges','bottom_offset'}:self.ctrl.refresh_platforms()
        self.ctrl.changed.emit()
        if key=='manual' and value:self.pet.activateWindow();self.pet.setFocus()

    def refresh(self):
        states={'idle':'正在看看四周','walk-right':'正在向右散步','walk-left':'正在向左散步','sitting':'在桌边坐一会儿','sleeping':'正安静打着盹','jumping':'轻轻腾空，又落下','waving':'正在向你招手','running':'灵光一闪，记下一笔','review':'认真端详着','failed':'好像被什么卡住了'}
        self.state_text.setText(states.get(self.ctrl.state,'自在相伴'))
        if hasattr(self,'mood_badge'):
            mood=int(self.ctrl.store['mood'])
            self.mood_badge.setText(f'心情 {mood} · '+('神采很好' if mood>=80 else ('心情平稳' if mood>=45 else '有些疲倦')))
        if self.ctrl.focus_end:
            sec=max(0,math.ceil(self.ctrl.focus_end-time.monotonic()));self.focus_button.setText('结束本次专注');self.focus_text.setText(f'还有 {sec//60:02d}:{sec%60:02d} · 她在这里')
        else:self.focus_button.setText(f'陪我专注 · {int(self.ctrl.store["focus_minutes"])} 分钟');self.focus_text.setText('专注时，安静陪在旁边。')
        self.stat_labels[0].setText(str(int(self.ctrl.store['affinity'])));self.stat_labels[1].setText(f'{int(self.ctrl.store["focus_sessions"])} 次');self.stat_labels[2].setText(f'{int(self.ctrl.store["total_seconds"]//60)} 分钟')
        for key,cb in self.checks.items():cb.blockSignals(True);cb.setChecked(self.ctrl.store[key]);cb.blockSignals(False)
        if hasattr(self,'voice_env_label'):
            self.voice_env_label.setText('当前环境：'+native.voice_output_allowed(self.ctrl.store)[1])
        for key,v in self.value_labels.items():
            if key=='scale':
                s=float(self.ctrl.store[key])
                v.setText(f'{s:.2f} 倍 ({round(192*s)}×{round(208*s)} px)')
            elif key=='speed':v.setText(f'{int(self.ctrl.store[key])} px/s')
            elif key=='gravity':v.setText(f'{int(self.ctrl.store[key])} px/s²')
            else:v.setText(f'{int(self.ctrl.store[key])} px')
        self.mood.setValue(int(self.ctrl.store['mood']))
        rows=self.ctrl.store['history'][-35:]
        meta_color=theme_colors()['journal_meta']
        content='<h3>相伴手记</h3>'+''.join(f'<p><span style="color:{meta_color}">{html.escape(str(r.get("at","")))}</span><br>{html.escape(str(r.get("text","")))}</p>' for r in reversed(rows) if isinstance(r,dict))
        if not rows:content+='<p>第一段相伴，从现在开始。</p>'
        if getattr(self,'_history_html',None)!=content:self.history.setHtml(content);self._history_html=content

    def export_save(self):
        filename,_=QFileDialog.getSaveFileName(self,'导出陪伴存档','祀-陪伴存档.json','JSON (*.json)')
        if filename:
            import json
            from pathlib import Path
            Path(filename).write_text(json.dumps(self.ctrl.store.values,ensure_ascii=False,indent=2),encoding='utf-8')

    def export_sync(self):
        filename,_=QFileDialog.getSaveFileName(self,'导出祀的同步事件包','祀-同步事件包.json','JSON (*.json)')
        if not filename:return
        from pathlib import Path
        try:
            with Path(filename).open('xb') as target:
                target.write(self.ctrl.store.sync_ledger.export())
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'未能导出同步事件包',str(exc))

    def open_lan_sync(self):
        try:
            from .sync_lan_ui import LanSync,LanSyncDialog
            if not getattr(self,'lan_manager',None):
                self.lan_manager=LanSync(self.ctrl,self)
                self.lan_dialog=LanSyncDialog(self.lan_manager,self)
            self.lan_dialog.show();self.lan_dialog.raise_();self.lan_dialog.activateWindow()
        except (ValueError,OSError,ImportError) as exc:
            QMessageBox.warning(self,'未能打开设备配对',str(exc))

    def import_sync(self):
        filename,_=QFileDialog.getOpenFileName(self,'导入祀的同步事件包','','JSON (*.json)')
        if not filename:return
        from pathlib import Path
        try:
            added=self.ctrl.store.import_sync_bundle(Path(filename).read_bytes())
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'未能导入同步事件包',str(exc));return
        self.ctrl.changed.emit();self.refresh()
        QMessageBox.information(self,'同步事件已合并',f'新增 {added} 条事件。')
    def hideEvent(self,event):
        if hasattr(self,'genai_status'):self.genai_status.cancel_check(update_status=False)
        super().hideEvent(event)
    def closeEvent(self,event):
        if getattr(self,'can_hide',True) and not QApplication.instance().property('si_quitting'):event.ignore();self.hide()
        else:super().closeEvent(event)
