"""Small loopback-only client for an existing VoiceStudio installation."""
import hashlib
import http.client
import io
import json
import logging
import os
from pathlib import Path
import threading
import urllib.parse
import wave


HEALTH_TIMEOUT=1.0


class VoiceUnavailable(RuntimeError):
    pass


def validate_wav(payload):
    try:
        with wave.open(io.BytesIO(payload), 'rb') as wav:
            channels=wav.getnchannels();rate=wav.getframerate()
            width=wav.getsampwidth();frames=wav.getnframes()
            if channels not in (1,2) or width!=2 or not 8000<=rate<=96000 or frames<rate//10:
                raise ValueError('invalid PCM audio parameters')
            if frames/rate>45:raise ValueError('unexpected audio duration')
            samples=wav.readframes(frames)
            if len(samples)!=frames*channels*width or not any(samples):
                raise ValueError('truncated or silent audio')
    except (wave.Error,EOFError,ValueError) as exc:
        raise VoiceUnavailable('VoiceStudio 未返回可播放的 16 位 WAV 音频') from exc


class VoiceClient:
    def __init__(self,cache_dir,base_url='http://127.0.0.1:3900',timeout=180):
        url=urllib.parse.urlsplit(base_url)
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.port!=3900 or url.path or url.query:
            raise ValueError('VoiceStudio connection must stay on 127.0.0.1:3900')
        self.base_url=base_url;self.cache_dir=Path(cache_dir);self.timeout=timeout

    def _request(self,path,data=None,timeout=None):
        timeout=self.timeout if timeout is None else timeout
        connection=http.client.HTTPConnection('127.0.0.1',3900,timeout=timeout)
        headers={'Content-Type':'application/x-www-form-urlencoded; charset=utf-8'} if data is not None else {}
        try:
            connection.request('POST' if data is not None else 'GET',path,body=data,headers=headers)
            response=connection.getresponse()
            if response.status!=200:
                raise VoiceUnavailable(f'VoiceStudio 请求失败（HTTP {response.status}）')
            try:
                payload=response.read(20_000_001)
                if len(payload)>20_000_000:raise VoiceUnavailable('VoiceStudio 音频过大')
                return payload
            finally:response.close()
        except (OSError,TimeoutError,ConnectionError) as exc:
            raise VoiceUnavailable('VoiceStudio 未连接或生成超时') from exc
        finally:connection.close()

    def health(self,timeout=HEALTH_TIMEOUT):
        """Return whether /health is ready, bounded by a short wall-clock deadline."""
        result=[];finished=threading.Event()
        def probe():
            try:
                payload=self._request('/health',timeout=timeout)
                status=json.loads(payload)
                result.append(isinstance(status,dict) and status.get('status')=='ok')
            except Exception:
                logging.debug('VoiceStudio health probe failed',exc_info=True)
                result.append(False)
            finally:finished.set()
        threading.Thread(target=probe,name='SiVoiceStudioHealthHTTP',daemon=True).start()
        finished.wait(timeout+0.1)
        return bool(result and result[0])

    def profile_exists(self,profile_id):
        try:profiles=json.loads(self._request('/profiles'))
        except (json.JSONDecodeError,UnicodeError) as exc:raise VoiceUnavailable('无法读取 VoiceStudio 声线列表') from exc
        return isinstance(profiles,list) and any(isinstance(p,dict) and str(p.get('id'))==profile_id for p in profiles)

    def render(self,text,profile_id):
        text=text.strip();profile_id=profile_id.strip()
        if not text or len(text)>120:raise ValueError('speech text must contain 1–120 characters')
        if not profile_id:raise VoiceUnavailable('请先设置 VoiceStudio 声线 ID')
        if not self.profile_exists(profile_id):raise VoiceUnavailable('选定的声线不在当前 VoiceStudio 中')
        digest=hashlib.sha256(f'v1\0{profile_id}\0{text}'.encode('utf-8')).hexdigest()
        path=self.cache_dir/f'{digest}.wav'
        if path.is_file():
            try:validate_wav(path.read_bytes());return path
            except VoiceUnavailable:path.unlink(missing_ok=True)
        form=urllib.parse.urlencode({'text':text,'profile_id':profile_id,'language':'Chinese','engine':'audiocpp'}).encode('utf-8')
        payload=self._request('/generate',form);validate_wav(payload)
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_bytes(payload);os.replace(temp,path)
        files=sorted(self.cache_dir.glob('*.wav'),key=lambda p:p.stat().st_mtime,reverse=True)
        for stale in files[80:]:stale.unlink(missing_ok=True)
        return path
