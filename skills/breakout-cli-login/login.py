#!/usr/bin/env python3
"""
breakout-cli Linux 扫码登录工具
================================
与 Windows/macOS 一致的登录体验：
生成授权链接 → 浏览器打开 → 微信扫码 → 确认授权 → token 写入 keyring → CLI 全通。

登录流程（官方桌面授权机制）：
  1. 发起设备授权，获取一次性授权链接
  2. 浏览器打开链接 → 官网微信扫码登录 → 确认授权设备（两步都要完成）
  3. 检测到授权完成后，换取访问凭证
  4. 凭证写入系统 keyring（service=breakout-cli），CLI 自动续期

UX 细节：
  - 分步提示：明确"①扫码登录官网 → ②点确认授权"，两步完成才算成功
  - 兜底提示：若登录后跳到首页，提醒重新打开链接点确认授权
  - 定期提醒：轮询期间每 60s 提示一次"请在授权页点确认授权"
  - 自动续期：链接到期前若仍未确认，自动生成新链接（最多续 1 次）
  - 自动验证：成功后自动运行 breakout doctor + auth status 展示结果

安全边界：
  - 不接收账号密码；扫码由用户在官网完成，凭据不经本工具
  - token 仅写入系统 keyring，不落盘、不打印
  - 本工具不含任何个人数据
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

BASE = os.environ.get('AIPOJU_BASE', 'https://aipoju.com/server')
POLL_INTERVAL = 3          # 轮询间隔秒
AUTH_TIMEOUT = 300         # 单次授权链接有效期（秒，与 expiresAt 对齐）
RENEW_THRESHOLD = 60       # 剩余秒数低于此值且未确认 → 自动续期
MAX_RENEW = 1              # 最多自动续期次数
REMIND_INTERVAL = 60       # 定期提醒间隔秒

# 官方接口端点（集中管理）
EP_DEVICE_AUTH = '/desktop-auth/authorizations'       # 发起设备授权
EP_TOKEN = '/desktop-auth/token'                       # 换取访问凭证


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def s256_challenge(verifier: str) -> str:
    return b64url(hashlib.sha256(verifier.encode()).digest())


def http_json(method: str, path: str, body=None, params=None):
    url = f"{BASE}{path}"
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0',
        'Origin': 'https://aipoju.com',
        'Referer': 'https://aipoju.com/',
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8', 'ignore'))
        except Exception:
            raise RuntimeError(f"HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}")


def create_authorization(platform: str, device_id: str) -> tuple:
    """发起设备授权，返回 (login_id, verification_uri, verifier, expires_at)"""
    verifier = b64url(secrets.token_bytes(32))
    challenge = s256_challenge(verifier)
    result = http_json('POST', EP_DEVICE_AUTH, {
        'platform': platform,
        'deviceId': device_id,
        'codeChallenge': challenge,
        'deviceName': 'breakout-cli',
    })
    if result.get('code') != 200:
        raise RuntimeError(f"发起授权失败: {result.get('msg', result)}")
    data = result['data']
    return data['loginId'], data['verificationUri'], verifier, data.get('expiresAt', '')


def print_guide(uri: str, expires_at: str, is_renew: bool = False) -> None:
    """打印引导提示（分步说明 + 兜底提示）"""
    print()
    print('=' * 62)
    if is_renew:
        print('  ⚠️  上一个授权链接即将过期，已自动生成新链接，请使用下面的新链接！')
        print('=' * 62)
    print('  请在浏览器中打开下面的链接，然后按两步操作：')
    print()
    print('  ① 打开链接 → 用微信扫码登录官网')
    print('  ② 登录后，在页面点击【确认授权】按钮')
    print()
    print('  ⚠️ 注意：登录成功 ≠ 授权完成！')
    print('     如果扫码后跳到了官网首页，说明还没点【确认授权】，')
    print('     请重新打开链接，在授权页面点【确认授权】。')
    print('=' * 62)
    print()
    print(f'  授权链接: {uri}')
    print(f'  有效期至: {expires_at}（约 {AUTH_TIMEOUT // 60} 分钟）')
    print()
    try:
        if webbrowser.open(uri):
            print('  （已尝试自动打开浏览器）')
    except Exception:
        pass
    print()
    print('  等待授权确认中...（Ctrl+C 取消）')


def ensure_keyring() -> None:
    if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ or not os.environ['DBUS_SESSION_BUS_ADDRESS']:
        out = subprocess.run(['dbus-launch', '--sh-syntax'], capture_output=True, text=True, timeout=10)
        for line in out.stdout.strip().splitlines():
            if '=' in line:
                k, _, v = line.partition('=')
                os.environ[k.strip()] = v.strip().strip("'")


def secret_tool_store(service: str, username_key: str, secret: str, label: str) -> None:
    proc = subprocess.run(
        ['secret-tool', 'store', '--label', label, 'service', service, 'username', username_key],
        input=secret.encode('utf-8'), capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"secret-tool store 失败: {proc.stderr.decode('utf-8', 'ignore')[:300]}")


def write_cli_config(device_id: str) -> None:
    cfg_dir = os.path.expanduser('~/.breakout')
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, 'config.json')
    cfg = {"baseUrl": BASE, "deviceId": device_id}
    if os.path.exists(cfg_path):
        try:
            old = json.load(open(cfg_path))
            if old.get('deviceId'):
                cfg['deviceId'] = old['deviceId']
        except Exception:
            pass
    with open(cfg_path, 'w') as f:
        json.dump(cfg, f)
    os.chmod(cfg_path, 0o600)


def run_verification() -> None:
    """授权完成后自动验证：doctor + auth status"""
    print()
    print('→ 自动验证登录状态...')
    print('-' * 62)
    try:
        doc = subprocess.run(['breakout', 'doctor'], capture_output=True, text=True, timeout=30)
        doc_out = doc.stdout or doc.stderr
        # 提取关键 check 结果
        for line in doc_out.splitlines():
            if any(k in line for k in ['配置文件', '登录凭据', '能力清单', '账号会话', '权限层级']):
                mark = '✓' if '"ok": true' in line or 'ok' in line.lower() else ('✓' if 'true' in line else '?')
                print(f'  {mark} {line.strip()}')
    except FileNotFoundError:
        print('  ⚠️ 未找到 breakout 命令，请确认 CLI 已安装')
    except Exception as e:
        print(f'  ⚠️ doctor 运行异常: {e}')
    try:
        st = subprocess.run(['breakout', 'auth', 'status', '--json'], capture_output=True, text=True, timeout=30)
        info = json.loads(st.stdout)
        name = info.get('fullName') or info.get('wechatName') or '?'
        num = info.get('userNumber', '?')
        print(f'  → 登录账号: {name} (userNumber {num})')
    except Exception:
        pass
    print('-' * 62)


def fmt_expiry(iso_ts: str) -> str:
    """ISO 时间转本地可读时间（UTC+8）"""
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(iso_ts.replace('Z', '+00:00'))
        return dt.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    except Exception:
        return iso_ts or '?'


def main():
    ap = argparse.ArgumentParser(description='breakout-cli Linux 扫码登录工具（与 Windows 体验一致）')
    ap.add_argument('--platform', default='win32', help='设备平台标识（默认 win32，保持与官方一致）')
    ap.add_argument('--service', default='breakout-cli', help='keyring service 名（默认 breakout-cli）')
    ap.add_argument('--no-browser', action='store_true', help='不自动打开浏览器，只打印链接')
    ap.add_argument('--device-id', default=None, help='deviceId（默认随机生成）')
    args = ap.parse_args()

    device_id = args.device_id or f'cli-{secrets.token_hex(8)}'
    platform = args.platform

    # ---------- 1. 发起设备授权 ----------
    print('→ 正在发起设备授权...')
    try:
        login_id, uri, verifier, expires_at = create_authorization(platform, device_id)
    except RuntimeError as e:
        print(f'✗ {e}', file=sys.stderr)
        sys.exit(2)
    print_guide(uri, fmt_expiry(expires_at), is_renew=False)

    # ---------- 2. 轮询授权状态（含自动续期 + 定期提醒）----------
    renew_count = 0
    reminder_next = time.time() + REMIND_INTERVAL
    deadline = time.time() + AUTH_TIMEOUT
    last_progress = 0.0

    while True:
        remaining = int(deadline - time.time())
        if remaining <= 0:
            # 当前链接过期
            if renew_count < MAX_RENEW:
                renew_count += 1
                print()
                print(f'→ 链接已过期，自动生成新链接（第 {renew_count}/{MAX_RENEW} 次续期）...')
                try:
                    login_id, uri, verifier, expires_at = create_authorization(platform, device_id)
                except RuntimeError as e:
                    print(f'✗ 续期失败: {e}', file=sys.stderr)
                    sys.exit(6)
                deadline = time.time() + AUTH_TIMEOUT
                reminder_next = time.time() + REMIND_INTERVAL
                print_guide(uri, fmt_expiry(expires_at), is_renew=True)
                continue
            print()
            print('✗ 等待超时（多次续期仍未确认），请重新运行', file=sys.stderr)
            sys.exit(4)

        # 到期前提醒续期（剩余 <60s 时提示，避免扫到一半链接失效）
        if remaining < RENEW_THRESHOLD and renew_count < MAX_RENEW:
            print()
            print(f'  ⚠️ 链接还剩 {remaining}s 即将过期，若还没扫码请加快，过期后会自动换新链接')

        # 定期提醒（每 60s）
        now = time.time()
        if now >= reminder_next:
            print()
            print('  💡 提醒：如果已扫码登录官网，请回到授权页面点击【确认授权】按钮！')
            print('     （登录成功≠授权完成，必须点确认授权才算完成）')
            reminder_next = now + REMIND_INTERVAL

        time.sleep(POLL_INTERVAL)
        try:
            st = http_json('GET', f'{EP_DEVICE_AUTH}/{login_id}')
        except Exception as e:
            print(f'  ! 轮询异常: {e}，重试中...')
            continue
        if st.get('code') != 200:
            continue
        status = st.get('data', {}).get('status')
        if status == 'approved':
            print()
            print('✓ 授权确认完成！正在获取访问凭证...')
            break
        if status in ('expired', 'rejected'):
            if status == 'expired' and renew_count < MAX_RENEW:
                continue  # 走顶部续期逻辑
            print(f'✗ 授权已{status}，请重新运行', file=sys.stderr)
            sys.exit(3)
        # pending: 每 15 秒显示一次剩余时间（不刷屏）
        now_t = time.time()
        if now_t - last_progress >= 15:
            last_progress = now_t
            print(f'  · 等待授权确认...（{remaining}s 剩余）')

    # ---------- 3. 交换 token ----------
    tok = http_json('POST', EP_TOKEN, {
        'loginId': login_id,
        'codeVerifier': verifier,
        'grantType': 'authorization_code',
    })
    if tok.get('code') != 200:
        print(f"✗ 获取访问凭证失败: {tok.get('msg', tok)}", file=sys.stderr)
        sys.exit(5)
    tdata = tok['data']
    token = tdata.get('token') or tdata.get('accessToken') or tdata.get('access_token')
    refresh = tdata.get('refreshToken') or tdata.get('refresh_token')
    if not token:
        print(f'✗ 响应缺少 token，请反馈结构: {json.dumps(tdata, ensure_ascii=False)[:300]}', file=sys.stderr)
        sys.exit(6)

    # ---------- 4. 写 keyring + config ----------
    ensure_keyring()
    svc = args.service
    secret_tool_store(svc, 'access-token', token, 'breakout access-token')
    if refresh:
        secret_tool_store(svc, 'refresh-token', refresh, 'breakout refresh-token')
    write_cli_config(device_id)
    print('✓ 凭证已写入系统 keyring')

    # ---------- 5. 自动验证 ----------
    run_verification()
    print()
    print('🎉 登录成功！现在可以正常使用 breakout 命令了。')
    print('   例如: breakout capabilities / breakout auth status --json')


if __name__ == '__main__':
    main()
