# rweb.ps1 — Windows launcher (equivalent of bin/rweb)
#
#   .\bin\rweb.ps1 caps
#   .\bin\rweb.ps1 json /r/programming/hot --limit 100
#   .\bin\rweb.ps1 login --cookie "token_v2=...; reddit_session=..."
#
# This CLI has no default subcommand, so the launcher passes arguments straight
# through after checking that the package imports.
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Cli  = Join-Path $Repo "rweb\cli.py"
$Py   = if ($env:RWEB_PY) { $env:RWEB_PY } else { "python" }

if (-not (Test-Path $Cli)) {
    Write-Error "[rweb] cannot find $Cli —— run .\install.sh from the repo root, or: pipx install ."
    exit 2
}

& $Py -c "import sys; sys.path.insert(0, r'$Repo'); import rweb" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "[rweb] package failed to import —— check that the repo is complete"
    exit 2
}

& $Py $Cli @args
