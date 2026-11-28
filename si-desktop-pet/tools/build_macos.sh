#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="2.1.2"
ARCH="$(uname -m)"
NAME="SiDesktopPet-${VERSION}-macOS-${ARCH}"
PYTHON="${PYTHON:-python3}"

echo "==> Building ${NAME} from ${ROOT}"

# 1. Generate macOS .icns icon from portraits/icon_badge.png when available
ICON_PNG="$ROOT/assets/portraits/icon_badge.png"
ICNS_PATH="$ROOT/assets/si.icns"
if [[ -f "$ICON_PNG" ]] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  ICONSET_DIR="$(mktemp -d "${TMPDIR:-/tmp}/si-icon.XXXXXX")/si.iconset"
  mkdir -p "$ICONSET_DIR"
  for sz in 16 32 64 128 256 512; do
    sips -z "$sz" "$sz" "$ICON_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}.png" >/dev/null
    sz2=$((sz * 2))
    sips -z "$sz2" "$sz2" "$ICON_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}@2x.png" >/dev/null
  done
  cp "$ICONSET_DIR/icon_512x512@2x.png" "$ICONSET_DIR/icon_512x512@2x.png" 2>/dev/null || true
  iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"
  rm -rf "$(dirname "$ICONSET_DIR")"
  echo "Generated $ICNS_PATH"
fi

# 2. Run full Python unit test suite in offscreen mode
QT_QPA_PLATFORM=offscreen PYTHONPATH="$ROOT/src" "$PYTHON" -m unittest discover -s "$ROOT/tests" -p "test_*.py" -v

# 3. Build macOS .app via PyInstaller
"$PYTHON" -m PyInstaller --noconfirm --clean "$ROOT/SiDesktopPet-macOS.spec"
APP_PATH="$ROOT/dist/SiDesktopPet.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing built app bundle: $APP_PATH" >&2
  exit 1
fi

# Ad-hoc sign so Apple Silicon macOS never kills unsigned Mach-O binaries
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_PATH"
fi

# 4. Frozen smoke test in offscreen mode
SMOKE_REPORT="$ROOT/qa/smoke-macos-${ARCH}.json"
mkdir -p "$ROOT/qa"
rm -f "$SMOKE_REPORT"
QT_QPA_PLATFORM=offscreen "$APP_PATH/Contents/MacOS/Si-DesktopPet" --quiet --smoke-test "$SMOKE_REPORT"
"$PYTHON" -c "import json, sys; from pathlib import Path; p=json.loads(Path('$SMOKE_REPORT').read_text(encoding='utf-8')); print(json.dumps(p, ensure_ascii=False, indent=2)); sys.exit(0 if p.get('ok') else 1)"

# 5. Package DMG + ZIP + SHA-256
mkdir -p "$ROOT/release"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/si-dmg-stage.XXXXXX")"
cp -R "$APP_PATH" "$STAGE_DIR/SiDesktopPet.app"
ln -s /Applications "$STAGE_DIR/Applications"
cat > "$STAGE_DIR/macOS使用说明.txt" <<'EOF'
祀桌宠（SiDesktopPet）v2.1.2 macOS / Mac mini 使用说明
=========================================================

1. 将 SiDesktopPet.app 拖入「Applications（应用程序）」文件夹。
2. 首次双击运行若系统提示“无法验证开发者”（Gatekeeper 拦截）：
   - 方法 A：打开「系统设置」->「隐私与安全性」-> 点击「仍要打开」；
   - 方法 B：在终端执行一次：
     xattr -cr /Applications/SiDesktopPet.app
3. 存档路径：~/Library/Application Support/SiDesktopPet/data/
4. 本地 AI（可选）：Mac mini 安装 Ollama 后选择「本机模型（11434）」即可；
   或选择 DeepSeek 官方 API / 自定义接口（API Key 仅留在内存，绝不写盘）。
EOF

DMG_PATH="$ROOT/release/${NAME}.dmg"
ZIP_PATH="$ROOT/release/${NAME}.zip"
rm -f "$DMG_PATH" "$ZIP_PATH"

if command -v hdiutil >/dev/null 2>&1; then
  hdiutil create -volname "SiDesktopPet ${VERSION}" -srcfolder "$STAGE_DIR" -ov -format UDZO "$DMG_PATH"
fi

ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
rm -rf "$STAGE_DIR"

"$PYTHON" - <<PY
import hashlib, json
from pathlib import Path

release_dir = Path("$ROOT/release")
out = {}
for name in ("${NAME}.dmg", "${NAME}.zip"):
    p = release_dir / name
    if p.is_file():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        out[name] = {"bytes": p.stat().st_size, "sha256": h}
(release_dir / "SHA256SUMS-${VERSION}-macOS-${ARCH}.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
