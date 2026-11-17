import time
from collections import deque
from PySide6.QtCore import Qt,QPoint,QTimer,QRectF,QRect
from PySide6.QtGui import QPainter,QColor,QPen,QCursor,QRegion
from PySide6.QtWidgets import QWidget,QLabel,QMenu,QApplication
from .companion import CONTACT_OPTIONS
from . import native

def request_exit():
    app=QApplication.instance()
    app.setProperty('si_quitting',True)
    app.quit()

class Bubble(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.ToolTip|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.text=QLabel(self);self.text.setWordWrap(True)
        self.text.setStyleSheet('color:#f4fff8;background:transparent;font:14px "Microsoft YaHei UI";')
        self.resize(310,100)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.hide)
    def speak(self,text,pet):
        self.text.setText(text);self.text.setFixedWidth(270);self.text.adjustSize()
        self.resize(310,self.text.height()+36);self.text.move(20,16)
        self.follow(pet);self.show();self.raise_();self.timer.start(4800)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor('#95c6ac'),1.5));p.setBrush(QColor('#183b35'))
        p.drawRoundedRect(QRectF(2,2,self.width()-4,self.height()-4),16,16)
    def follow(self,pet):
        screen=QApplication.screenAt(pet.geometry().center()) or QApplication.primaryScreen()
        g=screen.availableGeometry()
        x=max(g.left()+8,min(g.right()-self.width()-8,pet.x()+(pet.width()-self.width())//2))
        y=max(g.top()+8,pet.y()+round(28*pet.ctrl.store['scale'])-self.height())
        y=min(g.bottom()-self.height()-8,y)
        self.move(x,y)

class PetWindow(QWidget):
    def __init__(self,ctrl,open_panel):
        super().__init__()
        self.ctrl=ctrl;self.open_panel=open_panel;self.bubble=Bubble();self._frame_key=None
        self._last_tick=time.monotonic();self._press=None;self._drag=False;self._samples=deque(maxlen=12)
        self._size=None;self._flags=None
        self._fullscreen_check_at=0
        self.setWindowTitle('祀 · 桌面伙伴')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.ctrl.speech.connect(lambda text:self.bubble.speak(text,self))
        self.ctrl.changed.connect(self.sync)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(16)
        self.sync()

    def sync(self):
        cfg=self.ctrl.store
        flags=Qt.WindowType.FramelessWindowHint|Qt.WindowType.Tool
        if cfg['topmost'] and not self.ctrl.focus_end:flags|=Qt.WindowType.WindowStaysOnTopHint
        if cfg['clickthrough'] or self.ctrl.focus_end:flags|=Qt.WindowType.WindowTransparentForInput
        if not cfg['manual']:flags|=Qt.WindowType.WindowDoesNotAcceptFocus
        if flags!=self._flags:
            self._flags=flags;visible=self.isVisible();self.setWindowFlags(flags)
            if visible:self.show()
        size=(round(192*cfg['scale']),round(208*cfg['scale']))
        if size!=self._size:
            self._size=size;self.resize(*size);self._frame_key=None
            # A larger canvas can stick out of the screen even when the old
            # centre was valid. Re-clamp after every scale/DPI resize.
            if self.ctrl.scenes:self.ctrl.clamp_recovery()
        self.timer.setInterval(round(1000/cfg['fps']))
        self.setWindowOpacity(.62 if self.ctrl.focus_end or cfg['quiet_mode'] else 1.0)
        self.bubble.setWindowOpacity(self.windowOpacity())
        if self.ctrl.hidden:self._cancel_drag();self.hide();self.bubble.hide()
        self.update()

    def tick(self):
        now=time.monotonic();dt=min(now-self._last_tick,0.2);self._last_tick=now
        if now>=self._fullscreen_check_at:
            self._fullscreen_check_at=now+.5
            monitor=native.foreground_fullscreen_monitor() if self.ctrl.store['hide_fullscreen'] else None
            screen=QApplication.screenAt(QPoint(round(self.ctrl.physics.body.x),round(self.ctrl.physics.body.y-1)))
            suppressed=bool(monitor and screen and monitor==native.monitor_device_for_screen(screen))
            was_suppressed=self.ctrl.presentation_suppressed
            self.ctrl.presentation_suppressed=suppressed
            if suppressed:
                self._cancel_drag()
                self.hide();self.bubble.hide()
            elif was_suppressed and not self.ctrl.hidden:
                self.show()
        self.ctrl.update(dt)
        if self.ctrl.hidden or self.ctrl.presentation_suppressed:return
        b=self.ctrl.physics.body;s=self.ctrl.store['scale']
        self.move(round(b.x-96*s),round(b.y-202*s))
        if self.bubble.isVisible():self.bubble.follow(self)
        state,index=self.ctrl.animation()
        outline=self.ctrl.store['outline'] or self.underMouse() and not self.ctrl.store['clickthrough']
        shadow=self.ctrl.store['shadow'] and bool(b.support)
        key=(state,index,s,outline,shadow)
        if key!=self._frame_key:
            self._frame_key=key
            region=self.ctrl.sprites.mask(state,index,s)
            if outline:
                for dx,dy in [(-2,0),(2,0),(0,-2),(0,2)]:region=region.united(region.translated(dx,dy))
            if shadow:region=region.united(QRegion(QRect(round(62*s),round(197*s),round(68*s),round(7*s)),QRegion.RegionType.Ellipse))
            self.setMask(region)
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s=self.ctrl.store['scale'];state,index=self.ctrl.animation()
        pix=self.ctrl.sprites.pixmap(state,index,s,self.devicePixelRatioF())
        if self.ctrl.store['shadow'] and self.ctrl.physics.body.support:
            painter.setBrush(QColor(10,30,25,38));painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(62*s,197*s,68*s,7*s))
        if self.ctrl.store['outline'] or self.underMouse() and not self.ctrl.store['clickthrough']:
            tinted=self.ctrl.sprites.outline(state,index,s,self.devicePixelRatioF());painter.setOpacity(.65)
            for dx,dy in [(-2,0),(2,0),(0,-2),(0,2)]:painter.drawPixmap(dx,dy,tinted)
            painter.setOpacity(1)
        painter.drawPixmap(0,0,pix)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self._press=event.globalPosition().toPoint();self._drag=False
            self._offset=event.globalPosition().toPoint()-self.pos();self._samples.clear()
            self._samples.append((time.monotonic(),self.ctrl.physics.body.x,self.ctrl.physics.body.y))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._press is not None and event.buttons()&Qt.MouseButton.LeftButton:
            point=event.globalPosition().toPoint()
            if not self._drag and (point-self._press).manhattanLength()>QApplication.startDragDistance():
                self._drag=True;self.ctrl.physics.body.dragging=True;self.ctrl.physics.body.support=None
                self.bubble.hide()
            if self._drag:
                s=self.ctrl.store['scale'];p=point-self._offset;b=self.ctrl.physics.body
                b.x=p.x()+96*s;b.y=p.y()+202*s
                self._samples.append((time.monotonic(),b.x,b.y));self.move(p)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self._press is not None:
            if self._drag:
                now=time.monotonic();samples=[r for r in self._samples if now-r[0]<.14]
                vx=vy=0
                if len(samples)>1:
                    a,z=samples[0],samples[-1];dt=max(.02,z[0]-a[0]);vx=(z[1]-a[1])/dt;vy=(z[2]-a[2])/dt
                self.ctrl.clamp_recovery();self.ctrl.physics.release(vx,vy)
                self.ctrl.override_until=now+1;self.ctrl.action='idle';self.ctrl.docked=False;self.ctrl.quiet_dock=False
            else:self.ctrl.pat()
            self._press=None;self._drag=False;self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.ctrl.perform('jump')

    def contextMenuEvent(self,event):self.menu().exec(event.globalPos())

    def menu(self):
        menu=QMenu();menu.setStyleSheet('QMenu{background:#f7faf7;color:#183b35;padding:8px;border:1px solid #bdd3c5;border-radius:8px;}QMenu::item{padding:8px 24px;}QMenu::item:selected{background:#dcece2;}')
        menu.addAction('祀 · 陪伴面板',self.open_panel);menu.addSeparator()
        for text,action in [('散步','walk'),('坐下','sit'),('躺下休息','sleep'),('跳一下','jump'),('招招手','wave'),('灵光一闪','special')]:
            menu.addAction(text,lambda checked=False,a=action:self.ctrl.perform(a))
        menu.addAction('摸摸头',self.ctrl.pat);menu.addAction('投喂点心',self.ctrl.feed)
        contact=menu.addMenu('和祀说一句')
        for title,topic in CONTACT_OPTIONS:
            contact.addAction(title,lambda checked=False,t=topic:self.ctrl.contact(t))
        focus_action=menu.addAction('结束专注' if self.ctrl.focus_end else '陪我专注',lambda:self.ctrl.focus())
        menu.addAction('靠边坐好',self.ctrl.dock)
        menu.addSeparator()
        for text,key in [('自动散步','wander'),('安静陪伴','quiet_mode'),('站在窗口边缘','window_edges'),('手动模式（方向键）','manual'),('鼠标穿透（托盘恢复）','clickthrough')]:
            action=menu.addAction(text);action.setCheckable(True);action.setChecked(self.ctrl.store[key])
            action.setData(key)
            action.toggled.connect(lambda checked,k=key:self.option(k,checked))
        menu.addAction('找回祀 / 恢复鼠标交互',self.recover)
        menu.addAction('暂时隐藏',self.hide_pet);menu.addSeparator()
        menu.addAction('退出桌宠',request_exit)
        def sync_checks():
            focus_action.setText('结束专注' if self.ctrl.focus_end else '陪我专注')
            for action in menu.actions():
                if action.data() in {'wander','quiet_mode','window_edges','manual','clickthrough'}:
                    action.blockSignals(True);action.setChecked(self.ctrl.store[action.data()]);action.blockSignals(False)
        menu.aboutToShow.connect(sync_checks)
        return menu

    def option(self,key,value):
        if key=='quiet_mode':self.ctrl.set_quiet_mode(value);return
        self.ctrl.store[key]=value
        if key=='window_edges':self.ctrl.refresh_platforms()
        self.ctrl.store.save();self.ctrl.changed.emit()
        if key=='manual' and value:self.activateWindow();self.setFocus()

    def recover(self):self.ctrl.recover();self.sync();self.show();self.raise_()
    def _cancel_drag(self):
        """A hidden window may never receive the original mouse release."""
        if self.ctrl.physics.body.dragging:
            self.ctrl.physics.release(0,0)
        self._press=None;self._drag=False;self._samples.clear()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def hide_pet(self):
        self._cancel_drag();self.ctrl.hidden=True;self.hide();self.bubble.hide()

    def keyPressEvent(self,event):
        if self.ctrl.store['manual']:
            key=event.key()
            if key==Qt.Key.Key_Left:self.ctrl.keys.add('left')
            elif key==Qt.Key.Key_Right:self.ctrl.keys.add('right')
            elif key==Qt.Key.Key_Space:self.ctrl.perform('jump')
            elif key in (Qt.Key.Key_Up,Qt.Key.Key_Down):
                actions=['idle','sit','sleep','special','wave','review','think']
                index=actions.index(self.ctrl.action) if self.ctrl.action in actions else 0
                self.ctrl.perform(actions[(index+(1 if key==Qt.Key.Key_Up else -1))%len(actions)])
            elif key==Qt.Key.Key_Escape:self.option('manual',False)
    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key.Key_Left:self.ctrl.keys.discard('left')
        if event.key()==Qt.Key.Key_Right:self.ctrl.keys.discard('right')
    def focusOutEvent(self,event):self.ctrl.keys.clear();super().focusOutEvent(event)
