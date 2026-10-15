#requires -Version 5.1
[CmdletBinding()]
param(
  [string]$ControlRoot = 'F:\AI-Harness-Control-Test',
  [string]$WorkerRoot = 'F:\AI-Worker-Test',
  [string]$ServiceDataRoot = 'C:\ProgramData\QQ\AntigravityWorker-Test',
  [string]$WorkerAccount = 'AIWorker',
  [string]$ServiceName = 'QQAntigravityWorker-Test',
  [switch]$RemoveTestData,
  [switch]$RemoveWorkerAccount,
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\provisioning-safety.ps1')

$WorkerIdentity = "$env:COMPUTERNAME\$WorkerAccount"
$MarkerName = 'qq-antigravity-worker-test.marker.json'
$MarkerPath = $null
$Marker = $null
$ServiceRemoved = $false
$WorkerAccountRemoved = $false
$TestDataRemoved = $false

function Test-Step {
  param([Parameter(Mandatory = $true)][string]$Step)
  return @($Marker.completed_steps) -contains $Step
}

function Assert-Marker {
  param(
    [Parameter(Mandatory = $true)][object]$Candidate,
    [Parameter(Mandatory = $true)][string]$ResolvedControlRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedWorkerRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedServiceDataRoot
  )

  $requiredFields = @(
    'schema', 'mode', 'provisioning_id', 'worker_account_name',
    'worker_account_created_by_provisioning', 'service_name', 'service_account',
    'service_created_by_provisioning', 'control_root', 'worktree_root',
    'service_data_root', 'manifest_path', 'completed_steps'
  )
  foreach ($field in $requiredFields) {
    if ($null -eq $Candidate.PSObject.Properties[$field]) {
      Stop-Security 'PROVISIONING_MARKER_INVALID' "Marker thiếu trường bắt buộc: $field"
    }
  }
  if ((Get-Field $Candidate 'schema') -ne 'qq.antigravity.worker.marker.v2' -or (Get-Field $Candidate 'mode') -ne 'isolated-test-only') {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Marker không đúng schema hoặc mode test.'
  }
  try { [guid]::Parse([string](Get-Field $Candidate 'provisioning_id')) | Out-Null } catch { Stop-Security 'PROVISIONING_MARKER_INVALID' 'provisioning_id trong marker không hợp lệ.' }
  if ([string](Get-Field $Candidate 'worker_account_name') -ne $WorkerAccount -or [string](Get-Field $Candidate 'service_name') -ne $ServiceName) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' 'Tên account/service truyền vào không khớp marker; không dùng tên caller làm quyền xóa.'
  }
  if ((Convert-ToAbsolutePath ([string](Get-Field $Candidate 'control_root'))) -ne $ResolvedControlRoot -or
      (Convert-ToAbsolutePath ([string](Get-Field $Candidate 'worktree_root'))) -ne $ResolvedWorkerRoot -or
      (Convert-ToAbsolutePath ([string](Get-Field $Candidate 'service_data_root'))) -ne $ResolvedServiceDataRoot) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' 'Marker không khớp đúng ba root đang được yêu cầu.'
  }
  $manifest = Assert-ManifestPath $ResolvedServiceDataRoot ([string](Get-Field $Candidate 'manifest_path'))
  if ($manifest -ne (Convert-ToAbsolutePath ([string](Get-Field $Candidate 'manifest_path')))) {
    Stop-Security 'PROVISIONING_PATH_ESCAPE' 'manifest_path không canonical hoặc nằm ngoài SERVICE_DATA_ROOT.'
  }
  if ((@($Candidate.completed_steps) -contains 'ACCOUNT_CREATED') -and [string]::IsNullOrWhiteSpace([string](Get-Field $Candidate 'worker_sid'))) {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Marker ghi account đã tạo nhưng không có worker SID.'
  }
  if ((@($Candidate.completed_steps) -contains 'MANIFEST_WRITTEN') -and [string]::IsNullOrWhiteSpace([string](Get-Field $Candidate 'manifest_sha256'))) {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Marker ghi manifest đã tạo nhưng không có manifest hash.'
  }
}

function Assert-AccountOwnership {
  $expectedName = [string](Get-Field $Marker 'worker_account_name')
  $account = Get-LocalUser -Name $expectedName -ErrorAction SilentlyContinue
  if (-not $account) {
    return
  }
  $expectedSid = [string](Get-Field $Marker 'worker_sid')
  Assert-AccountSidOwnership $expectedName $account.Name $expectedSid $account.SID.Value ([bool](Get-Field $Marker 'worker_account_created_by_provisioning'))
  Assert-WorkerGroupMembership $expectedName
  try {
    $adminSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $adminGroup = $adminSid.Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
    $isAdmin = @(Get-LocalGroupMember -Group $adminGroup -ErrorAction Stop | Where-Object { $_.SID.Value -eq $expectedSid }).Count -gt 0
  } catch {
    Stop-Security 'ACL_VERIFICATION_FAILED' 'Không thể xác minh account có nằm trong Administrators hay không.'
  }
  if ($isAdmin) {
    Stop-Security 'WORKER_ACCOUNT_UNMANAGED' 'Không xóa account đang là thành viên Administrators.'
  }
}

function Assert-ManifestOwnership {
  $path = Convert-ToAbsolutePath ([string](Get-Field $Marker 'manifest_path'))
  if (-not (Test-Step 'MANIFEST_WRITTEN')) {
    if (Test-Path -LiteralPath $path) {
      Stop-Security 'PROVISIONING_MARKER_INVALID' "Manifest tồn tại nhưng marker không ghi nhận nó do instance này tạo: $path"
    }
    return
  }
  $expectedHash = [string](Get-Field $Marker 'manifest_sha256')
  Assert-ManifestHash $path $expectedHash
}

function Write-RollbackMarker {
  Write-RollbackMarkerObject -Marker $Marker -Path $MarkerPath -ServiceRemoved $ServiceRemoved -WorkerAccountRemoved $WorkerAccountRemoved -TestDataRemoved $TestDataRemoved
}

try {
  if ($WorkerAccount -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,19}$') {
    Stop-Security 'INVALID_INPUT' 'WorkerAccount không hợp lệ'
  }
  if ($ServiceName -notmatch '^[A-Za-z0-9_.-]+$') {
    Stop-Security 'INVALID_INPUT' 'ServiceName chứa ký tự không an toàn'
  }
  $resolvedControlRoot = Assert-TestRoot $ControlRoot 'CONTROL_ROOT'
  $resolvedWorkerRoot = Assert-TestRoot $WorkerRoot 'WORKTREE_ROOT'
  $resolvedServiceDataRoot = Assert-TestRoot $ServiceDataRoot 'SERVICE_DATA_ROOT'
  $roots = @($resolvedControlRoot, $resolvedWorkerRoot, $resolvedServiceDataRoot)
  for ($i = 0; $i -lt $roots.Count; $i++) {
    for ($j = $i + 1; $j -lt $roots.Count; $j++) {
      if ((Test-SameOrInside $roots[$i] $roots[$j]) -or (Test-SameOrInside $roots[$j] $roots[$i])) {
        Stop-Security 'PROVISIONING_PATH_ESCAPE' 'Các root rollback phải tách rời nhau.'
      }
    }
  }

  if ($DryRun) {
    [ordered]@{
      status = 'DRY_RUN_ONLY'
      service = "validate marker ownership, live SCM configuration, binary path/hash and worker SID before deleting $ServiceName"
      roots = @($resolvedControlRoot, $resolvedWorkerRoot, $resolvedServiceDataRoot)
      remove_test_data = [bool]$RemoveTestData
      remove_worker_account = [bool]$RemoveWorkerAccount
      safety = 'no service, account, ACL, right or file is changed by DryRun'
    } | ConvertTo-Json -Depth 6
    return
  }

  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Stop-Security 'ADMIN_REQUIRED' 'Cần chạy rollback bằng PowerShell Administrator.'
  }

  $MarkerPath = Join-Path $resolvedControlRoot $MarkerName
  if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
    $liveService = Get-LiveService $ServiceName
    $account = Get-LocalUser -Name $WorkerAccount -ErrorAction SilentlyContinue
    $managedRoots = @($resolvedControlRoot, $resolvedWorkerRoot, $resolvedServiceDataRoot)
    $anyRoot = @($managedRoots | Where-Object { Test-Path -LiteralPath $_ }).Count -gt 0
    if (-not $liveService -and -not $account -and -not $anyRoot) {
      [ordered]@{ status = 'ALREADY_UNPROVISIONED'; reason = 'marker and all managed resources are absent' } | ConvertTo-Json -Depth 5
      return
    }
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Không có marker cố định; không được đoán resource để xóa.'
  }

  Assert-NoReparseInPath $resolvedControlRoot
  Assert-ManagedAcl $resolvedControlRoot 'CONTROL_ROOT' $WorkerIdentity
  $Marker = Get-Content -Raw -LiteralPath $MarkerPath | ConvertFrom-Json
  Assert-Marker $Marker $resolvedControlRoot $resolvedWorkerRoot $resolvedServiceDataRoot
  if ((Get-Field $Marker 'service_logon_right_granted_by_provisioning') -eq $true) {
    Stop-Security 'SERVICE_LOGON_RIGHT_FAILED' 'Marker cho biết SeServiceLogonRight đã được cấp nhưng rollback chưa có provider SID/LSA an toàn để thu hồi.'
  }

  $markerAccountName = [string](Get-Field $Marker 'worker_account_name')
  $markerAccount = Get-LocalUser -Name $markerAccountName -ErrorAction SilentlyContinue
  Assert-RollbackDeletionPreconditions -RemoveTestData:$RemoveTestData -RemoveWorkerAccount:$RemoveWorkerAccount -WorkerAccountPresent:($null -ne $markerAccount) -RemovalSafetyProof $null

  $liveService = Get-LiveService ([string](Get-Field $Marker 'service_name'))
  if ($liveService) {
    Assert-ServiceOwnership $Marker $liveService
  }

  if ($RemoveWorkerAccount) {
    Assert-AccountOwnership
  }

  if ($RemoveTestData) {
    $rootSpecs = @(
      [pscustomobject]@{ Path = $resolvedControlRoot; Label = 'CONTROL_ROOT'; Step = 'CONTROL_ROOT_CREATED'; AllowWorkerWrite = $false; AllowWorkerTraverse = $false },
      [pscustomobject]@{ Path = $resolvedWorkerRoot; Label = 'WORKTREE_ROOT'; Step = 'WORKTREE_ROOT_CREATED'; AllowWorkerWrite = $false; AllowWorkerTraverse = $true },
      [pscustomobject]@{ Path = $resolvedServiceDataRoot; Label = 'SERVICE_DATA_ROOT'; Step = 'SERVICE_DATA_ROOT_CREATED'; AllowWorkerWrite = $true; AllowWorkerTraverse = $false }
    )
    foreach ($spec in $rootSpecs) {
      if (Test-Path -LiteralPath $spec.Path) {
        if (-not (Test-Step $spec.Step)) {
          Stop-Security 'ROLLBACK_OWNERSHIP_UNPROVEN' "$($spec.Label) tồn tại nhưng marker không ghi nhận resource do instance này tạo."
        }
        Assert-ManagedRoot $spec.Path $spec.Label $WorkerIdentity -AllowWorkerWrite:$spec.AllowWorkerWrite -AllowWorkerTraverse:$spec.AllowWorkerTraverse -RequireExisting
      }
    }
    if (Test-Path -LiteralPath $resolvedServiceDataRoot) {
      Assert-ManifestOwnership
    }
  }

  if ($liveService) {
    if ([string]$liveService.State -ne 'Stopped') {
      Stop-Service -Name ([string](Get-Field $Marker 'service_name')) -Force -ErrorAction Stop
    }
    & sc.exe delete ([string](Get-Field $Marker 'service_name')) | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-LiveService ([string](Get-Field $Marker 'service_name')))) {
      Stop-Security 'SERVICE_CONFIG_MISMATCH' 'SCM không xóa được service sau khi đã xác minh ownership.'
    }
    $ServiceRemoved = $true
  }

  if ($RemoveWorkerAccount) {
    $accountName = [string](Get-Field $Marker 'worker_account_name')
    $account = Get-LocalUser -Name $accountName -ErrorAction SilentlyContinue
    if ($account) {
      Assert-AccountOwnership
      Remove-LocalUser -Name $accountName -ErrorAction Stop
      $WorkerAccountRemoved = $true
    }
  }

  if ($RemoveTestData) {
    $manifestPath = Convert-ToAbsolutePath ([string](Get-Field $Marker 'manifest_path'))
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
      Assert-ManifestOwnership
      Remove-Item -LiteralPath $manifestPath -Force -ErrorAction Stop
      $TestDataRemoved = $true
    }
    foreach ($root in @($resolvedServiceDataRoot, $resolvedWorkerRoot, $resolvedControlRoot)) {
      if (Test-Path -LiteralPath $root) {
        $allowWorkerWrite = $root -eq $resolvedServiceDataRoot
        $allowWorkerTraverse = $root -eq $resolvedWorkerRoot
        Assert-ManagedRoot $root $root $WorkerIdentity -AllowWorkerWrite:$allowWorkerWrite -AllowWorkerTraverse:$allowWorkerTraverse -RequireExisting
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop
        $TestDataRemoved = $true
      }
    }
    foreach ($root in @($resolvedServiceDataRoot, $resolvedWorkerRoot, $resolvedControlRoot)) {
      if (Test-Path -LiteralPath $root) {
        Stop-Security 'ROLLBACK_OWNERSHIP_UNPROVEN' "Không xác nhận được root đã bị xóa: $root"
      }
    }
  } else {
    Write-RollbackMarker
  }

  [ordered]@{
    status = if ($RemoveTestData -and $RemoveWorkerAccount) { 'UNPROVISIONED_TEST_BOUNDARY' } else { 'ROLLED_BACK_PARTIAL' }
    service_removed = [bool]$ServiceRemoved
    test_data_removed = [bool]$TestDataRemoved
    worker_account_removed = [bool]$WorkerAccountRemoved
  } | ConvertTo-Json -Depth 6
} catch {
  [ordered]@{
    status = 'FAILED'
    error_code = Get-SecurityErrorCode $_
    message = $_.Exception.Message
    marker = $MarkerPath
  } | ConvertTo-Json -Depth 8
  exit 1
}
