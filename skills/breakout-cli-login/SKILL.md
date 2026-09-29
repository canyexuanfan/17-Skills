---
name: breakout-cli-linux-login
description: 破局官网 CLI（breakout）Linux 一键安装与登录配置。官方 CLI + 微信扫码授权 + token 写入系统 keyring，doctor 全绿。当用户要在 Linux 上安装/登录破局 CLI、或需要让任何 Linux 用户配置登录时用。
---

# breakout-cli Linux 安装与登录配置

> 让 Linux 用户像 Windows/macOS 用户一样，**微信扫码**登录破局官网 CLI。
> 一条命令完成：安装 → 授权 → 登录 → 验证。

## 快速开始

```bash
bash setup.sh
```

一键完成：检查 Node ≥ 18 → 安装官方 CLI → 安装 keyring 依赖 →
启动登录环境 → **自动打开授权链接** → 微信扫码 → 确认授权 → 完成。

登录步骤
---------
1. 运行后终端显示授权链接，并自动打开浏览器
2. **① 用微信扫码登录官网**
3. **② 登录后在页面点击【确认授权】按钮**（登录成功 ≠ 授权完成，两步都要做）
4. 工具自动完成：凭证写入系统 keyring → 自动验证 → 登录成功

如果扫码后跳到了官网首页（说明没确认设备），重新打开链接点【确认授权】即可。
链接到期前若未确认，工具会自动生成新链接，无需重跑。

## 文件结构

```
breakout-cli-linux-login/
├── README.txt    快速上手说明
├── SKILL.md      本文档
├── setup.sh      一键安装 + 登录脚本
└── login.py      扫码登录工具（核心）
```

## 依赖

- **Node.js ≥ 18**（CLI 运行需要）
- **gnome-keyring + libsecret-tools**（系统凭据存储）

```bash
# Ubuntu / Debian
sudo apt-get install -y gnome-keyring libsecret-tools
# CentOS / RHEL
sudo yum install -y gnome-keyring libsecret
# Fedora
sudo dnf install -y gnome-keyring libsecret
# Arch
sudo pacman -S --noconfirm gnome-keyring libsecret
```

官方 CLI 由 setup.sh 自动安装：`npm install -g @aipoju/breakout-cli`

## 登录后日常使用

```bash
breakout capabilities                              # 查看你的能力
breakout auth status --json                        # 账号信息
breakout doctor                                    # 环境健康检查
breakout call topic.query --set operation=list --set pageNum=1   # 示例：查主题
```

## 故障排查

| 现象 | 解决 |
|:--|:--|
| `auth status` 报 "failed to unlock correct collection" | 重跑 `bash setup.sh`（自动修复 keyring） |
| "Cannot create an item in a locked collection" | 重跑 setup.sh（自动重置 keyring daemon） |
| `secret-tool: command not found` | 安装 libsecret-tools（见上文） |
| 扫码后一直等待 | 微信扫码登录后，需在页面点**确认授权** |
| 授权链接过期 | 重新运行 `python3 login.py` 生成新链接 |
| 想换账号 | `breakout auth logout` 后重跑 `python3 login.py` |

## 安全说明

- 全程**不输入账号密码**——微信扫码在官网完成，凭据不经本工具
- 登录凭证只写入系统 keyring，不落盘、不打印、不进日志
- 工具内不含任何个人数据，可放心分发使用
- 卸载：`npm uninstall -g @aipoju/breakout-cli` 并删除 `~/.breakout`
