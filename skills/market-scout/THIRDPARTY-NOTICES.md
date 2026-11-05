# 第三方组件声明 (THIRD-PARTY NOTICES)

market-scout 技能使用了以下开源项目的部分内容，按其开源协议要求在此声明：

## getsentry/skills — skill-scanner

- 来源: https://github.com/getsentry/skills （skills/skill-scanner/scripts/scan_skill.py）
- 版权: Copyright (c) Sentry
- 协议: Apache License, Version 2.0（全文见本目录 APACHE-LICENSE-2.0.txt）
- 使用方式: market-scout 深检模块（`scripts/market_search.py`）中的以下模式定义清单
  派生自该脚本，并做了中文注释、部分增删改：
  - `PROMPT_INJECTION_PATTERNS`（提示注入检测模式，16 条）
  - `OBFUSCATION_PATTERNS`（零宽字符 / RTL 覆盖 / Unicode Tag 隐写检测）
  - `SECRET_PATTERNS`（硬编码密钥检测模式，13 条）
  - `DANGEROUS_SCRIPT_PATTERNS`（危险脚本行为模式：外传/凭证访问/危险执行/配置篡改，18 条）
  - `TRUSTED_DOMAINS`（可信域名白名单思路）
- 其余代码（检索/打分/深检框架/API 对接）为原创实现，与该脚本无关联。

依照 Apache-2.0 第 4 条要求：本文件与源码头部注释保留了上述声明；
对原始代码的修改已在文件内以中文注释标注。

## gitleaks/gitleaks — 密钥检测规则

- 来源: https://github.com/gitleaks/gitleaks （config/gitleaks.toml）
- 版权: Copyright (c) 2019 Zachary Rice
- 协议: MIT License
- 使用方式: `SECRET_PATTERNS` 中的 9 条规则移植自其规则库并转写为 Python 语法
  （阿里云 LTAI / JWT / GitLab PAT / Stripe / Telegram Bot / SendGrid / HuggingFace /
  Slack Webhook / 宽口径私钥块），源码内以中文注释标注来源。

## PyCQA/bandit — Python 危险调用检测

- 来源: https://github.com/PyCQA/bandit
- 版权: Copyright (c) 2014 Hewlett-Packard Development Company, L.P.
- 协议: Apache License, Version 2.0（全文见本目录 APACHE-LICENSE-2.0.txt）
- 使用方式: `DANGEROUS_SCRIPT_PATTERNS` 中 4 条检测思路参考其插件规则
  （pickle 反序列化 / yaml.load 无 SafeLoader / TLS 校验禁用 / tempfile.mktemp），
  正则为独立编写，源码内以中文注释标注。

## cisco-ai-defense/mcp-scanner — 检测思路参考

- 来源: https://github.com/cisco-ai-defense/mcp-scanner
- 协议: Apache License, Version 2.0（全文见本目录 APACHE-LICENSE-2.0.txt）
- 使用方式: 仅借鉴其"中文提示注入变体与工具投毒（跨工具劫持/延迟隐藏指令）"
  行为检测思路，模式正则为独立编写，未复制任何代码。

## ossf/scorecard — 评分方法论参考

- 来源: https://github.com/ossf/scorecard
- 协议: Apache License, Version 2.0（全文见本目录 APACHE-LICENSE-2.0.txt）
- 使用方式: 仅借鉴其 "Critical 检查命中即压制总分" 的熔断方法论
  （critical_hits > 0 时质量分封顶 0.3），未复制任何代码。

## 检索与质量评估方法论参考（仅思想，未复制代码）

- rank_bm25 (Apache-2.0, https://github.com/dorianbrown/rank_bm25) 与
  bm25s (MIT, https://github.com/xhluca/bm25s)：BM25 排序方法论参考。
  market-scout 的 `_bm25_score` 为独立编写的 binary-tf 变体，零第三方依赖。
- awesome-lint (MIT, https://github.com/sindresorhus/awesome-lint) 与
  anthropics/skills (https://github.com/anthropics/skills) 的 skill-creator 结构规范：
  "元数据完备性" 检查项的思路来源（必备 name/description，license/keywords 等为加成）。

—— market-scout by 十七°, 2026-11-05

## wb_auth.py（自包含凭证模块）

- 来源: 作者本人的 totorosir-workbuddy-checkin（v3.1.7）技能中「登录态 AtRestEncryption 读端解密」段落原样移植（同一作者授权收录）
- 用途: 在用户本机发现并解密其自己的 WorkBuddy 客户端登录态（零配置认证），只读不写
- 算法出处: 复刻 WorkBuddy 客户端 `packages/at-rest-crypto`（AES-256-GCM 信封读端），不含任何客户端专有代码分发
