"""Optional spoken replies; all synthesis runs away from the Qt UI thread."""
import logging
import json
import os
import threading
import time
import urllib.request
import wave
from pathlib import Path
from PySide6.QtCore import QObject,Signal,Slot,QProcess,Qt
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
        self._busy=False;self._lock=threading.Lock();self._last=('',0.0)
        self.backend=None;self._owned_backend=None;self._backend_shutdown=False
        self._probe_lock=threading.Lock();self._probe_generation=0;self._probing=False
        self.ready.connect(self._play);self.failed.connect(self._show_failure)
        self._health_ready.connect(self._health_done,Qt.ConnectionType.QueuedConnection)

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

    def speak(self,text):
        if not self.store['voice_enabled'] or winsound is None:return
        text=text.strip();now=time.monotonic()
        if not text or len(text)>120 or self.store['voice_volume']==0:return
        with self._lock:
            if self._busy or (text==self._last[0] and now-self._last[1]<12):return
            self._busy=True;self._last=(text,now)
        profile_id=self.store['voice_profile_id']
        self.status.emit('正在请 VoiceStudio 发声…')
        def work():
            try:self.ready.emit(str(self.client.render(text,profile_id)))
            except (VoiceUnavailable,OSError,ValueError) as exc:self.failed.emit(str(exc))
            except Exception:
                logging.exception('Unexpected VoiceStudio error');self.failed.emit('语音暂不可用，文字陪伴仍正常')
            finally:
                with self._lock:self._busy=False
        threading.Thread(target=work,name='SiVoiceStudio',daemon=True).start()

    @Slot(str)
    def _play(self,path):
        if not self.store['voice_enabled'] or winsound is None:return
        try:
            volume=int(self.store['voice_volume'])
            if volume<=0:return
            playback=scaled_wav(path,volume)
            winsound.PlaySound(str(playback),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
            self.status.emit('已播放 · 可随时关闭语音')
        except (OSError,wave.Error) as exc:self._show_failure(f'播放失败：{exc}')

    @Slot(str)
    def _show_failure(self,message):
        logging.warning('VoiceStudio: %s',message)
        self.status.emit(message)

    def stop(self):
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
