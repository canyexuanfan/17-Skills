# 微信表情包整理打包（wechat-sticker-pack）

把任意尺寸/大小的表情图片、视频素材转换为**微信表情开放平台**规范格式并打包 ZIP。纯本地处理，不上传、不联网、不调用生成式 AI。

- 静态表情：240×240 PNG，单张 ≤500KB 自动寻优压缩
- 动态表情：视频/GIF 自动转 240×240 GIF（10 级时长/帧率/色数阶梯寻优）
- 配套素材：详情页横幅 750×400、封面 240×240、聊天面板图标 50×50、赞赏指南/感谢图
- 自带规范校验器：逐文件 ✅/⚠️/❌ 报告（硬性问题拦截，建议级提示）
- 附官方制作规范存档与投稿前自查清单（`references/`、`assets/checklist-template.md`）

## 环境要求

- Python 3.8+
- 依赖：`pip install -r requirements.txt`（`pillow` + `imageio-ffmpeg`）

## 使用

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

打包完成自动调用校验器输出逐文件报告；`emoji_pack_wechat.zip`（含 original 备份 + wechat 规范格式）或 `emoji_pack_wechat_only.zip`（仅规范格式）即为投稿包。

## 作为 Agent Skill 使用

目录根的 `SKILL.md` 是 Agent 操作手册：复制整个目录进你的 Agent 技能目录即可，Agent 会引导提供素材、判定静态/动态、执行打包并解读校验报告。

## 投稿提示

- 平台：sticker.weixin.qq.com，个人/企业实名注册（限中国大陆身份）
- 表情 8~24 张，全套统一动态或静态；审核 ≤5 个工作日
- 投稿前过一遍 `assets/checklist-template.md` 自查清单，并核对官方规范是否有更新

## 许可

[MIT](LICENSE)
