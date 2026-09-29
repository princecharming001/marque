#!/usr/bin/env bash
# Idempotent Studio setup for an Ubuntu 24.04 host (a Claude Code cloud container, a CI box, a Linux server).
#
# Builds FFmpeg 8.1.2 from Ubuntu's source tarball (zscale/zimg, rubberband, libass, x264, x265, vidstab), installs
# the rubberband CLI, Node 24, the Python venv and the Remotion overlay project. Hosts that cannot reach
# remotion.media reuse a pre-installed Playwright headless shell (export STUDIO_REMOTION_BROWSER as printed below).
# Not available this way: libvmaf (CAMBI banding metric, advisory only) and AudioToolbox AAC (macOS only).
#
#   bash studio/scripts/setup-linux.sh          # from the repo root
set -euo pipefail

STUDIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${STUDIO_TOOLS_DIR:-$HOME/tools}"
FF_VER=8.1.2
FF_PREFIX=/opt/ffmpeg8
mkdir -p "$TOOLS"

# ---- system packages
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true   # unreachable PPAs are not fatal
apt-get install -y -qq build-essential nasm pkg-config libzimg-dev librubberband-dev rubberband-cli libass-dev \
  libx264-dev libx265-dev libvidstab-dev libfreetype-dev libfontconfig-dev libharfbuzz-dev libfribidi-dev \
  libmp3lame-dev libopus-dev libsoxr-dev libgles2 libegl1 libgl1 python3.12-venv python3.12-dev fonts-dejavu-core >/dev/null

# ---- FFmpeg 8
if ! "$FF_PREFIX/bin/ffmpeg" -hide_banner -filters 2>/dev/null | grep -qw zscale; then
  cd "$TOOLS"
  [ -f "ffmpeg_${FF_VER}.orig.tar.xz" ] || \
    curl -sSfO "http://archive.ubuntu.com/ubuntu/pool/universe/f/ffmpeg/ffmpeg_${FF_VER}.orig.tar.xz"
  [ -d "ffmpeg-${FF_VER}" ] || tar xf "ffmpeg_${FF_VER}.orig.tar.xz"
  cd "ffmpeg-${FF_VER}"
  ./configure --prefix="$FF_PREFIX" --enable-gpl --enable-version3 --disable-doc --disable-debug \
    --enable-libx264 --enable-libx265 --enable-libvidstab --enable-libzimg --enable-librubberband --enable-libass \
    --enable-libfreetype --enable-libfontconfig --enable-libharfbuzz --enable-libfribidi --enable-libmp3lame \
    --enable-libopus --enable-libsoxr >/dev/null
  make -j"$(nproc)" >/dev/null && make install >/dev/null
fi
ln -sf "$FF_PREFIX/bin/ffmpeg" /usr/local/bin/ffmpeg
ln -sf "$FF_PREFIX/bin/ffprobe" /usr/local/bin/ffprobe

# ---- Node 24
if ! /opt/node24/bin/node -v 2>/dev/null | grep -q '^v24'; then
  cd "$TOOLS"
  NODE_TAR=$(curl -sSf https://nodejs.org/dist/latest-v24.x/ | grep -oE 'node-v24\.[0-9.]+-linux-x64\.tar\.xz' | head -1)
  curl -sSfO "https://nodejs.org/dist/latest-v24.x/$NODE_TAR"
  tar xf "$NODE_TAR"
  ln -sfn "$TOOLS/${NODE_TAR%.tar.xz}" /opt/node24
fi
export PATH=/opt/node24/bin:$PATH

# ---- Python
cd "$STUDIO"
[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip wheel
.venv/bin/pip install -q -e ".[dev,test]"
# a pre-installed Playwright Chromium pins the matching Python Playwright (no browser download needed)
PW_JSON=$(ls /opt/node*/lib/node_modules/playwright/package.json 2>/dev/null | head -1 || true)
if [ -n "$PW_JSON" ] && [ -d /opt/pw-browsers ]; then
  .venv/bin/pip install -q "playwright==$(python3 -c "import json;print(json.load(open('$PW_JSON'))['version'])")"
fi

# ---- Remotion overlay project
cd "$STUDIO/overlay"
[ -x node_modules/.bin/remotion ] || npm ci --no-audit --no-fund >/dev/null
SHELL_BIN=$(ls /opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell 2>/dev/null | head -1 || true)
if ! npx remotion browser ensure >/dev/null 2>&1; then
  if [ -n "$SHELL_BIN" ]; then
    echo "remotion.media unreachable: export STUDIO_REMOTION_BROWSER=$SHELL_BIN"
  else
    echo "WARNING: no headless Chrome for Remotion (remotion.media unreachable, no Playwright shell found)" >&2
  fi
fi

ffmpeg -hide_banner -version | head -1
echo "Studio setup done. PATH needs /opt/node24/bin."
