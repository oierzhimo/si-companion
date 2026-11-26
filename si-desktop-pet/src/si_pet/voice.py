"""Optional spoken replies; all synthesis runs away from the Qt UI thread."""
import logging
import json
import os
import threading
import time
import urllib.request
import wave
from pathlib import Path
from PySide6.QtCore import QObject,Signal,Slot,QProcess,QProcessEnvironment,QTimer,Qt
from . import native
from .storage import data_root
from .voice_client import VoiceClient,VoiceUnavailable

try:import winsound
except ImportError:winsound=None


def scaled_wav(path,volume):
    if volume>=100:return Path(path)
    from array import array
    import sys
    source=Path(path);target=source.with_name(f'{source.stem}-volume-{volume}.wav')
    if target.exists() and target.stat().st_mtime>=source.stat().st_mtime:return target
    with wave.open(str(source),'rb') as reader:
        params=reader.getparams();samples=reader.readframes(reader.getnframes())
    pcm=array('h');pcm.frombytes(samples)
    if sys.byteorder!='little':pcm.byteswap()
    pcm=array('h',(round(value*volume/100) for value in pcm))
    if sys.byteorder!='little':pcm.byteswap()
    with wave.open(str(target),'wb') as writer:
        writer.setparams(params);writer.writeframes(pcm.tobytes())
    return target


class VoiceEngine(QObject):
    ready=Signal(str)
    failed=Signal(str)
    status=Signal(str)
    _health_ready=Signal(int,bool)

    def __init__(self,store):
        super().__init__();self.store=store
        self.client=VoiceClient(data_root()/'voice_cache')
        self._busy=False;self._lock=threading.Lock();self._last=('',0.0);self._playing=False
        self.backend=None;self._owned_backend=None;self._backend_shutdown=False
        self._probe_lock=threading.Lock();self._probe_generation=0;self._probing=False
        self._auto_warmed=False
        self.ready.connect(self._play);self.failed.connect(self._show_failure)
        self._health_ready.connect(self._health_done,Qt.ConnectionType.QueuedConnection)
        self._env_timer=QTimer(self);self._env_timer.setInterval(2500)
        self._env_timer.timeout.connect(self._poll_audio_game_guard)
        if self._store_get('voice_auto_headphone',False):
            self._env_timer.start()

    def _store_get(self,key,default=None):
        if hasattr(self.store,'get'):return self.store.get(key,default)
        try:return self.store[key]
        except (KeyError,TypeError):return default

    def effective_enabled(self,*,force_refresh=False):
        allowed,_reason=native.voice_output_allowed(self.store,force_refresh=force_refresh)
        return allowed

    def environment_summary(self,*,force_refresh=False):
        _allowed,reason=native.voice_output_allowed(self.store,force_refresh=force_refresh)
        return reason

    def _poll_audio_game_guard(self):
        if self._backend_shutdown:return
        allowed,reason=native.voice_output_allowed(self.store)
        if not allowed and self._playing:
            self._playing=False
            if winsound is not None:winsound.PlaySound(None,0)
            self.status.emit(reason)
        elif allowed and not self._auto_warmed and winsound is not None:
            self._auto_warmed=True
            self.start_backend()

    def start_backend(self):
        with self._probe_lock:
            if self._probing:
                message='正在检查 VoiceStudio 引擎…'
            elif self.backend is not None and self.backend.state()!=QProcess.ProcessState.NotRunning:
                message='VoiceStudio 引擎正在启动…'
            else:
                message=None
                self._backend_shutdown=False
                self._probe_generation+=1
                generation=self._probe_generation
                self._probing=True
        if message is not None:
            self.status.emit(message);return
        self.status.emit('正在检查 VoiceStudio 引擎…')
        threading.Thread(target=self._probe_health,args=(generation,),name='SiVoiceStudioHealth',daemon=True).start()

    def _probe_health(self,generation):
        try:healthy=bool(self.client.health())
        except Exception:
            logging.exception('Unexpected VoiceStudio health probe error');healthy=False
        try:self._health_ready.emit(generation,healthy)
        except RuntimeError:pass

    @Slot(int,bool)
    def _health_done(self,generation,healthy):
        with self._probe_lock:
            if generation!=self._probe_generation or self._backend_shutdown:return
            self._probing=False
        if healthy:
            self.status.emit('已连接现有 VoiceStudio 引擎');return
        self._start_local_backend(generation)

    def _start_local_backend(self,generation):
        with self._probe_lock:
            if generation!=self._probe_generation or self._backend_shutdown:return
            already_running=self.backend is not None and self.backend.state()!=QProcess.ProcessState.NotRunning
        if already_running:
            self.status.emit('VoiceStudio 引擎正在启动…');return
        root=Path(os.environ.get('APPDATA',str(Path.home()/'AppData/Roaming')))/'VoiceStudio'/'runtime-location.json'
        try:runtime=Path(json.loads(root.read_text(encoding='utf-8'))['root'])
        except (OSError,ValueError,KeyError,TypeError):
            self.status.emit('未找到 VoiceStudio 本地运行环境');return
        python=runtime/'project/.venv/Scripts/python.exe'
        backend=runtime/'project/backend/main.py'
        if not python.is_file() or not backend.is_file():
            self.status.emit('VoiceStudio 本地引擎文件不完整');return
        process=QProcess(self);process.setProgram(str(python));process.setArguments(['-u','main.py'])
        process.setWorkingDirectory(str(backend.parent));process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if hasattr(process,'setProcessEnvironment'):
            env=QProcessEnvironment.systemEnvironment()
            for k,v in (
                ('OMNIVOICE_TTS_BACKEND','audiocpp'),
                ('OMNIVOICE_AUDIOCPP_BACKEND','cpu'),
                ('OMNIVOICE_AUDIOCPP_THREADS','6'),
                ('OMNIVOICE_PRELOAD_TTS','0'),
                ('OMNIVOICE_PRELOAD_CAPTURE_ASR','0'),
                ('OMNIVOICE_PRELOAD_WATERMARK','0'),
                ('OMP_NUM_THREADS','4'),
            ):
                if not env.contains(k):env.insert(k,v)
            process.setProcessEnvironment(env)
        process.started.connect(lambda p=process:self._backend_started(p))
        process.errorOccurred.connect(lambda _error,p=process:self._backend_error(p))
        process.finished.connect(lambda *_args,p=process:self._backend_finished(p))
        with self._probe_lock:
            if generation!=self._probe_generation or self._backend_shutdown:
                process.deleteLater();return
            self._owned_backend=process;self.backend=process;process.start()

    def _backend_started(self,process):
        if process is not self._owned_backend or self._backend_shutdown:return
        self.status.emit('VoiceStudio 引擎正在启动，首次加载可能需要片刻…')

    def _backend_error(self,process):
        if process is not self._owned_backend or self._backend_shutdown:return
        self.status.emit('VoiceStudio 引擎未能启动')

    def _backend_finished(self,process):
        if process is not self._owned_backend or self._backend_shutdown:return
        self.status.emit('VoiceStudio 引擎已停止')

    def speak(self,text,*,force_audition=False):
        if winsound is None:return
        if force_audition:
            if native.running_blocked_game(extra_names=self._store_get('voice_blocked_games','')):
                self.status.emit('检测到本地游戏运行中 · 已暂停试听以免打扰');return
        elif not self.effective_enabled():
            return
        text=text.strip();now=time.monotonic()
        if not text or len(text)>2400 or float(self._store_get('voice_volume',65))<=0:return
        with self._lock:
            if self._busy or (not force_audition and text==self._last[0] and now-self._last[1]<12):return
            self._busy=True;self._last=(text,now)
        profile_id=str(self._store_get('voice_profile_id','06332768'))
        self._force_play_once=bool(force_audition)
        self.status.emit('正在请 VoiceStudio 发声（含拟声词与动作音效）…')
        if not self.client.health(timeout=0.6):
            self.start_backend()
        def work():
            try:
                try:
                    wav_path=self.client.render(text,profile_id)
                except VoiceUnavailable:
                    # Wait briefly if local VoiceStudio backend just started booting
                    if self._backend_shutdown:raise
                    deadline=time.monotonic()+35.0
                    ready_now=False
                    while not self._backend_shutdown and time.monotonic()<deadline:
                        if self.client.health(timeout=1.0):
                            ready_now=True;break
                        time.sleep(0.5)
                    if not ready_now:raise
                    wav_path=self.client.render(text,profile_id)
                self.ready.emit(str(wav_path))
            except (VoiceUnavailable,OSError,ValueError) as exc:self.failed.emit(str(exc))
            except Exception:
                logging.exception('Unexpected VoiceStudio error');self.failed.emit('语音暂不可用，文字陪伴仍正常')
            finally:
                with self._lock:self._busy=False
        threading.Thread(target=work,name='SiVoiceStudio',daemon=True).start()

    @Slot(str)
    def _play(self,path):
        force_once=getattr(self,'_force_play_once',False)
        self._force_play_once=False
        if winsound is None:return
        if not force_once and not self.effective_enabled(force_refresh=True):
            self.status.emit(self.environment_summary());return
        try:
            volume=int(float(self._store_get('voice_volume',65)))
            if volume<=0:return
            playback=scaled_wav(path,volume)
            self._playing=True
            winsound.PlaySound(str(playback),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
            self.status.emit('已同步播放语音与动作声 · 可随时关闭')
        except (OSError,wave.Error) as exc:
            self._playing=False;self._show_failure(f'播放失败：{exc}')

    @Slot(str)
    def _show_failure(self,message):
        logging.warning('VoiceStudio: %s',message)
        self.status.emit(message)

    def stop(self):
        self._playing=False
        if hasattr(self,'_env_timer'):self._env_timer.stop()
        if winsound is not None:winsound.PlaySound(None,0)
        with self._probe_lock:
            self._backend_shutdown=True
            self._probe_generation+=1
            self._probing=False
            process=self._owned_backend
        if process is not None and process.state()!=QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(2000):
                process.kill();process.waitForFinished(1000)
