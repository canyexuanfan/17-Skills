# image-to-prompt｜图片高保真反推 Skill

版本：**1.2.0**。更新日期：2026-09-28。

上传图片并执行 Skill，得到一个完整的自然语言文生图提示词。默认中文，图内原文不翻译。无需填写风格、主体或构图表，不自动出图，不要求用户把分析与负面提示词手工拼起来。

保留既有的**版面结构、几何位置、对象连续性、局部遮挡、文字与容器归属**流程。本版进一步把**字形与字面几何、行间空隙和留白、连续背景轮廓与颜色路径、定量和定性的相容性**纳入必查项。

每次按图片的主导要素选择相应分支；不会把所有图片套成分区海报，不要求所有渐变都有聚点，也不要求所有字体都超粗。内容少的图片仍可能需要精细的几何和形态描述。

## 安装或升级

已有旧版时，先在 Skill 加载目录之外保留备份，再用本包中的整个 `image-to-prompt` 目录替换实际加载的旧目录。不要只换主文件而混用旧参考规则。仅下载 ZIP 不表示已经安装，本次交付不会自动修改全局目录。

标准 Agent Skills 目录以 `SKILL.md` 为入口，`references` 和 `scripts` 为辅助资源；主文件名称与元数据规则按规范整理。[1]

| 宿主 | 项目内位置 | 调用方式 |
| --- | --- | --- |
| Codex | `.agents/skills/image-to-prompt/SKILL.md` | 在技能选择入口选择，或明确要求执行 `image-to-prompt` |
| Claude Code | `.claude/skills/image-to-prompt/SKILL.md` | `/image-to-prompt` |

目录位置与调用方式依据官方文档。[2][3] 其他宿主按其实际加载机制使用整个目录。宿主必须能够真实查看图片；规则文件不会给纯文本模型增加视觉能力。没有逐个平台做安装实测。

通用调用：

```text
执行 image-to-prompt，反推这张图片。
```

运行时无须上传生成结果或旧提示词。要求修订时才补充对照资料，并明确哪张是原图。明确要求英文时，描述可以改英文，图中文字仍按原样保留。

## 本版变化

| 机制 | 实际约束 |
| --- | --- |
| 文字作为主视觉形状 | 不止转录文案，核对骨架、字腔、笔画、字面尺寸、字色和留白 |
| 排版几何闭合 | 逐行可见框、整体框、行间空隙和边距彼此相容，不混用字号与字面 |
| 连续背景形态 | 记录亮暗包络、路径、宽窄、边缘进出位置和过渡带，不止列色名 |
| 交会与光源分离 | 不把宽域渐变或沿边亮区无依据改成聚点和物理射线 |
| 标识路径优先 | 先描述开闭、交叉、线宽与负空间，不用熟悉物体类比替换 |
| 单一描述基准 | 全文同一属性一致，数字与“大/紧/粗”等词相容，末尾不另起一套参数 |
| 分开记录场景、区域与层级 | 信息底面、框、留白与覆盖层不会因不是实景而被跳过 |
| 全图—边界—局部—全图复查 | 既看整体版式，也检查跨边界附近的连续轮廓 |
| 几何锚点 | 关键边界、主体中心与体量、动作节点、文字容器有统一坐标 |
| 局部遮挡 | 同一对象不同部分可以处于不同前后关系，不粗暴切断或复制 |
| 文字父子组件 | 文字、标识、子标签绑定正确底形和位置，不随意移到空角 |
| 反事实消歧 | 若换位、镜像、移走交点或合并区域仍符合文字，必须补约束 |
| 逐句证据回查 | 去掉旧提示词和生成结果带来的虚构道具、服饰和强化光效 |
| 样本隔离 | 分发包不存保留测试图、文案、答案、坐标或换名同构案例 |

名称、使用方式和单一代码块输出保持不变。核心已包含必要的结构检查与资源缺失时的替代流程；扩展参考文件用于更细致执行。

## 文件结构

```text
image-to-prompt/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── VALIDATION.md
├── requirements-optional.txt
├── references/
│   ├── typography-and-spacing.md
│   ├── continuous-fields.md
│   ├── constraint-consistency.md
│   ├── structure-and-occlusion.md
│   ├── visual-checklist.md
│   ├── output-contract.md
│   ├── quality-gates.md
│   └── comparison-protocol.md
├── scripts/
│   ├── image_probe.py
│   ├── layout_probe.py
│   └── typography_probe.py
└── tests/
    ├── test_image_probe.py
    ├── test_layout_probe.py
    ├── test_typography_probe.py
    ├── test_skill_contract.py
    └── acceptance-cases.md
```

所有文本为 UTF-8，归档路径使用 ASCII 名称。未打包图片、字体、私有凭证、模型或原图答案。

## 可选测量工具

核心反推不需要 Python、生图 API 或额外安装。下面的脚本仅在本地环境已有 Python 3.10+ 与 Pillow 时辅助观察，依赖声明在 `requirements-optional.txt`。缺依赖时跳过，不能擅自安装全局软件。

### image_probe.py

只读文件尺寸、方向、目标裁切比例、透明度及可选近似色板。无 OCR、自动分割、内容识别或联网，不修改源文件，不展示 GPS 等元数据。默认最多处理四千万像素；动图只读首帧。色板是近似取样，不做 ICC 色彩管理，也不自动判定某个颜色属于哪个对象。

在 Skill 根目录运行：

```bash
python scripts/image_probe.py "input.png"
python scripts/image_probe.py "input.png" --palette 6
```

### layout_probe.py

把**已经由使用者或 Agent 根据图像确认的**区域框与关键点换算成目标内的百分比、中心、体量。它不是自动布局识别器；计算正确不能证明输入的标注正确。不会要求用户手动填表才能执行 Skill，正常由具备工具的 Agent 自行按需使用。

语法示例，数值仅演示参数格式，不是任何真实输入的答案：

```bash
python scripts/layout_probe.py "input.png" --box region 40 30 220 160 --point anchor 120 90
```

图片须足够容纳给出的框。`--box` 和 `--point` 都可重复，标签需互不相同。框按左、上、右、下，点按横、纵。框可重叠、嵌套，面积之和不必为整幅。

可用 `--crop LEFT TOP RIGHT BOTTOM` 指定先前已经确认的目标区域。裁切框使用 EXIF 方向校正后的整图像素；**区域框和关键点使用裁切后目标本地坐标**，不是整图坐标。输出会标明这一坐标系，避免混用。点可以落在目标右/下边界，框必须非空且位于目标内。

脚本拒绝越界、无穷大、NaN、重复标签和缺少标注的输入。输出 JSON 仅供测量参考，最终仍需写成自然语言；目测标注在提示词里必须使用“约”，不能因算术输出有小数就冒充精密识别。

### typography_probe.py

只根据明确提供的普通横排文字可见字面框计算整体包络、逐行宽高、相邻行的字面空隙、相对行高的空隙比例、以及文本整体距目标或指定容器的边距。继承尺寸读取、方向校正与可选裁切坐标规则。

```bash
python scripts/typography_probe.py "input.png" --line row-a 30 40 230 90 --line row-b 30 120 230 175
```

上述数值只是普通算术语法，不是图片排版模板；源图须容纳框。`--line` 可以重复，按自上而下的顺序提供。`--container LEFT TOP RIGHT BOTTOM` 指定已确认容器，可选；默认用整个目标画布。`--crop` 使用方向校正后的整图坐标，字面框与容器使用裁切后目标本地坐标。

脚本不运行 OCR，不判断字形、字重、基线、CSS 字号、真实字体或提示词含义，不自动分类行距紧密程度。它不适用于竖排、弯曲、旋转或复杂透视文字，也不要求用户手动标注才能正常执行 Skill；缺工具时由 Agent 用可见近似值复查。

## 复测方式与边界

用新版做一次反推测试时，在新的会话只提供 Skill 与目标图片，不提供旧提示词、旧生成图或调试分析。之后把完整提示词单独送到另一个不含参考图的生成上下文。工具若自动继承图片，不能把该过程称为纯文字测试。

同一张开发时看过的图片可作为回归测试，但不是严格未见测试。检验泛化还需新的主题和构图，并包含没有分区、没有越界、没有文本底板的图片，防止过度套用规则。具体计划见 `tests/acceptance-cases.md`。

这是以可见信息为依据的高保真反推流程，不是能唯一确定每个像素的编码器。不会承诺自然语言必然复刻所有细节，也不会宣称已恢复原始种子或真实摄影参数。无法辨认的重要文字、边界和功能图码会说明限制。

## 验证

```bash
python -W error::DeprecationWarning -m unittest discover -s tests -p "test_*.py" -v
```

自动测试只检查脚本算术、文件行为和静态契约，不验证多模态识别或生成质量。真实执行结果、环境与未验证项见 `VALIDATION.md`。视觉验收计划不能当作已通过报告。

## 格式与宿主文档来源

以下链接沿用上一版列出的文件规范和宿主加载文档，本轮未重新核验在线文档或进行宿主安装。视觉拆解、自检和修订规则是本包实现，不声称来自这些规范。

```text
[1] Agent Skills — Specification
https://agentskills.io/specification

[2] OpenAI — Build skills / Where Codex loads local skills
https://developers.openai.com/codex/skills/
（上一版记录的跳转目标：https://learn.chatgpt.com/docs/build-skills）

[3] Claude Code — Extend Claude with skills
https://code.claude.com/docs/en/skills
```
