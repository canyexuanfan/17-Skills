---
name: wechat-to-ima
description: |
  将微信公众号的全部历史文章抓取并批量导入到 IMA 知识库。
  当用户说"把某公众号导入知识库"、"抓取公众号文章到IMA"、"公众号文章备份到知识库"、
  "把公众号URL导入IMA"、"微信公众号文章入库"、"把公众号文章存到知识库"时触发。
  本技能 = 微信文章抓取(fetch_articles.py，仅标准库) + ima-skill 的 ima_api.cjs 导入。
---

# wechat-to-ima — 微信公众号文章 → IMA 知识库

## 架构

```
Agent (Claude)
  ├─ fetch_articles.py  → 提取 __biz → 抓全部文章(每批3秒防频控) → 去重 → JSON
  └─ ima_api.cjs (来自 ima-skill) → 批量 import_urls 到知识库
```

依赖：

- 本技能目录的 `fetch_articles.py`（仅用标准库 `urllib`，**无需 pip install**）
- `ima-skill` 的 `ima_api.cjs`：
  `../ima-skill/ima_api.cjs`
  （IMA 凭证由 ima-skill 管理，见其 SKILL.md 的 Credential Check）

---

## 前置环境（首次运行前必须确认）

1. **微信凭证** `weixin_credentials.py`（与本 SKILL.md 同目录）：
   - `token` 留空，请在 `weixin_credentials.py` 中填写你自己的 token
   - `cookie` **必须补充**：浏览器打开 `https://mp.weixin.qq.com/` 扫码登录 →
     F12 → Application → Cookies → `mp.weixin.qq.com` → 右键 Copy All → 粘贴进 `cookie = "..."`
   - 凭证过期（API 返回 `200003 invalid session`）：重新扫码，更新 token + cookie
2. **IMA 凭证**：确认 ima-skill 凭证已配置（否则 `import_urls` 会失败）
3. **Python 3**：`python fetch_articles.py --help` 能正常打印说明

---

## 完整工作流（Agent 必须严格遵守）

### Step 1：抓取全部文章

```bash
python "<技能目录>/fetch_articles.py" ^
  --url "用户给的公众号文章URL" ^
  --name "公众号名称"
```

- 自动从 URL 源码提取 `__biz` → 循环抓取（每批 3 秒防频控）→ 去重 →
  保存为 `<name>_全部文章_YYYYMMDD.json`（含 title / link / date）
- ⚠️ 触发频控（`ret=200013`）会**立即停止**并提示，告知用户等 **30–60 分钟**
- 空列表 = 抓完；输出 JSON 路径在日志末尾打印

### Step 2：查找 IMA 知识库位置

```bash
# 搜索知识库，拿到 knowledge_base_id
node "../ima-skill/ima_api.cjs" ^
  'openapi/wiki/v1/search_knowledge_base' '{"query":"知识库名称","cursor":"","limit":10}'

# 浏览知识库内容，找目标文件夹（media_type=99 是文件夹），拿到 folder_id
node "../ima-skill/ima_api.cjs" ^
  'openapi/wiki/v1/get_knowledge_list' '{"knowledge_base_id":"kb_id","cursor":"","limit":50}'
```

> 凭证按 ima-skill 规范传递（环境变量或配置文件，详见 ima-skill SKILL.md 的 Credential Check）。

### Step 3：批量导入 URL（每批 10 个，间隔 0.5 秒）

读取 Step 1 的 JSON，提取所有 `link`，每 10 个一批调用 `import_urls`：

```bash
node "../ima-skill/ima_api.cjs" ^
  'openapi/wiki/v1/import_urls' ^
  '{"knowledge_base_id":"kb_id","folder_id":"folder_id","urls":["url1","url2",...]}'
```

- 导入到根目录：`folder_id` 留空字符串 `""`
- 每批之间 `sleep 0.5`
- **Agent 应写一个小脚本**（Python/Node）批量读取 JSON 并循环调用 `import_urls`，避免手动拼 URL 出错

### Step 4：汇报

```
✅ 公众号「XXX」共 XX 篇 | 📅 跨度：开始~结束 | 📂 已导入：知识库 > 文件夹
```

---

## 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 提示缺少 token / cookie | 凭证未填 | 编辑 `weixin_credentials.py` 补充 |
| 抓取返回 401 / 空列表 | cookie 失效 | 重新扫码，更新 cookie |
| `200003 invalid session` | 微信 token 过期 | 重新扫码，更新 token + cookie |
| `200013` 频控 | 抓太快 | 停止，等 30–60 分钟再跑（脚本已自动停） |
| `import_urls` 报错 | IMA 凭证未配置 | 按 ima-skill 配置凭证后重试 |
