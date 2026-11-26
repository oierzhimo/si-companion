"""Resilient auto-reconnecting WAN HTTPS tunnel for Si Web & Sync Server (port 8765)."""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time

WEB_ROOT = pathlib.Path(__file__).resolve().parent
URL_FILE = WEB_ROOT / 'wan_url.txt'
SSH = r'C:\WINDOWS\System32\OpenSSH\ssh.exe'
URL_RE = re.compile(r'https://[a-zA-Z0-9.-]+\.(?:serveousercontent\.com|lhr\.life)')

TARGETS = [
    [
        SSH, '-T', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL',
        '-o', 'ServerAliveInterval=25', '-o', 'ServerAliveCountMax=3',
        '-o', 'ExitOnForwardFailure=yes', '-R', '80:127.0.0.1:8765', 'serveo.net',
    ],
    [
        SSH, '-T', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL',
        '-o', 'ServerAliveInterval=25', '-o', 'ServerAliveCountMax=3',
        '-o', 'ExitOnForwardFailure=yes', '-R', '80:127.0.0.1:8765', 'nokey@localhost.run',
    ],
]


def main() -> None:
    idx = 0
    while True:
        cmd = TARGETS[idx % len(TARGETS)]
        idx += 1
        print(f'[wan-tunnel] connecting via {cmd[-1]} ...', flush=True)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
                if clean and len(clean) < 200:
                    print(clean, flush=True)
                m = URL_RE.search(clean)
                if m:
                    url = m.group(0)
                    URL_FILE.write_text(url + '\n', encoding='utf-8')
                    print(f'[wan-tunnel] ACTIVE WAN URL: {url}', flush=True)
            proc.wait(timeout=5)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f'[wan-tunnel] error: {exc}', flush=True)
        time.sleep(3)


if __name__ == '__main__':
    main()
