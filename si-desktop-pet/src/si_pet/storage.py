import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from .sync_v1 import Ledger, SyncError, COUNTERS, legacy_event

DEFAULTS = {
    'scale':2.0, 'speed':65, 'gravity':1250, 'fps':60,
    'wander':True, 'window_edges':True, 'cursor_follow':True,
    'clickthrough':False, 'topmost':True, 'manual':False,
    'bubbles':True, 'outline':False, 'shadow':True,
    'quiet_mode':False, 'auto_dock':True,
    'hide_fullscreen':True,
    'voice_enabled':False, 'voice_volume':65, 'voice_profile_id':'06332768',
    'bottom_offset':0, 'auto_start':False, 'focus_minutes':25,
    'affinity':0, 'snacks':0, 'pats':0, 'focus_sessions':0,
    'mood':85, 'last_x':None, 'last_y':None, 'history':[],
    'total_seconds':0, 'schema_version':1,
}

def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]

def resource_root():
    return Path(getattr(sys,'_MEIPASS',app_root()))

def data_root():
    path=app_root()/'data'
    try:
        path.mkdir(exist_ok=True)
        probe=path/'.writable'
        probe.write_text('ok',encoding='utf-8');probe.unlink()
        return path
    except OSError:
        path=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SiDesktopPet'
        path.mkdir(parents=True,exist_ok=True)
        return path

class Store:
    def __init__(self, path=None):
        self.path=Path(path) if path else data_root()/'settings.json'
        self.sync_ledger=None
        self.values=DEFAULTS.copy()
        self.values['history']=[]
        try:
            payload=json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(payload,dict): self.values.update({k:v for k,v in payload.items() if k in DEFAULTS})
        except (OSError,ValueError): pass
        for k,lo,hi in [('scale',0.75,3),('speed',15,220),('gravity',100,2500),('fps',15,60),('bottom_offset',0,200),('focus_minutes',1,120),('voice_volume',0,100)]:
            try: self.values[k]=max(lo,min(hi,float(self.values[k])))
            except (ValueError,TypeError): self.values[k]=DEFAULTS[k]
        for k in ['affinity','snacks','pats','focus_sessions','mood','total_seconds']:
            try: self.values[k]=max(0,float(self.values[k]))
            except (ValueError,TypeError): self.values[k]=DEFAULTS[k]
        for k,v in DEFAULTS.items():
            if isinstance(v,bool): self.values[k]=self.values[k] if isinstance(self.values[k],bool) else v
        if not isinstance(self.values['voice_profile_id'],str):self.values['voice_profile_id']=DEFAULTS['voice_profile_id']
        if not isinstance(self.values['history'],list): self.values['history']=[]
        self.values['history']=self.values['history'][-100:]

    def _legacy_snapshot_is_blank(self):
        """Only zero counters plus a genuinely empty journal is a blank install.

        A zero-valued legacy_snapshot would collide with a real PC's non-zero
        migration event during the first exchange. A journal written before
        syncing was enabled still belongs in a stable legacy baseline, even
        when every counter is zero. Any non-empty history, including rows
        that are not dicts, must not be treated as a fresh device.
        """
        return (all(self.values[key] == 0 for key in COUNTERS)
                and self.values['history'] == [])

    def _legacy_history_invalid_index(self):
        """Return the first row that seed_legacy would not preserve exactly.

        legacy_event() drops non-dict rows, stringifies at/text and truncates
        them to 40/500 characters. Enabling sync over such a save would create
        a journal that silently differs from settings.json, so callers must
        refuse instead of letting a later peer baseline overwrite it.
        """
        history=self.values['history']
        # seed_legacy only migrates the last 100 rows, matching the existing
        # save truncation. Rows already discarded by that window cannot make
        # an otherwise valid old save unsyncable.
        for index in range(max(0,len(history)-100),len(history)):
            entry=history[index]
            if not (isinstance(entry, dict)
                    and set(entry) == {'at', 'text'}
                    and isinstance(entry['at'], str)
                    and isinstance(entry['text'], str)
                    and len(entry['at']) <= 40
                    and len(entry['text']) <= 500):
                return index
        return None

    def enable_sync_preview(self, ledger_path=None):
        """Opt in to a sidecar event ledger without replacing the old save."""
        if self.sync_ledger is not None:
            return self.sync_ledger
        path=Path(ledger_path) if ledger_path else self.path.with_suffix('.sync-v1.json')
        if path.exists():
            ledger=Ledger(path)
            expected=ledger.summary()
            if any(self.values[key] > expected[key] for key in COUNTERS):
                raise SyncError('settings.json counters are ahead of the sync ledger; manual recovery required')
            projected_history=ledger.journal_rows()
            previous_history=self.values['history']
            if previous_history != projected_history:
                overlap=max((length for length in range(1,min(len(previous_history),len(projected_history))+1)
                             if previous_history[-length:]==projected_history[:length]),default=0)
                if previous_history and overlap < max(1,min(len(previous_history),len(projected_history))//2):
                    raise SyncError('settings.json history differs from the sync ledger; manual recovery required')
            if (any(self.values[key] < expected[key] for key in COUNTERS)
                    or previous_history != projected_history):
                # The event was durable but the previous process closed before
                # it could project counters or journal into the legacy save.
                for key in COUNTERS:
                    self.values[key]=expected[key]
                self.values['history']=projected_history
                self.save()
        else:
            # Validate the complete legacy snapshot before touching the
            # backup or creating a sidecar. A refusal must leave both the
            # original settings and the sync directory untouched.
            if not self._legacy_snapshot_is_blank():
                invalid=self._legacy_history_invalid_index()
                if invalid is not None:
                    raise SyncError('settings.json history row {} cannot be '
                                    'preserved exactly; sync was not enabled'.format(invalid))
                legacy_event(self.values)
            if self.path.exists():
                backup=self.path.with_suffix('.pre-sync.json')
                if not backup.exists():
                    backup.parent.mkdir(parents=True,exist_ok=True)
                    with backup.open('xb') as target:
                        target.write(self.path.read_bytes())
            ledger=Ledger(path)
            if self._legacy_snapshot_is_blank():
                # Persist the generated device identity now, like the mobile
                # clients' empty ledger, so a restart cannot mint a new ID.
                # No legacy event means this install can accept one real PC
                # baseline later without a zero-baseline conflict.
                ledger.save()
            else:
                ledger.seed_legacy(self.values)
        self.sync_ledger=ledger
        return ledger

    def record_sync(self, kind, payload=None):
        if self.sync_ledger is None:
            raise SyncError('Sync preview is not enabled')
        event=self.sync_ledger.add(kind,payload)
        summary=self.sync_ledger.summary()
        for key in COUNTERS:
            self.values[key]=summary[key]
        self.save()
        return event

    def import_sync_bundle(self, data):
        if self.sync_ledger is None:
            raise SyncError('Sync preview is not enabled')
        added=self.sync_ledger.import_bundle(data)
        if added:
            summary=self.sync_ledger.summary()
            for key in COUNTERS:
                self.values[key]=summary[key]
            self.values['history']=self.sync_ledger.journal_rows()
            self.save()
        return added

    def __getitem__(self,key): return self.values[key]
    def __setitem__(self,key,value): self.values[key]=value
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.values,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.path)
    def log(self,text):
        if self.sync_ledger is not None:
            event=self.sync_ledger.add('journal',{'text':text})
            at=datetime.fromisoformat(event['occurred_at']).astimezone().strftime('%m-%d %H:%M')
        else:at=time.strftime('%m-%d %H:%M')
        self.values['history'].append({'at':at,'text':text})
        self.values['history']=self.values['history'][-100:]
        if self.sync_ledger is not None:
            self.save()
