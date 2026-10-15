---
name: wechat-sticker-pack
description: 微信表情包整理打包。把任意尺寸/大小的表情图片、视频素材转换为符合微信表情开放平台规范的格式并打包 ZIP。触发词：表情包打包、表情包上架、微信表情、表情包整理、表情包投稿、sticker pack。支持静态（240x240 PNG）与动态（240x240 GIF）表情包，含横幅/封面/图标/赞赏图转换与规范校验。
---

# 微信表情包整理打包（wechat-sticker-pack）

把不合规的图片/视频素材转换为微信表情开放平台规范格式。纯本地处理，核心管线：

- 图片压缩：色彩量化 16~128 步进 + 细节软化 0.72~0.94，单张 ≤500KB 自动寻优
- GIF 转码：10 级时长/帧率/色数阶梯 + offset 候选 + palettegen/paletteuse 单 pass
- 命名与打包结构：对齐微信表情开放平台投稿要求

## 规范速查（官方原文核对于 2026-10-14，详见 references/）

| 素材 | 数量 | 格式 | 尺寸 | 大小 |
|---|---|---|---|---|
| 表情图 | **8~24 任意** | 静态:PNG/JPG/GIF 动态:GIF | 240×240 | ≤500KB |
| 详情页横幅 | 1 | PNG/JPG | 750×400 | ≤500KB |
| 封面图 | 1 | PNG | 240×240 | ≤500KB |
| 聊天面板图标 | 1 | PNG | 50×50 | ≤100KB |
| 赞赏引导图 | 1 | GIF/PNG | 750×560 | ≤500KB |
| 赞赏致谢图 | 1 | GIF/PNG | 750×750 | ≤500KB |

注意：数量超范围是硬性问题直接拒绝打包；透明底/循环播放/横幅禁文字等是官方"须/应/避免"建议级，校验器只 ⚠️ 提示不拦截（实测不透明封面也可上架）。

## 工作流

1. **确认输入**：让用户提供素材目录（8~24 张表情图或视频）。可选配套素材路径（横幅/封面/图标/赞赏图）。
2. **判断类型**：静态（图片）或动态（视频/GIF）。不确定时问用户；文件混合时按多数判定。
3. **执行打包**（运行环境：Python 3.8+，依赖 `pillow` 与 `imageio-ffmpeg`，先 `pip install -r requirements.txt` 安装）：

```bash
cd <本 Skill 目录>/scripts

# 静态包
python build_pack.py --input <素材目录> --output <输出目录>

# 动态包（视频自动转 240 GIF）
python build_pack.py --input <素材目录> --type dynamic --output <输出目录>

# 带配套素材
python build_pack.py --input <素材目录> --banner x.png --cover x.png --icon x.png \
    --guide x.png --thanks x.png --output <输出目录>

# 仅输出微信格式（不保留 original 目录）
python build_pack.py --input <素材目录> --wechat-only
```

4. **读报告**：脚本自动转换 + 打包 + 调用 validate_pack.py 校验，输出逐文件 ✅/⚠️/❌ 报告。
   - ❌ 硬性问题（尺寸/格式/大小/数量超限）必须处理后重跑
   - ⚠️ 建议级（透明底、循环、横幅透明）口头提示用户即可
5. **交付**：输出目录下的 `emoji_pack_wechat.zip`（含 original + wechat）或 `emoji_pack_wechat_only.zip`（仅 wechat）。

## 输出结构

```
输出目录/
├── emoji_pack_wechat.zip
│   ├── wechat/          # 规范格式（投稿用）
│   │   ├── emoji_1_240.png ... emoji_N_240.png        # 静态
│   │   ├── emoji_anim_1_240.gif ...                   # 动态
│   │   ├── banner_750x400.png / cover_240.png / icon_50.png
│   │   └── donation_guide_750x560.png / donation_thanks_750x750.png
│   └── original/        # 原素材备份
```

## 常见问题处理

- **数量不在 8~24**：拒绝打包，告知用户官方硬性要求（单品 1 张可直接在平台上传，不经本工具）
- **PNG 压不到 500KB**：管线会输出最优结果并标 ⚠️，报告里有压缩路径说明
- **GIF 全阶梯仍超 500KB**：输出最优结果标 ⚠️，建议用户换更短/更简单的素材
- **动态素材是 GIF 而非视频**：`--type dynamic` 会收集 .gif 输入并重编码
- **素材顺序**：按文件名排序编号，提醒用户用文件名控制顺序（如 01_xxx、02_xxx）

## 投稿提示（供参考，详见 references/wechat-spec-official-*.md）

- 平台：sticker.weixin.qq.com，个人/企业实名注册（限中国大陆身份）
- 审核 ≤5 个工作日；名称 ≤8 汉字不可重名；含义词 ≤4 字同套不重复
- 动态/静态不可混在一套
