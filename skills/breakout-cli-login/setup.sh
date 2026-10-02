#!/usr/bin/env bash
# =============================================================================
# breakout-cli Linux 一键安装 + 登录配置   (v2 — 按实测踩坑记录重写)
#
#   装官方 CLI → 装 keyring 依赖 → 起 D-Bus 并解锁 keyring → 微信扫码授权 → doctor 全绿
#
# 用法:
#   bash setup.sh                 # 完整流程（推荐）
#   bash setup.sh --dry-run       # 只装环境，不登录
#   bash setup.sh --keyring-only  # 只修 D-Bus / keyring（服务器重启后跑这个）
#   bash setup.sh --mirror tuna   # 装依赖前先换清华镜像源（apt 卡住时用）
#   bash setup.sh --no-bashrc     # 不改 ~/.bashrc（自己管理环境变量）
#   bash setup.sh --force         # 起 keyring 前先清掉旧 gnome-keyring-daemon
#
# 设计说明（为什么这么写）:
#   · 故意不开 `set -e` —— 任何一步失败都要打印「可执行的修复指引」，而不是直接退出
#   · keyring 必须用 `dbus-launch` 起的 session + `--unlock` 从 stdin 读密码，
#     否则会触发图形 prompter（gcr-prompter）在无显示器服务器上无限等待
#   · 不要用 `--start`（与 `--unlock` 不兼容）、不要用 `dbus-run-session`（会走 prompter 分支）
# =============================================================================
set -uo pipefail

# ---------- 参数 ----------
DRY_RUN=0; KEYRING_ONLY=0; WRITE_BASHRC=1; FORCE=0; MIRROR="none"
for arg in "$@"; do
  case "$arg" in
    --dry-run)      DRY_RUN=1 ;;
    --keyring-only) KEYRING_ONLY=1 ;;
    --no-bashrc)    WRITE_BASHRC=0 ;;
    --force)        FORCE=1 ;;
    --mirror=*)     MIRROR="${arg#--mirror=}" ;;
    -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
  esac
  case "$arg" in --mirror) ;; esac
done
# 兼容 `--mirror tuna` 写法
prev=""
for arg in "$@"; do
  [ "$prev" = "--mirror" ] && MIRROR="$arg"
  prev="$arg"
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGIN_PY="$SCRIPT_DIR/login.py"
BRC_BEGIN="# >>> breakout-cli keyring env >>>"
BRC_END="# <<< breakout-cli keyring env <<<"

say()  { printf '%s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
err()  { printf '✗ %s\n' "$*" >&2; }

say "=============================================="
say " breakout-cli Linux 安装与登录配置 (v2)"
say "=============================================="

# ---------- 工具函数 ----------
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    err "非 root 且无 sudo，无法自动安装依赖；请用 root 运行或手动执行下面打印的命令。"
  fi
fi

detect_distro() {
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"; DISTRO_LIKE="${ID_LIKE:-}"
  else
    DISTRO_ID="unknown"; DISTRO_LIKE=""
  fi
  case "$DISTRO_ID $DISTRO_LIKE" in
    *debian*|*ubuntu*) PKG=apt ;;
    *rhel*|*fedora*|*centos*|*opencloudos*|*openeuler*|*anolis*|*almalinux*|*rocky*) PKG=rpm ;;
    *arch*) PKG=pacman ;;
    *) PKG=unknown ;;
  esac
  say "   发行版: ${PRETTY_NAME:-$DISTRO_ID}  (包管理器: $PKG)"
}
detect_distro

# ---------- 1. Node.js ----------
step_check_node() {
  say ""; say "[1/6] 检查 Node.js (>= 18)..."
  if ! command -v node >/dev/null 2>&1; then
    err "未安装 Node.js。安装方式（任选）:"
    say "   # Ubuntu/Debian:"
    say "   curl -fsSL https://deb.nodesource.com/setup_20.x | ${SUDO} -E bash - && ${SUDO} apt-get install -y nodejs"
    say "   # CentOS/RHEL/openEuler:"
    say "   curl -fsSL https://rpm.nodesource.com/setup_20.x | ${SUDO} bash - && ${SUDO} yum install -y nodejs"
    say "   # 或已装 nvm:  nvm install 20"
    return 1
  fi
  NODE_MAJOR="$(node -e 'console.log(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
  if [ "${NODE_MAJOR:-0}" -lt 18 ]; then
    err "Node.js 版本过低: $(node -v)（需要 >= 18）。已装 nvm 的话: nvm install 20 && nvm use 20"
    return 1
  fi
  ok "Node.js $(node -v)"
  NPM_BIN_DIR="$(dirname "$(readlink -f "$(command -v npm 2>/dev/null || echo /usr/bin/npm)")" 2>/dev/null || true)"
}

# ---------- 2. apt 依赖安装的三种「系统级坑」处理 ----------
apt_fix_universe() {
  # 坑：sources.list 只有 main restricted，gnome-keyring/libsecret-tools 在 universe
  if grep -rqsE '^[^#]*\buniverse\b' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
    return 0
  fi
  warn "apt 源里没有 universe 仓库（gnome-keyring / libsecret-tools 在 universe）"
  command -v add-apt-repository >/dev/null 2>&1 || {
    say "   先安装 software-properties-common..."
    $SUDO apt-get install -y software-properties-common >/dev/null 2>&1 || true
  }
  if command -v add-apt-repository >/dev/null 2>&1; then
    $SUDO add-apt-repository -y universe >/dev/null 2>&1 && ok "已启用 universe" || warn "启用 universe 失败，请手动执行: sudo add-apt-repository universe"
  else
    warn "没有 add-apt-repository，请手动在 sources.list 里加上 universe 后重跑"
  fi
}

apt_clear_lock() {
  # 坑：上次 apt 卡死留下锁文件，后续全部报 Could not get lock
  local held=0
  for l in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do
    [ -e "$l" ] || continue
    if command -v fuser >/dev/null 2>&1; then
      fuser "$l" >/dev/null 2>&1 && held=1
    fi
  done
  pgrep -x apt-get >/dev/null 2>&1 && held=1
  pgrep -x apt >/dev/null 2>&1 && held=1
  pgrep -x dpkg >/dev/null 2>&1 && held=1
  [ "$held" -eq 1 ] || return 0
  warn "检测到 apt/dpkg 锁被占用（常见于上次 apt 卡死后进程没清干净），正在清理..."
  $SUDO pkill -9 -f 'apt-get'  >/dev/null 2>&1 || true
  $SUDO pkill -9 -f 'apt '      >/dev/null 2>&1 || true
  $SUDO pkill -9 -f 'dpkg'      >/dev/null 2>&1 || true
  sleep 1
  $SUDO rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock
  $SUDO dpkg --configure -a >/dev/null 2>&1 || true
  ok "apt 锁已清理"
}

apt_use_tuna() {
  # 坑：官方源慢到 update 卡死几分钟
  local mirror="$1"
  case "$mirror" in
    tuna)    BASE="https://mirrors.tuna.tsinghua.edu.cn" ;;
    aliyun)  BASE="https://mirrors.aliyun.com" ;;
    *)       return 1 ;;
  esac
  warn "切换到 $mirror 镜像源（原文件会备份为 *.bak-<时间戳>）"
  TS="$(date +%s)"
  if [ -f /etc/apt/sources.list ]; then
    $SUDO cp /etc/apt/sources.list "/etc/apt/sources.list.bak-$TS"
    # 把 archive./security./ports. 的主机名换掉，路径保持不变
    $SUDO sed -i -E "s#https?://(archive|security|ports)\.(ubuntu|debian)\.org#${BASE}#g; s#https?://[a-z.]*debian\.org/debian#${BASE}/debian#g" /etc/apt/sources.list
  fi
  for f in /etc/apt/sources.list.d/*.list; do
    [ -f "$f" ] || continue
    $SUDO cp "$f" "$f.bak-$TS"
    $SUDO sed -i -E "s#https?://(archive|security|ports)\.(ubuntu|debian)\.org#${BASE}#g" "$f"
  done
  ok "已换源（回滚: sudo cp /etc/apt/sources.list.bak-$TS /etc/apt/sources.list）"
}

apt_install_pkgs() {
  apt_fix_universe
  apt_clear_lock
  if [ "$MIRROR" != "none" ]; then apt_use_tuna "$MIRROR" || warn "未知镜像名: $MIRROR（可选 tuna / aliyun）"; fi
  say "   apt-get update（最多等 150 秒；若卡住请改用: bash setup.sh --mirror tuna）..."
  if ! timeout 150 $SUDO apt-get update -o Acquire::Retries=2 >/dev/null 2>&1; then
    warn "apt-get update 超时/失败 —— 大概率是官方源太慢，建议执行:"
    say  "   bash setup.sh --mirror tuna"
    apt_clear_lock
  fi
  # libsecret-tools 只在部分发行版存在；gnome-keyring 自带 secret-tool
  for pkgset in "gnome-keyring libsecret-tools dbus-x11 dbus-bin" "gnome-keyring libsecret-tools dbus-x11" "gnome-keyring dbus-x11"; do
    if $SUDO apt-get install -y $pkgset >/dev/null 2>&1; then
      ok "已安装: $pkgset"
      return 0
    fi
  done
  err "apt 安装失败。手动执行看看具体报错:"
  say  "   sudo apt-get install -y gnome-keyring dbus-x11"
  return 1
}

rpm_install_pkgs() {
  local MGR=yum
  command -v dnf >/dev/null 2>&1 && MGR=dnf
  say "   使用 $MGR 安装（EL 系需要 dbus-x11 才有 dbus-launch）..."
  if ! $SUDO $MGR install -y gnome-keyring dbus-x11 >/dev/null 2>&1; then
    $SUDO $MGR install -y gnome-keyring dbus-tools >/dev/null 2>&1 || true
    $SUDO $MGR install -y gnome-keyring >/dev/null 2>&1 || {
      err "安装失败。手动执行: sudo $MGR install -y gnome-keyring dbus-x11"
      return 1
    }
  fi
  ok "已安装: gnome-keyring dbus-x11"
}

# ---------- 3. 安装/更新官方 CLI ----------
step_install_cli() {
  say ""; say "[2/6] 安装官方 CLI (@aipoju/breakout-cli)..."
  if command -v breakout >/dev/null 2>&1; then
    ok "已安装: $(breakout version 2>/dev/null | tail -1)"
    say "   如需更新: npm install -g @aipoju/breakout-cli@latest"
    return 0
  fi
  say "   npm install -g @aipoju/breakout-cli ..."
  if ! npm install -g @aipoju/breakout-cli 2>&1 | tail -3; then
    warn "安装失败，2 秒后重试一次..."
    sleep 2
    npm install -g @aipoju/breakout-cli || {
      err "npm 安装失败。若网络受限可先换源: npm config set registry https://registry.npmmirror.com"
      return 1
    }
  fi
  ok "CLI 安装完成: $(breakout version 2>/dev/null | tail -1)"
}

# ---------- 4. keyring 依赖 ----------
step_keyring_deps() {
  say ""; say "[3/6] 检查 keyring 依赖 (gnome-keyring / secret-tool / dbus-launch)..."
  local need=0
  command -v gnome-keyring-daemon >/dev/null 2>&1 || { warn "缺 gnome-keyring-daemon"; need=1; }
  command -v dbus-launch        >/dev/null 2>&1 || { warn "缺 dbus-launch（Debian/EL 都在 dbus-x11 包里）"; need=1; }
  command -v secret-tool        >/dev/null 2>&1 || { warn "缺 secret-tool（gnome-keyring / libsecret-tools 提供）"; need=1; }

  if [ "$need" -eq 1 ]; then
    case "$PKG" in
      apt)      apt_install_pkgs || return 1 ;;
      rpm)      rpm_install_pkgs || return 1 ;;
      pacman)   say "   Arch: $SUDO pacman -S --noconfirm gnome-keyring libsecret dbus"; $SUDO pacman -S --noconfirm gnome-keyring libsecret dbus >/dev/null 2>&1 || { err "pacman 安装失败"; return 1; } ;;
      *)        err "未识别的发行版，请手动安装 gnome-keyring + dbus-x11 + libsecret"; return 1 ;;
    esac
  fi

  command -v gnome-keyring-daemon >/dev/null 2>&1 || { err "gnome-keyring-daemon 仍缺失，无法继续"; return 1; }
  command -v dbus-launch         >/dev/null 2>&1 || { err "dbus-launch 仍缺失（装 dbus-x11），无法继续"; return 1; }
  command -v secret-tool         >/dev/null 2>&1 || warn "secret-tool 缺失 —— 脚本仍会尝试启动 keyring，但写入可能失败"
  ok "keyring 依赖就绪"
}

# ---------- 5. D-Bus + 解锁 keyring（核心）----------
secrets_up() {
  if command -v dbus-send >/dev/null 2>&1; then
    dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
      org.freedesktop.DBus.NameHasOwner string:org.freedesktop.secrets 2>/dev/null | grep -q 'true' && return 0 || return 1
  fi
  if command -v gdbus >/dev/null 2>&1; then
    gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
      --method org.freedesktop.DBus.NameHasOwner org.freedesktop.secrets 2>/dev/null | grep -qi true && return 0 || return 1
  fi
  if command -v secret-tool >/dev/null 2>&1; then
    printf 'probe' | secret-tool store --label=probe service breakout-probe username probe >/dev/null 2>&1 \
      && { secret-tool clear service breakout-probe username probe >/dev/null 2>&1; return 0; } || return 1
  fi
  return 1
}

start_dbus_keyring() {
  # 坑：必须用 dbus-launch（不是 dbus-run-session），否则会走 gcr-prompter 图形弹窗分支
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    if command -v dbus-launch >/dev/null 2>&1; then
      say "   启动全新 session D-Bus (dbus-launch)..."
      eval "$(dbus-launch --sh-syntax)"
    else
      err "没有 dbus-launch，无法建立 session D-Bus"; return 1
    fi
  else
    say "   复用已有 session D-Bus: $DBUS_SESSION_BUS_ADDRESS"
  fi

  if [ "$FORCE" -eq 1 ]; then
    warn "--force：先停掉旧的 gnome-keyring-daemon"
    pkill -f gnome-keyring-daemon >/dev/null 2>&1 || true
    sleep 1
  fi

  # 核心：--unlock 从 stdin 读密码（空密码），直接创建空密码 collection，不触发 prompter
  # ⚠️ 不要加 --start（与 --unlock 不兼容）；不要用 dbus-run-session
  say "   解锁 keyring (gnome-keyring-daemon --unlock --components=secrets)..."
  printf '\n' | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 &
  sleep 5
}

step_start_keyring() {
  say ""; say "[4/6] 启动 D-Bus session 与 keyring..."
  if secrets_up; then ok "keyring 已可用，跳过"; return 0; fi

  start_dbus_keyring
  if secrets_up; then ok "keyring 已就绪（org.freedesktop.secrets 在线）"; return 0; fi

  # 二次尝试：换一个全新 session（旧 session 里 daemon 注册不上时用）
  warn "当前 session 里没起来，改用全新 session 重试一次..."
  unset DBUS_SESSION_BUS_ADDRESS
  start_dbus_keyring
  if secrets_up; then ok "keyring 已就绪（org.freedesktop.secrets 在线）"; return 0; fi

  err "keyring 仍未就绪。手动排查:"
  say "   eval \"\$(dbus-launch --sh-syntax)\""
  say "   printf '\\n' | gnome-keyring-daemon --unlock --components=secrets &   # 注意：不要加 --start"
  say "   sleep 5 && secret-tool store --label=t service t username t <<< 'x' && echo OK"
  say "   ⚠️ 不要把 org.gnome.keyring.SystemPrompter 指向 /bin/true —— 会被判定为「用户取消」"
  return 1
}

# ---------- 6. 持久化 D-Bus 地址 ----------
step_persist_env() {
  say ""; say "[5/6] 持久化 DBUS_SESSION_BUS_ADDRESS..."
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then warn "没有 DBUS_SESSION_BUS_ADDRESS 可写"; return 0; fi

  mkdir -p "$HOME/.breakout"
  cat > "$HOME/.breakout/env.sh" <<EOF
# 由 setup.sh 生成 —— 服务器重启后需重新执行: bash setup.sh --keyring-only
export DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
EOF
  ok "已写入 ~/.breakout/env.sh"

  if [ "$WRITE_BASHRC" -eq 0 ]; then
    warn "--no-bashrc：请自行在 shell 启动文件里 source ~/.breakout/env.sh，否则新开终端 breakout 找不到 keyring"
    return 0
  fi

  if grep -qF "$BRC_BEGIN" "$HOME/.bashrc" 2>/dev/null; then
    # 幂等更新已有块
    python3 - "$HOME/.bashrc" "$BRC_BEGIN" "$BRC_END" "$DBUS_SESSION_BUS_ADDRESS" <<'PY' || warn "更新 ~/.bashrc 失败，请手动处理"
import sys, pathlib, re
p, b, e, addr = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t = pathlib.Path(p).read_text(encoding='utf-8', errors='ignore')
block = f"{b}\nexport DBUS_SESSION_BUS_ADDRESS=\"{addr}\"\n{e}"
t = re.sub(re.escape(b) + r'.*?' + re.escape(e), block, t, flags=re.S)
pathlib.Path(p).write_text(t, encoding='utf-8')
PY
    ok "~/.bashrc 中的环境变量块已更新"
  else
    cp "$HOME/.bashrc" "$HOME/.bashrc.bak-$(date +%s)" 2>/dev/null || true
    {
      printf '\n%s\n' "$BRC_BEGIN"
      printf '# 让新开的终端也能找到 keyring（服务器重启后需重跑 setup.sh --keyring-only）\n'
      printf 'export DBUS_SESSION_BUS_ADDRESS="%s"\n' "$DBUS_SESSION_BUS_ADDRESS"
      printf '%s\n' "$BRC_END"
    } >> "$HOME/.bashrc"
    ok "已写入 ~/.bashrc（原文件已备份）"
  fi
  say "   提示：当前 shell 若已在运行，请先执行  export DBUS_SESSION_BUS_ADDRESS=\"${DBUS_SESSION_BUS_ADDRESS}\""
  say "         或直接  source ~/.bashrc"
}

# ---------- 7. 扫码登录 ----------
step_login() {
  say ""; say "[6/6] 引导登录（微信扫码，与 Windows/macOS 体验一致）..."
  if [ "$DRY_RUN" -eq 1 ]; then warn "[dry-run] 跳过登录"; return 0; fi
  if [ ! -f "$LOGIN_PY" ]; then err "找不到 login.py（应与 setup.sh 同目录）"; return 1; fi
  python3 "$LOGIN_PY"
}

# ---------- 主流程 ----------
RC=0
if [ "$KEYRING_ONLY" -eq 1 ]; then
  say ""; say "模式: 只修复 D-Bus / keyring（服务器重启后用）"
  step_keyring_deps || RC=1
  [ "$RC" -eq 0 ] && { step_start_keyring || RC=1; }
  [ "$RC" -eq 0 ] && { step_persist_env || true; }
else
  step_check_node         || RC=1
  [ "$RC" -eq 0 ] && { step_install_cli   || RC=1; }
  [ "$RC" -eq 0 ] && { step_keyring_deps  || RC=1; }
  [ "$RC" -eq 0 ] && { step_start_keyring || RC=1; }
  [ "$RC" -eq 0 ] && { step_persist_env   || true; }
  [ "$RC" -eq 0 ] && { step_login         || RC=1; }
fi

say ""
say "=============================================="
if [ "$RC" -eq 0 ]; then
  say " ✅ 完成！验证一下："
else
  say " ⚠️  有步骤没通过（见上方红字提示），修完可重跑本脚本"
fi
say ""
say "   breakout doctor              # 应全部 check ok"
say "   breakout auth status --json  # 应显示你的账号"
say ""
say " 日常使用:"
say "   breakout capabilities"
say "   breakout call topic.query --set operation=list --set pageNum=1"
say ""
say " ⚠️ 服务器重启后 session D-Bus 会消失（token 本身不会丢），重跑:"
say "   bash setup.sh --keyring-only"
say "=============================================="
exit "$RC"
