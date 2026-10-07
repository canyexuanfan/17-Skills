# remove-visible-watermark｜图片去水印（纯算法）

版本：**1.3.0**。更新日期：2026-10-07。

用**纯传统图像算法**（OpenCV / NumPy / Pillow）处理图片中的可见水印。Agent 负责查看图片、定位水印和编排；像素修复由本地代码完成，不调用生成式图像模型、不下载神经网络权重，图像处理脚本全程不联网。

**1.3.0 继续改进选区稳定性和纹理修复流程**：文字分割用周边只读上下文、字行几何和细桥分离，降低框选边界变化对掩膜的影响；弱字芯附近用有依据的短暗轮廓延伸补齐描边。纹理修复加入原图边界背景引导、初修一致性检查、双侧可见线支撑和加权重叠融合。Skill 处理未知水印或有纹理背景时直接调用 `--method exemplar --refine consensus`，同一次执行完成初修与精修，用户无需选择或串联算法。CLI 仍以 `--refine none` 为兼容默认值，效果始终需要检查。按原始像素分辨率处理局部区域，**绝不先缩小原图再放大输出**。预览图的展示缩放不影响处理图。

核心能力：

- **文字区域细化（`--text-roi`）**：在指定区域内提取浅色笔画、暗描边及边缘，不需要固定字形模板。允许读取框外最多 32 像素上下文进行统计，写入仍限定在框内，不直接擦除整个矩形。初始尺度可按当前字行高度重估一次；先分离连接字形与场景高光的细桥，再有限恢复附近真实笔画。
- **同图纹理修复（`exemplar`）**：从原图无水印区域搜索完整补丁，依据可信邻域匹配并修补掩膜内像素。适合有相似纹理可借用的木纹、织物等背景，结果标为需复核。
- **结构引导的重叠供体融合（`--refine consensus`）**：从原图固定边界建立低频背景引导，检查初修与邻近原图证据的一致性；遮挡两端的干净原图均支持同向线条时，保留这部分初修结构作为弱匹配支撑。用干净 19×19 完整供体的颜色和内部梯度做加权重叠融合，再以 screened Poisson 重建原允许掩膜。供体全部来自真实原图；平均融合仍可能软化木纹，规则纹理相位和真实被遮细节也可能错误，不承诺每张图都改善。
- **WorkBuddy 角标模板检测与反算**：针对右下角"AI生成 / WORKBUDDY"同款半透明水印，自动检测字形并按已知 alpha 反算还原背景
- **纯色背景修复**：水印周围提供一致纯色证据时，估计背景色只填修补掩膜
- **精确掩膜与保护区域**：`--mask` 指定修补像素，`--protect-mask` 保留指定像素；完整水印范围同时用于排除供体，防止把受保护的残留字样复制到其他位置。
- **指定不借用的区域**：`--source-exclude-mask` 排除桌沿、邻近文字、其他材质等不适合作为纹理来源的区域，仅与 `--method exemplar` 合用。它控制纹理来源与可信匹配区域，不扩大修补范围，也不代替保护掩膜。
- **三档初始计算预算**：`--quality fast|balanced|high` 调整初始 exemplar 的补丁和搜索范围，默认 `balanced`；consensus 自身使用固定参数，不随 quality 升档。`high` 会增加计算量，不保证每张图都更准确。
- **批量处理**：同款水印批量自动清理，保留相对目录结构，生成汇总报告
- **像素级校验与掩膜预览**：同时输出实际改动掩膜、允许修补范围及其叠加预览。报告含 `input_unchanged` / `outside_mask_changed_pixels` / `alpha_changed_pixels`，跳过状态绝不伪装成成功。

`--method auto` 保留已有的纯色证据与已知模板分流；其未知区域分支仍先运行基础 exemplar，源补丁不足或达到资源上限时仅尝试一次 NS 传统插值回退，并记录原因。Skill 的未知纹理完整流程显式指定 `exemplar --refine consensus`，供体、覆盖、预算或求解失败均报错，不静默换算法或交付未完成精修的结果。

## 诚实边界（使用前必读）

| 能做 | 不做 / 做不好 |
|---|---|
| 角标模板水印（自动检测反算） | **隐形盲水印**（那是频域修改，本工具不管，请用隐形水印类技能） |
| 指定区域内的浅色文字及暗描边 | 深色文字、彩色 Logo、贴纸或复杂形状，请提供精确 `--mask`；文字细化入口不能可靠覆盖这些情况 |
| 有邻近相似材质可借用的局部纹理修复 | 大块遮挡、独一无二的物体或结构被完全遮住，无法保证恢复原本内容 |
| 纯色/近似纯色背景上的叠字 | 无充分背景证据时，不能随意填成单色 |
| Agent 先定位或用户明确指定区域的局部修复 | 不提供全图纯盲识别任意水印；默认自动模式无模板匹配且未指定区域时，会**直接跳过** |
| 静态 8 位图（≤4000 万像素） | 动图、多页文档、视频 |
| 批量同款模板水印 | 批量各不相同的位置、文字区域或掩膜，需要逐张定位调用 |

**本工具定位是清理自己图片上的水印（如 AI 生成标识、自家模板字样）**，不承诺"视觉改善 = 原图恢复"。报告分别标注模板反算、背景估计、纹理复制或传统插值及其假设；掩膜外零变化只能证明修改范围正确，不能证明水印已完全去净或背景已精确复原。

## 安装

需要 Python 3.10+。**无需手动装依赖**：Agent 执行时先跑 `scripts/bootstrap.py --check-only`，缺依赖时用 `--install` 自动创建隔离缓存环境（按 `requirements.txt` 的版本约束安装）。后续使用自举脚本返回的 Python 路径执行处理命令。首次安装依赖需要联网，图片处理不需要联网。

依赖仍为 numpy、Pillow、opencv-python-headless（headless 版无 GUI 依赖，适合服务器和自动化），**1.3.0 未增加运行时依赖**。

安装到 Agent 宿主：整个 `remove-visible-watermark` 目录放入 skills 加载目录即可。

## 触发方式

对 Agent 说「帮我去掉这张图的水印」「批量清理这些图的角标」「remove watermark」等即可触发。完整触发词见 SKILL.md 的 description。

也可以说：「去掉右下角的浅色文字水印，先检查笔画和描边范围，保留其他文字、图案与原始分辨率，输出修补范围预览和前后对比。」

### 命令示例

在 Skill 根目录执行。将下面的 `python` 替换为 `bootstrap.py` 返回的可用解释器；区域数字只是示例，应按实际图片定位。`x0,y0,x1,y1` 为朝向归一化后图片的像素坐标，右边界、下边界不包含在区域内。

指定浅色文字的区域，执行 Skill 的完整修复流程：

```bash
python scripts/run.py --input input.png --text-roi 100,100,360,180 --method exemplar --refine consensus --quality balanced --output clean.png
```

使用精确修补掩膜和保护掩膜：

```bash
python scripts/run.py --input input.png --mask mask.png --protect-mask protect.png --method exemplar --refine consensus --quality balanced --output clean.png
```

掩膜中的非黑色且非透明像素表示选中。`--text-roi`、`--mask`、`--roi` 三者互斥；其中 `--roi` 会把整个矩形作为修补范围，使用前应确认这一范围确实需要重建。文字掩膜不可靠时，应缩紧检测区域或提供精确掩膜，不扩大范围强行擦除。

### 完整流程与兼容用法

Agent 对未知水印和有纹理背景直接传入 `--method exemplar --refine consensus`。原始 exemplar 仍作为内部初修；精修同时利用原图边界证据和经过一致性检查的初修，不将初修中的错误内容直接当作真实背景。近纯色或已知模板继续走填色/反算分支。用户明确只需快速初修时可指定 `--refine none`；直接使用 CLI 而省略该参数也保持这一兼容行为，`auto` 不会自行开启 consensus。

```bash
python scripts/run.py --input input.png --text-roi 100,100,360,180 --method exemplar --refine consensus --quality balanced --output clean.png
```

`--refine consensus` 可配合 `--text-roi` 或精确 `--mask`，也可同时使用 `--source-exclude-mask` 和 `--protect-mask`；必须显式指定 `--method exemplar`，不接受 `auto` 或其他方法。供体不足、投票覆盖不足、超出固定预算或求解失败均明确报错，不静默退回初始结果或改用 NS。Agent 始终检查选择区与处理前后对比，不把更平滑视为恢复真实木纹。

精修优先寻找 112 像素邻域内的完整供体；没有可用邻域供体时，仅扩展到同一次已经限定的上下文。有效匹配支撑不足的块可从受支持邻块继承供体提案，仍须符合完整原图和排除约束。这些传播块不会以零误差计入独立匹配质量。精修上限为 4 万选区像素、含边界扩展的 100 万上下文像素、2000 个目标块中心、累计 1.28 亿匹配位置；背景引导和最终重建各自最多求解 500 次。预算限制计算量，不能保证画质。

文字分割生成的掩膜周围额外留出 2 像素源排除圈，防止残余字边进入供体或可信匹配；这圈不扩大允许写入区域。初始贪心匹配可使用同一目标补丁内更远处的可信支撑跨过排除圈，不把圈内原图像素重新当成干净背景。

### 结果借入了桌沿或其他材质怎么办？

先查看原图和局部对比。若修补区出现原本不应有的邻近文字、桌沿短线或另一种材质，可由 Agent 根据可见位置生成与原图同尺寸的供体排除掩膜：非黑色且非透明像素表示**这部分不能借用**。算法不会自动理解图中哪部分是桌面、椅背或正常文案。

有上述具体依据时，保留**原始输入图、已经检查正确的修补范围以及保护掩膜**，增加供体排除掩膜，继续执行完整流程并另存结果。若检查发现选区本身漏选或误选，先依据原图修正这些明确位置。例如，接续上面的文字区域示例：

```bash
python scripts/run.py --input input.png --text-roi 100,100,360,180 --method exemplar --refine consensus --quality balanced --source-exclude-mask donor_exclude.png --output clean_refined.png
```

始终从原图重新处理，不将首轮修复图作为输入。`--source-exclude-mask` 可与三种区域入口之一组合，但必须显式指定 `--method exemplar`，批量模式不接受共享供体排除掩膜。排除后没有足够供体会报错，不自动回退 NS。该参数不保证被选中的像素不被修改；需要保护某处时仍应使用 `--protect-mask`。使用 consensus 时，被源排除但保留在写入掩膜外的原图像素仍是 Poisson 的固定边界值，可能影响邻近修补颜色；它们不会因此成为供体或匹配样本。

每轮先集中列出范围、残字、纹理与边界的具体问题，再按可见原因修正并比较效果；始终保持 `needs_review`。若仍有异常，应如实说明；提高 `--quality` 或无目的反复运行不能保证改善。

## 输出说明

以 `--output clean.png` 为例，默认完成处理后生成六个文件：

| 文件 | 内容 |
|---|---|
| `clean.png` | 处理图，保留原始像素分辨率和透明通道 |
| `clean.mask.png` | 实际发生 RGB 改动的像素掩膜 |
| `clean.selection.png` | 扣除保护区域后的允许修补范围 |
| `clean.comparison.png` | 局部处理前后对比 |
| `clean.selection-preview.png` | 允许修补范围叠加到原图上的预览，用于查看是否漏字或误选 |
| `clean.report.json` | 算法、掩膜范围、假设、回退原因、警告与像素校验 |

`--no-preview` 可省略两张预览图。批量任务另有 `summary.json` 汇总。允许修补范围与实际改动范围可能不同，查看效果时应一起检查掩膜预览和局部对比。

状态语义：`processed` = 算法与范围校验通过（仍建议人工看效果）；`needs_review` = 已输出但含近似修复或数值疑点；`skipped_no_match` = 自动模式未匹配，输出未修改的原图副本——**跳过不代表图上没水印**。

## 文件结构

```text
remove-visible-watermark/
├── SKILL.md                  # Agent 执行入口：约束、流程、方法分流
├── README.md                 # 本文件
├── CHANGELOG.md              # 更新记录
├── VALIDATION.md             # 自测校验记录
├── requirements.txt          # 依赖版本约束
├── agents/openai.yaml        # 其他宿主的展示配置
├── assets/                   # WorkBuddy 角标模板（字形掩膜 + 参数）
├── references/               # cli / methods / profiles 深入文档
└── scripts/                  # bootstrap / run / detect / text_mask / patch_inpaint / consensus / restore / core / selftest
```

## 开源致谢与许可声明

本项目自身代码（`scripts/` 全部 `.py` 文件）为原创，使用 **MIT License** 发布（见 LICENSE 文件）。

运行时依赖均为第三方宽松许可库，经 pip 安装、不随仓库分发：numpy（BSD-3-Clause）、Pillow（MIT-CMU）、opencv-python（Apache 2.0）。本项目未内嵌、未修改任何第三方库源码。
