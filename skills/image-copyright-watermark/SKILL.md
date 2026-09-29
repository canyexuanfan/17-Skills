---
name: image-copyright-watermark
display_name: 隐形版权水印
display_name_en: Invisible Copyright Watermark
description: 隐形版权水印技能。给图片嵌入肉眼不可见的盲水印 + EXIF/tEXt 版权署名双保险，被盗图后可提取水印溯源取证，也可清除自己图片的水印。当用户要求"加隐形水印""图片防盗""版权保护""盲水印""查这张图是不是我的""去除隐形水印""图片署名"时使用。可见水印（角标logo）不在此范围。触发词：隐形水印、盲水印、图片版权、防盗图、水印溯源、图片署名。
description_zh: 给图片嵌入肉眼不可见的盲水印与属性署名双保险，盗图后可提取溯源取证，也支持清除自己图片的水印。纯本地运行，图片不上传。
description_en: Embed invisible frequency-domain blind watermarks plus EXIF/tEXt copyright metadata into images, verify ownership by extracting the watermark after theft, and remove watermarks from your own images. Runs fully offline.
category: content-creation
version: 1.0.0
author: 十七°
agent_created: true
---

# Image Copyright Watermark（隐形版权水印）

一个工具三个动作：**嵌入**（盲水印 + 属性署名双保险）→ **查验**（双路取证）→ **清除**。底层 DWT-DCT-SVD 频域算法（blind-watermark 库，MIT），纯本地运行，图片不上传任何服务器。

## 双保险原理（对用户解释时用这个表）

| 层 | 载体 | 扛得住 | 扛不住 |
|---|---|---|---|
| 属性署名 | EXIF（JPEG）/ tEXt chunk（PNG） | 文件级倒手：拷贝、网盘、发原图 | **平台上传必被剥离**（微信/微博/小红书等合规行为）、截图、编辑器重存 |
| 盲水印 | 像素频域 | **平台压缩二压**（实测 JPEG q85 后仍 94% 命中）、缩放后回原尺寸（实测 100%）、格式转换 | 裁剪（实测裁 20% 失败）、缩小不回弹、截图、AI 重绘 |

两层失守场景几乎不重叠。普通人盗图路径（另存→发群→传平台→再转）全程盲水印存活。

**三条铁律（必须向用户讲清，防预期落空差评）：**
1. **绝不承诺"属性永远在"**——EXIF 过微信/微博/小红书必被剥离，这是平台行为。正确话术："文件没被重新编码时属性在，被压过就查盲水印"。
2. **嵌入后让用户保存凭证**：`wm_text / wm_bits / password` 三元组（脚本 JSON 会输出），查验时必须提供——这是防盗机制的私钥属性，丢了就查不了。
3. **清除没有无损方案**——盲水印是频域加性修改，数学上不可精确逆转。最优先引导用户"嵌水印前保留无水印原图"，清除=用原图重出；没有原图才用破坏性清除（有可见画质损失）。

## 工作流

### 嵌入
1. 按铁律2（同 image-compress）确认原图真实位置，产物放回原图文件夹。
2. 让用户提供水印文本（建议含可识别标识如 `©十七°` 或 `@账号名`，10~30 字符为佳），密码默认 1（要保密就换）。
3. 执行 embed（见下）。产物默认 PNG（无损保水印）；用户坚持 JPEG 时脚本自动走 q95+4:4:4 高保真。
4. 交付时把 JSON 里的凭证三元组明确报给用户，嘱咐保存。

### 查验（被盗图溯源）
1. 用户需提供凭证：水印文本 + 密码（+ wm_bits，嵌入时 JSON 有；若图片元数据没被剥，脚本也能自动读出）。
2. 执行 verify。判定：`match`(≥0.9 命中) / `weak`(0.6~0.9，找更高质量版本再验) / `no-match` / `meta-only`(仅属性层存活=文件级倒手未被压缩)。
3. 取证报告可直接给用户截图存证；涉及维权建议同时公证原图。

### 清除
1. 先问"有没有嵌入前的无水印原图？"——有：`--mode from-original --original 原图`，画质零损失。
2. 没有：`--mode destructive`（重采样+轻模糊+剥 EXIF），**必须向用户明示画质有可见损失**，清除后建议跑一次 verify 确认提取已失败。

## 脚本用法

依赖见包内 `requirements.txt`（blind-watermark / Pillow / numpy / opencv-python / PyWavelets）。**执行前先做依赖自检**：`python -c "import blind_watermark"` 失败时，创建隔离 venv 并 `pip install -r requirements.txt`（Windows 可参考：`python -m venv <环境目录>`，再用该 venv 的 `Scripts/python.exe -m pip install`）。下文 `{PY}` 指该 venv 的 python，`{TOOL}` 指本 skill 的 `scripts/watermark_tool.py` 绝对路径（**运行时按实际安装位置解析，不要照抄示例路径**）。

```bash
# 嵌入（默认输出 原名-watermarked.png 到原目录）
{PY} {TOOL} embed --input "原图路径" --text "©十七° shiqidu@2026" --password 7

# 查验（属性层自动读；盲水印层需凭证）
{PY} {TOOL} verify --input "待检图" --password 7 --text "©十七° shiqidu@2026" --wm-bits 184

# 清除（优先原图重出）
{PY} {TOOL} remove --input "带水印图" --mode from-original --original "无水印原图"
# 兜底：破坏性清除
{PY} {TOOL} remove --input "带水印图" --mode destructive
```

输出：stdout 一行 JSON（日志已静默）。`embed` 关键字段 `wm_bits`（水印位长，查验凭证之一）；`verify` 关键字段 `verdict` + `wm_match_ratio`；exit 2 = 文件不存在或缺凭证。

## 实测数据（2026-09-29，1200×800 测试图，水印"©十七° shiqidu@2026"，184 bits）

| 场景 | 提取结果 | verdict |
|---|---|---|
| 无损 PNG 直取 | 100% 逐字命中 | match |
| JPEG q85 重压缩（模拟平台二压） | 94.1%（仅"©"符号受损） | match |
| 缩小 50% 再放回原尺寸（压图床再下载） | 100% 逐字命中 | match |
| 属性被剥离且无凭证 | 正确跳过并提示补凭证 | — |
| 裁剪 20% / 缩小不回弹 | 提取失败 | no-match（诚实边界，勿对外宣传可抗裁剪） |

## 环境注意事项

- blind_watermark 首次运行有欢迎语，脚本已通过 `bw_notes.close()` 静默，stdout 保证只有 JSON。
- `WaterMark` 对象不可复用（库的坑），脚本每次操作新建实例，勿在临时代码里复用。
- wm_bits 只与水印文本和编码有关，同文本 bits 相同；str 模式按字符编码展开，**不要自己估算长度**，一律用 embed 输出的真实值。
- 中文路径全部加双引号；沙箱内长内联 Python 会报错，逻辑写进 .py 再跑。

## Resources

- `scripts/watermark_tool.py`：三合一 CLI（embed/verify/remove），直接执行无需读入。
- `requirements.txt`：Python 依赖清单，环境缺失时 `pip install -r requirements.txt`。
- `tests/run_test.py`：端到端回归测试（造图→嵌入→三攻击→验证），改动脚本后必跑。
- `references/technique.md`：DWT-DCT-SVD 原理与鲁棒性实测细节（向较真的用户解释时用）。
- `README.md` / `CHANGELOG.md` / `VALIDATION.md`：面向用户的介绍、版本记录与实测校验记录。
