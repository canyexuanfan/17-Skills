breakout-cli Linux 安装与登录（微信扫码）
==========================================
让 Linux 用户像 Windows/macOS 用户一样，微信扫码登录破局官网 CLI。

使用（任选一）
--------------
1. 完整一键安装 + 登录：
   bash setup.sh
   （自动：检查 Node>=18 → 安装官方 CLI → keyring 依赖 → 打开授权链接）

2. 只登录（CLI 已装好时）：
   python3 login.py

登录步骤
---------
1. 运行后终端会显示一个授权链接（并尝试自动打开浏览器）
2. 手机/电脑浏览器打开链接 → 官网页面 → 微信扫码登录
3. 登录后在页面点「确认授权」
4. 工具自动完成：凭证写入系统 keyring → 登录成功

验证登录
---------
breakout doctor
breakout auth status --json

依赖
-----
- Node.js >= 18（CLI 运行需要）
- gnome-keyring + libsecret-tools（系统凭据存储）
  各发行版安装命令见 SKILL.md

详细说明见 SKILL.md
