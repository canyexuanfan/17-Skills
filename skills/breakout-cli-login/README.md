breakout-cli Linux 安装与登录（微信扫码）
==========================================

> 本 Skill 已收录于 [17-Skills](https://github.com/canyexuanfan/17-Skills) 合集。原仓库 [breakout-cli-linux-login](https://github.com/canyexuanfan/breakout-cli-linux-login) 已归档，历史与 Star 保留，后续更新在本仓库进行。

让 Linux 用户获得与 Windows / macOS 一致的体验：微信扫码登录破局官网 CLI。

使用（任选一）
--------------
1. 完整一键安装 + 登录：
   bash setup.sh

2. 只登录（CLI 与依赖都装好时）：
   python3 login.py

3. 服务器重启后（session D-Bus 会消失，token 不会丢）：
   bash setup.sh --keyring-only

4. 装依赖时 apt 卡住：
   bash setup.sh --mirror tuna

登录步骤
---------
1. 运行后终端会显示一个授权链接（并尝试自动打开浏览器）
2. 手机/电脑浏览器打开链接 → 官网页面 → 微信扫码登录
3. 登录后在页面点【确认授权】（登录成功 ≠ 授权完成，两步都要做）
4. 工具自动完成：凭证写入系统 keyring → 登录成功

验证登录
---------
breakout doctor
breakout auth status --json

依赖
-----
- Node.js >= 18（CLI 运行需要）
- gnome-keyring（提供 gnome-keyring-daemon 与 secret-tool）
- dbus-x11（提供 dbus-launch）—— 最容易漏装的一个

  Ubuntu / Debian： sudo add-apt-repository -y universe && sudo apt-get update
                    sudo apt-get install -y gnome-keyring libsecret-tools dbus-x11
  CentOS/RHEL/openEuler/OpenCloudOS： sudo dnf install -y gnome-keyring dbus-x11

  （EL 系没有 libsecret-tools 这个包名，secret-tool 由 gnome-keyring 自带）

排错
-----
见 SKILL.md 的「故障排查」三张表（装依赖 / keyring / 登录使用），
已覆盖实测踩过的 11 类问题。

详细说明见 SKILL.md
