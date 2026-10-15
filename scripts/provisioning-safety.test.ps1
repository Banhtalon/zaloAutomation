#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\provisioning-safety.ps1')

$passed = 0

function Assert-Equal {
  param([object]$Actual, [object]$Expected, [string]$Name)
  if ($Actual -ne $Expected) {
    throw "${Name}: expected [$Expected], got [$Actual]"
  }
  $script:passed++
  Write-Output "PASS $Name"
}

function Assert-SecurityCode {
  param([scriptblock]$Action, [string]$ExpectedCode, [string]$Name)
  try {
    & $Action
    throw "Không fail-closed: $Name"
  } catch {
    $code = if ($_.Exception.Data.Contains('security_code')) { [string]$_.Exception.Data['security_code'] } else { $null }
    Assert-Equal $code $ExpectedCode $Name
  }
}

$manifestRoot = 'C:\ProgramData\AntigravityWorker-Test'
Assert-Equal (Assert-ManifestPath $manifestRoot "$manifestRoot\manifest.json") "$manifestRoot\manifest.json" 'manifest nằm trực tiếp trong service-data root'
Assert-SecurityCode { Assert-ManifestPath $manifestRoot 'C:\ProgramData\outside.json' } 'PROVISIONING_PATH_ESCAPE' 'manifest path خارج root'
Assert-SecurityCode { Assert-ManifestPath $manifestRoot "$manifestRoot\..\outside.json" } 'PROVISIONING_PATH_ESCAPE' 'manifest path traversal'
Assert-SecurityCode { Assert-TestRoot 'C:\Windows' 'CONTROL_ROOT' } 'PROVISIONING_PATH_ESCAPE' 'production root bị từ chối'
Assert-Equal (Convert-ToAbsolutePath 'C:\') ([IO.Path]::GetPathRoot('C:\')) 'drive root vẫn giữ dấu gạch cuối'
Assert-Equal (Test-SameOrInside 'C:\' 'C:\Windows') $true 'containment của drive root hoạt động'

$dryRunCases = @(
  [pscustomobject]@{
    Name = 'provision default dry-run'
    Script = (Join-Path $PSScriptRoot 'provision-antigravity-worker.ps1')
    Arguments = @('-DryRun')
  },
  [pscustomobject]@{
    Name = 'unprovision default dry-run'
    Script = (Join-Path $PSScriptRoot 'unprovision-antigravity-worker.ps1')
    Arguments = @('-DryRun', '-RemoveTestData')
  }
)
foreach ($dryRunCase in $dryRunCases) {
  $dryRunOutput = (& pwsh -NoLogo -NoProfile -File $dryRunCase.Script @($dryRunCase.Arguments) 2>&1 | Out-String).Trim()
  $dryRunExit = $LASTEXITCODE
  try {
    $dryRunResult = $dryRunOutput | ConvertFrom-Json
  } catch {
    throw "$($dryRunCase.Name) không trả JSON: $dryRunOutput"
  }
  $dryRunCode = Get-Field $dryRunResult 'error_code'
  if ($dryRunExit -ne 0 -and $dryRunCode -eq 'PROVISIONING_FAILED') {
    throw "$($dryRunCase.Name) vẫn trả lỗi đường dẫn chung chung: $dryRunOutput"
  }
  $passed++
  Write-Output "PASS $($dryRunCase.Name) trả kết quả có cấu trúc"
}

$partialRollbackRoot = Join-Path ([IO.Path]::GetTempPath()) ('qq-partial-rollback-' + [guid]::NewGuid().Guid)
[IO.Directory]::CreateDirectory($partialRollbackRoot) | Out-Null
$partialMarkerPath = Join-Path $partialRollbackRoot 'marker.json'
Set-Content -LiteralPath $partialMarkerPath -Value '{"schema":"qq.antigravity.worker.marker.v2"}' -Encoding UTF8
$deleteProbe = [pscustomobject]@{ Data = 0; Account = 0 }
try {
  Assert-SecurityCode {
    Assert-RollbackDeletionPreconditions -RemoveTestData -WorkerAccountPresent $true -RemovalSafetyProof $null
    $deleteProbe.Data++
    Remove-Item -LiteralPath $partialMarkerPath -Force
  } 'ROLLBACK_OWNERSHIP_UNPROVEN' 'partial rollback giữ marker khi account còn'
  Assert-Equal (Test-Path -LiteralPath $partialMarkerPath -PathType Leaf) $true 'partial rollback không xóa marker'
  Assert-Equal $deleteProbe.Account 0 'partial rollback không tự xóa account'

  Assert-RollbackDeletionPreconditions -RemoveWorkerAccount -WorkerAccountPresent $true -RemovalSafetyProof $null
  $deleteProbe.Account++
  Assert-Equal $deleteProbe.Data 0 'lần gỡ tiếp theo không xóa dữ liệu ngoài yêu cầu'
  Assert-Equal (Test-Path -LiteralPath $partialMarkerPath -PathType Leaf) $true 'marker còn cho lần gỡ tiếp theo'

  $unsafeProof = [pscustomobject]@{
    status = 'VERIFIED_EXCLUSIVE_REMOVAL'
    exclusive_delete_lock = $true
    worker_stopped = $true
    active_handles = 0
    root_fingerprint_before = 'before'
    root_fingerprint_after = 'after'
  }
  Assert-SecurityCode {
    Assert-RollbackDeletionPreconditions -RemoveTestData -RemoveWorkerAccount -WorkerAccountPresent $false -RemovalSafetyProof $unsafeProof
    $deleteProbe.Data++
  } 'ROLLBACK_SAFE_STOP_UNAVAILABLE' 'cây thay đổi sau kiểm tra phải dừng trước xóa'
  Assert-Equal $deleteProbe.Data 0 'nhánh nguy hiểm không gọi xóa'
} finally {
  if (Test-Path -LiteralPath $partialRollbackRoot) {
    Remove-Item -LiteralPath $partialRollbackRoot -Recurse -Force
  }
}

$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$broadRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
  'Everyone',
  [System.Security.AccessControl.FileSystemRights]::FullControl,
  [System.Security.AccessControl.InheritanceFlags]::ContainerInherit,
  [System.Security.AccessControl.PropagationFlags]::None,
  [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.AddAccessRule($broadRule)
Assert-Equal (@(Get-BroadWriteRules $acl).Count) 1 'broad write ACL bị nhận diện'
Assert-Equal (Test-WritableRights ([System.Security.AccessControl.FileSystemRights]::Read)) $false 'Read không bị nhận nhầm là writable'
Assert-Equal (Test-WritableRights ([System.Security.AccessControl.FileSystemRights]::Traverse)) $false 'Traverse không bị nhận nhầm là writable'
Assert-Equal (Test-WritableRights ([System.Security.AccessControl.FileSystemRights]::Write)) $true 'Write được nhận diện'
Assert-Equal (Test-WritableRights ([System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles)) $true 'DeleteSubdirectoriesAndFiles được nhận diện'
Assert-Equal (Test-WritableRights ([System.Security.AccessControl.FileSystemRights]::FullControl)) $true 'FullControl được nhận diện'
Assert-Equal (Test-RightsSubset ([System.Security.AccessControl.FileSystemRights]::Modify) (
  [System.Security.AccessControl.FileSystemRights]::Modify -bor [System.Security.AccessControl.FileSystemRights]::Synchronize
)) $true 'Modify nằm trong quyền worker cho phép'
Assert-Equal (Test-RightsSubset ([System.Security.AccessControl.FileSystemRights]::FullControl) (
  [System.Security.AccessControl.FileSystemRights]::Modify -bor [System.Security.AccessControl.FileSystemRights]::Synchronize
)) $false 'FullControl không được coi là Modify hợp lệ'

$managedAclRule = [pscustomobject]@{
  IdentityReference = [pscustomobject]@{ Value = 'NT AUTHORITY\SYSTEM' }
  AccessControlType = [System.Security.AccessControl.AccessControlType]::Allow
  FileSystemRights = [System.Security.AccessControl.FileSystemRights]::FullControl
}
$workerIdentity = "$env:COMPUTERNAME\AIWorker"
$trustedOwnerFixture = [pscustomobject]@{
  AreAccessRulesProtected = $true
  Owner = 'NT AUTHORITY\SYSTEM'
  Access = @($managedAclRule)
}
Assert-ManagedAclObject -Acl $trustedOwnerFixture -Path 'managed-fixture' -Label 'managed-fixture' -WorkerIdentity $workerIdentity
$passed++
Write-Output 'PASS managed root owner tin cậy được chấp nhận'
$unknownOwnerFixture = [pscustomobject]@{
  AreAccessRulesProtected = $true
  Owner = 'NT AUTHORITY\INTERACTIVE'
  Access = @($managedAclRule)
}
Assert-SecurityCode {
  Assert-ManagedAclObject -Acl $unknownOwnerFixture -Path 'managed-fixture' -Label 'managed-fixture' -WorkerIdentity $workerIdentity
} 'ACL_VERIFICATION_FAILED' 'managed root owner lạ bị từ chối'
$workerOwnerFixture = [pscustomobject]@{
  AreAccessRulesProtected = $true
  Owner = $workerIdentity
  Access = @($managedAclRule)
}
Assert-SecurityCode {
  Assert-ManagedAclObject -Acl $workerOwnerFixture -Path 'managed-fixture' -Label 'managed-fixture' -WorkerIdentity $workerIdentity
} 'ACL_VERIFICATION_FAILED' 'managed root owner là worker bị từ chối'

$untrustedAcl = [System.Security.AccessControl.DirectorySecurity]::new()
$untrustedRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
  'NT AUTHORITY\INTERACTIVE',
  [System.Security.AccessControl.FileSystemRights]::Write,
  [System.Security.AccessControl.InheritanceFlags]::None,
  [System.Security.AccessControl.PropagationFlags]::None,
  [System.Security.AccessControl.AccessControlType]::Allow
)
$untrustedAcl.AddAccessRule($untrustedRule)
Assert-SecurityCode {
  Assert-NoUntrustedWritableAces -Acl $untrustedAcl -Label 'fixture'
} 'ACL_VERIFICATION_FAILED' 'writable ACE ngoài allowlist bị từ chối'
Assert-SecurityCode {
  Assert-ManagedRoot (Join-Path ([IO.Path]::GetTempPath()) 'qq-missing-worker-Test') 'WORKTREE_ROOT' "$env:COMPUTERNAME\AIWorker" -RequireExisting
} 'PROVISIONING_RESOURCE_MISMATCH' 'root bị mất phải làm rerun dừng'

Assert-SecurityCode {
  Assert-AccountSidOwnership 'AIWorker' 'AIWorker' 'S-1-5-21-expected' 'S-1-5-21-replaced' $true
} 'WORKER_ACCOUNT_SID_MISMATCH' 'cùng tên nhưng khác SID'
Assert-SecurityCode {
  Assert-AccountSidOwnership 'AIWorker' 'AIWorker' 'S-1-5-21-expected' 'S-1-5-21-expected' $false
} 'ROLLBACK_OWNERSHIP_UNPROVEN' 'account unmanaged không bị nhận nuôi'

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('qq-provisioning-safety-' + [guid]::NewGuid().Guid)
[IO.Directory]::CreateDirectory($fixtureRoot) | Out-Null
try {
  $binary = Join-Path $fixtureRoot 'worker.exe'
  $manifest = Join-Path $fixtureRoot 'manifest.json'
  Set-Content -LiteralPath $binary -Value 'approved-worker-fixture' -Encoding UTF8
  Set-Content -LiteralPath $manifest -Value 'approved-manifest-fixture' -Encoding UTF8
  $binaryHash = Get-FileSha256 $binary
  $manifestHash = Get-FileSha256 $manifest
  Assert-ManifestHash $manifest $manifestHash
  Set-Content -LiteralPath $manifest -Value 'tampered' -Encoding UTF8
  Assert-SecurityCode { Assert-ManifestHash $manifest $manifestHash } 'PROVISIONING_MARKER_INVALID' 'manifest bị sửa phải dừng'

  Set-Content -LiteralPath $manifest -Value 'approved-manifest-fixture' -Encoding UTF8
  $marker = [pscustomobject]@{
    service_created_by_provisioning = $true
    service_binary_path = $binary
    service_binary_sha256 = $binaryHash
    service_account = "$env:COMPUTERNAME\AIWorker"
  }
  $service = [pscustomobject]@{
    PathName = ('"{0}" --service' -f $binary)
    StartName = "$env:COMPUTERNAME\AIWorker"
  }
  Assert-ServiceOwnership $marker $service
  $badMarker = [pscustomobject]@{
    service_created_by_provisioning = $true
    service_binary_path = (Join-Path $fixtureRoot 'replaced-worker.exe')
    service_binary_sha256 = $binaryHash
    service_account = "$env:COMPUTERNAME\AIWorker"
  }
  Assert-SecurityCode { Assert-ServiceOwnership $badMarker $service } 'SERVICE_CONFIG_MISMATCH' 'service binary path bị thay thế'

  $rollbackMarkerPath = Join-Path $fixtureRoot 'rollback-marker.json'
  $rollbackMarker = [ordered]@{
    schema = 'qq.antigravity.worker.marker.v2'
    mode = 'isolated-test-only'
    provisioning_id = [guid]::NewGuid().Guid
    worker_account_name = 'AIWorker'
    worker_account_created_by_provisioning = $true
    service_name = 'QQAntigravityWorker-Test'
    service_account = "$env:COMPUTERNAME\AIWorker"
    service_created_by_provisioning = $true
    control_root = $fixtureRoot
    worktree_root = (Join-Path $fixtureRoot 'worktree')
    service_data_root = $fixtureRoot
    manifest_path = $manifest
    completed_steps = @('INITIALIZED', 'ACCOUNT_CREATED', 'MANIFEST_WRITTEN')
  } | ConvertTo-Json -Depth 12 | ConvertFrom-Json
  Write-RollbackMarkerObject -Marker $rollbackMarker -Path $rollbackMarkerPath -ServiceRemoved $false -WorkerAccountRemoved $false -TestDataRemoved $false
  $firstRollback = Get-Content -Raw -LiteralPath $rollbackMarkerPath | ConvertFrom-Json
  Assert-Equal ([bool]$firstRollback.rollback.service_removed) $false 'rollback marker lần đầu ghi được property mới'

  Write-RollbackMarkerObject -Marker $firstRollback -Path $rollbackMarkerPath -ServiceRemoved $true -WorkerAccountRemoved $false -TestDataRemoved $false
  $secondRollback = Get-Content -Raw -LiteralPath $rollbackMarkerPath | ConvertFrom-Json
  Assert-Equal ([bool]$secondRollback.rollback.service_removed) $true 'rollback marker lần hai cập nhật được property'
} finally {
  if (Test-Path -LiteralPath $fixtureRoot) {
    Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
  }
}

$rollbackSource = Get-Content -Raw (Join-Path $PSScriptRoot 'unprovision-antigravity-worker.ps1')
$provisionSource = Get-Content -Raw (Join-Path $PSScriptRoot 'provision-antigravity-worker.ps1')
$safetySource = Get-Content -Raw (Join-Path $PSScriptRoot 'lib\provisioning-safety.ps1')
foreach ($required in @(
  'Assert-ServiceOwnership',
  'Assert-AccountSidOwnership',
  'Assert-ManifestHash',
  'Assert-TrustedAclOwner',
  'Assert-ManagedAclObject',
  'Assert-RollbackDeletionPreconditions',
  'Assert-RollbackDeletionProof',
  'Write-JsonObjectPropertyAtomic',
  'Write-RollbackMarkerObject',
  'ROLLBACK_SAFE_STOP_UNAVAILABLE',
  'SERVICE_CONFIG_MISMATCH',
  'WORKER_ACCOUNT_SID_MISMATCH',
  'Assert-WorkerGroupMembership',
  'RequireExisting',
  'AntigravityExecutableSha256',
  '[IO.Directory]::CreateDirectory'
)) {
  if ((($rollbackSource + $provisionSource + $safetySource).IndexOf($required, [StringComparison]::Ordinal)) -lt 0) {
    throw "Rollback thiếu guard bắt buộc: $required"
  }
  $passed++
  Write-Output "PASS source guard $required"
}
if ($provisionSource -match 'New-Item\s+-ItemType\s+Directory\s+-LiteralPath') {
  throw 'Provision vẫn dùng New-Item -LiteralPath không tồn tại'
}
$passed++
Write-Output 'PASS provision không dùng New-Item -LiteralPath'
if ($rollbackSource -match '\$Marker\.rollback\s*=') {
  throw 'Rollback vẫn gán trực tiếp property không tồn tại trên marker'
}
$passed++
Write-Output 'PASS rollback không gán trực tiếp property JSON chưa tồn tại'
foreach ($source in @($provisionSource, $rollbackSource)) {
  if ($source -match 'Test-SameOrInside[^\r\n]*-or\s+Test-SameOrInside') {
    throw 'Kiểm tra root chồng lấn vẫn truyền -or như tham số hàm'
  }
}
$passed++
Write-Output 'PASS kiểm tra root chồng lấn có ngoặc an toàn'

foreach ($scriptFile in @(
  (Join-Path $PSScriptRoot 'provision-antigravity-worker.ps1'),
  (Join-Path $PSScriptRoot 'unprovision-antigravity-worker.ps1'),
  (Join-Path $PSScriptRoot 'lib\provisioning-safety.ps1'),
  $PSCommandPath
)) {
  $parseTokens = $null
  $parseErrors = $null
  [System.Management.Automation.Language.Parser]::ParseFile($scriptFile, [ref]$parseTokens, [ref]$parseErrors) | Out-Null
  if ($parseErrors.Count -ne 0) {
    throw "PowerShell parse failed for $scriptFile at line $($parseErrors[0].Extent.StartLineNumber): $($parseErrors[0].Message)"
  }
  $passed++
  Write-Output "PASS parse $([IO.Path]::GetFileName($scriptFile))"
}

Write-Output "provisioning safety tests: $passed passed"
