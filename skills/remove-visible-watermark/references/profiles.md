# 水印模板与自定义 Profile

供调用本 Skill 的 Agent 使用。Profile 是本地数据配置，不包含可执行代码。

## 选择与未命中行为

- `profile="auto"`：读取指定模板目录的 JSON；`workbuddy` 是 `workbuddy-corner-v1` 的别名。
- 也可传入完整 profile id，或一个本地 JSON 文件的**绝对路径**。
- `profile="none"`：关闭模板检测，返回 `found=False`。
- 形状、可见对比度、逆算残差或数值检查不满足时返回 `found=False`；不得因此擦除整个角落或 ROI。
- 无效参数、缺失模板或错误 schema 会抛出 `ValueError`；修正配置后再执行，不要把配置错误描述为“没有水印”。

## 完整示例

以下示例对应随包提供的实际模板；自定义配置需提供自己的匹配模板，并更改 `id`。

```json
{
  "schema_version": 1,
  "id": "workbuddy-corner-v1",
  "template": "workbuddy-corner-v1-alpha.png",
  "template_kind": "alpha_u8",
  "foreground_rgb": [255, 255, 255],
  "compositing_domain": "encoded_rgb",
  "reference_canvas": [1920, 1072],
  "reference_bbox": [1788, 1009, 1915, 1066],
  "anchor": "bottom_right",
  "search_radius_px": 48,
  "scale_modes": ["native", "canvas"],
  "alpha_support_threshold": 0.00392156862745098,
  "detection": {
    "min_shape_score": 0.48,
    "min_gray_score": 0.08,
    "min_inverse_improvement": 0.20,
    "max_out_of_range_fraction": 0.05
  },
  "calibration": {
    "source": "用户提供的近黑底水印样本",
    "foreground_white_is_assumed": true,
    "alpha_is_assumed_T_divided_by_255": true,
    "official_ground_truth": false
  },
  "notes": "黑底观测提供 T=alpha*W；白色前景为假设。"
}
```

## 实际 Schema 限制

- 除 `calibration` 和 `notes` 外，示例中的顶层字段均为必填；不接受其他顶层字段。
- `calibration` 为可选元数据对象，其内容不参与执行；`notes` 为不超过 20,000 字符的字符串。
- `schema_version` 仅支持整数 `1`；`id` 为 1–80 个小写 ASCII 字母、数字、连字符或下划线。目录内 id 不可重复。
- `template_kind` 仅支持 `alpha_u8`；合成域仅支持 `encoded_rgb`；锚点仅支持 `bottom_right`。
- `foreground_rgb` 必须是三个 0–255 的整数；`reference_canvas` 为两个 1–100,000 的整数，顺序为宽、高。
- `reference_bbox` 为画布内非空整数 `[x0,y0,x1,y1]`，右、下边界不包含在区域内。
- `search_radius_px` 在 0–512 之间；`scale_modes` 为 `native`、`canvas` 的非空无重复列表。
- `alpha_support_threshold` 在 0–0.95 之间；四个 `detection` 字段均必填，值在 0–1 之间。
- JSON 不超过 256 KiB；不接受重复键、NaN、Infinity。一次最多加载 128 个 profile。

## 模板文件与路径

- `template` 必须是相对于 **JSON 所在目录** 的 PNG 路径；不得为绝对路径，不得含 `..` 或反斜杠。
- 可以使用目录内的子目录；解析后不得越出 JSON 所在目录，指向目录外的符号链接也会拒绝。
- 模板必须是 PNG、8 位灰度 `L`；宽高必须严格等于 `reference_bbox` 的宽高，总面积不得超过 400 万像素。
- 模板像素除以 255 得到局部 alpha；写入支撑为 `alpha > alpha_support_threshold`。
- 支撑至少 8 个像素，模板需有空间变化；所有 alpha 必须小于 0.98。本检测配置不适用于完全不透明模板。

## 校准含义与缩放坐标

黑底观测只能近似提供预乘颜色 `T=alpha*W`，不能唯一确定 alpha。内置模板假定前景 `W=[255,255,255]`，因此采用 `alpha=T/255`；这不是官方水印真值。新增模板必须记录已知条件和假设，不要把截图亮度直接宣称为准确透明度。本包没有自动校准脚本。

`native` 搜索原生模板尺寸；`canvas` 使用输入画布与参考画布宽、高比例中的较小值，保持等比例。显式 `scale` 覆盖自动比例，范围为 0.125–8。默认围绕右下锚点搜索；放大时搜索半径随比例扩大，缩小时保留原搜索半径。

缩放按**原参考画布的坐标相位**处理：缩小时使用面积采样，放大时使用双线性采样。例如原模板起点 `y=1009` 缩到一半会产生半像素相位，不能仅对裁出的模板直接 resize。放大后的返回 bbox 含插值边缘；应使用返回的 bbox 与同尺寸局部数组，不要套用原 bbox。

`roi` 是输入图片坐标中的 xyxy 搜索范围，必须完整容纳模板。显式 ROI 替代默认锚点附近的搜索范围，不会改变模板尺寸。

## 结果解释

`score` 是灰度、高通形状与梯度的加权相似度，**不是成功概率或复原准确率**。接受结果还需通过逆算残差改善、数值越界比例与可见对比度检查。

旋转、非等比例拉伸、裁掉部分字形、字体或透明度变化、压缩损伤以及不同重采样滤镜可能未命中。未命中时保留输入，报告原因；若有用户提供的精确掩膜，可按主 Skill 的手动掩膜流程处理。
