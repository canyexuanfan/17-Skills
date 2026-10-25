# gzh-to-ima-skill

> ⚠️ **能力边界（先读这条）**：微信已**关闭「按 fakeid 抓他人公众号全部历史」的后台接口**（非自己的号一律返回 `200013`，属权限墙，换 cookie / 换 IP / 等待都无效）。
> 因此本 Skill 分两条路：**自己的号**（后台有权限）走 `appmsgpublish` 可拿全量并验收；**别人的号**只能靠「合集 + 搜狗」多渠道**尽可能多**地抓，**不保证是全部文章**。
>
> 本 Skill 已收录于 [17-Skills](https://github.com/canyexuanfan/17-Skills) 合集。原仓库 [gzh-to-ima-skill](https://github.com/canyexuanfan/gzh-to-ima-skill) 已归档，历史与 Star 保留，后续更新在本仓库进行。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/canyexuanfan/17-Skills/actions/workflows/ci.yml/badge.svg)](https://github.com/canyexuanfan/17-Skills/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org)
[![Node](https://img.shields.io/badge/node-18%2B-green.svg)](https://nodejs.org)

把**微信公众号文章**批量抓取并导入到 **IMA 知识库**的自动化技能（skill）。

> 给一个公众号文章链接 → **先判断是不是你自己的号**：自己的号抓全量并验收；别人的号走合集/搜狗多渠道尽量多抓（不保证全量）→ 用 IMA OpenAPI 把文章逐条导入指定知识库/文件夹。

**Batch-import WeChat official account articles into an IMA knowledge base** — own account: fetch full history via `appmsgpublish` and verify against `total_count`; others' accounts: multi-channel best-effort (albums + Sogou), then per-URL `import_urls` through the IMA OpenAPI.

---

## 目录

- [功能](#功能)
- [架构](#架构)
- [目录结构](#目录结构)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置凭证](#配置凭证)
- [使用](#使用)
- [频控警告](#频控警告非常重要)
- [安全 / 脱敏说明](#安全--脱敏说明)
- [贡献](#贡献)
- [License](#license)

---

## 功能

> ⚠️ **先看这条再往下**：本工具**能抓全量的范围只有「你自己管理的公众号」**（你有后台 admin）。
> 别人的号走多渠道尽量多抓，**不保证全量** —— 微信已关闭「按 fakeid 抓他人全部历史」的后台接口，
> 非自己的号一律返回 `200013`。详见下方 [能力边界](#能力边界重要)。

- 🕷️ **抓自己管理的号（可验收全量）**：`fetch_articles.py --own` 走后台「发表记录」接口，
  **不需要 `__biz`**，以 `total_count` 为验收判据（篇数不等就不算抓完）。
- 🧭 **抓别人的号（多渠道，尽量多）**：`fetch_other_channels.py` 组合**合集(album)** + **搜狗微信搜索**，
  合并去重并输出渠道分布与时间覆盖。零凭据可用。
- 🔁 **断点续抓**：支持从断点继续，不重抓已抓过的文章；频控触发自动停止并落盘。
- 📄 **增量交付**：对比已交付记录，只输出「本次新增且从未发过」的文章清单（Markdown）。
- 📥 **导入 IMA**：经 IMA OpenAPI `import_urls` 写入知识库 —— **单条导入 + 3s 间隔**
  （实测批量 5 条只成 1 条），并提供 `--check` 验收裸链接率与重复组。
- 🚀 **一键全自动**：`--auto-import` 让抓取完成后自动导入 IMA。
- 🎯 **CLI 驱动**：所有脚本都通过命令行参数控制，无需手动编辑代码文件。

---

## 能力边界（重要）

| 目标号 | 能拿到 | 说明 |
|:--|:--|:--|
| **自己管理的号** | **全量历史** | 走后台发表记录接口，`total_count` 可验收 |
| 别人的号 + 建过合集 | 该合集全部文章 | 免登录、免凭据 |
| 别人的号（无合集） | 关键词命中的一部分 | 搜狗通道，按相关性排序、非时间线 |
| 别人的号要全量 | 需微信客户端登录态 | `profile_ext` + `uin/key/pass_ticket`，见配套说明 |

**已失效的做法**：`cgi-bin/appmsg?action=list_ex&fakeid=<别人的biz>` —— 微信关闭了这条权限，
恒返回 `200013 freq control`。这是权限墙，等一小时/换 cookie/换 IP 都无效，不是本工具的 bug。

---


## 架构

```
Step 0：判断目标号是不是你自己管理的
   │
   ├── 是（有后台 admin）──► fetch_articles.py --own
   │                        appmsgpublish 发表记录 → 不需要 __biz → total_count 验收 → JSON
   │
   └── 否（别人的号）──────► fetch_other_channels.py
                            合集(album, 免登录) + 搜狗微信搜索 → 合并去重 → JSON
                            ⚠️ 只能尽量多，不保证全量（fakeid 抓全量的接口已被微信关闭）
                                   │
                                   ▼
                     ┌─────────────────────────────┐
                     │  ima-skill (依赖，随仓库附带) │
                     │  └─ ima_api.cjs              │  import_urls / search_knowledge_base / ...
                     └──────────────┬──────────────┘
                                    │ 单条导入 + 3s（批量会大批失败）
                                    ▼
                     IMA 知识库（knowledge_base_id + folder_id），最后 --check 验收
```

---

## 目录结构

```
gzh-to-ima-skill/
├── README.md
├── .gitignore
├── wechat-to-ima/                 # 主技能：抓取 + 交付
│   ├── SKILL.md                   # 技能说明（Agent 读取）：Step 0 判定 → 分支 A/B
│   ├── fetch_articles.py              # 自己的号：appmsgpublish 发表记录，total_count 验收（仅标准库）
│   ├── fetch_other_channels.py        # 别人的号：合集(album) + 搜狗，多渠道合并去重（仅标准库）
│   ├── parse_cookie.py                # 把浏览器 Copy All 的 Cookie 转成请求头字符串
│   ├── resume_crawl.py                # 断点续抓 + 增量交付（支持 CLI 参数，无需改文件）
│   ├── batch_import_to_ima.py         # 导入 IMA（单条 + 3s），支持 --check 验收
│   └── weixin_credentials.example.py  # 微信凭证模板（复制后填）
└── ima-skill/                         # 依赖：IMA OpenAPI 客户端（原样附带）
    ├── SKILL.md
    ├── ima_api.cjs
    ├── meta.json
    ├── notes/
    └── knowledge-base/
```

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.8+ | 仅用标准库（`urllib`/`re`/`json`），**无需 pip install** |
| Node.js | 18+ | 运行 `ima_api.cjs`（仅导入 IMA 时需要） |

---

## 安装

把两个目录放进你的 Agent 技能目录（任意支持 Skill 的 Agent 均可）：

```bash
# 放到你的 Agent 技能目录（示例）
cp -r wechat-to-ima  <你的技能目录>/
cp -r ima-skill      <你的技能目录>/
```

---

## 配置凭证

### 1. 微信凭证（必须）

```bash
cd wechat-to-ima
cp weixin_credentials.example.py weixin_credentials.py
```

然后编辑 `weixin_credentials.py`：

- **token**：浏览器打开 `https://mp.weixin.qq.com/` 扫码登录 → 地址栏 `token=数字` 取后面的数字。
- **cookie**：F12 → Application → Cookies → `mp.weixin.qq.com` → 右键 **Copy All** →
  粘到文本文件 `cookies_dump.txt` → 运行 `python parse_cookie.py < cookies_dump.txt` 得到单行 Cookie 字符串，填进 `cookie = "..."`。

> 凭证过期（接口返回 `200003`）时，重新扫码并更新 token + cookie。

### 2. IMA 凭证（导入时需要）

二选一：

```bash
# 方式 A：环境变量
export IMA_CLIENT_ID="你的ClientID"
export IMA_API_KEY="你的APIKey"

# 方式 B：配置文件
mkdir -p ~/.config/ima
echo "你的ClientID"  > ~/.config/ima/client_id
echo "你的APIKey"    > ~/.config/ima/api_key
```

> ClientID / APIKey 在 `ima.qq.com` 的 Agent 开放接口页面生成。

---

## 使用

### Step 1A：抓「自己的号」（可验收全量）

```bash
cd wechat-to-ima
python fetch_articles.py --own --name "我的公众号"
```

输出：`<name>_全部文章_YYYYMMDD.json`。结束会打印 `篇数 / total_count`，
**两者相等才算抓完**（不等则退出码 2，可加 `--resume` 续抓）。`200013` = 频控，立即停止。

### Step 1B：抓「别人的号」（多渠道，尽量多，不保证全量）

```bash
cd wechat-to-ima
# 最简：只用搜狗（零凭据）
python fetch_other_channels.py --name "目标号名称" --sogou-pages 4

# 已知该号有合集时，合集会给出该合集全部文章
python fetch_other_channels.py --name "目标号" --biz "MzUx..." --album-id 3061234567890
```

输出：`<name>_多渠道_YYYYMMDD.json`，含 `source`（album/sogou）。
脚本结束会打印渠道分布、时间覆盖，以及「非自己管理的号无法获取全量」的声明。

### Step 2（可选）：断点续抓 + 增量交付

```bash
cd wechat-to-ima
python resume_crawl.py --biz "公众号__biz" --name "公众号名称"
```

> ⚠️ `resume_crawl.py` 走的是老的 `fakeid` 路径，**只适用于你自己的号**；
> 别人的号该路径已被微信关闭（`200013`）。

支持 CLI 参数控制，无需编辑脚本文件。更多选项：

```bash
# 指定 master 和已交付记录路径
python resume_crawl.py --biz "公众号__biz" --name "公众号名" \\
    --master "./master.json" --delivered "./delivered.json"

# 续抓后自动导入 IMA
python resume_crawl.py --biz "公众号__biz" --name "公众号名" \\
    --import-ima --kb-id "知识库ID"
```

### Step 3：批量导入 IMA 知识库

```bash
cd wechat-to-ima

# 先搜索知识库 ID
python batch_import_to_ima.py --search "知识库名称"

# 列出知识库内容（查文件夹 ID）
python batch_import_to_ima.py --list "知识库ID"

# 批量导入到指定文件夹
python batch_import_to_ima.py --import-json "公众号_全部文章_YYYYMMDD.json" \\
    --kb-id "知识库ID" --folder-id "文件夹ID"

# 导入到根目录（folder-id 留空）
python batch_import_to_ima.py --import-json "公众号_全部文章_YYYYMMDD.json" \\
    --kb-id "知识库ID"
```

### 一键全流程

抓取 + 自动导入一步搞定，适合无人值守运行：

```bash
python fetch_articles.py --url "https://mp.weixin.qq.com/s/xxxx" \\
    --name "公众号名称" \\
    --save-master "./master.json" \\
    --auto-import --kb-id "知识库ID"
```

`--save-master` 同时保存 master 全量基准。
`--auto-import` 抓取完成后自动调用 batch_import_to_ima.py 导入 IMA。
两者配合，跑一次就等于完成了整个工作流。

---

## 频控警告（非常重要）

微信文章列表接口有严格频控：

- `ret=200013` / `ret=200003` = **频率控制**。脚本已内置「立即停止 / 退避重试」，但请务必：
  - 抓取时每批保持间隔（默认 3 秒），**不要拉全量列表时高频请求**。
  - 触发频控后**立刻停止**，等待冷却（数十分钟到 24 小时），否则可能触发更严的封禁。
- 不要轻信第三方库把 `200013` 翻译成「token 错误」的误导提示，请直接检查 `base_resp.ret`。

---

## 安全 / 脱敏说明

本仓库已做脱敏处理，**不含任何私人凭证**：

- `weixin_credentials.py`（含真实微信 token/cookie）**不纳入版本控制**，仅提供 `.example.py` 模板。
- 运行时生成的状态/数据文件（`resume_state.json` / `delivered_links.json` / `master_articles.json`）被 `.gitignore` 排除。
- 脚本中硬编码的公众号 `biz` / 名称 / 绝对路径已改为示例占位符，请按你的目标公众号修改。

请勿把填好真实凭证的 `weixin_credentials.py` 提交或转发他人。

---

## 贡献

欢迎提交 Issue 和 Pull Request！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [行为准则](CODE_OF_CONDUCT.md)。

> 提 Bug / 功能请求时，请使用仓库预设的 Issue 模板，信息更完整、处理更快。

提交前请确保：
- 不提交任何私人凭证（`weixin_credentials.py` 已被 `.gitignore` 排除）；
- 通过 CI 的敏感信息扫描（见 [CONTRIBUTING.md](CONTRIBUTING.md) 自检查骤）。

---

## License

[MIT License](LICENSE) © 2026 canyexuanfan

详见 [LICENSE](LICENSE) 文件。
