# 更新记录

## 1.0.0 — 2026-09-29

### 首发：一个工具三个动作

- **嵌入（embed）**：DWT-DCT-SVD 频域盲水印 + EXIF（JPEG）/ tEXt chunk（PNG）版权署名，一次操作双保险。产物默认 PNG 无损；用户坚持 JPEG 时自动走 q95 + 4:4:4 高保真。
- **查验（verify）**：双路取证——自动读属性层（Artist/Copyright/水印文本/位长）+ 凭密码提取频域水印，输出 match / weak / no-match / meta-only 四级判定与匹配率，可直接截图存证。
- **清除（remove）**：`from-original`（用无水印原图重出，画质零损失）优先；`destructive`（重采样+轻模糊+剥 EXIF）兜底，须明示画质代价。

### 鲁棒性按实测写，不按官方 README 写

官方宣称的抗裁剪能力实测未复现（裁剪 20% 提取失败），因此对外文档一律不宣传抗裁剪。实测通过的场景：无损拷贝 100%、JPEG q85 重压缩 94.1%（模拟平台二压）、缩小 50% 再放回原尺寸 100%。完整矩阵见 VALIDATION.md 与 `references/technique.md`。

### 工程细节

- blind_watermark 欢迎语会污染 stdout，已用 `bw_notes.close()` 静默，脚本输出保证为一行 JSON。
- 水印位长（wm_bits）不可按字符数估算，embed 时写入图片元数据并输出给用户保存；verify 三级回退（参数 > 图片元数据 > 提示补凭证）。
- 凭证三元组 `wm_text / wm_bits / password` 由用户自行保存，图片本身不携带密码。
