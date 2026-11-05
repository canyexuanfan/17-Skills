# market-lens —— 全市场技能侦察官

> v1.2.0（2026-11-05）｜一句话需求，全市场找技能：**29 万+ 候选秒级检索 → 六维量化打分 + 玫瑰风向图 → 10 项源码级深检 → 给出有依据的最优推荐 → 用户确认后一键安装**。检索/深检全程只读，不影响市场上任何技能的统计数据。

## 它解决什么问题

WorkBuddy 生态里技能、专家、连接器分散在两个市场、四个数据源，累计 29 万+ 条。靠 UI 搜索只能看名字和热度，看不出：

- 措辞不同的同能力技能（跨语言改写漏召回）
- 描述与实际能力不符的"货不对板"包
- 带提示注入、硬编码密钥、隐写、危险脚本的恶意/低质包

market-lens 把四个源拉成本地全量索引，用 BM25 检索 + 源码安全深检回答"哪个技能最好"。

## 能力总览

| 能力 | 说明 |
|---|---|
| 全量检索 | 官方技能 43,239 / 官方专家 32,790 / 官方专家包 429 / SkillHub 社区 217,137 |
| BM25 排序 | IDF 加权 + 长度归一化，title/name/desc 分字段加权合成，泛词不再淹没特征词 |
| 同义词扩展 | ~40 组中英同义表，中文需求可召回英文描述技能（权重 0.45） |
| 六维打分 | 契合 0.35 + 质量 0.20 + 效果 0.10 + 热度 0.15 + 活跃 0.10 + 权威 0.10 |
| 玫瑰风向图 | `--radar out.svg` 生成 top 候选六维叠加雷达图（纯 SVG 零依赖） |
| 实时补搜 | `--live` 直调官方/SkillHub 在线 API，数据更新到当天 |
| 源码深检 | 官方 zip 下载分析 + SkillHub API 直读源码，同一套 10 项检查口径 |
| 安装闭环 | 推荐经用户确认后，直接调用 WorkBuddy 内置安装工具装上（先搜后装、逐项报告结果） |
| 零依赖 | 纯 Python 标准库，装上即用，无任何 pip 包 |

## 快速开始

```bash
# 1) 检索（默认离线全量，秒级）+ 六维玫瑰风向图
python scripts/market_search.py "pdf转word" --top 8 --radar radar.svg

# 2) 实时补搜（需要网络与登录态）
python scripts/market_search.py "pdf转word" --live --top 5

# 3) 源码深检（官方技能传 skill_id；SkillHub 传 owner/slug；可逗号分隔批量）
python scripts/market_search.py --inspect skill_2095322904487550976
python scripts/market_search.py --inspect user_5fb53b05/evidence-based-teaching-optimizer

# 4) 数据新鲜度自检
python scripts/market_search.py --stats
```

正常输出为 Markdown 报告（适合 AI 直接阅读后做二次评估）；`--json` 输出结构化结果。

## 10 项源码深检（决定性一票）

官方 zip 与 SkillHub 源码直读两通道同一套口径，报告按两组呈现：

**功能质量组（能不能达到效果）**：SKILL.md 完整性 / 脚本工程化（真实实现代码 vs 纯 prompt 空壳）/ 文档参考齐备 / TODO 完成度 / 描述一致性（货不对板）/ 元数据完备性

**安全质量组（有没有恶意风险）**：无硬编码密钥（22 类规则）/ 无提示注入（22 条模式含中文变体与工具投毒）/ 无字符混淆隐写 / 无危险脚本行为（含 bandit 式 Python 危险调用）

**critical 熔断**：安全组任一 critical 级命中，质量分直接封顶 0.3（OSSF Scorecard 方法论），杜绝"多数项通过"把高危包稀释成及格分。

**推荐规则**：源码质量分 < 0.6 的候选不能当第一推荐，哪怕检索分最高。

## 认证（三级自动降级，免配置）

1. **零配置**：自动解密本机 WorkBuddy 客户端登录态（`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`），Bearer 令牌直接调门户 API，只读不写
2. **Cookie 兜底**：`config.json` 的 `auth.cookie_file`（浏览器 F12 → Copy as cURL 提取）
3. **环境变量**：`WORKBUDDY_BEARER_TOKEN` 显式指定

SkillHub 全部 API 免登录（列表/文件清单/单文件源码/版本安全报告）。

## 数据源维护

索引快照存于技能根目录 `data/`（`config.json` 各源 `path` 已配好相对路径，可不改）。首次使用或数据过期后重建：

```bash
python scripts/pull_snapshot.py          # 官方两源+专家包（几分钟，推荐先跑）
python scripts/pull_snapshot.py all      # 含 SkillHub 全量（约 30-60 分钟）
```

官方源需登录态（零配置自动解密客户端登录态；或 config.json 配 cookie_file；或环境变量 WORKBUDDY_BEARER_TOKEN）；SkillHub 与专家包全程免登录。

`--stats` 可查看各源快照天数与深检缓存状态（>7 天给出重建警告）。

## 目录结构

```
market-lens/
├── SKILL.md                  # AI 工作流说明（触发后按此执行）
├── README.md                 # 本文件
├── CHANGELOG.md              # 版本记录
├── VALIDATION.md             # 实测校验记录
├── config.json               # 数据源路径 / 打分权重 / 认证
├── scripts/
│   ├── market_search.py      # 核心脚本（检索/实时/深检/统计/玫瑰图）
│   └── pull_snapshot.py      # 本地索引快照构建（可选，首次使用建议执行）
├── data/                     # 快照数据目录（pull_snapshot.py 生成，不入包）
├── cache/                    # 深检下载的技能包缓存（运行时生成，坏包自愈重下）
├── THIRDPARTY-NOTICES.md     # 开源引用声明（协议合规）
└── APACHE-LICENSE-2.0.txt    # Apache-2.0 全文（被引用项目要求附随）
```

## 开源致谢与协议

检索与深检的设计参考了以下项目，声明详见 [THIRDPARTY-NOTICES.md](THIRDPARTY-NOTICES.md)：

| 项目 | star | 协议 | 参考方式 |
|---|---:|---|---|
| [getsentry/skills](https://github.com/getsentry/skills) (skill-scanner) | 1,125 | Apache-2.0 | 安全扫描模式清单（代码级移植） |
| [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | 30,682 | MIT | 密钥检测规则（代码级移植） |
| [PyCQA/bandit](https://github.com/PyCQA/bandit) | 8,371 | Apache-2.0 | Python 危险调用检测（思路） |
| [cisco-ai-defense/mcp-scanner](https://github.com/cisco-ai-defense/mcp-scanner) | 1,106 | Apache-2.0 | 中文注入/工具投毒检测（思路） |
| [ossf/scorecard](https://github.com/ossf/scorecard) | 5,814 | Apache-2.0 | critical 熔断评分方法论（思路） |
| [dorianbrown/rank_bm25](https://github.com/dorianbrown/rank_bm25) / [xhluca/bm25s](https://github.com/xhluca/bm25s) | 1,415 / 1,822 | Apache-2.0 / MIT | BM25 排序方法论（独立实现） |
| [sindresorhus/awesome-lint](https://github.com/sindresorhus/awesome-lint) / [anthropics/skills](https://github.com/anthropics/skills) | 860 / 187,469 | MIT / 无 | 元数据完备性检查思路 |

*star 数为 2026-11-05 实测值。*

## 已知边界

- **只读与安装的边界**：本 skill 的检索与深检零写入——查询 API + CDN 签名直链下载 zip 到本地缓存，不产生 install_count 等数据变化。安装环节由用户确认后经 WorkBuddy 内置安装工具执行，走正常安装流程、正常计数（用户真实安装，非抓取副作用）
- 离线检索基于快照，新上架技能需 `--live` 或重建索引
- 服务化技能（无 zip/源码文件）只能做元数据深检
- 深检为静态分析，不能替代运行时沙箱（动态行为不在此能力范围）
- 官方市场认证依赖本机客户端登录态，Linux/Mac 未适配（Windows 优先）
