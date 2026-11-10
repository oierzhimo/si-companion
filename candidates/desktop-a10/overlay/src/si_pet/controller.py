import math
import random
import time
from PySide6.QtCore import QObject, Signal, QPoint
from PySide6.QtGui import QCursor, QGuiApplication
from .physics import Physics, Platform
from .companion import Companion, CONTACT_OPTIONS
from . import native

class Controller(QObject):
    changed=Signal()
    speech=Signal(str)
    voice_speech=Signal(str)
    contact_reply=Signal(str)
    focus_done=Signal()

    def __init__(self,store,sprites):
        super().__init__()
        self.store=store;self.sprites=sprites;self.physics=Physics(gravity=store['gravity'])
        self.companion=Companion()
        self.state='idle';self.state_started=time.monotonic();self.override_until=0
        self.decision_at=time.monotonic()+3;self.direction=1;self.action='idle'
        self.sleeping=False;self.focus_end=None;self.focus_duration=None;self.last_feed=0;self.last_pat=0
        self.docked=False;self.quiet_dock=False
        self.last_speech=0;self.speech_enabled=True;self.hidden=False
        self.presentation_suppressed=False
        self._paused_for_visibility=False
        self.next_check_in=time.monotonic()+random.uniform(1200,2400)
        self.walk_distance=0;self.idle_next=time.monotonic()+random.uniform(3,7)
        self.idle_clip=None;self.idle_until=0;self.idle_last=None
        self.keys=set();self.last_save=time.monotonic();self.started=time.monotonic()
        self.scenes=list(QGuiApplication.screens())
        self.refresh_platforms()
        screen=QGuiApplication.primaryScreen().availableGeometry()
        b=self.physics.body
        try:
            b.x=float(store['last_x']);b.y=float(store['last_y'])
            if not any(s.geometry().adjusted(-100,-100,100,100).contains(round(b.x),round(b.y)) for s in self.scenes):raise ValueError()
        except (TypeError,ValueError):b.x=screen.right()-240;b.y=screen.bottom()-120
        self.clamp_recovery()

    def set_state(self,state):
        if state not in self.sprites.frames:state='idle'
        if self.state!=state:
            self.state=state;self.state_started=time.monotonic();self.changed.emit()

    def say(self,text,force=False):
        now=time.monotonic()
        if not self.hidden and not self.presentation_suppressed and (force or now-self.last_speech>4):
            self.last_speech=now
            if self.store['bubbles']:self.speech.emit(text)
            if force:self.voice_speech.emit(text)

    def refresh_platforms(self):
        self.scenes=list(QGuiApplication.screens())
        floors=[]
        for s in self.scenes:
            g=s.availableGeometry()
            floors.append(Platform('floor:'+s.name(),g.left()+16,g.right()-16,g.bottom()-self.store['bottom_offset'],True))
        windows=native.window_platforms(self.scenes,headroom=self.sprite_headroom()) if self.store['window_edges'] else []
        self.physics.set_platforms(floors+windows)

    def sprite_headroom(self):
        """Logical screen margin needed to keep the visible figure below the top edge."""
        scale=self.store['scale']
        top=self.sprites.mask('idle',0,scale).boundingRect().top()
        return max(8,202*scale-top)

    def clamp_recovery(self):
        b=self.physics.body
        if not self.scenes:return
        screen=min(self.scenes,key=lambda s:(s.availableGeometry().center().x()-b.x)**2+(s.availableGeometry().center().y()-b.y)**2)
        g=screen.availableGeometry()
        scale=self.store['scale'];half_width=96*scale
        b.x=max(g.left()+half_width+8,min(g.right()-half_width-8,b.x))
        b.y=max(g.top()+202*scale+8,min(g.bottom()-self.store['bottom_offset']-6*scale,b.y))

    def _walk_margin(self,screen):
        """Keep the whole sprite canvas inside a screen while walking."""
        g=screen.availableGeometry()
        return min(96*self.store['scale'],max(0,(g.width()-2)/2))

    def _walk_inside(self,x,y):
        for screen in self.scenes:
            g=screen.availableGeometry();margin=self._walk_margin(screen)
            if g.adjusted(margin,0,-margin,0).contains(round(x),round(y)):return True
        return False

    def recover(self):
        self.store['clickthrough']=False;self.hidden=False;self.sleeping=False
        self.focus_end=None;self.docked=False;self.quiet_dock=False
        g=QGuiApplication.primaryScreen().availableGeometry();b=self.physics.body
        b.x=g.right()-250;b.y=g.top()+min(450,g.height()*.5);b.vx=b.vy=0;b.support=None;b.dragging=False
        self.action='idle';self.override_until=0;self.decision_at=time.monotonic()+4
        self.clamp_recovery()
        self.say(self.companion.choose('return'),True);self.changed.emit()

    def dock(self):
        self._dock(True)
        self.quiet_dock=False

    def set_quiet_mode(self,value):
        self.store['quiet_mode']=value;self.store.save()
        if value:
            # Quiet mode settles into the seated pose, so do not leave a stale
            # sleeping flag suppressing later idle eye tracking.
            self.sleeping=False
        if not value and self.quiet_dock:
            self.quiet_dock=False;self.docked=False;self.override_until=0
            self.action='idle';self.decision_at=time.monotonic()+4;self.sleeping=False
        self.changed.emit()

    def _dock(self,hold):
        """Put the figure at the screen edge and stop autonomous movement."""
        self.quiet_dock=False
        b=self.physics.body
        screen=QGuiApplication.screenAt(QPoint(round(b.x),round(b.y))) or QGuiApplication.primaryScreen()
        g=screen.availableGeometry();scale=self.store['scale']
        # Body coordinates mark the sprite's horizontal center. Keep its full
        # 192 px canvas inside the usable screen when docking.
        b.x=g.right()-96*scale-8;b.y=g.bottom()-self.store['bottom_offset']-6*scale
        b.vx=b.vy=0;b.support=None;self.sleeping=False
        self.action='sit';self.docked=hold
        self.override_until=float('inf') if hold else (self.focus_end or time.monotonic()+30)
        self.changed.emit()

    def perform(self,action,announce=True):
        now=time.monotonic();self.sleeping=False
        self.docked=False;self.quiet_dock=False
        self.action=action;self.override_until=now+{'sit':18,'sleep':999999,'wave':2,'think':5,'review':5,'special':4,'idle':8}.get(action,5)
        self.physics.body.vx=0
        if action=='sleep':self.sleeping=True;self.say('借你桌面一角。醒来再继续。',True) if announce else None
        elif action=='walk':self.override_until=0;self.decision_at=now+random.uniform(6,12);self.action='walk';self.say('别急，我随处看看。',True) if announce else None
        elif action=='jump':
            if self.physics.jump(520):self.override_until=now+1.2;self.say('这点高度，还难不倒我。',True) if announce else None
            else:self.override_until=0
        elif action=='sit' and announce:self.say('坐一会儿。你继续，我看着。',True)
        elif action=='wave' and announce:self.say('找我？这次又有什么新鲜事。',True)
        elif action=='special' and announce:self.say('嗯……这个念头，倒值得留住。',True)
        self.store.log({'sit':'她在桌边坐下','sleep':'她安静休息','walk':'她开始散步','jump':'她轻轻一跃','wave':'她向你招手','think':'她在思考','review':'她认真端详','special':'她有了一个新主意'}.get(action,'她回到了你身边'))
        self.changed.emit()

    def pat(self):
        now=time.monotonic()
        if now-self.last_pat<1:return
        self.last_pat=now
        if self.store.sync_ledger is not None:self.store.record_sync('pat')
        else:self.store['pats']+=1;self.store['affinity']+=1
        self.store['mood']=min(100,self.store['mood']+2)
        self.perform('wave',announce=False)
        self.say(self.companion.choose('pat'),True)

    def feed(self):
        now=time.monotonic()
        if now-self.last_feed<20:
            self.say('慢一点，先让我尝完这一口。',True);return
        self.last_feed=now
        if self.store.sync_ledger is not None:self.store.record_sync('snack')
        else:self.store['snacks']+=1;self.store['affinity']+=3
        self.store['mood']=min(100,self.store['mood']+8)
        self.perform('special',announce=False);self.say(self.companion.choose('feed'),True)
        self.store.log('你送了她一份点心');self.changed.emit()

    def contact(self,topic):
        """Reply only to an explicit choice; never infer the user's real activity."""
        if topic not in {key for _,key in CONTACT_OPTIONS}:raise ValueError('Unknown contact topic')
        if topic=='quiet':
            self.store['quiet_mode']=True;self.store.save();self._dock(True);self.quiet_dock=True
        elif not self.focus_end:
            self.perform('sit' if topic=='tired' else 'review',announce=False)
        reply=self.companion.choose(topic)
        self.contact_reply.emit(reply)
        self.say(reply,True)
        return reply

    def focus(self,minutes=None):
        if self.focus_end:
            self.focus_end=None;self.override_until=0;self.action='idle';self.docked=False;self.quiet_dock=False
            self.focus_duration=None
            self.sleeping=False;self.decision_at=time.monotonic()+random.uniform(3,7)
            self.say('好，歇一会儿。我们不赶。',True)
        else:
            minutes=minutes or self.store['focus_minutes'];self.focus_duration=int(minutes)
            self.focus_end=time.monotonic()+minutes*60
            self.sleeping=False;self.action='sit';self.override_until=time.monotonic()+minutes*60
            if self.store['auto_dock']:self._dock(False)
            self.say(self.companion.choose('focus_start'),True);self.store.log(f'一起开始 {int(minutes)} 分钟专注')
        self.changed.emit()

    def gaze(self):
        b=self.physics.body;cursor=QCursor.pos();scale=self.store['scale']
        dx=cursor.x()-b.x;dy=cursor.y()-(b.y-125*scale)
        if abs(dx)+abs(dy)<40 or dx*dx+dy*dy>(650*scale)**2:return 'idle',None
        angle=(math.degrees(math.atan2(dx,-dy))+360)%360
        return 'look',int((angle+11.25)//22.5)%16

    def _window_turn_margin(self,speed,dt,current_vx=0.0):
        """Space needed to reverse smoothly before a narrow window ledge ends.

        The controller's walk speed eases toward its target at 9/s, so a full
        reversal still carries the body roughly current/9 pixels forward.  Add
        one simulation step and a small guard band; keep it independent of the
        sprite canvas because ledges are allowed to be narrower than the art.
        """
        v=max(abs(float(speed)),abs(float(current_vx)),1.0)
        return v/9+v*max(dt,.016)+4.0

    def _guide_window_walk(self,now,dt,velocity):
        """Turn before a window ledge ends; wait on ledges too short to pace."""
        b=self.physics.body
        support=None
        if b.support and b.support.startswith('window:'):
            # Read the current segment every frame: a moved or occluded window
            # may have replaced the old key/bounds underneath the pet.
            support=next((p for p in self.physics.platforms if p.key==b.support),None)
        if support is None:
            return velocity
        speed=float(self.store['speed'])
        margin=self._window_turn_margin(speed,dt,b.vx)
        left=support.left+margin;right=support.right-margin
        # Require about half a second of clear travel between the two turn
        # points.  Otherwise the pet would flip direction every few frames or
        # step off while braking, so let the walk settle back into idle.
        if right-left<max(24.0,speed*.5):
            center=(support.left+support.right)/2
            outside=b.x<left or b.x>right
            if outside:
                self.direction=1 if b.x<center else -1
                if right>=left:
                    # Keep steering back into the safe corridor; settle only
                    # after there is enough room to brake inside it.
                    return self.direction*speed
            self.action='idle'
            self.decision_at=now+random.uniform(5,12)
            if outside:
                # No safe corridor at all: one inward nudge, then let the
                # normal smoother brake instead of pacing.
                return self.direction*speed
            return 0.0
        # Once a direction is flipped it points away from this edge, so the
        # same test cannot fire again on the next frame.
        if self.direction>0 and b.x>=right:
            self.direction=-1
            return -speed
        if self.direction<0 and b.x<=left:
            self.direction=1
            return speed
        return velocity

    def update(self,dt):
        now=time.monotonic();b=self.physics.body
        self.physics.gravity=self.store['gravity']
        if self.focus_end and now>=self.focus_end:
            self.focus_end=None
            if self.store.sync_ledger is not None:
                self.store.record_sync('focus_completed',{'minutes':self.focus_duration or int(self.store['focus_minutes'])})
            else:self.store['focus_sessions']+=1;self.store['affinity']+=5
            self.focus_duration=None
            self.override_until=0;self.docked=False;self.quiet_dock=False
            self.perform('wave',announce=False);self.say(self.companion.choose('focus_done'),True);self.focus_done.emit()
        if now>=self.next_check_in:
            self.next_check_in=now+random.uniform(1200,2400)
            if not (self.focus_end or self.store['quiet_mode'] or self.hidden):
                self.say(self.companion.choose('check_in'))
        # Keep focus/journal clocks alive while the pet is not visible, but
        # do not let it wander or accumulate a throw behind a fullscreen app.
        if self.hidden or self.presentation_suppressed:
            if not self._paused_for_visibility:
                b.vx=b.vy=0
            self._paused_for_visibility=True
            self._save_progress(now)
            return
        if self._paused_for_visibility:
            self._paused_for_visibility=False
            if not self.store['manual']:
                if self.action=='walk':self.action='idle'
                self.decision_at=max(self.decision_at,now+3)
        velocity=None
        if b.dragging:
            self.set_state('jumping');return
        if self.store['manual']:
            velocity=(int('right' in self.keys)-int('left' in self.keys))*self.store['speed']
        elif now<self.override_until:
            velocity=0
        elif self.focus_end or self.store['quiet_mode']:
            self.action='sit';self.sleeping=False;velocity=0
        elif now>=self.decision_at:
            actions=['idle','walk','sit','sleep','special'] if self.store['wander'] else ['idle','sit','special']
            weights=[61,19,15,3,2] if self.store['wander'] else [75,22,3]
            self.action=random.choices(actions,weights)[0]
            self.direction=random.choice([-1,1]);self.decision_at=now+random.uniform(12,30)
        if not self.store['manual'] and self.action=='walk' and now>=self.override_until and not (self.focus_end or self.store['quiet_mode']):
            velocity=self.direction*self.store['speed']
        # Turn at screen edges, but allow a contiguous extended screen at the same height.
        if velocity and b.support:
            target=b.x+velocity*max(dt,0.016)
            if not self._walk_inside(target,b.y-1):
                self.direction*=-1;velocity=-velocity
            elif not self.store['manual'] and self.action=='walk':
                # Window ledges are narrower than the screen: turn on the
                # current visible segment instead of walking over its end.
                # Manual keys are deliberately not guided, so the user can
                # still step off a window edge on purpose.
                velocity=self._guide_window_walk(now,dt,velocity)
        if b.support:
            target_velocity=velocity or 0
            blend=1-math.exp(-min(dt,.2)*9)
            velocity=b.vx+(target_velocity-b.vx)*blend
            if abs(velocity)<.5:velocity=0
        old_x=b.x;was_grounded=bool(b.support)
        impact=self.physics.step(dt,velocity,restitution=0.12)
        if was_grounded and b.support:self.walk_distance+=abs(b.x-old_x)
        if not b.support:
            self.set_state('jumping')
        elif abs(b.vx)>12:
            self.set_state('walk-right' if b.vx>0 else 'walk-left')
        else:
            action=self.action
            if now>=self.override_until and action not in {'idle','walk','sit','sleep','special'}:action='idle'
            state={'sit':'sitting','sleep':'sleeping','wave':'waving','think':'running','special':'running','review':'review','jump':'idle'}.get(action,'idle')
            self.set_state(state)
        if impact>900:self.say('落地。下次先打个招呼？')
        # Recover after display removal or an externally moved window leaving virtual desktop.
        if not any(s.geometry().adjusted(-220,-800,220,400).contains(round(b.x),round(b.y)) for s in self.scenes):self.clamp_recovery()
        self._save_progress(now)

    def _save_progress(self,now):
        b=self.physics.body
        if now-self.last_save>15:
            self.store['total_seconds']+=now-self.last_save
            self.store['last_x']=b.x;self.store['last_y']=b.y
            self.store.save();self.last_save=now;self.changed.emit()

    def animation(self):
        now=time.monotonic();elapsed=(now-self.state_started)*1000
        if self.state.startswith('walk-'):
            count=len(self.sprites.frames[self.state])
            stride=max(16,self.store['speed']*.96)
            return self.state,int(self.walk_distance/stride*count)%count
        if self.state=='idle':
            if now>=self.idle_next:
                choices=['blink','waiting','review']
                choices=[c for c in choices if c!=self.idle_last]
                self.idle_clip=random.choices(choices,[7 if c=='blink' else 1 for c in choices])[0]
                self.idle_last=self.idle_clip;self.idle_started=now
                duration=.32 if self.idle_clip=='blink' else sum(self.sprites.durations[self.idle_clip])/1000
                self.idle_until=now+duration;self.idle_next=self.idle_until+random.uniform(9,22)
            if now<self.idle_until:
                if self.idle_clip=='blink':return 'idle',min(3,1+int((now-self.idle_started)/.11))
                return self.idle_clip,self.sprites.index(self.idle_clip,(now-self.idle_started)*1000)
        if self.state=='idle' and self.store['cursor_follow'] and not self.sleeping:
            state,index=self.gaze()
            if state=='look':return 'look',index
        if self.state=='idle':return 'idle',0
        # Non-walking gestures play once and settle instead of looping noisily.
        if self.state in {'waving','review','running','jumping','sitting','sleeping'}:
            total=sum(self.sprites.durations[self.state])
            return self.state,self.sprites.index(self.state,min(elapsed,max(0,total-1)))
        return self.state,self.sprites.index(self.state,elapsed)
