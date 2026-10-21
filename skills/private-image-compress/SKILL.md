---
name: private-image-compress
display_name: 本机隐私图片压缩
display_name_en: Private Local Image Compress
description: 图片压缩双模式技能。隐私模式（主打）：启动本机网页工具，图片全程不经过 AI 对话、不上传任何服务器、零记录零落盘，点击或拖入自动定位原图位置，压缩结果自动写回原图文件夹，适合证件照、合同票据、私人照片等敏感图片。Agent 模式（默认）：直接告诉 AI 大小（KB/MB）和/或尺寸要求，AI 自动定位原图、压缩并写回，二分搜索最大化画质。触发词：压缩图片、压到xxKB、改尺寸、图片太大、头像上传失败、隐私模式压图、别动我的图。
description_zh: 隐私模式下图片全程留在本机处理、AI 不接触图片内容、零上传零记录，自动写回原图文件夹；Agent 模式下指定大小和尺寸要求即可自动压缩，画质最大化。
description_en: "Dual-mode image compression. Privacy mode (highlight): a local web tool where images never enter AI conversations, never leave your machine, and leave zero traces - results are written back to each image's original folder automatically. Agent mode (default): just state size (KB/MB) and/or dimension limits, the agent locates originals, compresses with binary-search quality maximization, and writes back. Ideal for ID photos, contracts, invoices and private pictures."
category: productivity-tools
version: 2.0.0
author: 十七°
agent_created: true
---

# 本机隐私图片压缩 / Private Local Image Compress

按指定的大小（KB/MB）和/或尺寸约束压缩图片，在满足全部约束的前提下最大化画质。**两种模式可选**：

| 模式 | 适合场景 | 用户做什么 | AI 做什么 |
|---|---|---|---|
| **🔒 隐私模式**（主打） | 证件照、合同/发票截图、私人照片、商业敏感图——不想让图片进入 AI 对话 | 在本机网页里拖入/点选图片、设参数、点压缩 | 仅启动本地工具并打开页面，**全程不接触图片内容**，结束后按需关闭服务 |
| **⚡ Agent 模式**（默认） | 普通图片，追求省事 | 只提要求（大小/尺寸） | 定位原图 → 压缩 → 写回原图文件夹 → 交付 |

模式判定：用户未指定 → Agent 模式；用户消息出现「隐私」「我自己操作」「别动我的图」「手动」等 → 隐私模式；拿不准时问一句。

两种模式共同的核心能力：
- **自动写回原图文件夹**：点击选择时直接获取原图真实路径；拖入时按「文件名+字节大小」全盘自动反查原图（同名唯一时按名称兜底），压缩结果写到原图所在文件夹，命名加 `-compressed` 后缀，绝不覆盖原图。
- **字节级精确限额**：二分搜索最大可用 JPEG 质量；平台限额按十进制字节（1KB=1000B）并留 5% 余量，不受 Windows 资源管理器 1024 进制显示的误导。
- **画质最大化**：质量不足时自动在「降分辨率 vs 降质量」之间寻找最优平衡点；透明图自动走 PNG 调色板压缩。

## 模式一：🔒 隐私模式（主打）

**隐私承诺**（如实向用户说明）：
- 图片从磁盘到浏览器到本机服务（127.0.0.1），**全程不经过任何 AI 模型、不上传任何外部服务器**
- **零落盘**：无日志、无配置文件、无浏览记录；唯一的会话目录缓存只存在于进程内存，服务退出即消失
- 压缩用临时文件用完即删
- AI 仅负责启动/关闭本地服务，**不代替用户操作页面**

### 启动步骤

1. **环境自检**（首次使用）：确认 Python 3.10+ 可用，Pillow 缺失时安装：
   ```bash
   python -c "import PIL" 2>/dev/null || python -m pip install Pillow
   ```
2. **端口检查**（必须）：`netstat -ano | findstr :8765`（Windows）或 `ss -tlnp | grep 8765`（macOS/Linux）。被占用说明服务已在跑，直接打开 `http://127.0.0.1:8765`。
3. **启动服务**（必须后台运行，不阻塞对话）：
   ```bash
   python "{skill目录}/scripts/privacy_server.py"
   ```
   服务启动后自动打开浏览器页面（Windows 用户也可直接双击 `scripts/start_privacy.bat`）。
4. **告知用户**：「工具已在浏览器打开，请自行拖入图片或点击虚线框选择，设置大小/尺寸限制后点开始压缩，结果会自动写回原图所在文件夹。处理完告诉我，我帮你关掉服务。」
5. **边界**：不要代替用户操作页面；不要模拟请求处理用户未明确要求的图；用户确认完成后关闭后台服务进程。

### 页面功能（用于解答用户疑问）
- **虚线框两用**：点击 = 系统原生文件选择框（直接获取原图真实路径，最准确）；拖入 = 按「同名+同字节大小」自动反查原图（并行扫描所有磁盘，图片特征目录优先，实测亚秒级命中）。
- **拖入即定位**：图片进入列表时立即自动查找原图位置，实时显示状态（已找到原图 / 同名原图 / 未找到原图）。
- **两级匹配**：同名+同字节大小为精确匹配；仅同名时采用名称兜底（图片目录优先命中）。
- **反查不到时**（如截图从未另存）：自动压缩拖入内容，保存到本会话最近使用的位置；首次使用会出现一次「选择保存位置」按钮。
- 参数：大小限制默认 500KB（十进制字节，自动留 5% 余量），留空不限；尺寸可选「保持原尺寸」/限制最长边/指定宽高；格式自动（透明图 PNG，其余 JPEG）。

## 模式二：⚡ Agent 模式

### 两条铁律（历史踩坑，必须遵守）

**铁律1：大小以「字节」为准，绝不信 KB 显示**
- 平台限额「100KB」几乎都是十进制 = 100,000 字节；Windows 资源管理器按 1KB=1024B 显示会虚低 2.4%。
- 做法：`字节 = 用户 KB × 1000 × 0.95` 作为目标；最终只用 `os.path.getsize` 的字节数验证。

**铁律2：确认原图真实位置，产物放回原图文件夹**
- 用户在聊天中发送的图片路径（各类 AI 会话的缓存目录，如 blobs/、clipboard-images/）只是会话缓存副本，不是原图位置。
- 做法：用户给了路径就用用户的；否则按文件名搜索常见图片目录，找不到就问用户；产物放原图真实所在文件夹；禁止覆盖原图（除非用户明确要求）。

### 执行

环境自检：`python -c "import PIL"` 失败时 `python -m pip install Pillow`（或按 requirements.txt 建 venv）。

```bash
python "{skill目录}/scripts/compress_image.py" \
  --input "原图真实路径" \
  --output "产物路径" \
  --max-bytes 475000 \
  --width 1024 --height 1024     # 或 --long-side 1024，或都省略=保持原尺寸
```

- `--max-bytes N`：字节数（用户 KB × 1000 × 0.95）。
- 尺寸三选一：`--width --height` ｜ `--long-side N` ｜ 不传（保持原尺寸）。
- `--format jpeg|png`：默认按扩展名和透明通道自动判断。
- 输出 JSON：`{bytes, kb_decimal, kb_binary, width, height, quality, format, resized}`；exit 2 = 约束不可满足，向用户说明需放宽哪项。

### 验证与交付
1. 校验 `bytes ≤ 限额字节`、尺寸符合要求。
2. 查看产物确认无明显压缩噪点。
3. 产物放原图文件夹，向用户报告「字节/KB 双单位 + 尺寸 + 质量」。

## 注意事项

- 本工具为本地服务，仅监听 127.0.0.1，无任何外部网络请求。
- Windows 用户可用 `scripts/start_privacy.bat` 一键启动（自动检测 Python 并安装 Pillow）；macOS/Linux 用户按上面步骤手动启动。
- 长内联 Python 命令可能被执行沙箱拒绝时，改为调用脚本文件或写临时脚本执行。
- 路径含中文/空格时全部加双引号。

## Resources

- `scripts/compress_image.py`：Agent 模式压缩脚本（二分质量搜索 + 分辨率-质量平衡 + PNG 调色板），直接执行。
- `scripts/privacy_server.py` + `scripts/privacy_index.html`：隐私模式本地服务 + 网页界面，原样使用。
- `scripts/start_privacy.bat`：Windows 一键启动（自动检测环境）。
