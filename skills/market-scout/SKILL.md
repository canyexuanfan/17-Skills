---
name: market-scout
display_name: 全市场技能侦察官
display_name_en: Market Scout
description: 全市场技能侦察官。输入一句话需求，自动在 WorkBuddy 官方运营市场（技能 43k/专家 32k/专家包 429）与 SkillHub 社区市场（217k）四大数据源中检索候选，BM25 排序 + 同义词扩展跨语言召回，六维打分（契合/质量/效果/热度/活跃/权威，--radar 生成玫瑰风向图），对候选做 10 项源码级深检（功能质量组/安全质量组，critical 熔断），对比推荐最优；用户确认后直接调用内置工具完成安装，一站式闭环。纯标准库零依赖，免配置（自动复用本机客户端登录态），检索/深检全程只读不影响市场数据。触发词：帮我找个技能/找专家/哪个技能最好/市场里有没有/推荐技能/对比技能/skill 选型/帮我装上。
description_zh: 一句话需求，全市场（官方运营市场 + SkillHub）检索候选，六维评分与玫瑰风向图对比，源码级深检防货不对板与恶意包，推荐最优技能并可在确认后一键安装。
description_en: "Search the whole WorkBuddy skill ecosystem (official market + SkillHub), rank candidates with BM25 and six-dimension scoring, deep-inspect source code for quality and safety, recommend the best skill, and install it on user confirmation."
category: productivity-tools
version: 1.1.0
author: 十七°
---

# market-scout —— 全市场技能侦察官

目标：用户只需说出需求，你负责在全市场找出最合适的技能/专家，并给出有依据的对比评估结论。

## 可用数据源（本地全量索引，脚本秒级扫描）

| 源 | 规模 | 特点 |
|---|---|---|
| 官方运营市场·技能 | 43,239 | 含 use_count/install_count 热度、分类、更新时间 |
| 官方运营市场·专家 | 32,790 | agent 型专家，含描述、来源权威度 |
| 官方专家包(内置) | 429 | 腾讯官方出品，免登录 COS 源 |
| SkillHub 社区 | 217,137 | 下载量/stars，社区生态 |

## 工作流程

### 第 0 步：可选，构建本地全量索引（一次性，几分钟）
脚本命令均在本技能根目录执行（`{SKILL_DIR}` 即本 SKILL.md 所在目录，运行时按实际安装位置解析）：
```bash
python "{SKILL_DIR}/scripts/pull_snapshot.py"          # 官方两源+专家包（推荐）
python "{SKILL_DIR}/scripts/pull_snapshot.py" all      # 含 SkillHub 全量（约 30-60 分钟）
```
- 官方源需登录态（零配置自动解密客户端登录态；或 config.json 配 cookie；或环境变量）
- 未建快照时直接用 `--live` 实时搜索也能完成整个流程，只是搜索范围限于关键词命中而非全量扫描

### 第 1 步：解析需求
从用户需求中提取：核心功能词（中文）+ 对应英文词 + 领域词。例："我要处理发票报销" → 查询词 "发票 报销 invoice"。
- 同义扩展：转换/转 = convert；水印 = watermark；爬取/抓取 = crawl/scrape
- 一次检索不够时，用 2~3 组不同关键词分别跑

### 第 2 步：离线全量检索（默认首选；未建快照则用 --live）
```bash
python "{SKILL_DIR}/scripts/market_search.py" "查询词" --top 8
```
- `--json` 输出结构化结果；`--sources official_skills,skillhub` 可指定源
- 返回四源 Top-N 及**六维分数（综合分 = 契合 0.35 + 质量 0.20 + 效果 0.10 + 热度 0.15 + 活跃 0.10 + 权威 0.10）**；质量分在检索层是"交付认真度"代理（描述充实度/命名/分类/版本），**功能是否真实实现以决赛圈源码深检为准**
- `--radar out.svg` 生成 top 候选六维玫瑰风向图（各源 top3 合并最多 6 个候选叠加，纯 SVG 零依赖），供对比参考
- 检索内核为 **BM25 排序**（IDF 加权 + 长度归一化，方法论参考 rank_bm25/bm25s，独立实现零依赖）：高权字段 title×0.5 / name×0.2 / desc×0.3，各字段按最大值归一后合成契合分——高频泛词（工具/文档）不再淹没特征词
- **同义词查询扩展**：内置中英同义表（~40 组），命中即附加英文/同义表达参与检索（权重 0.45），跨语言改写召回显著提升；扩展只增召回，BM25 的 IDF 会自动压低泛词

### 第 3 步：按需实时补搜
以下情况加 `--live`（实时调 API，数据更新到当天）：
- 离线结果契合度普遍偏低（< 0.3）
- 需求涉及最近几天刚出现的新事物
- 用户明确要最新数据
```bash
python ".../market_search.py" "查询词" --top 8 --live
```
- **关键词匹配特性**：官方 API 的 keyword 是**连续子串匹配**，多词长查询会漏（"Excel 数据清洗去重"→0，"数据清洗"→100）。live 前把查询拆成 1~2 个核心短词分别跑
- live 偶发网络抖动失败属正常（Clash 代理），重跑一次即可；失败不影响离线结果
- live 认证三级（自动降级）：① **零配置**——`scripts/wb_auth.py` 自包含模块自动发现并解密**用户本机** WorkBuddy 客户端登录态（Windows/macOS/Linux 标准路径 + `~/.workbuddy/auth` 兜底；accessToken 兼容明文 JWT 与 5.6.2+ AES-256-GCM 加密信封；**只读不写、绝不打印完整凭证**，本 Skill 不含任何硬编码凭证）→ Bearer 调门户 API（已实测技能/专家接口均接受，30 天有效）；② cookie 兜底（`config.json` 的 auth.cookie_file，浏览器 F12 → Network → 任意 /portal/ 请求 → Copy as cURL 提取，7 天过期）；③ 环境变量 `WORKBUDDY_BEARER_TOKEN` 显式指定。401 时提示：重新登录客户端后重试，或 F12 Copy as cURL 更新 cookie 文件

### 第 4 步：语义复评（必须做，不能只看分数）
对每源 Top 3~5 逐条读描述，进行 AI 判断：
- **剔除**：关键词擦边（名字沾边但功能不符）、明显劣质（描述空洞、纯营销）、与需求场景错位
- **升权**：描述与需求细节精确对应、覆盖需求全流程、有明确输出物定义

### 第 5 步：源码级深检（最终推荐前对头部候选执行）
检索分数只基于元数据（名称/描述/热度），**不等于看过代码**。对最终对比表中前 2~3 名，逐个运行源码深检：
```bash
# 官方市场技能（下载真实技能包 zip，解包分析 SKILL.md/脚本/目录结构/密钥泄露）
python ".../market_search.py" --inspect skill_2095395851789164544
# SkillHub 技能（免登录 API 直读源码：文件清单+逐文件内容+sha256+平台安全报告 keen/sanbu）
python ".../market_search.py" --inspect tencent-adm/tencent-docs
```
深检产出（10 项检查，官方 zip 与 SkillHub 源码直读两通道同一套口径）：SKILL.md 完整性、脚本工程化（真实代码行数）、references 目录、**无硬编码密钥**（22 类精确规则：AWS/GitHub/OpenAI/Anthropic/Slack/私钥 + 中国云（阿里 LTAI）/JWT/GitLab/Stripe/Telegram/SendGrid/HuggingFace 等）、**无提示注入**（22 条模式：英文指令覆盖/越狱/角色重置/系统提示窃取 + **中文变体**（忽略指令/中文越狱/角色重置/提示词窃取）+ **工具投毒**（跨工具劫持/延迟隐藏指令））、**无字符混淆隐写**（零宽字符/RTL 覆盖/Unicode Tag 隐写——对中文技能尤其重要）、**无危险脚本行为**（外传/凭证访问/危险执行/Agent 配置篡改 + bandit 式 Python 危险调用（pickle 反序列化/yaml.load 无 SafeLoader/TLS 校验禁用），22 条模式）、TODO/FIXME 完成度、**描述一致性（货不对板检查**：包内多语言描述 vs 市场描述重叠度，<30% 警告）、**元数据完备性**（frontmatter 必备 name+description，license/keywords/author/version 为加成，思路参考 awesome-lint 与 anthropics/skills 结构规范）→ 源码质量分（10 项通过率，**critical 熔断**：密钥/注入/危险行为任一 critical 级命中即封顶 0.3，参考 OSSF Scorecard 方法论）。安全规则派生/参考 Sentry skill-scanner、gitleaks、PyCQA/bandit（协议与声明见 THIRDPARTY-NOTICES.md）。SkillHub 渠道额外输出平台侧安全报告（keen=科恩实验室 tix.qq.com / sanbu=云鼎实验室，benign=无风险）与 sha256 完整性校验。外部 URL 检查自动过滤可信域名白名单（github/pypi/npmjs 等）。
支持**批量**（一次扫描索引+并发下载缓存，不再每个 id 重扫）：
```bash
python ".../market_search.py" --inspect skill_a,skill_b,skill_c
```
zip 缓存在 `market-scout/cache/`，二次深检秒出；数据新鲜度自查：`python ".../market_search.py" --stats`（快照超 7 天会警告）。
**读透源码再评价**：深检报告给的是结构化信号，你必须基于其中的文件清单和 SKILL.md 长度判断「这个技能真的能做用户要的事吗」，不能只把分数搬进对比表。

### 第 6 步：输出对比结论
必须给出结构化对比，格式：

1. **对比表**（精选 3~5 个真正入围者）：

| 候选 | 来源 | 契合 | 质量 | 效果 | 热度 | 活跃 | 源码质量 | 一句话点评 |
|---|---|---:|---:|---:|---:|---:|---|---|

2. **最终推荐**：明确说"推荐 X"，说明理由（契合点 + 功能质量证据 + 效果/活跃度）
2.5 **玫瑰风向图**（候选间差异值得直观呈现时）：用 `--radar out.svg` 生成的 SVG 让用户比较六维形状差异
3. **备选**：1 个替代方案及适用条件差异
4. **结论诚实度**：若全市场无真正满足需求的选择，直接说"没有现成的，建议自建"，并给出最接近的参考实现

### 第 7 步：安装交付（用户确认后执行，形成闭环）
推荐得到用户认可、用户明确要装（"就装这个"/"帮我装上"）时，直接用宿主内置工具安装，不要让用户自己去市场搜：
1. 调用 `search_and_install_skills`，`{"action": "search", "keywords": ["<技能关键词>"]}` 重新搜索（**必须先搜后装**：name 需逐字复制最近一次搜索结果，不能凭深检时的记忆拼）
2. 在搜索结果中定位推荐的那个技能（按 display_name/描述与推荐结论对上），然后 `{"action": "install", "skill_names": ["<name>"]}`
3. 逐项报告结果：`installed` / `already_installed` / `refused`——不宣称失败项已安装；此工具不升级已装技能
4. 搜索结果里找不到目标（如 SkillHub 技能未收录进该工具）时，兜底引导用户：客户端「技能市场」搜索技能名 → 安装，并给出技能准确名称
5. 安装成功后可跟进一句：需要的话用新技能实际跑一遍验证效果（功能是否达标以实测为准，呼应质量优先原则）
注：安装动作由用户确认触发，走 WorkBuddy 正常安装流程、产生正常安装计数——这与本 skill 检索/深检的"只读"不冲突（推荐环节依旧零写入）。

## 评估维度定义

**质量优先原则：先看能不能达到效果，再看其他。**

- **功能质量（第一裁决，inspect 决赛圈）**：源码深检中的功能组——SKILL.md 完整性（工作流是否写清）/脚本工程化（**真实实现代码 vs 纯 prompt 空壳**，看脚本数与真实行数）/文档参考齐备/内容完成度/描述一致性（说的功能包里真有没有）/元数据完备。推荐结论必须以功能组结果为主要依据
- **交付质量代理（quality，检索层全量可算）**：描述充实度 0.55 + 命名规范 0.15 + 分类明确 0.15 + 版本维护 0.15。是代理不是质量本身——功能是否真实实现只能靠源码深检裁决
- **效果验证（adoption，仅官方技能源可算）**：use_count/install_count 使用转化率——装了的人是否真的在用。⚠ 仅是参考信号：低频专业功能使用天然少，不可单看，与其他维共同加权
- **需求契合（relevance）**：BM25 排序的名称+描述语义匹配，AI 复评后以此与质量共同定夺
- **热度（popularity）**：官方 use_count/install_count；SkillHub downloads/stars。仅作参考证据，**不与质量混同**
- **维护活跃（freshness）**：updated_at 距今，120 天半衰期
- **权威性（authority）**：官方 builtin/official_sync > featured > 普通；SkillHub verified > 普通
- **安全质量（inspect 决赛圈）**：无密钥/无提示注入/无混淆隐写/无危险行为 4 项 + critical 熔断（critical 命中即封顶 0.3）。源码质量分 < 0.6 的候选不能当第一推荐，哪怕检索分最高

检索结果末尾自带「⚡ 跨源速览」：按质量×契合合成排序四源 Top 候选；热度分各源内归一化，不可跨源横比，跨源对比时以此速览榜为起点再做 AI 复评。

## 注意事项

- 数据快照日 2026-11-05；官方市场每日上新约几十条，重要决策前用 `--live` 复核头部候选
- **全程只读设计**：检索走查询 API、深检走 CDN 签名直链下载 zip 到本地缓存分析，均不经过任何安装/计数接口，不影响市场上任何技能的 install_count 等数据；只有用户自己在客户端里手动安装才产生真实安装数据
- 专家（expert）解决"怎么想"，技能（skill）解决"怎么做"——需求偏咨询判断推荐专家，偏产出交付推荐技能，可同时推荐
- 连接器暂无全量索引（官方无 REST 接口），涉及连接器需求时告知用户去客户端「连接器」页签查看
- 数据文件缺失时：运行 `python "<技能目录>/scripts/pull_snapshot.py"` 重建本地索引（认证同 live 三级自动降级；SkillHub 源免登录但全量较久，未拉取时该源自动跳过、不影响其余源）
