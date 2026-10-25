---
name: wechat-to-ima
description: |
  将微信公众号文章抓取并批量导入 IMA 知识库。**第一步必须先判断目标公众号是不是自己管理的号**：
  自己的号走后台发表记录接口，可拿全量并验收；别人的号走合集/搜狗等多渠道尽可能多抓，
  且必须声明拿不到全量（微信已关闭按 fakeid 抓他人全量的后台接口）。
  当用户说"把某公众号导入知识库"、"抓取公众号文章到IMA"、"公众号文章备份到知识库"、
  "把公众号URL导入IMA"、"微信公众号文章入库"、"把公众号文章存到知识库"时触发。
  本技能 = 抓取(fetch_articles.py 自己的号 / fetch_other_channels.py 别人的号) + ima-skill 的 ima_api.cjs 导入。
---

# wechat-to-ima — 微信公众号文章 → IMA 知识库

## Step 0　先判断：这个号是不是你自己管理的？（决定后面全部路径）

**问用户三个问题，不要自己猜：**

1. 你能在浏览器扫码登录 `mp.weixin.qq.com`，且后台左上角显示的号名就是目标号？
2. 后台「内容管理 → 发表记录 / 图文素材」里能看到这个号的文章？
3. 你对这个号有管理员或运营者权限？

| 答案 | 分支 | 能拿到 |
|:--|:--|:--|
| 全部「是」 | **A：自己的号** | 全量历史，可用 `total_count` 验收 |
| 任一「否」 | **B：别人的号** | 多渠道尽量多，**不保证全量** |

> 接口侧验证：自己的号调 `appmsgpublish` 返回 `ret=0`；
> 别人的号调 `appmsg?action=list_ex&fakeid=<他的biz>` **恒返回 `200013`**。

---

## 分支 A：自己的号（推荐，可验收）

```bash
python fetch_articles.py --own --name "我的公众号" --token <TOKEN> --cookie <COOKIE>
```

- 走后台「发表记录」接口 `appmsgpublish?sub_action=list_ex`，**不需要 `__biz` / `fakeid`**
- 每页 5 条、页间 3.5s、每页成功立即落盘（支持 `--resume` 续抓）
- 结束时打印 `篇数 / total_count`，**两者相等才叫抓完**（不等则退出码 2）
- 实测：183 篇 / 37 页 / 全程无 200013

**凭证**：
- `token`：后台地址栏 `?token=1234567890` 里那串数字
- `cookie`：F12 → Application → Cookies → `mp.weixin.qq.com` → Copy All → 单行字符串
- ⚠️ 约 **1~1.5 小时**失效：`200003` = cookie 死了（重新扫码）；`200040` = 只有 token 旧（只换 token）

## 分支 B：别人的号（多渠道，尽量多）

> ⚠️ **声明一（接口）**：按 `fakeid` 抓任意公众号全量历史的后台接口**已被微信关闭**，
> 非自己的号一律 `200013 freq control`。这是权限墙，等一小时、换 cookie、换 IP 都无效，
> 不是本工具的能力缺陷。
>
> ⚠️ **声明二（完整性）**：下列渠道只能**尽可能多**地拿到文章，**不保证是全部**。
> 汇报必须写明覆盖率与缺口，**禁止说成"已抓全"**。

```bash
python fetch_other_channels.py --name "目标号名称" --sogou-pages 4
# 若已知该号建过合集（album_id 从任意文章页源码里取）：
python fetch_other_channels.py --name "目标号" --biz "MzUx..." --album-id 3061234567890
# 给一篇文章链接，顺带抠 biz / nickname / album_id：
python fetch_other_channels.py --from-article "https://mp.weixin.qq.com/s/xxxx"
```

| 渠道 | 需要什么 | 能拿到 | 坑 |
|:--|:--|:--|:--|
| 合集 album | `album_id`（文章页源码里） | 该合集**全部**文章 | 号没建合集 = 这条路不存在 |
| 搜狗微信搜索 | 只要号名 | 关键词命中的一部分 | 按相关性排序、非时间线；显示名可能是新改的名字 |

- 搜狗：每页 10 条、最多 10 页、页间 6s；`antispider` 出现即停
- 认号不能看显示名（可能已改名）⇒ 用「搜狗标题集 ∩ IMA 已有标题集」比对
- 要真正拿到别人的号全量：只能靠微信客户端登录态（`profile_ext` + `uin/key/pass_ticket`），
  见配套 skill `wechat-profile-ext-scraper`

---

## Step 3　导入 IMA

```bash
# 查知识库 / 文件夹
python batch_import_to_ima.py --search "知识库名称"
python batch_import_to_ima.py --list "知识库ID"

# 导入（默认单条 + 3s 间隔，别改成批量）
python batch_import_to_ima.py --import-json "xxx.json" --kb-id "KB_ID" --folder-id "FOLDER_ID"

# 导入后验收（裸链接率 / 重复组）
python batch_import_to_ima.py --check --kb-id "KB_ID" --folder-id "FOLDER_ID"
```

🔴 **必须单条导入**：一批 5 条实测只成 1 条（其余留裸链接）；单条 + 3s 间隔 6/6 全成。
`--batch-size` 保留只为兼容旧调用，>1 会打印警告。

其他硬约束：
- IMA **按 URL 去重、不按文章去重** ⇒ 同一篇换签名重导会变成两条
- 解析失败会留「裸链接」条目（标题就是 URL），**OpenAPI 没有删除接口** ⇒ 只能到客户端手删
- `200005` = IMA 每日导入额度用尽，次日续跑
- 解析是异步的：导完等 30~60s 再 `--check`

## Step 4　汇报口径

```
拿到 N 篇（自己的号：N == total_count ✅ / 别人的号：合集 X + 搜狗 Y）
时间覆盖：YYYY-MM-DD ~ YYYY-MM-DD
缺口：别人的号按渠道尽力收集，中间可能仍有未覆盖时段
```

## 故障速查

| 现象 | 真因 | 动作 |
|:--|:--|:--|
| `200013` | 自己的号=真频控；**别人的号=权限墙** | 前者等 30~60min；后者换渠道 |
| `200003` | cookie 会话过期（约 1~1.5h） | 重新扫码 |
| `200040` | token 旧、cookie 好 | 只换 token |
| `200005` | IMA 每日额度用尽 | 次日续跑 |
| IMA 标题是 URL | 签名过期/被掐 | 重取签名后单条重导 |
| `appmsgalbum` 报 `10004` | 缺/错 `album_id` | 号没建合集，放弃 |
| 搜狗「暂无认证订阅号」 | 搜狗账号索引已废 | 改用 `type=2` 文章搜索 |
