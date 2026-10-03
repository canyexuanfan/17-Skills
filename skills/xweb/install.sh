#!/usr/bin/env bash
# xweb 安装脚本：装依赖 + 把启动器放进 PATH
#
#   ./install.sh                    # 默认装到 ~/.local/bin
#   XWEB_BIN_DIR=/usr/local/bin ./install.sh
#
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="${XWEB_PY:-python3}"
BIN="${XWEB_BIN_DIR:-$HOME/.local/bin}"

command -v "$PY" >/dev/null 2>&1 || { echo "✗ 需要 python3（>= 3.9），没找到 $PY" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' \
  || { echo "✗ 需要 Python 3.9 或更高版本" >&2; exit 1; }

echo "== 安装依赖 curl_cffi =="
if "$PY" -c 'import curl_cffi' >/dev/null 2>&1; then
  echo "  已安装，跳过"
else
  "$PY" -m pip install --user --quiet curl_cffi \
    || "$PY" -m pip install --quiet --break-system-packages curl_cffi \
    || { echo "✗ 装 curl_cffi 失败，请手动：$PY -m pip install curl_cffi" >&2; exit 1; }
fi

chmod +x "$REPO/bin/xweb"
mkdir -p "$BIN"
ln -sf "$REPO/bin/xweb" "$BIN/xweb"
echo "== 启动器已链接：$BIN/xweb -> $REPO/bin/xweb =="

case ":$PATH:" in
  *":$BIN:"*) : ;;
  *) echo "⚠️  $BIN 不在 PATH 里，把它加进去："
     echo "     echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.bashrc && source ~/.bashrc" ;;
esac

echo "== 自检 =="
"$BIN/xweb" caps >/dev/null && echo "✓ 安装完成。试一下：$BIN/xweb elonmusk"
