$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Không tìm thấy venv. Hãy cài requirements-dev.txt trước."
}

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $python -X utf8 @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label thất bại với exit code $LASTEXITCODE."
    }
}

Invoke-PythonStep "Ruff" @("-m", "ruff", "check", ".")
Invoke-PythonStep "Mypy" @("-m", "mypy")
Invoke-PythonStep "Coverage reset" @("-m", "coverage", "erase")
Invoke-PythonStep "Regression tests + branch coverage" @(
    "-m", "coverage", "run", "-m", "unittest", "discover",
    "-s", "tests", "-p", "test_*.py"
)
Invoke-PythonStep "Coverage threshold" @("-m", "coverage", "report", "--skip-covered")
Invoke-PythonStep "Phase 3 invariant gate" @("tests\check_phase3_quality.py")
Invoke-PythonStep "Phase 4 invariant gate" @("tests\check_phase4_quality.py")
Invoke-PythonStep "Phase 5 invariant gate" @("tests\check_phase5_quality.py")
Invoke-PythonStep "Schema + active jobs" @("-m", "tests.check_phase2_schema")
Invoke-PythonStep "Hermetic app smoke" @("-m", "tests.check_app_smoke")

$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) {
    $bundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $bundledNode) {
        $nodePath = $bundledNode
    }
    else {
        throw "Không tìm thấy Node.js để chạy frontend contract gate."
    }
}
else {
    $nodePath = $node.Source
}

Write-Host "`n[Frontend contract]" -ForegroundColor Cyan
& $nodePath "tests\check_template_js.js"
if ($LASTEXITCODE -ne 0) {
    throw "Frontend contract thất bại với exit code $LASTEXITCODE."
}

Invoke-PythonStep "Compile all" @(
    "-m", "compileall", "-q", ".",
    "-x", "(venv|\.backups|zalo_session)"
)

Write-Host "`nQUALITY GATE PASSED" -ForegroundColor Green
