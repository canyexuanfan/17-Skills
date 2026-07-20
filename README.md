# gzh-to-ima-skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/canyexuanfan/gzh-to-ima-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/canyexuanfan/gzh-to-ima-skill/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org)
[![Node](https://img.shields.io/badge/node-18%2B-green.svg)](https://nodejs.org)

把**微信公众号文章**批量抓取并导入到 **IMA 知识库**的自动化技能（skill）。

> 给一个公众号文章链接 → 脚本抓取该号全部历史文章（去重）→ 用 IMA OpenAPI 把文章 URL 批量导入指定知识库/文件夹。

**Batch-import WeChat official account articles into an IMA knowledge base** — fetch via `__biz`, dedupe, then `import_urls` through the IMA OpenAPI.

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

- 🕷️ **抓取**：从一篇公众号文章 URL 自动提取 `__biz`，循环抓取该号全部历史文章，去重后导出 JSON（含标题/链接/时间）。
- 🔁 **断点续抓**：支持从断点 `offset` 继续，不重抓已抓过的文章；频控触发会自动退避重试。
- 📄 **增量交付**：对比已交付记录，只输出「本次新增且从未发过」的文章清单（Markdown）。
- 📥 **导入 IMA**：通过 IMA OpenAPI `import_urls` 把文章 URL 批量灌入知识库，每批 10 条。

---

## 架构

```
用户给出公众号文章 URL
        │
        ▼
┌─────────────────────────────┐
│  wechat-to-ima (本仓库主技能) │
│  ├─ fetch_articles.py        │  提取 __biz → 循环抓全量(每批间隔防频控) → 去重 → JSON
│  └─ resume_crawl.py          │  断点续抓 + 增量交付文档
└──────────────┬──────────────┘
               │ 提取到的文章链接
               ▼
┌─────────────────────────────┐
│  ima-skill (依赖，随仓库附带) │
│  └─ ima_api.cjs              │  IMA OpenAPI 客户端：import_urls / search_knowledge_base / ...
└──────────────┬──────────────┘
               │
               ▼
         IMA 知识库（指定 knowledge_base_id + folder_id）
```

---

## 目录结构

```
gzh-to-ima-skill/
├── README.md
├── .gitignore
├── wechat-to-ima/                 # 主技能：抓取 + 交付
│   ├── SKILL.md                   # 技能说明（Agent 读取）
│   ├── fetch_articles.py          # 全量抓取（仅标准库，无需 pip install）
│   ├── parse_cookie.py            # 把浏览器 Copy All 的 Cookie 转成请求头字符串
│   ├── resume_crawl.py            # 断点续抓 + 增量交付
│   └── weixin_credentials.example.py  # 微信凭证模板（复制为 weixin_credentials.py 后填）
└── ima-skill/                     # 依赖：IMA OpenAPI 客户端（原样附带）
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
| Node.js | 18+ | 运行 `ima_api.cjs` |

---

## 安装

把两个目录放进你的 WorkBuddy 技能目录（或任意目录，调用时写对路径即可）：

```bash
# 放到 WorkBuddy 用户级技能目录（示例）
cp -r wechat-to-ima  ~/.workbuddy/skills/
cp -r ima-skill      ~/.workbuddy/skills/
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

### Step 1：抓取全部文章

```bash
cd wechat-to-ima
python fetch_articles.py \
  --url "https://mp.weixin.qq.com/s/xxxx" \
  --name "公众号名称"
```

输出：`<name>_全部文章_YYYYMMDD.json`。空列表 = 已抓到末尾；`200013` = 频控立即停止。

### Step 2（可选）：断点续抓 + 增量交付

编辑 `resume_crawl.py` 顶部三项（`MASTER` / `NAME` / `BIZ`）指向你的目标公众号，然后：

```bash
python resume_crawl.py
```

脚本从断点续抓，把「本次新增且从未发过」的文章生成一份 Markdown 文档（路径以 `DELIVER_DOC:` 开头打印）。

### Step 3：导入 IMA 知识库

先查知识库 ID 和文件夹 ID：

```bash
node ../ima-skill/ima_api.cjs 'openapi/wiki/v1/search_knowledge_base' '{"query":"知识库名称","cursor":"","limit":10}'
node ../ima-skill/ima_api.cjs 'openapi/wiki/v1/get_knowledge_list' '{"knowledge_base_id":"kb_id","cursor":"","limit":50}'
```

拿到 `knowledge_base_id` 和 `folder_id` 后，每 10 条一批导入：

```bash
node ../ima-skill/ima_api.cjs 'openapi/wiki/v1/import_urls' \
  '{"knowledge_base_id":"kb_id","folder_id":"folder_id","urls":["url1","url2",...]}'
```

> 导入到根目录时 `folder_id` 留空字符串 `""`。建议写个小脚本批量读取 JSON 并循环调用 `import_urls`（每批 10 条、批间 `sleep 0.5`），避免手工拼 URL。

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

欢迎提交 Issue 和 Pull Request！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

提交前请确保：
- 不提交任何私人凭证（`weixin_credentials.py` 已被 `.gitignore` 排除）；
- 通过 CI 的敏感信息扫描（见 [CONTRIBUTING.md](CONTRIBUTING.md) 自检查骤）。

---

## License

[MIT License](LICENSE) © 2026 canyexuanfan

详见 [LICENSE](LICENSE) 文件。
