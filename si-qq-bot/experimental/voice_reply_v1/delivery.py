"""Pure state machine for a multi-part QQ voice delivery; no I/O.

The owner must durably write each returned state before the corresponding
send or next transition. On restart an in-flight state is outcome-unknown.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Delivery:
    total: int
    confirmed: int = 0
    phase: str = 'ready'

    def __post_init__(self):
        if (type(self.total) is not int or not 1 <= self.total <= 5
                or type(self.confirmed) is not int or not 0 <= self.confirmed <= self.total
                or self.phase not in {'ready', 'inflight', 'done', 'failed', 'unknown'}
                or (self.phase in {'ready', 'inflight'} and self.confirmed >= self.total)
                or (self.phase == 'done' and self.confirmed != self.total)
                or (self.phase in {'failed', 'unknown'} and self.confirmed >= self.total)):
            raise ValueError('invalid delivery state')

    def begin(self):
        if self.phase != 'ready':
            raise ValueError('delivery not ready')
        return replace(self, phase='inflight')

    def acknowledge(self, outcome):
        if self.phase != 'inflight' or outcome not in {'success', 'failure', 'unknown'}:
            raise ValueError('invalid acknowledgement')
        if outcome == 'success':
            count = self.confirmed + 1
            return replace(self, confirmed=count, phase='done' if count == self.total else 'ready')
        return replace(self, phase='failed' if outcome == 'failure' else 'unknown')

    def recover_after_restart(self):
        return replace(self, phase='unknown') if self.phase == 'inflight' else self

    @property
    def text_fallback_allowed(self):
        return self.phase == 'failed' and self.confirmed == 0
