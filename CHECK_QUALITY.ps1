param(
    [switch]$Isolated,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Không tìm thấy venv. Hãy cài requirements-dev.txt trước."
}

$isolatedRoot = $null
$qualityLogPath = $null
$transcriptStarted = $false
$originalEnvironment = @{}
$isolatedEnvironmentNames = @(
    "ZALO_QUALITY_ISOLATED",
    "DATABASE_PATH",
    "RUNTIME_ROOT",
    "BROWSER_PROFILE_PATH",
    "LOCK_ROOT",
    "EVIDENCE_ROOT",
    "DB_BACKUP_ROOT",
    "LOG_DIR",
    "UPLOAD_ROOT",
    "COVERAGE_FILE",
    "PYTHONPYCACHEPREFIX",
    "GOOGLE_FORM_MODE",
    "BCNB_DRY_RUN",
    "ZALO_AI_ASSIST_CLASSIFIER",
    "FORM_URL",
    "BCNB_URL",
    "BCNB_CLASS_STATUS_URL",
    "LMS_URL"
)
$coverageJsonPath = Join-Path $PSScriptRoot "coverage-phase9a.json"

if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
    if (-not [System.IO.Path]::IsPathRooted($LogPath)) {
        throw "LogPath phải là đường dẫn tuyệt đối ngoài repository."
    }
    $qualityLogPath = [System.IO.Path]::GetFullPath($LogPath)
}
elseif ($Isolated) {
    $qualityLogPath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("zalo-quality-isolated-" + [guid]::NewGuid().ToString("N") + ".log")
}

if ($null -ne $qualityLogPath) {
    $repositoryRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    $normalizedLogPath = $qualityLogPath.TrimEnd('\')
    if ($normalizedLogPath.StartsWith($repositoryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "LogPath phải nằm ngoài repository."
    }
    $logParent = Split-Path -Parent $qualityLogPath
    if (-not [string]::IsNullOrWhiteSpace($logParent)) {
        New-Item -ItemType Directory -Path $logParent -Force | Out-Null
    }
    Start-Transcript -Path $qualityLogPath -Force | Out-Null
    $transcriptStarted = $true
}

if ($Isolated) {
    $isolatedRoot = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("zalo-quality-isolated-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $isolatedRoot -Force | Out-Null

    foreach ($name in $isolatedEnvironmentNames) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable(
            $name,
            "Process"
        )
    }

    $isolatedValues = @{
        ZALO_QUALITY_ISOLATED = "1"
        DATABASE_PATH = (Join-Path $isolatedRoot "database.sqlite3")
        RUNTIME_ROOT = (Join-Path $isolatedRoot "runtime")
        BROWSER_PROFILE_PATH = (Join-Path $isolatedRoot "browser-profile")
        LOCK_ROOT = (Join-Path $isolatedRoot "locks")
        EVIDENCE_ROOT = (Join-Path $isolatedRoot "evidence")
        DB_BACKUP_ROOT = (Join-Path $isolatedRoot "db-backups")
        LOG_DIR = (Join-Path $isolatedRoot "logs")
        UPLOAD_ROOT = (Join-Path $isolatedRoot "uploads")
        COVERAGE_FILE = (Join-Path $isolatedRoot ".coverage")
        PYTHONPYCACHEPREFIX = (Join-Path $isolatedRoot "pycache")
        GOOGLE_FORM_MODE = "dry-run"
        BCNB_DRY_RUN = "true"
        ZALO_AI_ASSIST_CLASSIFIER = "false"
        FORM_URL = ""
        BCNB_URL = "http://127.0.0.1/quality-disabled/check_nhanxet.php"
        BCNB_CLASS_STATUS_URL = "http://127.0.0.1/quality-disabled/hocsinh.php"
        LMS_URL = "http://127.0.0.1/quality-disabled/lms-class-sessions"
    }
    foreach ($entry in $isolatedValues.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $entry.Key,
            [string]$entry.Value,
            "Process"
        )
    }
    $coverageJsonPath = Join-Path $isolatedRoot "coverage-phase9a.json"
    Write-Host "`n[Isolated quality context] $isolatedRoot" -ForegroundColor DarkCyan
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

try {
    if ($Isolated) {
        Invoke-PythonStep "Initialize isolated database" @(
            "-c", "from db.database import init_db; init_db()"
        )
    }
    Invoke-PythonStep "Ruff" @("-m", "ruff", "check", ".")
    Invoke-PythonStep "Mypy" @("-m", "mypy")
    Invoke-PythonStep "Coverage reset" @("-m", "coverage", "erase")
    Invoke-PythonStep "Regression tests + branch coverage" @(
        "-m", "coverage", "run", "-m", "unittest", "discover",
        "-s", "tests", "-p", "test_*.py"
    )
    Invoke-PythonStep "Coverage threshold" @("-m", "coverage", "report", "--skip-covered")
    Invoke-PythonStep "Coverage JSON" @(
        "-m", "coverage", "json", "-o", $coverageJsonPath
    )
    Invoke-PythonStep "Phase 9A security + critical coverage gate" @(
        "tests\check_phase9a_quality.py", "--coverage-json", $coverageJsonPath
    )
    Invoke-PythonStep "Phase 3 invariant gate" @("tests\check_phase3_quality.py")
    Invoke-PythonStep "Phase 4 invariant gate" @("tests\check_phase4_quality.py")
    Invoke-PythonStep "Phase 5 invariant gate" @("tests\check_phase5_quality.py")
    Invoke-PythonStep "Phase 6 invariant gate" @("tests\check_phase6_quality.py")
    Invoke-PythonStep "Phase 7 invariant gate" @("tests\check_phase7_quality.py")
    Invoke-PythonStep "Phase 8 invariant gate" @("tests\check_phase8_quality.py")
    Invoke-PythonStep "Phase 9 local readiness gate" @("tests\check_phase9_quality.py")
    Invoke-PythonStep "Phase 9B staging contract gate" @("tests\check_phase9b_quality.py")
    Invoke-PythonStep "Dependency compatibility" @("-m", "pip", "check")
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
}
finally {
    if ($Isolated) {
        foreach ($name in $originalEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable(
                $name,
                $originalEnvironment[$name],
                "Process"
            )
        }
        if ($null -ne $isolatedRoot -and (Test-Path -LiteralPath $isolatedRoot)) {
            Remove-Item -LiteralPath $isolatedRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            # Preserve the gate result if transcript shutdown is unavailable.
        }
    }
}
