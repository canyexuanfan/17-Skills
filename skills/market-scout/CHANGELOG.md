# 更新日志

## 1.1.0（2026-11-05）

认证自包含重构（为开源发布准备）。

- 新增 `scripts/wb_auth.py`：自包含的本机 WorkBuddy 凭证发现与解密模块（跨平台路径探测 + 5.6.2+ AES-256-GCM 加密信封兼容 + 密钥多级定位），由作者本人的 totorosir-workbuddy-checkin（v3.1.7）登录态解密段原样移植，不再依赖本机已安装的其他技能
- `_load_bearer()` 重构为引用 wb_auth；cookie 兜底与环境变量 `WORKBUDDY_BEARER_TOKEN` 降级路径不变
- 安全承诺固化：模块只读不写、绝不打印完整凭证（仅 `mask_token()` 掩码）；全包不含任何硬编码凭证
- THIRDPARTY-NOTICES.md 补 wb_auth 来源声明；SKILL.md / README / config.json 认证说明同步
- CI 新增硬编码凭证扫描（eyJ/Bearer/AKID/cookie/uid 五类模式）

## 1.0.0（2026-11-05）

首发版本。

- 四源全量检索：官方运营市场（技能/专家/专家包）+ SkillHub 社区市场
- BM25 排序（IDF 加权 + 长度归一化）+ 中英同义词查询扩展（~40 组）
- 六维打分：契合 0.35 + 质量 0.20 + 效果 0.10 + 热度 0.15 + 活跃 0.10 + 权威 0.10
- `--radar` 六维玫瑰风向图（纯 SVG 零依赖）
- 10 项源码级深检（官方 zip 与 SkillHub 源码直读双通道同口径），功能质量组/安全质量组分组呈现
- 安全规则：密钥 22 类（含中国云 LTAI/JWT 等）、提示注入 22 条（含中文变体与工具投毒）、字符混淆隐写 3 类、危险脚本行为 22 条（含 bandit 式 Python 危险调用），critical 熔断封顶 0.3
- SkillHub 深检附平台侧安全报告（keen/sanbu）与 sha256 完整性校验
- 认证三级自动降级：客户端登录态零配置解密（自包含 wb_auth 模块，兼容 5.6.2+ 加密信封）→ cookie 兜底 → 环境变量
- `scripts/pull_snapshot.py` 一键构建本地索引快照（官方源几分钟，SkillHub 全量免登录）
- 检索/深检全程只读；安装由用户确认后经 WorkBuddy 内置工具执行
