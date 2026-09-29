#!/usr/bin/env bash
# =============================================================================
# breakout-cli Linux 一键安装 + 登录配置
# 让 Linux 用户获得与 Windows/macOS 一致的体验：
#   装官方 CLI → 微信扫码授权 → token 写入 keyring → doctor 全绿
#
# 用法:
#   bash setup.sh              # 完整流程（推荐）
#   bash setup.sh --dry-run    # 只装环境不登录（测试）
# =============================================================================
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGIN_PY="$SCRIPT_DIR/login.py"

echo "=============================================="
echo " breakout-cli Linux 安装与登录配置"
echo "=============================================="

# ---------- 1. 检查 Node.js >= 18 ----------
step_check_node() {
  echo ""
  echo "[1/5] 检查 Node.js..."
  if ! command -v node >/dev/null 2>&1; then
    echo "✗ 未安装 Node.js。安装方式："
    echo "   # Ubuntu/Debian:"
    echo "   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
    echo "   # CentOS/RHEL:"
    echo "   curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo yum install -y nodejs"
    return 1
  fi
  NODE_MAJOR=$(node -e 'console.log(process.versions.node.split(".")[0])')
  if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "✗ Node.js 版本过低: $(node -v)（需要 >= 18）"
    echo "   请升级: https://nodejs.org/"
    return 1
  fi
  echo "✓ Node.js $(node -v)"
}

# ---------- 2. 安装/更新官方 CLI ----------
step_install_cli() {
  echo ""
  echo "[2/5] 安装官方 CLI (@aipoju/breakout-cli)..."
  if command -v breakout >/dev/null 2>&1; then
    VER=$(breakout version 2>/dev/null | tail -1 || echo "?")
    echo "   已安装: $VER"
    echo "   如需更新: npm install -g @aipoju/breakout-cli@latest"
  else
    npm install -g @aipoju/breakout-cli 2>&1 | tail -3 || {
      echo "✗ npm 安装失败，尝试重试..."
      sleep 2
      npm install -g @aipoju/breakout-cli || return 1
    }
    echo "✓ CLI 安装完成: $(breakout version 2>/dev/null | tail -1)"
  fi
}

# ---------- 3. 安装 keyring 依赖 ----------
step_keyring_deps() {
  echo ""
  echo "[3/5] 检查 keyring 依赖 (gnome-keyring / libsecret-tools)..."
  MISSING=()
  command -v secret-tool >/dev/null 2>&1 || MISSING+=("libsecret-tools")
  command -v gnome-keyring-daemon >/dev/null 2>&1 || MISSING+=("gnome-keyring")
  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "✗ 缺少: ${MISSING[*]}"
    echo "   安装方式（任选你的发行版）:"
    echo "   # Ubuntu/Debian:  sudo apt-get install -y gnome-keyring libsecret-tools"
    echo "   # CentOS/RHEL:    sudo yum install -y gnome-keyring libsecret"
    echo "   # Fedora:         sudo dnf install -y gnome-keyring libsecret"
    echo "   # Arch:           sudo pacman -S --noconfirm gnome-keyring libsecret"
    return 1
  fi
  echo "✓ keyring 依赖就绪"
}

# ---------- 4. 启动 D-Bus + keyring（含自愈）----------
step_start_keyring() {
  echo ""
  echo "[4/5] 启动 D-Bus session 与 keyring..."
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    echo "   启动 session dbus..."
    eval "$(dbus-launch --sh-syntax)"
    echo "   export DBUS_SESSION_BUS_ADDRESS=\"$DBUS_SESSION_BUS_ADDRESS\"" >> ~/.bashrc
  fi

  # keyring 可用性自检：能写入 = 正常；锁死/多实例冲突 = 重置
  keyring_ok() {
    printf '%s' 'probe' | secret-tool store --label='probe' service breakout-probe username probe >/dev/null 2>&1 \
      && secret-tool clear service breakout-probe username probe >/dev/null 2>&1
  }

  if keyring_ok; then
    echo "✓ keyring 可用"
    return 0
  fi

  echo "   keyring 不可用（锁死/多实例冲突），执行自愈..."
  echo "   · 停止旧 daemon（备份 keyring 文件）..."
  pkill -f gnome-keyring-daemon 2>/dev/null || true
  sleep 1
  if [ -d ~/.local/share/keyrings ]; then
    TS=$(date +%s)
    for f in ~/.local/share/keyrings/*.keyring ~/.local/share/keyrings/user.keystore; do
      [ -f "$f" ] && mv "$f" "$f.bak-$TS" 2>/dev/null || true
    done
  fi
  echo "   · 启动全新 keyring（空密码）..."
  # 前台启动喂空密码（不要 --daemonize：会丢 stdin 密码导致 collection 锁死）
  printf '\n' | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 &
  sleep 2
  if keyring_ok; then
    echo "✓ keyring 自愈完成"
  else
    echo "⚠️ keyring 仍不可用，请手动检查："
    echo "   eval \"\$(dbus-launch --sh-syntax)\""
    echo "   printf '\n' | gnome-keyring-daemon --unlock --components=secrets"
  fi
}

# ---------- 5. 引导扫码登录 ----------
step_login() {
  echo ""
  echo "[5/5] 引导登录（微信扫码，与 Windows/macOS 体验一致）..."
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "   [dry-run] 跳过登录"
    return 0
  fi
  python3 "$LOGIN_PY"
}

# ---------- 主流程 ----------
step_check_node
step_install_cli
step_keyring_deps
step_start_keyring
step_login

echo ""
echo "=============================================="
echo " ✅ 安装配置完成！"
echo ""
echo " 验证命令："
echo "   breakout doctor              # 应全部 check ok"
echo "   breakout auth status --json  # 应显示你的账号"
echo ""
echo " 之后即可正常使用："
echo "   breakout capabilities"
echo "   breakout call topic.query --set operation=list --set pageNum=1"
echo "=============================================="
