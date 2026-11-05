# 实测校验记录（2026-11-05，Windows 11 + Python 3.13）

以下均为真实执行的测试结果，未做实测的项目不在此列。

## 检索

| 查询 | 结果 |
|---|---|
| pdf转word | 官方《PDF 文档处理》（324 万使用）第一，契合 0.44；《Word 文档创作与美化》第二 |
| 提取视频字幕（同义改写） | 官方字幕提取类技能排头部（描述含 subtitle/caption，跨语言召回生效） |
| 表格数据清洗透视（改写） | 《Excel 表格处理与数据分析助手》第一 |
| 小红书文案 | 官方《小红书文案撰写》第一 |

## 安全深检

| 测试 | 结果 |
|---|---|
| 恶意样本 1（英文注入+隐写+OpenAI 密钥+数据外传+配置篡改） | 5 项 FAIL，质量分 0.33，触发禁推荐线 |
| 恶意样本 2（中文注入+阿里云 LTAI+JWT+pickle 反序列化） | 密钥/注入/危险 3 项 FAIL，质量分 0.3（critical 熔断封顶） |
| 正常中文文档误报测试（"安装后运行""忽略重复行"等常规措辞） | 零误报 |
| 官方技能深检回归（真实包） | 质量分 0.89，10 项检查正常 |
| SkillHub 技能深检回归（真实包） | 质量分 0.89，sha256 校验 + keen/sanbu 安全报告（benign）正常 |

## 快照构建（pull_snapshot.py）

| 项 | 结果 |
|---|---|
| 认证链路 | 登录态 Bearer 解密 OK，x-user-id 自动获取 |
| SkillHub 字段映射 | 实测 API 字段（downloads/stars/updated_at epoch ms/ownerName）映射正确，时间戳转 ISO 正常 |
| 官方接口兼容 | 技能列表（total 43,548）与专家列表（total 36,513）均 200 |

## 玫瑰风向图

- 图例与图形坐标校验：数据多边形最大 x≈499，图例起始 x=624，零重叠
- 4~5 候选叠加渲染正常（多边形/图例/总分）

## 名称占用自检（--check-name，v1.2.0 新增，2026-11-05 实测）

| 测试 | 结果 |
|---|---|
| `--check-name "market-scout"` | 官方市场（skill_2112682252112871424「上架前市场调研」v1.1.1）与 SkillHub（@indiv-kio831/market-scout v5.0.0）双双命中占用，退出码 2 ✅ |
| `--check-name "market-radar"` | SkillHub 已被占用（@clawhub_law52525 v1.0.0），退出码 2 ✅ |
| `--check-name "market-lens"` | 四源全部未占用（仅提示泛词相近名 market），退出码 0 ✅ |
| 数据缺失源 | 明确警告「该源未被本次自检覆盖」，不静默放行 ✅ |

## 开源协议合规核查（2026-11-05，GitHub API 逐仓库实查）

| 声明项目 | 声明协议 | GitHub 实查 | 核查结果 |
|---|---|---|---|
| getsentry/skills (1125★) | Apache-2.0 | Apache-2.0 ✓ | 一致，改动已标注、全文随包 |
| gitleaks (30692★) | MIT | MIT ✓ | 一致，MIT 全文+版权行已随包附上 |
| PyCQA/bandit (8372★) | Apache-2.0 | Apache-2.0 ✓ | 一致 |
| cisco-ai-defense/mcp-scanner (1107★) | Apache-2.0 | Apache-2.0 ✓ | 一致，仅思路未复制代码 |
| ossf/scorecard (5814★) | Apache-2.0 | Apache-2.0 ✓ | 一致 |
| rank_bm25 (1415★) | Apache-2.0 | Apache-2.0 ✓ | 一致 |
| bm25s (1822★) | MIT | MIT ✓ | 一致，MIT 全文已附 |
| awesome-lint (860★) | MIT | MIT ✓ | 一致，MIT 全文已附 |
| anthropics/skills (187579★) | （仅思路参考） | **无 LICENSE 文件** | 已在声明中如实标注无协议、未复制内容 |
| canyexuanfan/agent-checkin-automation（同作者自有，★1） | MIT | MIT ✓ | 同作者项目非第三方引用；解密段移植内嵌为 wb_auth.py，来源已在模块头声明 |
| canyexuanfan/17-Skills（同作者自有，★22） | MIT | MIT ✓ | 同作者 Skill 合集，声明文件引用 |

## 认证自包含（v1.2.0 合并仓库 1.1.0 重构）

| 测试 | 结果 |
|---|---|
| `import wb_auth; wb_auth.load_token_best()` 本机登录态解密 | 正常返回 Bearer（仓库 1.1.0 已实测，原样移植） |
| `market_search.py --check-name "market-lens"`（合并后回归） | 四源全绿，退出码 0 ✅ |
| 仓库版与本版 diff 复核 | 仅认证模块来源声明与名称字符串差异，无逻辑分叉 |

## 已知边界（未实测/不支持）

- 未在 macOS/Linux 验证（登录态解密路径为 Windows 专属；SkillHub 免登录功能跨平台应可用）
- 静态分析不能替代运行时沙箱；动态行为不在检测范围
- 深检缓存依赖磁盘空间（每包约几十 KB~几 MB）
