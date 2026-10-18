#!/usr/bin/env bash
# 17-Skills 一键安装器（Linux / macOS / Git Bash）
# 用法：
#   ./install.sh                            # 交互式选择
#   ./install.sh xweb                       # 安装单个 Skill
#   ./install.sh all                        # 安装全部
#   ./install.sh xweb --target ~/.claude/skills
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$ROOT/skills"

mapfile -t AVAILABLE < <(find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort)

SKILL="${1:-}"
TARGET="${2:-}"

# 交互式选择
if [ -z "$SKILL" ]; then
    echo ""
    echo "17-Skills 可安装的 Skill："
    for i in "${!AVAILABLE[@]}"; do
        printf "  [%d] %s\n" $((i + 1)) "${AVAILABLE[$i]}"
    done
    printf "  [a] 全部安装 (%d 个)\n" "${#AVAILABLE[@]}"
    read -r -p "选择编号（多选用逗号分隔，a=全部）: " pick
    CHOSEN=()
    IFS="," read -ra PARTS <<< "$pick"
    for p in "${PARTS[@]}"; do
        p="$(echo "$p" | tr -d ' ')"
        if [ "$p" = "a" ]; then CHOSEN=("${AVAILABLE[@]}"); break; fi
        if [[ "$p" =~ ^[0-9]+$ ]] && [ "$p" -ge 1 ] && [ "$p" -le "${#AVAILABLE[@]}"; ]; then
            CHOSEN+=("${AVAILABLE[$((p - 1))]}")
        fi
    done
    if [ "${#CHOSEN[@]}" -eq 0 ]; then echo "未选择任何 Skill，退出。"; exit 0; fi
elif [ "$SKILL" = "all" ]; then
    CHOSEN=("${AVAILABLE[@]}")
else
    found=""
    for a in "${AVAILABLE[@]}"; do [ "$a" = "$SKILL" ] && found=1; done
    if [ -z "$found" ]; then echo "未找到 Skill '$SKILL'。可用：${AVAILABLE[*]}" >&2; exit 1; fi
    CHOSEN=("$SKILL")
fi

# 目标目录
if [ -z "$TARGET" ]; then
    cat <<'TIP'
目标目录 = 你的 Agent 技能根目录（Skill 会被复制为 <目标>/<skill 名>）。
常见位置：
  ZCode    : ~/.zcode/skills
  Claude   : ~/.claude/skills
  手动指定：直接输入任意路径
TIP
    read -r -p "目标目录: " TARGET
fi
if [ -z "$TARGET" ]; then echo "目标目录不能为空" >&2; exit 1; fi
mkdir -p "$TARGET"

# 复制
for name in $(printf "%s\n" "${CHOSEN[@]}" | sort -u); do
    cp -r "$SKILLS_DIR/$name" "$TARGET/$name"
    echo "  ✓ $name -> $TARGET/$name"
done
echo ""
echo "完成。重启你的 Agent 后按各 Skill 的 SKILL.md 生效。"
