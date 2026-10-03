# xweb

**X（x.com）命令行客户端 —— 不开浏览器、不用 API key。**

访客态零账号即可读公开数据；填入**你自己账号**的 cookie 后解锁搜索、时间线、粉丝、收藏、通知等全站读能力。

> 仅供学习、研究与个人数据使用。请遵守 [X 的服务条款](https://x.com/en/tos)，不要高频抓取。详见文末[免责声明](#免责声明)。

---

## 能力总览

| 命令 | 说明 | 访客 | 登录 |
|:--|:--|:--:|:--:|
| `xweb user <handle>` | 资料（粉丝/推文数/简介/加入时间） | ✅ | ✅ |
| `xweb tweets <handle> [-n N] [--pages P]` | 时间线（可翻页或封顶快照，见[边界](#边界与已知限制)） | ✅ | ✅ |
| `xweb latest <handle>` | **真正的最新推文**（页面 SSR + CDN） | ✅ | ✅ |
| `xweb tweet <id\|url>` | 单条推文 | ✅ | ✅ |
| `xweb users <id,...>` | 按 ID 批量查人 | ✅ | ✅ |
| `xweb trends [-n N] [--list]` | 趋势榜 / 地区表 | ✅ | ✅ |
| `xweb quote <TICKER> [--timeframe 1D]` | 股票行情卡（价格 / 24h 涨跌 / 市值 / 迷你走势） | ✅ | ✅ |
| `xweb detail <id\|url> [-n N]` | 推文详情 **+ 回复串** | ✅ | ✅ |
| `xweb detail <id1,id2,...> \| --ids-file f [-j N]` | 批量详情（并发），单条失败不影响整批 | ✅ | ✅ |
| `xweb community <id\|url> [--what info\|posts\|about\|media]` | 社区（帖子走页面 SSR） | ✅ | ✅ |
| `xweb ops` / `xweb op <名>` | 查操作表 / **调用任意操作**（通用逃生口） | 按 op | 全量 |
| `xweb search "<query>" [--product Top\|Latest\|Media\|People]` | 搜索 | ❌ | ✅ |
| `xweb tweets <h> --tab replies\|media\|originals\|photos\|videos\|reposts\|articles\|likes` | 各 tab 时间线 | ❌ | ✅ |
| `xweb home [--latest]` / `bookmarks` / `notifications` | 首页 / 收藏 / 通知 | ❌ | ✅ |
| `xweb relations <h> --followers\|--following` | 粉丝 / 关注列表 | ❌ | ✅ |
| `xweb settings [--json]` | 账号设置（41 个字段） | ❌ | ✅ |
| `xweb stream <media_key>` / `hashflags` | 直播流状态 / 活动表情表 | ❌ | ✅ |
| `xweb rest <path> [--method --data]` | REST 直连（`/1.1/*`，自动补签名） | ❌ | ✅ |
| `xweb post "文字" [--reply-to ID] [--quote ID]` | 发推 / 回复 / 引用 | ❌ | ✅ `--yes` |
| `xweb like \| unlike \| repost \| del <id>` | 互动与删除 | ❌ | ✅ `--yes` |
| `xweb follow \| unfollow \| block \| mute <handle>` | 关注 / 拉黑 / 静音 | ❌ | ✅ `--yes` |
| `xweb doctor` / `xweb caps` | 自检 / 当前能力清单 | ✅ | ✅ |

**全局选项（`--json` / `--verbose` / `--dry-run`）必须写在子命令之前**：

```bash
xweb --json user X      # ✅
xweb user X --json      # ❌
```

---

## 安装

需要 **Python 3.9+**，唯一依赖是 [`curl_cffi`](https://pypi.org/project/curl_cffi/)（用于浏览器指纹模拟）。

> 包里自带各平台的**预编译签名模块**（abi3，Python 3.9+ 通用）：Linux `x86_64` / `aarch64`、Windows `amd64`、macOS `arm64` / `x86_64`。
> 登录态下少数接口（搜索、粉丝、收藏、设置等）要求一个随会话变化的请求头，由它提供；访客态不需要它。
> 查看本机加载的是哪一份：`python3 -m xweb.xclid`。

**方式 1：脚本安装（最简单）**

```bash
git clone https://github.com/canyexuanfan/17-Skills.git
cd 17-Skills/skills/xweb
./install.sh                 # 装依赖 + 把 xweb 链接到 ~/.local/bin
xweb elonmusk                # 试一下
```

**方式 2：pipx / pip（作为 Python 包安装）**

```bash
pipx install .               # 或：pip install .
xweb elonmusk
```

**方式 3：不安装，直接在仓库里跑**

```bash
python3 -m pip install -r requirements.txt
./bin/xweb elonmusk          # 或 python3 -m xweb elonmusk
```

Windows 用 `bin/xweb.ps1`（需要 PowerShell + Python）：

```powershell
.\bin\xweb.ps1 elonmusk
```

---

## 快速开始（访客态，零账号）

```bash
xweb elonmusk                    # 资料（默认子命令 = user）
xweb latest elonmusk -n 5        # 最新 5 条推文
xweb tweet https://x.com/x/status/2099051728318660621
xweb trends -n 10                # 趋势榜
xweb quote NVDA --timeframe 1D   # 行情卡
xweb caps                        # 当前模式下能做什么，一屏看清
```

访客态**不需要任何凭据**，能读的是 X 对未登录访问放行的那一部分（`xweb caps` 会列出实际可用的操作）。

---

## 登录态（读全站，约 30 秒）

> cookie = 你账号的**完全访问权**（能发推、能改资料、能删数据）。**请用小号**，别用主号。

**1. 拿两个 cookie 值**

1. 用 Chrome / Edge / Safari 正常登录 `x.com`（不要无痕、不要自动化）
2. `F12` 打开 DevTools → **Application**（Safari 是「存储 / Storage」）
3. 左侧展开 **Cookies** → `https://x.com`
4. 复制这两行的值：

| Cookie | 长什么样 | 作用 |
|:--|:--|:--|
| `auth_token` | 40 位左右十六进制 | 登录凭据 |
| `ct0` | 160 位左右十六进制 | CSRF token |

> 取不到 `ct0`？别从 Network 请求头整条复制（有些浏览器会省略），**在 Application 面板里逐个复制**最稳。

**2. 交给 CLI（三种方式任选）**

```bash
xweb login --cookie "auth_token=xxxxxx; ct0=yyyyyy"
xweb login --cookie-file cookie.txt          # 内容长/带换行时推荐
xweb login --auth-token xxxxxx --ct0 yyyyyy
```

CLI 会从你粘的任意文本里自动挑出这两个值、检查是否截断、以 `600` 权限存盘，并立刻验证：

```
✓ 已保存凭据（600）
✓ 登录成功：@your_burner_account
```

清除：`xweb login --logout`。凭据位置：`~/.config/x-web/config.json`。

**3. 开始用**

```bash
xweb search "ai agents" --product Latest
xweb tweets nasa --tab replies -n 20
xweb bookmarks
xweb detail 2099051728318660621 -n 10
```

**写操作永远是显式确认的**：

```bash
xweb --dry-run post "测试" --yes     # 先看要发什么（凭据脱敏），不发送
xweb post "测试" --yes               # 确认后再发
```

---

## 通用逃生口：`ops` / `op`

CLI 内置一张操作表，覆盖搜索、时间线、详情、用户、社区、互动各族。找不到现成命令时：

```bash
xweb ops --filter 'Community'            # 有哪些操作，列出来
xweb ops --status verified --json        # 只看已确认可用的，出结构化结果
xweb op CommunityQuery --auto-vars       # 自动补全该操作的必填变量
xweb op CommunityQuery --var communityId=1471580197908586507
xweb op UserByScreenName --vars '{"screen_name":"elonmusk"}'
```

多数常用操作在表里预置了变量样例，**不带变量也能跑**。

---

## 边界与已知限制

请按这节的内容理解「能做什么」，别把能用说成全能。

1. **访客态 ≠ 完整 X。** 搜索、详情、粉丝、收藏、通知这些接口只对登录用户开放，这是 X 服务端的限制，不是没实现。访客态只能用其中一部分（`xweb caps` 会实时列出）。
2. **时间线可能不全。** 有的账号可翻页且新鲜（`--pages N`），有的只剩一段较早的采样（无游标，翻不动）；CLI 会明确告诉你是哪种。
   - 要**最新动态** → 用 `xweb latest <handle>`（通常能拿到当天/前一天）。
3. **访客拿不到"全量历史"。** 页面 SSR 只有最新 5~10 条且无分页游标；GraphQL 时间线访客最多约 100 条采样。要全量只能靠登录态。
4. **单次结果不代表全部。** 某次查询为空，可能只是这次没有内容；某些账号的时间线只有较早的采样。别用一次结果下结论。
5. **操作表是快照。** 服务端会调整接口，表中的操作可能失效或变更——`xweb caps` 会实时显示当前状态，失效时以新版本为准。
6. **风控与 IP。** 「数据中心 IP + 新 cookie」在风控系统里是高风险组合。**优先在自己的家用电脑/本地网络跑**；机房环境请用小号并降低频率。
7. **不要提交 cookie。** `~/.config/x-web/config.json` 不要进 git、不要贴到 issue 或聊天窗口。
8. **长推文可能被截断。** 超过 ~280 字的推文，在访客态/备用通道里只拿得到截断版；`--json` 输出里每条形如 `text_truncated: true` 就是「这段正文不完整」。要全文请用登录态（走 GraphQL，会取完整正文）。
9. **签名模块是预编译的。** 登录态下少数接口额外要求一个随会话变化的请求头，`xweb/xclid/` 里的原生扩展负责生成它（它会去 x.com 取公开页面素材，和其他命令一样）。该模块按平台分发二进制、不随包发源码；不想用二进制的话，访客态不受影响。

---

## 常见问题

**Q：一定要登录吗？**
不是。访客态就能查资料、单推、最新推文、趋势、行情卡、社区帖子。搜索/粉丝/收藏这些必须登录。

**Q：会不会导致账号被封？**
cookie 等于把你的账号交给这个程序，而且自动化访问本身可能与平台政策冲突。**用小号、别高频、别做批量写操作**，风险自担。

**Q：支持 Windows / macOS 吗？**
支持。Linux（`x86_64` / `aarch64`）、Windows（`amd64`）、macOS（`arm64` / `x86_64`）都随包带好了签名模块，Python 3.9+ 通用；
Windows 启动器用 `bin/xweb.ps1`。若你的平台不在列表里，`python3 -m xweb.xclid` 会直接把缺的那一份报出来。

**Q：为什么依赖 `curl_cffi`？**
它提供浏览器的 TLS/HTTP2 指纹，纯 `requests` 在很多站点上会被直接拒绝。

**Q：数据从哪来？**
全部来自 x.com 公开页面/接口的响应，本项目只是把你手动能做的一步步变成命令。

---

## 给 AI Agent 用（可选）

本目录根带了一份 **Agent 操作手册**：`SKILL.md`。把它放进你的 agent 技能目录（或直接读），
它包含「用户说什么 → 跑什么命令」的映射、`--json` 输出约定、**错误解释表**（`404 空 body` /
`Internal server error` / 空结果到底什么意思）和**写操作安全规则**。

```bash
# 例：丢进通用 skills 目录（各 agent 约定不同，按你的来）
mkdir -p ~/.my-agent/skills/xweb && cp SKILL.md ~/.my-agent/skills/xweb/
```

## 免责声明

本项目**仅供学习与个人数据研究**，与 X Corp. 无任何关联，未获其授权或认可。

- 使用者需自行确保其使用方式符合当地法律与 X 的服务条款；
- 请勿用于大规模抓取、自动化骚扰、垃圾信息、账号滥用等场景；
- 作者不对因使用本工具造成的任何账号处罚、数据丢失或其他损失负责。

如果你的使用场景涉及大量数据，请走 [X 官方 API](https://developer.x.com/)。

## 许可

[Apache-2.0](LICENSE)。仓库内的 Python 代码、文档、数据表都按 Apache-2.0 授权；
`xweb/xclid/bin/` 下的预编译签名模块以二进制形式随包分发（可自由使用与再分发，只是不带源码）。
