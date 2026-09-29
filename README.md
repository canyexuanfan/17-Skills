# 17-Skills · 十七° Skill 合集

[![CI](https://github.com/canyexuanfan/17-Skills/actions/workflows/ci.yml/badge.svg)](https://github.com/canyexuanfan/17-Skills/actions/workflows/ci.yml)

**十七° Agent（s17）官方 Skill 合集。** 每个目录是一个可独立安装的 Agent Skill——把对应文件夹放进你的 Agent 的 skills 目录即可使用。后续会持续收录新的 Skill。

**Official skills collection for the s17 terminal agent.** Each folder is a standalone installable Agent skill — drop it into your agent's skills directory and go. More skills coming.

## 收录的 Skill

| Skill | 一句话 | 许可 |
|-------|--------|------|
| [gzh-to-ima](skills/gzh-to-ima/) | 微信公众号文章批量抓取并导入 IMA 知识库（含 `wechat-to-ima` 与 `ima-skill` 两组子技能） | MIT |
| [breakout-cli-login](skills/breakout-cli-login/) | 破局官网 CLI 的 Linux 一键安装与微信扫码登录 | AGPL-3.0 |

## 安装

以 gzh-to-ima 为例，把整个目录复制进 Agent 的 skills 根目录：

```text
skills/
└── gzh-to-ima/           ← 整目录复制
    ├── wechat-to-ima/
    ├── ima-skill/
    └── SKILL.md
```

各 Skill 的详细配置（凭证、依赖、频控等）见对应目录内的 README 与 SKILL.md。

## 配套项目 · Ecosystem

- **十七° Agent**：终端中的通用智能体，安装与发行见 [shiqi-agent-releases](https://github.com/canyexuanfan/shiqi-agent-releases)
- **17deg Atlas**：Agent 原生知识管理，见 [17deg-atlas](https://github.com/canyexuanfan/17deg-atlas)

## 许可 · License

- 合集骨架（根 README / CI / .gitignore）：[MIT](LICENSE)
- 各 Skill 保留原始许可：gzh-to-ima 为 [MIT](skills/gzh-to-ima/LICENSE)，breakout-cli-login 为 [AGPL-3.0](skills/breakout-cli-login/LICENSE)
- 收录历史：两个 Skill 由同名原仓库 subtree 迁入（提交历史完整保留），原仓库已归档并指向本仓库
