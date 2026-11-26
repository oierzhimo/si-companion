"""Live Web Showcase & Companion Chat-Data Sync Server for si_accompany.

Serves the bilingual Si Companion v2.1.2 website AND provides real-time
read/write synchronization with `si-desktop-pet/data` (`settings.json`,
`settings.sync-v1.json`, `multimodal_conversation.json`) plus rate-limited
live Soul Engine V1.2 chat via local Ollama (`127.0.0.1:11434`).
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import sys
import threading
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent
PET_ROOT = REPO_ROOT / 'si-desktop-pet'
PET_DATA = PET_ROOT / 'data'

if str(PET_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(PET_ROOT / 'src'))

from si_pet import __version__, native  # noqa: E402
from si_pet.chat_backend import ChatError, ChatSession  # noqa: E402
from si_pet.conversation import ConversationError, request_reply  # noqa: E402
from si_pet.storage import Store  # noqa: E402

_STATE_LOCK = threading.Lock()
_RATE_WINDOW_S = 60.0
_MAX_CHATS_PER_MIN = 8
_CLIENT_HITS: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
    dq = _CLIENT_HITS[client_ip]
    while dq and now - dq[0] > _RATE_WINDOW_S:
        dq.popleft()
    if len(dq) >= _MAX_CHATS_PER_MIN:
        return False
    dq.append(now)
    return True


def build_sync_snapshot() -> dict:
    """Read current companion counters, journal, voice policy, and synced chat turns."""
    with _STATE_LOCK:
        store = Store(PET_DATA / 'settings.json')
        try:
            ledger = store.enable_sync_preview(PET_DATA / 'settings.sync-v1.json')
        except Exception:
            ledger = store.sync_ledger

        mm_path = PET_DATA / 'multimodal_conversation.json'
        turns_out = []
        try:
            session = ChatSession(mm_path)
            if ledger is not None:
                session.sync_with_ledger(ledger)
            for t in session.turns[-10:]:
                turns_out.append({
                    'user': str(t.get('user', ''))[:500],
                    'assistant': str(t.get('assistant', ''))[:1200],
                    'occurred_at': str(t.get('occurred_at', '')),
                })
        except Exception:
            turns_out = []

        journal_out = []
        for row in (store['history'] or [])[-8:]:
            if isinstance(row, dict):
                journal_out.append({
                    'at': str(row.get('at', ''))[:40],
                    'text': str(row.get('text', ''))[:240],
                })

        voice_ok, voice_reason = native.voice_output_allowed(store)
        return {
            'live': True,
            'version': __version__,
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'device_id': getattr(ledger, 'device_id', 'local-desktop')[:16],
            'stats': {
                'affinity': int(store['affinity']),
                'pats': int(store['pats']),
                'snacks': int(store['snacks']),
                'focus_sessions': int(store['focus_sessions']),
                'mood': int(store['mood']),
                'total_minutes': int(store['total_seconds'] // 60),
            },
            'voice': {
                'active': voice_ok,
                'summary': voice_reason,
            },
            'journal': list(reversed(journal_out)),
            'turns': turns_out,
        }


def handle_live_chat(user_text: str) -> dict:
    """Send *user_text* to local Si Soul Engine model and persist into sync ledger + session."""
    text = (user_text or '').strip()
    if not text or len(text) > 500:
        raise ValueError('请输入 1 至 500 字的消息。')

    with _STATE_LOCK:
        store = Store(PET_DATA / 'settings.json')
        try:
            store.enable_sync_preview(PET_DATA / 'settings.sync-v1.json')
        except Exception:
            pass
        mm_path = PET_DATA / 'multimodal_conversation.json'
        session = ChatSession(mm_path)
        if store.sync_ledger is not None:
            session.sync_with_ledger(store.sync_ledger)
        endpoint = os.environ.get('SI_WEB_LLM_ENDPOINT', 'http://127.0.0.1:11434/v1')
        model = os.environ.get('SI_WEB_LLM_MODEL', 'si-qwen2.5-3b-lang-lora:latest')
        messages = session.messages(text)

    # Run model inference outside lock so status polling never blocks
    reply = request_reply(endpoint, model, messages, '')

    with _STATE_LOCK:
        store = Store(PET_DATA / 'settings.json')
        try:
            store.enable_sync_preview(PET_DATA / 'settings.sync-v1.json')
        except Exception:
            pass
        session = ChatSession(PET_DATA / 'multimodal_conversation.json')
        if store.sync_ledger is not None:
            session.sync_with_ledger(store.sync_ledger)
        ev = store.record_chat_turn(text, reply, ())
        eid = str(ev.get('event_id', '')) if isinstance(ev, dict) else ''
        session.commit(text, reply, event_id=eid)
        store.log('通过网页同步与管理员聊了一句')
        store.save()

        # Trigger smart headphone/game-gated voice on the host PC if headphones are active!
        voice_triggered = False
        allowed, voice_reason = native.voice_output_allowed(store, force_refresh=True)
        if allowed:
            def _bg_speak():
                try:
                    import tempfile
                    import winsound
                    from si_pet.chat_backend import synthesize_voice
                    from si_pet.voice import scaled_wav

                    wav_bytes = synthesize_voice(reply, profile_id=str(store['voice_profile_id']))
                    if native.running_blocked_game(extra_names=store.get('voice_blocked_games', ''), force_refresh=True):
                        return
                    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
                        tf.write(wav_bytes)
                        tmp = Path(tf.name)
                    scaled = scaled_wav(tmp, int(float(store['voice_volume'])))
                    try:
                        winsound.PlaySound(str(scaled), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                    finally:
                        for p in (scaled, tmp):
                            Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

            threading.Thread(target=_bg_speak, daemon=True, name='si-web-tts').start()
            voice_triggered = True

    return {
        'ok': True,
        'reply': reply,
        'voice_triggered': voice_triggered,
        'voice_summary': voice_reason,
        'snapshot': build_sync_snapshot(),
    }


def handle_companion_action(action: str) -> dict:
    """Record a gentle remote headpat or snack into the synced ledger."""
    if action not in ('pat', 'snack'):
        raise ValueError('不支持的互动动作')
    with _STATE_LOCK:
        store = Store(PET_DATA / 'settings.json')
        try:
            store.enable_sync_preview(PET_DATA / 'settings.sync-v1.json')
        except Exception:
            pass
        if store.sync_ledger is not None:
            store.record_sync(action)
        else:
            if action == 'pat':
                store['pats'] += 1
                store['affinity'] += 1
            else:
                store['snacks'] += 1
                store['affinity'] += 3
        store['mood'] = min(100, int(store['mood']) + (2 if action == 'pat' else 6))
        store.log('网页端摸了摸她的头' if action == 'pat' else '网页端送了一份点心')
        store.save()
    return {'ok': True, 'snapshot': build_sync_snapshot()}


class SiWebHandler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        sys.stdout.write('[si-web] ' + (fmt % args) + '\n')
        sys.stdout.flush()

    def _send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        clean_path = self.path.split('?', 1)[0]
        if clean_path in ('/api/sync-status', '/api/health'):
            try:
                self._send_json(200, build_sync_snapshot())
            except Exception as exc:
                self._send_json(500, {'live': False, 'error': str(exc)})
            return

        # Strict static allow-list inside WEB_ROOT
        rel = clean_path.lstrip('/') or 'index.html'
        candidate = (WEB_ROOT / rel).resolve()
        if not candidate.is_relative_to(WEB_ROOT) or not candidate.is_file() or candidate.name == 'server.py':
            self._send_json(404, {'error': 'Not Found'})
            return
        ctype, _ = mimetypes.guess_type(str(candidate))
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype or 'application/octet-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        clean_path = self.path.split('?', 1)[0]
        client_ip = self.headers.get('CF-Connecting-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or self.client_address[0]
        if not _check_rate_limit(client_ip):
            self._send_json(429, {'ok': False, 'error': '请求过于频繁，请稍后再试。'})
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if length <= 0 or length > 16_384:
            self._send_json(400, {'ok': False, 'error': '请求内容大小无效。'})
            return
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            self._send_json(400, {'ok': False, 'error': 'JSON 格式无效。'})
            return

        if clean_path == '/api/chat':
            try:
                res = handle_live_chat(str(body.get('message', '')))
                self._send_json(200, res)
            except (ValueError, ConversationError, ChatError) as exc:
                self._send_json(400, {'ok': False, 'error': str(exc)})
            except Exception:
                self._send_json(502, {'ok': False, 'error': '本机模型暂未响应，请稍后重试。'})
            return

        if clean_path == '/api/interact':
            try:
                res = handle_companion_action(str(body.get('action', '')))
                self._send_json(200, res)
            except ValueError as exc:
                self._send_json(400, {'ok': False, 'error': str(exc)})
            except Exception:
                self._send_json(500, {'ok': False, 'error': '同步互动暂不可用。'})
            return

        self._send_json(404, {'ok': False, 'error': '未知接口'})


def main() -> int:
    parser = argparse.ArgumentParser(description='Si Companion Web & Live Sync Server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--dump-snapshot', type=Path, help='Write sync-snapshot.json and exit')
    args = parser.parse_args()

    snap = build_sync_snapshot()
    snap_static = dict(
        snap,
        live=False,
        turns=[
            {
                'user': '小祀，今天也在桌面上陪我一起忙吗？',
                'assistant': '（祀执笔在宣纸角落轻轻落墨，推过一杯温茶）哼，既然管理员都开口了，我就在这儿陪你待一会儿。戴上耳机且没打游戏的时候，我也会出声叫你的。',
                'occurred_at': snap['updated_at'],
            }
        ],
    )
    (WEB_ROOT / 'sync-snapshot.json').write_text(
        json.dumps(snap_static, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    if args.dump_snapshot:
        args.dump_snapshot.write_text(
            json.dumps(snap_static, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        return 0

    httpd = ThreadingHTTPServer((args.host, args.port), SiWebHandler)
    print(f'Si Web & Sync Server listening on http://{args.host}:{args.port}', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
