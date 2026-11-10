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
from . import native,__version__

STYLE='''
QWidget{font-family:"Microsoft YaHei UI","Segoe UI";font-size:13px;color:#213f37;}
QWidget#panel{background:#eef3ee;} QFrame#sidebar{background:#fafcf8;border-right:1px solid #dce5db;}
QLabel#brand{font-size:33px;font-weight:650;color:#254b3b;} QLabel#muted{color:#7a8c80;font-size:12px;}
QLabel#headline{font-size:30px;font-weight:650;color:#244839;} QLabel#eyebrow{font-size:11px;color:#7a8c80;letter-spacing:2px;}
QLabel#subhead{font-size:18px;font-weight:600;} QFrame#card{background:#fafcf9;border:1px solid #dbe5db;border-radius:19px;}
QPushButton{background:#fafcf8;border:1px solid #d3dfd3;border-radius:12px;padding:11px 17px;text-align:left;}
QPushButton:hover{background:#e2eee2;border-color:#98b29d;} QPushButton:pressed{background:#ccdcca;}
QPushButton#primary{background:#2f5946;color:#fff;border:0;font-weight:600;}QPushButton#primary:hover{background:#406f58;}
QPushButton#nav{border:0;background:transparent;color:#6c8072;padding:13px 16px;border-radius:11px;}
QPushButton#nav:checked{background:#e3ede0;color:#244839;font-weight:600;}
QSlider::groove:horizontal{height:5px;background:#dbe5d9;border-radius:2px;}QSlider::sub-page:horizontal{background:#6a9575;border-radius:2px;}
QSlider::handle:horizontal{background:#2e5945;width:16px;margin:-6px 0;border-radius:8px;}
QCheckBox{spacing:10px;padding:8px 0;}QCheckBox::indicator{width:17px;height:17px;border:1px solid #b6cbb8;border-radius:5px;background:#fafcf8;}
QCheckBox::indicator:checked{background:#547d5d;border-color:#547d5d;}
QComboBox{background:#fafcf8;border:1px solid #cbd9cc;border-radius:8px;padding:7px;}
QScrollArea{border:0;background:transparent;}QScrollArea>QWidget>QWidget{background:transparent;}
QTextBrowser{background:#fafcf8;border:1px solid #dbe5db;border-radius:14px;padding:13px;}
QProgressBar{background:#e1eade;border:0;border-radius:4px;height:8px;text-align:center;}
QProgressBar::chunk{background:#789b75;border-radius:4px;}
'''

def label(text,kind=None,wrap=False):
    w=QLabel(text)
    if kind:w.setObjectName(kind)
    w.setWordWrap(wrap)
    return w

def button(text,fn,primary=False):
    w=QPushButton(text)
    if primary:w.setObjectName('primary')
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
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g=QLinearGradient(0,0,self.width(),self.height());g.setColorAt(0,QColor('#e1ead8'));g.setColorAt(1,QColor('#c7d9c8'))
        p.setBrush(g);p.setPen(Qt.PenStyle.NoPen);p.drawRoundedRect(self.rect(),20,20)
        p.setBrush(QColor(250,253,242,65));p.drawEllipse(self.width()-190,-65,260,260);p.drawEllipse(-85,self.height()-145,230,230)
        state,index=self.ctrl.animation()
        pix,position,_,_=self.frame_layout(state,index)
        p.drawPixmap(position,pix)
        p.setPen(QColor('#52725b'));p.setFont(QFont('Segoe UI',9));p.drawText(21,32,'SI  /  A LITTLE COMPANY')
        p.setFont(QFont('Microsoft YaHei UI',9));p.drawText(21,self.height()-18,'随心所欲 · 也会为你停留')

class Panel(QWidget):
    def __init__(self,ctrl,pet,voice=None):
        super().__init__();self.ctrl=ctrl;self.pet=pet;self.voice=voice;self.setObjectName('panel')
        self.setWindowTitle('祀 · 桌面伙伴');self.setWindowIcon(ctrl.sprites.icon())
        self.setMinimumSize(900,680);self.resize(1000,740);self.setStyleSheet(STYLE)
        outer=QHBoxLayout(self);outer.setSpacing(0);outer.setContentsMargins(0,0,0,0)
        side=QFrame();side.setObjectName('sidebar');side.setFixedWidth(200)
        sl=QVBoxLayout(side);sl.setContentsMargins(24,30,24,24);sl.setSpacing(7)
        sl.addWidget(label('祀','brand'));sl.addWidget(label('桌 面 伙 伴','eyebrow'));sl.addSpacing(34)
        self.stack=QStackedWidget();self.nav=QButtonGroup(self);self.nav.setExclusive(True)
        for i,text in enumerate(['相伴此刻','行为与显示','陪伴手记','关于祀']):
            b=QPushButton(text);b.setObjectName('nav');b.setCheckable(True)
            self.nav.addButton(b,i);sl.addWidget(b);b.clicked.connect(lambda checked=False,n=i:self.stack.setCurrentIndex(n))
        self.nav.button(0).setChecked(True);sl.addStretch()
        sl.addWidget(button('找回桌宠',pet.recover));sl.addSpacing(12)
        sl.addWidget(label('本地陪伴 · 存档随行','muted'));sl.addWidget(label(f'Windows · {__version__} '+('协同预览' if __version__.startswith('2.0') else '互动预览'),'muted'))
        outer.addWidget(side)
        container=QWidget();cl=QVBoxLayout(container);cl.setContentsMargins(32,28,32,24)
        cl.addWidget(self.stack);outer.addWidget(container,1)
        self.stack.addWidget(self.home());self.stack.addWidget(self.settings());self.stack.addWidget(self.journal());self.stack.addWidget(self.about())
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1000)
        ctrl.changed.connect(self.refresh);self.refresh()

    def heading(self,title,subtitle):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,0,0,14);l.setSpacing(8)
        l.addWidget(label(title,'headline'));l.addWidget(label(subtitle,'muted',True));return w

    def home(self):
        page=QWidget();lay=QVBoxLayout(page);lay.setContentsMargins(0,0,0,0);lay.setSpacing(18)
        header=QHBoxLayout();header.addWidget(self.heading('管理员，今天也一起吧。','不必时时说话。你认真做事，她自在停留。'),1)
        header.addWidget(button('唤到身边',self.pet.recover,True));lay.addLayout(header)
        middle=QHBoxLayout();middle.setSpacing(20);self.preview=Preview(self.ctrl);middle.addWidget(self.preview,1)
        right=QVBoxLayout();right.setSpacing(12)
        right.addWidget(label('她的此刻','subhead'));self.state_text=label('正在看看四周','muted');right.addWidget(self.state_text)
        right.addWidget(button('散散步   ↗',lambda:self.ctrl.perform('walk')))
        right.addWidget(button('坐一会儿',lambda:self.ctrl.perform('sit')))
        right.addWidget(button('摸摸头',self.ctrl.pat));right.addWidget(button('送一份点心',self.ctrl.feed))
        contact=button('和祀说一句',lambda:None)
        contact_menu=QMenu(contact)
        for title,topic in CONTACT_OPTIONS:
            contact_menu.addAction(title,lambda checked=False,t=topic:self.ctrl.contact(t))
        contact.setMenu(contact_menu);right.addWidget(contact)
        right.addWidget(button('自由聊聊 · 本机或学校反代',self.open_conversation))
        self.contact_reply_text=label('选一个话题，她会认真听。','muted',True)
        right.addWidget(self.contact_reply_text)
        self.ctrl.contact_reply.connect(self.contact_reply_text.setText)
        right.addWidget(button('靠边坐好',self.ctrl.dock))
        self.focus_button=button('陪我专注 · 25 分钟',lambda:self.ctrl.focus(),True);right.addWidget(self.focus_button)
        self.focus_text=label('专注时，安静陪在旁边。','muted',True);right.addWidget(self.focus_text);right.addStretch()
        middle.addLayout(right,1);lay.addLayout(middle,1)
        stats=QHBoxLayout();stats.setSpacing(12);self.stat_labels=[]
        for title in ['亲密度','一起专注','相伴时间']:
            c=card();l=QVBoxLayout(c);l.setContentsMargins(18,14,18,14);l.addWidget(label(title,'muted'))
            value=label('0','subhead');l.addWidget(value);self.stat_labels.append(value);stats.addWidget(c)
        lay.addLayout(stats)
        lay.addWidget(label('拖动她到任意位置，松开会自然下落。右键有更多动作；双击可以轻轻跳起。','muted',True))
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

    def settings(self):
        body=QWidget();l=QVBoxLayout(body);l.setContentsMargins(0,0,12,0);l.setSpacing(16)
        l.addWidget(self.heading('由你决定，她的节奏。','设置即时生效，并随便携文件夹保存。'))
        self.checks={};self.sliders={};self.value_labels={}
        for title,key,lo,hi,factor,unit in [('显示大小','scale',75,300,100,'倍'),('散步速度','speed',15,180,1,''),('重力强度','gravity',100,2500,1,''),('下边界微调','bottom_offset',0,160,1,'')]:
            c=card();cl=QVBoxLayout(c);cl.setContentsMargins(18,14,18,14)
            row=QHBoxLayout();row.addWidget(label(title,'subhead'));row.addStretch();v=label('','muted');row.addWidget(v);cl.addLayout(row)
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(lo,hi);slider.setValue(round(self.ctrl.store[key]*factor))
            slider.valueChanged.connect(lambda val,k=key,f=factor:self.setting(k,val/f))
            self.sliders[key]=slider;self.value_labels[key]=v;cl.addWidget(slider);l.addWidget(c)
        c=card();cl=QVBoxLayout(c);cl.setContentsMargins(18,14,18,14);cl.addWidget(label('桌面行为','subhead'))
        cb=QCheckBox('全屏应用时自动隐藏，退出全屏后恢复');cb.setChecked(self.ctrl.store['hide_fullscreen'])
        cb.toggled.connect(lambda value:self.setting('hide_fullscreen',value));self.checks['hide_fullscreen']=cb;cl.addWidget(cb)
        for title,key in [('自动散步与随机小动作','wander'),('安静陪伴：停止自主走动，降低存在感','quiet_mode'),('专注时自动靠边','auto_dock'),('可站在普通窗口顶边','window_edges'),('眼神跟随鼠标','cursor_follow'),('显示陪伴短句','bubbles'),('始终在其他窗口上方','topmost'),('鼠标穿透（从托盘找回；快捷键可用时也可 Ctrl+Alt+S）','clickthrough'),('手动控制（选中桌宠后使用方向键）','manual'),('选中轮廓','outline'),('脚下轻影','shadow'),('登录 Windows 时启动','auto_start')]:
            cb=QCheckBox(title);cb.setChecked(self.ctrl.store[key]);cb.toggled.connect(lambda value,k=key:self.setting(k,value));self.checks[key]=cb;cl.addWidget(cb)
        l.addWidget(c)
        voice_card=card();vl=QVBoxLayout(voice_card);vl.setContentsMargins(18,14,18,14)
        vl.addWidget(label('VoiceStudio · 语音陪伴','subhead'))
        voice_toggle=QCheckBox('启用语音（仅主动互动与专注完成时发声）')
        voice_toggle.setChecked(self.ctrl.store['voice_enabled']);voice_toggle.toggled.connect(lambda value:self.setting('voice_enabled',value))
        self.checks['voice_enabled']=voice_toggle;vl.addWidget(voice_toggle)
        vl.addWidget(label('本机 VoiceStudio 需已运行；连接失败时继续显示文字，不会自动切换成其他声线。','muted',True))
        profile_row=QHBoxLayout();profile_row.addWidget(label('声线 ID'))
        self.voice_profile=QLineEdit(str(self.ctrl.store['voice_profile_id']))
        self.voice_profile.editingFinished.connect(lambda:self.setting('voice_profile_id',self.voice_profile.text().strip()))
        profile_row.addWidget(self.voice_profile,1);vl.addLayout(profile_row)
        volume_row=QHBoxLayout();volume_row.addWidget(label('语音音量'))
        volume=QSlider(Qt.Orientation.Horizontal);volume.setRange(0,100);volume.setValue(round(self.ctrl.store['voice_volume']))
        volume.valueChanged.connect(lambda value:self.setting('voice_volume',value))
        volume_row.addWidget(volume,1);vl.addLayout(volume_row)
        audition=button('试听一句',lambda:self.voice.speak('管理员，我在。') if self.voice else None)
        audition.setEnabled(self.voice is not None);vl.addWidget(audition)
        connect=button('连接本机 VoiceStudio 引擎',self.voice.start_backend if self.voice else lambda:None)
        connect.setEnabled(self.voice is not None);vl.addWidget(connect)
        self.voice_status=label('语音默认关闭，启用后可试听。','muted',True);vl.addWidget(self.voice_status)
        if self.voice:self.voice.status.connect(self.voice_status.setText)
        l.addWidget(voice_card)
        self.genai_status=GenAIStatusWidget()
        l.addWidget(self.genai_status)
        row=QHBoxLayout();row.addWidget(label('帧率上限'));fps=QComboBox();fps.addItems(['15','30','60']);fps.setCurrentText(str(int(self.ctrl.store['fps'])));fps.currentTextChanged.connect(lambda t:self.setting('fps',int(t)));row.addWidget(fps)
        row.addSpacing(20);row.addWidget(label('专注时长'));minutes=QComboBox();minutes.addItems(['5','15','25','45','60']);minutes.setCurrentText(str(int(self.ctrl.store['focus_minutes'])));minutes.currentTextChanged.connect(lambda t:self.setting('focus_minutes',int(t)));row.addWidget(minutes);row.addStretch();l.addLayout(row)
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
        l.addWidget(self.heading('祀 / SI','桌面很小，也装得下片刻自在。'))
        text=QTextBrowser();text.setOpenExternalLinks(True)
        text.setHtml('''<h2>独立的桌面伙伴</h2><p>一款可复制的 Windows 同人桌宠。基础陪伴离线运行；可选语音通过本机 VoiceStudio 合成。现有运行图集仍是旧版生成素材；v12 造型候选的面部尚未定稿，也未接入运行时动画。</p><h3>陪伴方式</h3><p>对管理员采用熟悉、放松而克制的互动。摸头、点心、专注计时、五个明确话题的短句均由本地预设驱动，不是游戏原台词，也不是联网 AI 对话。安静陪伴与靠边坐好可减少遮挡。</p><h3>适用范围</h3><p>Windows 10/11，64 位。普通窗口顶边与多显示器支持依赖系统窗口几何；移动端与多 PC 同步仍在后续版本。</p><h3>署名与许可</h3><p>行为设计参考 <a href="https://github.com/isHarryh/Ark-Pets">Ark-Pets</a>。代码：GPL-3.0。图形框架：PySide6 / Qt，LGPL-3.0。角色“祀”及《明日方舟：终末地》相关权利属于原权利人。本项目为非官方同人作品；角色图像素材不随代码许可证授予原作权利。</p>''')
        l.addWidget(text,1);l.addWidget(button('查看项目文件',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_root())))))
        return w

    def setting(self,key,value):
        if key=='quiet_mode':self.ctrl.set_quiet_mode(value);return
        if key=='auto_start':
            try:native.set_startup(value)
            except OSError as exc:QMessageBox.warning(self,'未能修改启动设置',str(exc));return
        if key=='scale':self.ctrl.sprites.cache.clear();self.ctrl.sprites.mask_cache.clear();self.ctrl.sprites.outline_cache.clear()
        self.ctrl.store[key]=value;self.ctrl.store.save()
        if key=='voice_enabled' and not value and self.voice:self.voice.stop()
        if key in {'scale','window_edges','bottom_offset'}:self.ctrl.refresh_platforms()
        self.ctrl.changed.emit()
        if key=='manual' and value:self.pet.activateWindow();self.pet.setFocus()

    def refresh(self):
        states={'idle':'正在看看四周','walk-right':'正在向右散步','walk-left':'正在向左散步','sitting':'在桌边坐一会儿','sleeping':'安静休息中','jumping':'轻轻腾空，又落下','waving':'正在向你招手','running':'有一个新念头','review':'认真端详着'}
        self.state_text.setText(states.get(self.ctrl.state,'自在相伴'))
        if self.ctrl.focus_end:
            sec=max(0,math.ceil(self.ctrl.focus_end-time.monotonic()));self.focus_button.setText('结束本次专注');self.focus_text.setText(f'还有 {sec//60:02d}:{sec%60:02d} · 她在这里')
        else:self.focus_button.setText(f'陪我专注 · {int(self.ctrl.store["focus_minutes"])} 分钟');self.focus_text.setText('专注时，安静陪在旁边。')
        self.stat_labels[0].setText(str(int(self.ctrl.store['affinity'])));self.stat_labels[1].setText(f'{int(self.ctrl.store["focus_sessions"])} 次');self.stat_labels[2].setText(f'{int(self.ctrl.store["total_seconds"]//60)} 分钟')
        for key,cb in self.checks.items():cb.blockSignals(True);cb.setChecked(self.ctrl.store[key]);cb.blockSignals(False)
        for key,v in self.value_labels.items():v.setText(f'{self.ctrl.store[key]:.2f} 倍' if key=='scale' else str(int(self.ctrl.store[key])))
        self.mood.setValue(int(self.ctrl.store['mood']))
        rows=self.ctrl.store['history'][-35:]
        content='<h3>相伴手记</h3>'+''.join(f'<p><span style="color:#81917e">{html.escape(str(r.get("at","")))}</span><br>{html.escape(str(r.get("text","")))}</p>' for r in reversed(rows) if isinstance(r,dict))
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
