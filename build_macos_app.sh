#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="TradSubtitleBurner"
APP_DIR="$ROOT_DIR/dist/${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RES_DIR="$CONTENTS_DIR/Resources"

mkdir -p "$MACOS_DIR" "$RES_DIR"

cp "$ROOT_DIR/app.py" "$RES_DIR/app.py"
cp "$ROOT_DIR/assets/app.icns" "$RES_DIR/app.icns"

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>TradSubtitleBurner</string>
  <key>CFBundleIdentifier</key>
  <string>com.autocaption.tradsubtitleburner</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleIconFile</key>
  <string>app.icns</string>
  <key>CFBundleName</key>
  <string>TradSubtitleBurner</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/$APP_NAME" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
RES_DIR="$(cd "$SELF_DIR/../Resources" && pwd)"
PROJECT_DIR_FALLBACK="__PROJECT_ROOT__"
PROJECT_DIR="$(cd "$SELF_DIR/../../../.." && pwd)"
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ] && [ -x "$PROJECT_DIR_FALLBACK/.venv/bin/python" ]; then
  PROJECT_DIR="$PROJECT_DIR_FALLBACK"
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY_BIN="$PROJECT_DIR/.venv/bin/python"
elif command -v /usr/local/bin/python3 >/dev/null 2>&1; then
  PY_BIN="/usr/local/bin/python3"
elif command -v /opt/homebrew/bin/python3 >/dev/null 2>&1; then
  PY_BIN="/opt/homebrew/bin/python3"
else
  PY_BIN="/usr/bin/python3"
fi

if ! "$PY_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
mods = ["opencc", "tkinterdnd2", "faster_whisper"]
raise SystemExit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)
PY
then
  /usr/bin/osascript -e 'display alert "依赖缺失" message "请先执行: pip3 install -r requirements.txt（或 .venv/bin/pip install -r requirements.txt）" as warning'
  exit 1
fi

exec "$PY_BIN" "$RES_DIR/app.py"
LAUNCHER

sed -i '' "s|__PROJECT_ROOT__|$ROOT_DIR|g" "$MACOS_DIR/$APP_NAME"

chmod +x "$MACOS_DIR/$APP_NAME"

echo "Built app: $APP_DIR"
