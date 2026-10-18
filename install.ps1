# 17-Skills 一键安装器（Windows PowerShell）
# 用法：
#   .\install.ps1                          # 交互式选择
#   .\install.ps1 -Skill xweb              # 安装单个 Skill
#   .\install.ps1 -Skill all               # 安装全部
#   .\install.ps1 -Skill xweb -Target C:\path\to\agent\skills
param(
    [string]$Skill = "",
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$SkillsDir = Join-Path $Root "skills"

if (!(Test-Path $SkillsDir)) { Write-Error "未找到 skills 目录：$SkillsDir"; exit 1 }

$Available = Get-ChildItem -Directory $SkillsDir | Select-Object -ExpandProperty Name

# 交互式选择
if ([string]::IsNullOrWhiteSpace($Skill)) {
    Write-Host "`n17-Skills 可安装的 Skill：" -ForegroundColor Cyan
    for ($i = 0; $i -lt $Available.Count; $i++) {
        Write-Host ("  [{0}] {1}" -f ($i + 1), $Available[$i])
    }
    Write-Host ("  [a] 全部安装 ({0} 个)" -f $Available.Count)
    $pick = Read-Host "选择编号（多选用逗号分隔，a=全部）"
    $chosen = @()
    foreach ($p in ($pick -split "," | ForEach-Object { $_.Trim() })) {
        if ($p -eq "a") { $chosen = $Available; break }
        if ($p -match "^\d+$" -and [int]$p -ge 1 -and [int]$p -le $Available.Count) {
            $chosen += $Available[[int]$p - 1]
        }
    }
    if ($chosen.Count -eq 0) { Write-Host "未选择任何 Skill，退出。" -ForegroundColor Yellow; exit 0 }
} else {
    if ($Skill -eq "all") { $chosen = $Available }
    elseif ($Available -contains $Skill) { $chosen = @($Skill) }
    else { Write-Error "未找到 Skill '$Skill'。可用：$($Available -join ', ')"; exit 1 }
}

# 目标目录
if ([string]::IsNullOrWhiteSpace($Target)) {
    Write-Host @"
目标目录 = 你的 Agent 技能根目录（Skill 会被复制为 <目标>\<skill 名>）。
常见位置：
  ZCode   : $env:USERPROFILE\.zcode\skills
  手动指定：直接粘贴任意路径
"@ -ForegroundColor Cyan
    $Target = Read-Host "目标目录"
}
if ([string]::IsNullOrWhiteSpace($Target)) { Write-Error "目标目录不能为空"; exit 1 }
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# 复制
foreach ($name in ($chosen | Select-Object -Unique)) {
    $src = Join-Path $SkillsDir $name
    $dst = Join-Path $Target $name
    Copy-Item -Recurse -Force -Path $src -Destination $dst
    Write-Host "  ✓ $name -> $dst" -ForegroundColor Green
}
Write-Host "`n完成。重启你的 Agent 后按各 Skill 的 SKILL.md 生效。" -ForegroundColor Cyan
