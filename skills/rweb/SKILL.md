---
name: rweb
version: 1.2.0
description: 用 `rweb` CLI 读 reddit.com —— 不开浏览器、不用 API key、不装第三方包：访客态零账号可读版块 / 帖子 / 评论树 / 搜索 / 用户页 / Atom 源；填本人 cookie 后解锁账号数据（收藏 / 订阅 / 投票）与写操作。当用户说"不用浏览器看 reddit""查某个版块 / 某人的帖子""reddit 搜索""拉 reddit 评论树""reddit 投票收藏""把 reddit 接到 Agent / CLI"时使用。本文档是 Agent 操作手册：命令映射、`--json` 输出、错误解释、写操作安全规则、能力边界。需先安装 rweb（见文末）。
metadata:
  requires:
    bins: ["rweb"]
---

# rweb — 用命令行读 reddit.com

**Agent 操作手册。** 前半部分是"用户说什么 → 你跑什么"，后半部分是**错误解释**
与**边界**（这两块最容易答错）。

安装：见仓库 README（`bash install.sh` / `pipx install .` / `python3 -m rweb`）。
没装先跑 `rweb caps` 看能不能用。

---

## 0. 三条命令确认环境

```bash
rweb doctor                              # 连通性自检 + 核心模块是否就位
rweb --selftest                          # 核心模块加载 + 分类行为 + 真跑一次会话初始化
rweb caps                                # 当前模式（访客/登录）能做什么，一屏看清
rweb json /r/programming/hot --limit 1   # 冒烟：能读到数据
```

`caps` 的输出随登录态变化 —— 报"能做什么"之前先跑它，别凭记忆答。
`rweb --selftest` 是**排查本机环境**的第一选择：它会把平台标签、实际加载的核心模块
路径、离线分类结果、以及一次真实会话初始化的结果全打出来。用户在某个系统上报
"不能用"时，让 ta 先跑这个，把输出贴回来。

## 1. 用户说什么 → 你跑什么

| 用户说 | 你跑 |
|:--|:--|
| 看某版块热门 / 最新 | `rweb json /r/<sub>/hot --limit 100` · `.../new` · `.../top?t=week` |
| 翻多页 | `rweb json /r/<sub>/hot --pages 3 --out posts.jsonl` |
| 看某个帖子 + 全部评论 | `rweb json /r/<sub>/comments/<id>` |
| 搜索 | `rweb json "/search?q=<q>&sort=new&restrict_sr=0"` |
| 看某人发过什么 | `rweb json /user/<name>/submitted`（`/comments` 同理） |
| 不看评论、只要清单 | `rweb rss /r/<sub>?limit=100`（轻量，见 §3） |
| 这个构建支持哪些操作 | `rweb ops` / `rweb ops --write` / `rweb ops --read` |
| 直接调某个操作 | `rweb op <Name> --var k=v [--var k2=v2]` |
| 打任意 JSON 路径 | `rweb api /api/... --param k=v` |
| 投票 | `rweb vote <id> --dir up` （写，见 §5） |
| 订阅 / 退订 | `rweb subscribe <sub>` / `rweb subscribe <sub> --off`（写） |
| 我的收藏 / 订阅 / 我 | `rweb me` · `rweb json /user/<me>/saved` · `rweb json /subreddits/mine/subscriber` |

## 2. 输出契约

- **给人看**：默认表格 / 摘要。
- **给程序看**：加全局 `--json`，**一行一个 JSON 对象**，可直接管进 `jq`。

```bash
rweb --json json /r/programming/hot --limit 100 | jq -r '.title'
rweb --json json /r/rust/comments/<id> | jq -r '.[1].data.children[].data.author'
```

`--json` 模式下**只输出 JSON**，没有横幅、没有提示行 —— 报错走 stderr 且退出码非 0。

## 3. 两种访客读法，怎么选

| 通道 | 命令 | 给什么 | 什么时候用 |
|:--|:--|:--|:--|
| **JSON** | `rweb json <path>` | **完整字段**：`score` `ups` `upvote_ratio` `num_comments` `author_fullname` `link_flair_text`；**原生 `after` 翻页** | 默认首选 |
| **Atom** | `rweb rss <path>` | 字段少（**无 score / 无评论数 / 无评论树**），但**免会话** | 只要标题+链接；或 JSON 通道被限流时的旁路 |

`rss` 的 `?limit=` 上限 **100**，翻页要用 `after=<本页最后一条的 id>` —— 用第一条 id 只会挪一位，
看着像"翻页没生效"。

## 4. 错误怎么读（**最容易答错的地方**）

先记住一条：**判"通不通"看 `Content-Type` + 能不能 `json.loads`，不看裸状态码。**
本站对未注册路径会回 `200` + 一大坨 HTML 外壳（几百 KB、零信息）。

| 症状 | 真意 | 怎么办 |
|:--|:--|:--|
| `rweb session` 打印 **`OK  session usable (upstream throttled, HTTP 403)`** | 会话**是好的**，是**上游按出口限流** | 等一会儿或换出口；**别说"坏了"** |
| `rweb session` 打印 `FAIL ...` | 会话没建立成功 | 重跑一次；仍失败则是出口被硬拦 |
| `rweb doctor` 的 4 项里 `json api` / `graphql` FAIL | 常见就是同一件事：**出口配额被打满** | 看 `session check` 与 `rss` 是否 OK；那是限流不是装坏 |
| 裸路径 404 且 **0 字节** | 该路径没注册 | 该端点不存在，换路径 |
| 404 但**带结构化 JSON** 或跳登录 | 端点在世、**需要凭据** | 如实说"要登录"，别说"没有" |
| 写操作回 **500** | **多半是取值措辞不对** | 对 §5 的枚举表逐字核对；`--dry-run` 先看发出的请求 |
| `Internal server error` | 兜底文案 | 别拿它当结论；先 `--dry-run` 看请求形状 |
| 结果为空 | 先怀疑**样本腐烂**（帖子被删 / 版块改名） | 换个版块/关键词复核，别直接下结论 |

**"上游限流" 与 "没有会话" 必须分开报。** 访客读有**按出口的配额（约 100/窗口）**，
共享出口（机房 IP、公共代理、被别人用过的 Tor 出口）常常已经被打满 ——
这时 `doctor` 可能只拿 2/4，而 `session` 仍然是对的。把限流说成"初始化失败 / 坏了"
是**假阴性**，会误导用户去改根本没坏的东西。

## 5. 写操作安全规则

**任何改状态的调用都需要显式 `--yes`，没有 `--yes` 一律不发。**

```bash
rweb --dry-run op UpdatePostVoteState --var input.postId=t3_xxx --var input.voteState=UP
rweb op        UpdatePostVoteState --var input.postId=t3_xxx --var input.voteState=UP --yes
```

**取值枚举是逐字敏感的（写错回 500，不是"参数错误"）：**

| 字段 | 合法值 |
|:--|:--|
| `voteState` | **`UP`** / `DOWN` / `NONE`（**不是 `UPVOTE`**） |
| `saveState` | `SAVED` / `NONE` |
| `hideState` | `HIDDEN` / `NONE` |
| `favoriteState` | `FAVORITED` / `NONE` |
| `followState` | `FOLLOWED` / `UNFOLLOWED` |
| 订阅（legacy） | `action=sub` / `unsub` |

**给 Agent 的纪律**：发帖 / 评论 / 删除 / mod 类操作**对外可见或不可逆** ——
不要为了"验证能力"去试。可逆的（投票 / 收藏 / 隐藏 / 订阅）也应当**先把结果给用户看、
拿到明确同意再执行**，执行后若需要复原，用相反枚举调回去。

## 6. 边界（**别把"没做"写成"做不到"**）

- **读**：版块列表 / 帖子 / 评论树 / 搜索 / 用户页 / Atom —— 访客态即可，无需账号。
- **账号**：`me`、收藏、订阅、投票状态等需**用户自己的 cookie**。本工具**不提供注册**，
  也不该替你注册 —— 正确路径是让用户在自己浏览器登录后把 cookie 交过来。
- **GitHub 风格的 API key**：不需要，也不使用。所有请求只发往 reddit.com。
- **限流**：访客读有配额，别当爬虫农场；保持人类速率。
- 调用前用 `rweb caps` / `rweb ops` 确认当前构建到底支持什么 —— **别凭本文件推测**。

## 7. 装

```bash
bash install.sh          # 写一个 rweb 启动器到 ~/.local/bin
# 或
pipx install .           # 若已 clone
python3 -m rweb --help   # 不安装直接跑（包形态）
```

要求：Python 3.8+ 与 `curl` 在 PATH 上。**零 pip 依赖** —— 会话处理核心是**随包分发的
预编译原生扩展**（5 平台，`abi3`，Python 3.9+ 通用，装的时候不需要编译器）：

```
rweb/rcore/bin/{linux-x86_64,linux-aarch64,macos-arm64,macos-x86_64}/_m0.abi3.so
rweb/rcore/bin/windows-amd64/_m0.abi3.pyd
```

别的平台（如 linux-riscv64 / BSD）会明确告诉你本包内置了哪些平台，而不是静默失败。
判断"装好了没"最省事的办法：`rweb --selftest`。
