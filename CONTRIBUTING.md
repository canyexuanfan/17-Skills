# 贡献指南 (Contributing)

感谢你考虑为 **gzh-to-ima-skill** 做贡献！🎉

## 提交流程

1. **Fork** 本仓库到你的账号。
2. 基于 `main` 创建特性分支：`git checkout -b feat/your-change`。
3. 提交改动：`git commit -m "feat: 简短描述"`。
4. 推送到你的 Fork：`git push origin feat/your-change`。
5. 在 GitHub 上发起 **Pull Request** 到本仓库的 `main` 分支。

## 硬性规则（务必遵守）

### 1. 绝不要提交私人凭证
- `weixin_credentials.py`（含微信 `token` / `cookie`）已被 `.gitignore` 排除，**请勿强行 `git add -f`**。
- 只在 `weixin_credentials.example.py` 中维护**空占位模板**。
- 不要在 Issue / PR / 评论里粘贴你的真实 `token`、`cookie`、`client_id`、`api_key`。

### 2. 保持脱敏
- 脚本中涉及具体公众号的 `biz` / 名称 / 绝对路径，请使用示例占位符（如 `YOUR_BIZ_HERE` / `示例公众号` / 相对路径）。
- 提交前本地跑一次敏感词自检：
  ```bash
  grep -rIin "wxuin=\|slave_sid=\|bizuin=\|AKID\|token=15" . --exclude-dir=.git
  ```
  应为空。CI 也会自动扫描，命中会报错拦截。

### 3. 频控铁律（涉及抓取逻辑时）
- 微信文章列表接口 `ret=200013` / `ret=200003` = 频率控制，会封 IP。任何抓取改动都必须保留「间隔 + 立即停 / 退避」机制，**不得为了速度牺牲频控安全**。
- 不要引入「先拉全量列表」的高频逻辑。

## 代码风格
- Python：仅用标准库，保持 `py_compile` 通过；函数/变量命名清晰，关键步骤加中文注释。
- Node：`.cjs` 保持 `node --check` 通过。
- 提交信息建议用 `feat:` / `fix:` / `docs:` / `chore:` 前缀。

## 提交前自检清单
- [ ] `python -m py_compile wechat-to-ima/*.py` 无报错
- [ ] `node --check ima-skill/ima_api.cjs` 无报错
- [ ] 无私人凭证 / 硬编码路径泄漏
- [ ] 更新了相关文档（README / SKILL.md）

如有疑问，欢迎开 Issue 讨论。
