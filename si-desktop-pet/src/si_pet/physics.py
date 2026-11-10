"""Pure, deterministic desktop physics in Qt logical pixels; no GUI dependencies."""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Platform:
    key: str
    left: float
    right: float
    top: float
    floor: bool = False
    origin_x: float | None = None

@dataclass
class Body:
    x: float = 600
    y: float = 400
    vx: float = 0
    vy: float = 0
    support: str | None = None
    dragging: bool = False

class Physics:
    def __init__(self, body=None, gravity=1250.0):
        self.body = body or Body()
        self.gravity = gravity
        self.platforms: list[Platform] = []
        self.previous: dict[str, Platform] = {}
        self.last_impact = 0.0

    def set_platforms(self, platforms):
        """Follow a supporting window while it moves; fall if it closes/minimizes."""
        current = {p.key: p for p in platforms}
        b = self.body
        if b.support and b.support in self.previous and not b.dragging:
            old=self.previous[b.support]
            new=current.get(b.support)
            if old.key.startswith('window:'):
                group=old.key.rsplit(':',1)[0]+':'
                candidates=[p for p in platforms if p.key.startswith(group)]
                if candidates:
                    anchor=old.origin_x if old.origin_x is not None else old.left
                    candidate_anchor=candidates[0].origin_x if candidates[0].origin_x is not None else candidates[0].left
                    expected=b.x+(candidate_anchor-anchor)
                    if not new or not new.left<=expected<=new.right:
                        new=next((p for p in candidates if p.left<=expected<=p.right),None)
            if new:
                old_anchor=old.origin_x if old.origin_x is not None else old.left
                new_anchor=new.origin_x if new.origin_x is not None else new.left
                dx,dy=new_anchor-old_anchor,new.top-old.top
                if abs(dx)<600 and abs(dy)<600:
                    b.x+=dx;b.y+=dy;b.support=new.key
                else:b.support=None
            else:b.support=None
        elif b.support and b.support not in current:
            b.support=None
        self.platforms = list(platforms)
        self.previous = current

    def release(self, vx=0.0, vy=0.0):
        b = self.body
        b.dragging = False
        b.support = None
        b.vx = max(-1100, min(1100, vx))
        b.vy = max(-900, min(1400, vy))

    def jump(self, strength=500):
        if self.body.support:
            self.body.support = None
            self.body.vy = -strength
            return True
        return False

    def step(self, dt, walk_velocity=None, restitution=0.0):
        dt = min(max(dt, 0), 0.2)
        steps=max(1,math.ceil(dt/0.025))
        impact=0
        for _ in range(steps):
            impact=max(impact,self._step(dt/steps,walk_velocity,restitution) or 0)
        self.last_impact=impact
        return impact

    def _step(self, dt, walk_velocity=None, restitution=0.0):
        b = self.body
        self.last_impact = 0
        if b.dragging:
            return
        support = next((p for p in self.platforms if p.key == b.support), None)
        if support and not support.left <= b.x <= support.right:
            b.support = None
            support = None
        if support:
            b.y = support.top
            b.vy = 0
            b.vx = walk_velocity if walk_velocity is not None else b.vx * math.exp(-dt*14)
        elif walk_velocity is not None:
            b.vx += (walk_velocity-b.vx)*min(1, dt*2)
        prev_y = b.y
        b.x += b.vx*dt
        if support and not support.left <= b.x <= support.right:
            b.support = None
            support = None
        if not support:
            # Exact constant-acceleration integration, then swept one-way collision.
            b.y += b.vy*dt + 0.5*self.gravity*dt*dt
            b.vy = min(1900, b.vy+self.gravity*dt)
            candidates = [p for p in self.platforms
                          if b.vy >= 0 and p.left <= b.x <= p.right
                          and prev_y <= p.top+0.5 and b.y >= p.top]
            if candidates:
                p = min(candidates, key=lambda p:p.top)
                self.last_impact = b.vy
                b.y = p.top
                if restitution and b.vy > 420:
                    b.vy = -b.vy*restitution
                else:
                    b.vy = 0
                    b.support = p.key
        return self.last_impact

def visible_segments(left, right, covered, min_width=40):
    segments = [(left,right)]
    for a,b in covered:
        result=[]
        for lo,hi in segments:
            if b<=lo or a>=hi:
                result.append((lo,hi))
            else:
                if lo<a: result.append((lo,a))
                if b<hi: result.append((b,hi))
        segments=result
    return [(a,b) for a,b in segments if b-a>=min_width]
