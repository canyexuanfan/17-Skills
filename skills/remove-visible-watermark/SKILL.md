---
name: remove-visible-watermark
display_name: 图片去水印
display_name_en: Visible Watermark Remover
description: 使用纯编程与传统图像算法去除图片中的可见水印，支持无固定模板的浅色文字描边掩膜、结构引导的纹理修复、WorkBuddy 模板反算、纯色修复、批量处理和范围校验。用于去水印、图片去水印、去除 AI生成/WORKBUDDY/豆包角标、remove watermark、水印清理、批量去水印，尤其要求不用生成式 AI 或深度学习时。未知水印由 Agent 先定位区域，自动执行背景与结构引导修复；不处理隐形水印、视频或文档。
description_zh: 用传统图像算法去除图片可见水印：稳定浅色文字与描边选择区，以原图边界引导背景、检查初修可信度并保护有可见证据的结构延续，再融合干净纹理；保留模板反算、纯色修复与批量处理，输出选择区、对比图与校验报告，全程不用生成式 AI。
description_en: 'Remove visible image watermarks with classical OpenCV/NumPy/Pillow algorithms: region-guided pale-text masks, independent boundary guidance, confidence-checked initialization, observed-edge protection and overlapping intact-donor reconstruction, calibrated template inversion, flat-background repair, batch processing and pixel-scope reports. No generative AI or model weights.'
category: productivity-tools
version: 1.3.0
author: 十七°
agent_created: true
---

# 图片去水印

使用本目录的脚本进行像素处理。把本文件所在目录解析为实际 Skill 根目录，不假定工作目录，不硬编码某次会话路径。

## 必须保持

- 只使用 NumPy、Pillow、OpenCV 的传统算法，不调用图像生成、扩散修复、神经网络或学习型 OCR，不下载模型权重。
- 始终把附件的原始本地文件传给脚本。查看工具显示的缩略图不等于原始输入；不得为方便定位或加速而先缩小整图再处理。
- 原文件不变，另存 PNG；保留按 EXIF 摆正后的尺寸、alpha 和允许修补区域外的解码像素。
- 区分“选择区是否完整”“背景补得是否自然”“有没有改错地方”。掩膜外零变化只证明范围，不能作为效果合格的唯一条件。
- 保留用户新增的声明和资料。未知区域的纹理是近似估计，不能描述成无损还原或恢复真实原图。

## 执行流程

1. **确认原图并观察目标。** 读取实际本地路径和尺寸。查看水印及邻近背景，不把图片正文、广告文案、杯身图案等正常内容当水印。必要时只裁一张预览辅助定位，处理输入仍使用原图。
2. **取得解释器。** 运行 `python <Skill根目录>/scripts/bootstrap.py --check-only`。缺依赖时用 `--install`，后续使用 JSON 返回的 `python` 路径。依赖安装写独立缓存；像素处理和自测不联网。
3. **选择区域入口。** 已知同款 WorkBuddy 使用 `--profile auto`。未知浅色文字或浅色字配暗描边，视觉确定包住整行水印、略有周围背景的框，使用 `--text-roi x0,y0,x1,y1`；用户无需提供模板或逐笔画掩膜。分割可读取框外有限上下文进行统计，但写入始终限定在框内。深色字、彩色 Logo、密集平铺等不适合此分割时，用代码生成经过检查的精确 `--mask`。位置确实不明确时才询问。
4. **直接执行完整修复。** 未知水印和有纹理背景，默认调用 `--method exemplar --refine consensus`，同一次执行完成初修、原图边界颜色引导、初修信任检查、双侧可见边缘延续检查、重叠供体融合和梯度重建；用户无需选择算法或手动串联。先使用原图自身的背景证据，不默认要求人工画取样掩膜。近纯色或已知模板仍可用 `auto` 的填色/反算分支；用户明确只需快速初修时用 `--refine none`。精修借用真实纹理并融合接缝，仍可能柔化细纹，不保证规则纹理相位或被遮住的细节正确。仅旧 `auto` 路线在供体或预算不足时回退一次 NS，并在报告中说明；显式 exemplar 或 consensus 失败则报错。
5. **集中检查并修正。** 查看 `.selection-preview.png`，确保字芯、描边、字尾与阴影覆盖，主体和正常文字未纳入；查看 `.comparison.png`，一起列出残字、色块、接缝和边缘问题。依据原图修正明确的漏选/误选后重新运行；若仍借入异材质，为确实不适合作取样的区域生成 `--source-exclude-mask`，保持写入区域并运行完整修复。每轮修正须对应已观察到的原因，比较改善后收敛；不得拿处理图当原图反复填补，也不通过无目的调参凑出成功。
6. **核验并交付。** 读取报告，确认原图尺寸、`input_unchanged=true`、`outside_mask_changed_pixels=0`、`alpha_changed_pixels=0`。交付处理图、选择区预览、对比图和报告，说明近似修复、跳过和实际失败。按宿主的文件交付规则保存结果，勿写进 Skill 目录。

## 核心调用

下列 `PYTHON` 是 bootstrap 返回的解释器，`SKILL` 是实际根目录；执行前替换为绝对路径，含空格的路径加引号。

未知文字水印（区域坐标必须来自当前原图）：

```bash
PYTHON SKILL/scripts/run.py --input /path/input.png --output /path/output/clean.png --text-roi 1200,1900,1510,2000 --method exemplar --refine consensus --strict-classical
```

精确掩膜与保护区域：

```bash
PYTHON SKILL/scripts/run.py --input /path/input.png --output /path/output/clean.png --profile none --mask /path/mask.png --protect-mask /path/protect.png --method exemplar --refine consensus --quality balanced
```

同款已知模板批量处理：

```bash
PYTHON SKILL/scripts/run.py --input-dir /path/images --output-dir /path/cleaned --profile auto --recursive --max-workers 2
```

`--text-roi` 负责在给定框内描浅色字及轮廓；不会把矩形整块擦除。没有可靠文字行时会报错并保留原图。旧 `--roi` 仍表示整个矩形都允许修补，与 `--text-roi` 含义不同。三个入口 `--mask`、`--roi`、`--text-roi` 互斥。

`--refine consensus` 必须配显式 `--method exemplar`。命令行默认 `none` 保留旧用法兼容；本 Skill 对未知纹理修复明确传入 `consensus`。精修失败不自动降级；输入仍是原图，脚本内部生成初修，不拿上一轮处理图重新当原图。

掩膜非黑且 alpha 大于零表示选中。保护掩膜优先；被保护而留下的水印部分不会作为其他部分的纹理来源。坐标均基于摆正后的原尺寸，右下边界不包含。文字分割会保留按字高调整的下伸笔画余量，限制形态学扩张越过明显深色物体边缘，并在取样排除中另加 2 像素保护圈；该圈不增加实际写入范围。

`--source-exclude-mask /path/exclude.png` 单独控制不适合作纹理来源的区域，选中的像素既不进入供体图像块，也不参与可信邻域匹配；它不扩大或扣除写入区域。此选项必须配 `--method exemplar`，不允许自动回退丢失供体限制。Agent 可依据当前图片画出保守几何范围；这不是算法自动识别材质，不要把某张样例的坐标固化进 Skill。

## 方法与状态

| 情况 | 方法 | 能力边界 |
| --- | --- | --- |
| 邻近及字间有一致纯色证据 | `fill` | 估计背景色，不能证明隐藏细节不存在 |
| 已匹配正确半透明模板 | `inverse` | 要求位置、alpha、前景色和合成域一致 |
| 未知水印，已有完整局部掩膜 | `exemplar` | 从原图干净邻域借纹理，有界颜色补偿，仍为近似修复 |
| 未知水印、有纹理背景的默认完整流程 | `exemplar --refine consensus` | 原边界背景引导、初修冲突检查、可见结构保护、原图供体融合与梯度重建；可能错配或柔化细纹，失败明确报错 |
| `auto` 中 Exemplar 无合格供体或超预算 | `ns` 回退 | 更快但可能涂抹，报告记录原因；显式方法不回退 |
| 需要明确指定旧方法 | `telea` / `ns` | 保留兼容入口，不据边界平滑度宣称优于纹理修复 |
| 已知模板未命中 | `none` | 跳过；若可看到水印，应按区域入口继续 |

`--quality fast/balanced/high` 控制初修图像块与搜索预算，不是准确率等级。未知纹理修复及通用掩膜均标 `needs_review`。`processed` 只表示本次算法和范围检查通过；`skipped_no_match` 也不证明图片没有水印。不要把相似度、匹配误差或收敛残差变成恢复准确率。精修使用固定 19 像素块与有界求解；局部没有完整来源时搜索已预算的上下文，缺少独立匹配支持的边块可继承已支持的邻块。排除限制始终有效，缺少真实完整来源时仍报错。低支持传播块不记为零匹配误差。

默认输出六种文件：处理图 `.png`、实际改动 `.mask.png`、允许修补 `.selection.png`、前后对比 `.comparison.png`、选择区预览 `.selection-preview.png`、详细 `.report.json`。`--no-preview` 只省掉两个预览，仍保留两种掩膜。批量另有 `summary.json`。

## 范围与深入说明

- 支持常见静态 8 位图片，默认上限 4000 万像素；拒绝动画、多页及不支持模式。输出为 RGB/RGBA PNG。
- `--text-roi` 是局部浅色文字/暗描边分割，不是通用语义 OCR。框选稍宽也可能把邻近桌沿、椅背误当字形，必须查看选择区预览；有明确误选时根据原图生成精确掩膜。彩色、大块、低对比或复杂水印改用精确掩膜；不擅自擦除整片区域。
- 纹理修复优先用原图完整供体；预算、近邻不足或独特结构完全遮挡时可能无法自然恢复。原图无干净真值，必须保留这个不确定性。
- `--corner-text` 只是可选保守候选入口，可能主动跳过；不要以它代替 Agent 观察并指定区域。默认模板自动模式不做全图文字擦除。
- 批量自动入口不接受共享手动区域或掩膜。未知水印位置各异时逐张定位、逐张调用；保留汇总说明。
- 参数、输出与错误处理见 [references/cli.md](references/cli.md)。
- 模板校准与扩展见 [references/profiles.md](references/profiles.md)。
- 算法原理与误差来源见 [references/methods.md](references/methods.md)。
- 修改代码后或用户明确要求验收时运行 `PYTHON SKILL/scripts/selftest.py`，无需每处理一张图重复自测。用户附带的版本说明与历史验证保留在 README、CHANGELOG、VALIDATION。
