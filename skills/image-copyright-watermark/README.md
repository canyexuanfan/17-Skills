# image-copyright-watermark｜隐形版权水印 Skill

版本：**1.0.0**。更新日期：2026-09-29。

一个工具三个动作：**嵌入** → **查验** → **清除**。给图片加上肉眼完全不可见的频域盲水印，同时把版权署名写进图片属性（JPEG 走 EXIF，PNG 走 tEXt chunk），形成双保险。图片被盗后可提取水印溯源取证；也可清除自己图片的水印。底层为 DWT-DCT-SVD 算法（blind-watermark 库，MIT 协议），**纯本地运行，图片不上传任何服务器**。

可见水印（角标 logo、平铺文字）不在此 Skill 范围。

## 双保险各管一段（使用前必读）

| 层 | 载体 | 扛得住 | 扛不住 |
|---|---|---|---|
| 属性署名 | EXIF / tEXt | 文件级倒手：拷贝、网盘、发原图 | **平台上传必被剥离**（微信/微博/小红书等，平台合规行为）、截图、编辑器重存 |
| 盲水印 | 像素频域 | **平台压缩二压**（实测 JPEG q85 后仍 94% 命中）、缩放后回原尺寸（实测 100%）、格式转换 | 裁剪（实测裁 20% 失败）、缩小不回弹、截图、AI 重绘 |

两层失守场景几乎不重叠。普通人盗图的常见路径（右键另存 → 发群 → 传平台 → 再转发）全程盲水印存活——这才是维权时真正能拿出手的证据层。

**三条诚实边界，本 Skill 不做超出实测的承诺：**
1. 不承诺「属性永远在」——EXIF 过社交平台必被剥离。正确理解：文件没被重新编码时属性在，被压过就查盲水印。
2. 嵌入后必须保存**凭证三元组**（水印文本 / wm_bits / 密码），查验时必需。这是防盗机制的私钥属性，丢了查不了。
3. 清除没有无损方案——盲水印是频域加性修改，数学上不可精确逆转。最优做法是嵌入前保留无水印原图；无原图时清除会有可见画质损失，脚本会明示。

## 安装

需要 Python 3.10+ 环境与 `requirements.txt` 所列依赖（blind-watermark / Pillow / numpy / opencv-python / PyWavelets），`pip install -r requirements.txt` 即可。标准 Agent Skills 目录以 `SKILL.md` 为入口，`scripts` 为执行脚本，`references/technique.md` 为算法细节。安装后将整个 `image-copyright-watermark` 目录放入宿主的 skills 加载目录。

## 触发方式

对 Agent 说「给这张图加隐形水印」「查这张图是不是我的」「帮我去除隐形水印」「图片版权保护」等即可触发。完整触发词见 SKILL.md 的 description。

## 鲁棒性实测矩阵（2026-09-29，1200×800 测试图，水印「©十七° shiqidu@2026」184 bits）

| 场景 | 提取结果 | 判定 |
|---|---|---|
| 无损拷贝 | 100% 逐字命中 | match |
| JPEG q85 重压缩（模拟平台二压） | 94.1%（仅「©」符号受损） | match |
| 缩小 50% 再放回原尺寸（压图床再下载） | 100% 逐字命中 | match |
| 裁剪 20% | 提取失败 | no-match |
| 缩小 50% 不回弹 | 提取失败 | no-match |
| 截图 / AI 重绘 | 预期破坏（未纳入自动化测试） | — |

提取水印**不需要原图**，但需要凭证三元组。完整测试方法与数据见 VALIDATION.md。

## 文件结构

```text
image-copyright-watermark/
├── SKILL.md                 # Agent 执行入口：工作流、铁律、脚本用法
├── README.md                # 本文件
├── CHANGELOG.md             # 更新记录
├── VALIDATION.md            # 交付校验与实测记录
├── requirements.txt         # Python 依赖（必需）
├── scripts/
│   └── watermark_tool.py    # 三合一 CLI：embed / verify / remove
├── references/
│   └── technique.md         # DWT-DCT-SVD 原理与鲁棒性细节
└── tests/
    ├── run_test.py          # 端到端回归测试（造图→嵌入→攻击→验证）
    └── run_attack2.py       # 补充攻击场景（缩放回弹、裁剪）
```

本包不含真实用户图片、密码或凭证样本；测试素材为脚本临时生成的合成图。
