# xweb.ps1 — Windows 启动器（等价于 bin/xweb）
#
#   .\bin\xweb.ps1 elonmusk
#   .\bin\xweb.ps1 login --cookie "auth_token=...; ct0=..."
#
# 默认子命令是 user：`xweb.ps1 elonmusk` == `xweb.ps1 user elonmusk`
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Cli  = Join-Path $Repo "xweb\cli.py"
$Py   = if ($env:XWEB_PY) { $env:XWEB_PY } else { "python" }

if (-not (Test-Path $Cli)) {
    Write-Error "[xweb] 找不到 $Cli —— 请在仓库根目录运行，或用 pipx install ."
    exit 2
}

$subs = & $Py $Cli --list-subs 2>$null

$hasSub = $false
foreach ($a in $args) {
    if ($subs -contains $a) { $hasSub = $true; break }
}

if ($hasSub) {
    & $Py $Cli @args
} else {
    & $Py $Cli "user" @args
}
