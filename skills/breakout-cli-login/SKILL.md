---
name: breakout-cli-linux-login
description: 破局官网 CLI（breakout）Linux 一键安装与登录配置。官方 CLI + 微信扫码授权 + token 写入系统 keyring，doctor 全绿。当用户要在 Linux 上安装/登录破局 CLI、或需要让任何 Linux 用户配置登录时用。
---

# breakout-cli Linux 安装与登录配置

> 让 Linux 用户获得与 Windows / macOS 一致的体验：**微信扫码**登录破局官网 CLI。
> 一条命令完成：安装 → 授权 → 登录 → 验证。

## 快速开始

```bash
bash setup.sh
```

一键完成：检查 Node ≥ 18 → 安装官方 CLI → 自动安装 keyring 依赖 →
起 D-Bus 会话并解锁 keyring → **自动打开授权链接** → 微信扫码 → 确认授权 → 完成。

### 可选参数

| 参数 | 用途 |
|:--|:--|
| `--dry-run` | 只装环境，不登录（先验证依赖是否齐） |
| `--keyring-only` | **只修 D-Bus / keyring**（服务器重启后跑这个） |
| `--mirror tuna` | 装依赖前先换清华镜像源（`apt-get update` 卡住时用） |
| `--mirror aliyun` | 同上，换阿里云源 |
| `--no-bashrc` | 不写 `~/.bashrc`（自行管理环境变量时用） |
| `--force` | 起 keyring 前先清掉旧的 `gnome-keyring-daemon` |

## 登录流程

1. 运行后终端会显示授权链接（并尝试自动打开浏览器）
2. **① 用微信扫码登录官网**
3. **② 登录后在页面点击【确认授权】**（登录成功 ≠ 授权完成，两步都要做）
4. 工具自动完成：凭证写入系统 keyring → 自动验证 → 登录成功

> 如果扫码后跳到了官网首页，说明还没确认设备 —— 重新打开链接点【确认授权】即可。
> 链接到期前若仍未确认，工具会自动生成一条新链接，无需重跑。

## 为什么 Linux 需要这个工具

官方 CLI 的独立登录（`breakout auth login`）目前**只支持 Windows 和 macOS**，
在 Linux 上直接运行会提示「当前登录接口只支持 Windows 和 macOS」。

本工具在 Linux 上提供**同样的设备授权登录体验**：申请一次性授权链接 → 浏览器微信扫码 →
在页面确认授权 → 把拿到的凭证写入系统 keyring，之后 `breakout` 各命令即可正常使用。

## 依赖

| 组件 | 作用 | 说明 |
|:--|:--|:--|
| **Node.js ≥ 18** | CLI 运行环境 | `node -v` 查看 |
| **gnome-keyring** | 提供 `gnome-keyring-daemon` / `secret-tool` | 系统凭据存储 |
| **dbus-x11** | 提供 `dbus-launch` | **最容易漏装的一个**，Debian/Ubuntu 与 EL 系都在这个包里 |

```bash
# Ubuntu / Debian（注意：这两个包在 universe 仓库，需先启用）
sudo add-apt-repository -y universe
sudo apt-get update
sudo apt-get install -y gnome-keyring libsecret-tools dbus-x11

# CentOS / RHEL / openEuler / OpenCloudOS 等 EL 系（注意：没有 libsecret-tools 这个包名，
# secret-tool 由 gnome-keyring 自带）
sudo dnf install -y gnome-keyring dbus-x11    # 老系统用 yum

# Fedora
sudo dnf install -y gnome-keyring dbus-x11

# Arch
sudo pacman -S --noconfirm gnome-keyring libsecret dbus
```

官方 CLI 由 setup.sh 自动安装：`npm install -g @aipoju/breakout-cli`
（npm 慢可先换源：`npm config set registry https://registry.npmmirror.com`）

## ⚠️ 关键实现约定（写错任何一条都会卡死，别自己改）

这几条是实测踩出来的，`setup.sh` / `login.py` 已按此实现：

1. **必须用 `dbus-launch` 起 session，不要用 `dbus-run-session`**
   `dbus-run-session` 会走到图形密码弹窗那条分支，在没有显示器的服务器上直接无限等待。
2. **`gnome-keyring-daemon` 用 `--unlock` 从 stdin 读密码（空密码即可），不要加 `--start`**
   `--start` 与 `--unlock` 不兼容，会直接报错退出。
   正确写法（实测有效）：
   ```bash
   eval "$(dbus-launch --sh-syntax)"
   printf '\n' | gnome-keyring-daemon --unlock --components=secrets &
   sleep 5
   ```
3. **不要把 `org.gnome.keyring.SystemPrompter` 指向 `/bin/true`**
   看起来能"跳过弹窗"，实际会让 keyring 判定为「用户取消了 prompt」，
   collection 创建直接失败（`PromptDismissedException`）。
4. **`DBUS_SESSION_BUS_ADDRESS` 要持久化**（写 `~/.bashrc`），
   否则新开的终端里 `breakout` 找不到 keyring，会报解锁失败。setup.sh 默认会写（幂等、改前备份）。
5. **不要用 `secretstorage` 的 `unlock()` 传密码**
   3.5+ 版本该方法只接受 `timeout` 参数，传字符串会被当成超时值直接报类型错误。

## 重启服务器后（重要）

session D-Bus 是进程级的，**服务器重启后会消失**（token 本身存在 keyring 文件里，不会丢）。
表现是 `breakout auth status` 报解锁失败。一条命令修回来：

```bash
bash setup.sh --keyring-only
```

## 登录后日常使用

```bash
breakout capabilities                              # 查看你的能力
breakout auth status --json                        # 账号信息
breakout doctor                                    # 环境健康检查
breakout call topic.query --set operation=list --set pageNum=1   # 示例：查主题
```

## 故障排查

### A. 装依赖阶段

| 现象 | 原因 | 解决 |
|:--|:--|:--|
| `apt-get install gnome-keyring libsecret-tools` 报「找不到包」 | apt 源只配了 `main restricted`，这两个包在 **universe** | `sudo add-apt-repository -y universe && sudo apt-get update` |
| `E: Could not get lock /var/lib/dpkg/lock-frontend` | 上次 apt 卡死后进程没清干净 | `sudo pkill -9 -f apt-get; sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock; sudo dpkg --configure -a` |
| `apt-get update` 几分钟没反应 | 官方源太慢 | 换镜像源（`bash setup.sh --mirror tuna`），或手动改 `/etc/apt/sources.list` 为 `mirrors.tuna.tsinghua.edu.cn` |
| EL 系找不到 `libsecret-tools` | 这个包名只在 Debian 系有 | 装 `gnome-keyring` 即可，`secret-tool` 由它自带 |
| `dbus-launch: command not found` | 缺 `dbus-x11` 包 | `sudo apt-get install -y dbus-x11` / `sudo dnf install -y dbus-x11` |

### B. keyring 阶段（最集中）

| 现象 | 原因 | 解决 |
|:--|:--|:--|
| `secret-tool store` 一直卡住不动 | 系统里已有旧的 dbus session，但 `gnome-keyring-daemon` 没注册上去，`org.freedesktop.secrets` 不存在 | 用**全新** session：`eval "$(dbus-launch --sh-syntax)"` 后再启动 daemon |
| `The --start option is incompatible with --unlock` | 两个参数不能同时用 | 去掉 `--start`，只留 `--unlock` |
| 创建 collection 时无限等待（机器无显示器） | keyring 激活了图形弹窗 `gcr-prompter`（GTK 程序），没有界面就永远等 | 用 `--unlock` 从 stdin 读密码，**从根上不触发弹窗**（见「关键实现约定」第 2 条） |
| 把 `SystemPrompter` 指向 `/bin/true` 后报 `PromptDismissed` | 被判定为"用户取消" | **不要这么做**，恢复默认 prompter，改用第 2 条的正解 |
| `Cannot create an item in a locked collection` / `failed to unlock correct collection` | keyring 没解锁 / `DBUS_SESSION_BUS_ADDRESS` 没传给当前 shell | `bash setup.sh --keyring-only`；或手动 `eval "$(dbus-launch --sh-syntax)"` + `printf '\n' \| gnome-keyring-daemon --unlock --components=secrets &` |
| Python `secretstorage` 报 `unsupported operand type(s) for +: 'float' and 'str'` | 3.5+ 的 `unlock()` 不再接受密码参数，字符串被当成 timeout | 不要用它传密码；解锁交给 `gnome-keyring-daemon --unlock` |
| 用 `jeepney` 直接调 D-Bus 报参数类型错 | `OpenSession` 的 `sv` 需要 variant 元组 | 写 `('s', '')`，不要直接传字符串 |

### C. 登录 / 使用阶段

| 现象 | 解决 |
|:--|:--|
| 扫码后一直等待 | 微信扫码登录后，还需在页面点**【确认授权】** |
| 授权链接过期 | 重新运行 `python3 login.py` 生成新链接（setup.sh 内也会自动续一次） |
| 想换账号 | `breakout auth logout` 后重跑 `python3 login.py` |
| 新开终端后 `breakout` 报解锁失败 | 环境变量没生效：`source ~/.bashrc`，或确认 `~/.breakout/env.sh` 已被 source |
| 找不到 `breakout` 命令 | CLI 没装 / Node 由 nvm 安装但当前 shell 没加载 nvm / 全局 bin 不在 PATH |

## 安全说明

- 全程**不输入账号密码** —— 微信扫码在官网完成，凭据不经本工具
- 登录凭证只写入系统 keyring，不落盘、不打印、不进日志
- 工具内不含任何个人数据，可放心分发使用
- 卸载：`npm uninstall -g @aipoju/breakout-cli`，并按需删除 `~/.breakout`

## 文件结构

```
breakout-cli-linux-login/
├── README.txt    快速上手说明
├── SKILL.md      本文档
├── setup.sh      一键安装 + 登录脚本
└── login.py      扫码登录工具（核心）
```
