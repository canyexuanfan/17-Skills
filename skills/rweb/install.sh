#!/usr/bin/env bash
# rweb installer — puts a `rweb` launcher on your PATH.
#
#   bash install.sh            # installs to ~/.local/bin
#   RWEB_BIN_DIR=/usr/local/bin bash install.sh
#
# Also works from Git Bash / MSYS on Windows (a POSIX launcher is written).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${RWEB_BIN_DIR:-$HOME/.local/bin}"
PY="${RWEB_PY:-python3}"

if [ ! -f "$SRC/rweb/cli.py" ]; then
  echo "[rweb] $SRC/rweb/cli.py not found — run this from the repo root" >&2
  exit 2
fi

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[rweb] $PY not found — set RWEB_PY or install Python 3.8+" >&2
  exit 2
fi

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/rweb" <<EOF
#!/usr/bin/env bash
exec "$PY" "$SRC/rweb/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/rweb"

# Windows users: also drop the PowerShell launcher next to the repo.
if [ -f "$SRC/bin/rweb.ps1" ]; then
  cp "$SRC/bin/rweb.ps1" "$BIN_DIR/rweb.ps1" 2>/dev/null || true
fi

echo "installed: $BIN_DIR/rweb"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "add it to PATH if it is not there already:"
     echo "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

"$BIN_DIR/rweb" --version || true
