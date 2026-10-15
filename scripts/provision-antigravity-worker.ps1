#requires -Version 5.1
[CmdletBinding()]
param(
  [string]$ControlRoot = 'F:\AI-Harness-Control-Test',
  [string]$WorkerRoot = 'F:\AI-Worker-Test',
  [string]$ServiceDataRoot = 'C:\ProgramData\QQ\AntigravityWorker-Test',
  [string]$WorkerAccount = 'AIWorker',
  [string]$ServiceName = 'QQAntigravityWorker-Test',
  [string]$ServiceExecutable = '',
  [string]$AntigravityExecutable = 'C:\Program Files\QQ\AntigravityWorker\agy.exe',
  [string]$AntigravityExecutableSha256 = '',
  [string]$HarnessAccount = '',
  [string]$ManifestPath = '',
  [switch]$RegisterService,
  [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\provisioning-safety.ps1')

if ([string]::IsNullOrWhiteSpace($HarnessAccount)) {
  $HarnessAccount = "$env:USERDOMAIN\$env:USERNAME"
}

$WorkerIdentity = "$env:COMPUTERNAME\$WorkerAccount"
$MarkerName = 'qq-antigravity-worker-test.marker.json'
$ProvisioningId = [guid]::NewGuid().Guid
$MarkerPath = $null
$ManifestPathResolved = $null
$ServiceBinaryInfo = $null
$AntigravityBinaryInfo = $null
$ScriptTransaction = [ordered]@{
  schema = 'qq.antigravity.worker.marker.v2'
  mode = 'isolated-test-only'
  provisioning_id = $ProvisioningId
  created_at = (Get-Date).ToUniversalTime().ToString('o')
  updated_at = (Get-Date).ToUniversalTime().ToString('o')
  worker_account_name = $WorkerAccount
  worker_sid = $null
  worker_account_created_by_provisioning = $false
  service_name = $ServiceName
  service_account = $WorkerIdentity
  service_binary_path = $null
  service_binary_sha256 = $null
  antigravity_executable_path = $null
  antigravity_executable_sha256 = $null
  service_created_by_provisioning = $false
  control_root = $null
  worktree_root = $null
  service_data_root = $null
  manifest_path = $null
  manifest_sha256 = $null
  service_logon_right_was_present_before = $null
  service_logon_right_granted_by_provisioning = $false
  completed_steps = @('INITIALIZED')
  status = 'INITIALIZED'
}

function Test-Step {
  param([Parameter(Mandatory = $true)][string]$Step)
  return @($ScriptTransaction.completed_steps) -contains $Step
}

function Write-TransactionMarker {
  if ([string]::IsNullOrWhiteSpace($script:MarkerPath)) {
    return
  }
  $ScriptTransaction.updated_at = (Get-Date).ToUniversalTime().ToString('o')
  $tempPath = "$script:MarkerPath.$ProvisioningId.tmp"
  $ScriptTransaction | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tempPath -Encoding UTF8
  Move-Item -LiteralPath $tempPath -Destination $script:MarkerPath -Force
}

function Add-Step {
  param([Parameter(Mandatory = $true)][string]$Step)
  if (-not (Test-Step $Step)) {
    $ScriptTransaction.completed_steps = @($ScriptTransaction.completed_steps + $Step)
  }
  Write-TransactionMarker
}

function New-EmptyDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (Test-Path -LiteralPath $Path) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' "Thư mục đã tồn tại; không được ghi đè dữ liệu: $Path"
  }
  [IO.Directory]::CreateDirectory($Path) | Out-Null
}

function Assert-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Stop-Security 'ADMIN_REQUIRED' 'Cần chạy script trong PowerShell đã được nâng quyền Administrator.'
  }
}

function New-AccessRule {
  param(
    [string]$Identity,
    [System.Security.AccessControl.FileSystemRights]$Rights,
    [System.Security.AccessControl.InheritanceFlags]$Inheritance = [System.Security.AccessControl.InheritanceFlags]::None,
    [System.Security.AccessControl.PropagationFlags]$Propagation = [System.Security.AccessControl.PropagationFlags]::None
  )
  return [System.Security.AccessControl.FileSystemAccessRule]::new(
    $Identity,
    $Rights,
    $Inheritance,
    $Propagation,
    [System.Security.AccessControl.AccessControlType]::Allow
  )
}

function Set-IsolatedAcl {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$AllowWorkerModify,
    [switch]$AllowWorkerTraverse
  )

  $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) {
    $acl.RemoveAccessRuleAll($rule) | Out-Null
  }

  $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
  $full = [System.Security.AccessControl.FileSystemRights]::FullControl
  $acl.AddAccessRule((New-AccessRule 'NT AUTHORITY\SYSTEM' $full $inherit))
  $acl.AddAccessRule((New-AccessRule 'BUILTIN\Administrators' $full $inherit))
  $acl.AddAccessRule((New-AccessRule $HarnessAccount $full $inherit))

  if ($AllowWorkerModify) {
    $acl.AddAccessRule((New-AccessRule $WorkerIdentity ([System.Security.AccessControl.FileSystemRights]::Modify) $inherit))
  } elseif ($AllowWorkerTraverse) {
    $acl.AddAccessRule((New-AccessRule $WorkerIdentity ([System.Security.AccessControl.FileSystemRights]::Traverse)))
  }

  Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
  Assert-ManagedAcl $Path $Path $WorkerIdentity -TrustedIdentities @($HarnessAccount) -AllowWorkerWrite:$AllowWorkerModify -AllowWorkerTraverse:$AllowWorkerTraverse
}

function Assert-ServiceLogonRightProvider {
  Stop-Security 'SERVICE_LOGON_RIGHT_FAILED' 'Chưa có provider LSA/SID đã được review để cấp và xác minh SeServiceLogonRight; provisioning phải fail-closed.'
}

function Assert-ExistingMarkerIdentity {
  param(
    [Parameter(Mandatory = $true)][object]$Marker,
    [Parameter(Mandatory = $true)][string]$ResolvedControlRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedWorkerRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedServiceDataRoot
  )

  if ((Get-Field $Marker 'schema') -ne 'qq.antigravity.worker.marker.v2' -or (Get-Field $Marker 'mode') -ne 'isolated-test-only') {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Marker không đúng schema hoặc mode test.'
  }
  try { [guid]::Parse([string](Get-Field $Marker 'provisioning_id')) | Out-Null } catch { Stop-Security 'PROVISIONING_MARKER_INVALID' 'provisioning_id trong marker không hợp lệ.' }
  if ((Convert-ToAbsolutePath ([string](Get-Field $Marker 'control_root'))) -ne $ResolvedControlRoot -or
      (Convert-ToAbsolutePath ([string](Get-Field $Marker 'worktree_root'))) -ne $ResolvedWorkerRoot -or
      (Convert-ToAbsolutePath ([string](Get-Field $Marker 'service_data_root'))) -ne $ResolvedServiceDataRoot) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' 'Marker không khớp đúng ba root đang được yêu cầu.'
  }
  if ([string](Get-Field $Marker 'worker_account_name') -ne $WorkerAccount -or [string](Get-Field $Marker 'service_name') -ne $ServiceName) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' 'Marker không khớp account/service đang được yêu cầu.'
  }
}

function Assert-CompletedProvisioning {
  param(
    [Parameter(Mandatory = $true)][object]$ExistingMarker,
    [Parameter(Mandatory = $true)][string]$ResolvedControlRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedWorkerRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedServiceDataRoot
  )

  $requiredSteps = @(
    'CONTROL_ROOT_CREATED',
    'ACCOUNT_CREATED',
    'WORKTREE_ROOT_CREATED',
    'SERVICE_DATA_ROOT_CREATED',
    'CONTROL_LAYOUT_CREATED',
    'ACL_APPLIED',
    'MANIFEST_WRITTEN',
    'WORKER_DATA_WRITE_GRANTED',
    'PROVISIONED'
  )
  foreach ($step in $requiredSteps) {
    if (@(Get-Field $ExistingMarker 'completed_steps') -notcontains $step) {
      Stop-Security 'PROVISIONING_MARKER_INVALID' "Marker PROVISIONED thiếu completed step: $step"
    }
  }

  Assert-ManagedRoot $ResolvedControlRoot 'CONTROL_ROOT' $WorkerIdentity -TrustedIdentities @($HarnessAccount) -RequireExisting
  Assert-ManagedRoot $ResolvedWorkerRoot 'WORKTREE_ROOT' $WorkerIdentity -TrustedIdentities @($HarnessAccount) -AllowWorkerTraverse -RequireExisting
  Assert-ManagedRoot $ResolvedServiceDataRoot 'SERVICE_DATA_ROOT' $WorkerIdentity -TrustedIdentities @($HarnessAccount) -AllowWorkerWrite -RequireExisting

  if ((Get-Field $ExistingMarker 'worker_account_created_by_provisioning') -ne $true) {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Marker hoàn tất nhưng không chứng minh account do instance này tạo.'
  }
  $account = Get-LocalUser -Name $WorkerAccount -ErrorAction SilentlyContinue
  if (-not $account -or $account.SID.Value -ne [string](Get-Field $ExistingMarker 'worker_sid')) {
    Stop-Security 'WORKER_ACCOUNT_SID_MISMATCH' 'Account hiện tại không khớp SID trong marker; không nhận diện là cùng instance.'
  }
  Assert-WorkerGroupMembership $WorkerAccount

  if (@(Get-Field $ExistingMarker 'completed_steps') -contains 'MANIFEST_WRITTEN') {
    $manifest = Convert-ToAbsolutePath ([string](Get-Field $ExistingMarker 'manifest_path'))
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf) -or (Get-FileSha256 $manifest) -ne ([string](Get-Field $ExistingMarker 'manifest_sha256')).ToUpperInvariant()) {
      Stop-Security 'PROVISIONING_MARKER_INVALID' 'Manifest không tồn tại hoặc hash không khớp marker.'
    }
  }

  $liveService = Get-LiveService $ServiceName
  if ((Get-Field $ExistingMarker 'service_created_by_provisioning') -eq $true) {
    if (-not $liveService) {
      Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Marker ghi service đã tạo nhưng SCM không còn service đó.'
    }
    Assert-ServiceOwnership $ExistingMarker $liveService
  } elseif ($liveService) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Có service cùng tên nhưng marker không chứng minh do instance này tạo.'
  }
}

function Get-Plan {
  param(
    [Parameter(Mandatory = $true)][string]$ResolvedControlRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedWorkerRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedServiceDataRoot,
    [Parameter(Mandatory = $true)][string]$ResolvedManifestPath
  )

  return [ordered]@{
    schema = 'qq.antigravity.worker.provision.v2'
    mode = 'isolated-test-only'
    provisioning_id = $ProvisioningId
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    user = [ordered]@{
      harness = $HarnessAccount
      worker = $WorkerIdentity
      worker_account_name = $WorkerAccount
      worker_sid = $ScriptTransaction.worker_sid
      worker_is_admin = $false
      worker_account_created_by_provisioning = [bool]$ScriptTransaction.worker_account_created_by_provisioning
      plaintext_password_in_harness = $false
      password_never_expires = $false
      password_rotation = 'operator-managed; this script never stores or rotates plaintext credentials'
    }
    service = [ordered]@{
      name = $ServiceName
      account = $WorkerIdentity
      executable = if ($ServiceBinaryInfo) { $ServiceBinaryInfo.path } else { $null }
      executable_sha256 = if ($ServiceBinaryInfo) { $ServiceBinaryInfo.sha256 } else { $null }
      antigravity_executable = if ($AntigravityBinaryInfo) { $AntigravityBinaryInfo.path } else { $null }
      antigravity_executable_sha256 = if ($AntigravityBinaryInfo) { $AntigravityBinaryInfo.sha256 } else { $null }
      register = [bool]$RegisterService
      created_by_provisioning = [bool]$ScriptTransaction.service_created_by_provisioning
      startup = 'Manual; never auto-start during provisioning'
      service_manager_holds_logon_secret = [bool]$RegisterService
      service_logon_right_provider = 'FAIL_CLOSED_UNIMPLEMENTED'
    }
    directories = [ordered]@{
      control_root = $ResolvedControlRoot
      worktree_root = $ResolvedWorkerRoot
      service_data_root = $ResolvedServiceDataRoot
    }
    manifest = [ordered]@{
      path = $ResolvedManifestPath
      sha256 = $ScriptTransaction.manifest_sha256
    }
    transaction = [ordered]@{
      completed_steps = @($ScriptTransaction.completed_steps)
      marker_path = $MarkerPath
    }
    acl = [ordered]@{
      parent_preflight = 'broad write principals and reparse paths are rejected before mutation'
      control_root = 'SYSTEM, BUILTIN\\Administrators and Harness Full; AIWorker no write'
      worktree_root = 'SYSTEM, BUILTIN\\Administrators and Harness Full; AIWorker traverse only'
      assigned_task = 'AIWorker Modify only on an explicitly assigned task directory'
      service_data = 'AIWorker Modify; Harness, SYSTEM and Administrators Full'
      broad_entries = 'forbidden: Everyone, Users, Authenticated Users, INTERACTIVE with write rights'
    }
    firewall = 'NONE; future IPC must remain local-only'
    windows_features = 'NONE'
    credentials = 'No password in source, JSON, environment, CLI arguments or workflow state; SecureString is consumed only by Windows APIs'
    antigravity_auth = 'Separate AIWorker profile/credential provisioning remains required; QQ profile and credential store are never copied'
  }
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
  $ManifestPathResolved = Assert-ManifestPath $resolvedServiceDataRoot $ManifestPath

  $roots = @(
    [pscustomobject]@{ Name = 'CONTROL_ROOT'; Path = $resolvedControlRoot },
    [pscustomobject]@{ Name = 'WORKTREE_ROOT'; Path = $resolvedWorkerRoot },
    [pscustomobject]@{ Name = 'SERVICE_DATA_ROOT'; Path = $resolvedServiceDataRoot }
  )
  for ($i = 0; $i -lt $roots.Count; $i++) {
    for ($j = $i + 1; $j -lt $roots.Count; $j++) {
      if ((Test-SameOrInside $roots[$i].Path $roots[$j].Path) -or (Test-SameOrInside $roots[$j].Path $roots[$i].Path)) {
        Stop-Security 'PROVISIONING_PATH_ESCAPE' "$($roots[$i].Name), $($roots[$j].Name) phải tách rời nhau"
      }
    }
  }

  if ($RegisterService) {
    if ([string]::IsNullOrWhiteSpace($ServiceExecutable)) {
      Stop-Security 'SERVICE_BINARY_UNTRUSTED' 'RegisterService cần ServiceExecutable là native worker service binary đã được review.'
    }
    $ServiceBinaryInfo = Assert-TrustedServiceBinary $ServiceExecutable $WorkerIdentity
    $approvedAntigravityPath = Convert-ToAbsolutePath 'C:\Program Files\QQ\AntigravityWorker\agy.exe'
    $resolvedAntigravityPath = Convert-ToAbsolutePath $AntigravityExecutable
    if ($resolvedAntigravityPath -ne $approvedAntigravityPath) {
      Stop-Security 'SERVICE_BINARY_UNTRUSTED' "AntigravityExecutable phải dùng đường dẫn cố định đã duyệt: $approvedAntigravityPath"
    }
    if ($AntigravityExecutableSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
      Stop-Security 'SERVICE_BINARY_UNTRUSTED' 'AntigravityExecutable cần SHA-256 đã được duyệt trước khi đăng ký service.'
    }
    $AntigravityBinaryInfo = Assert-TrustedServiceBinary $resolvedAntigravityPath $WorkerIdentity
    if ($AntigravityBinaryInfo.sha256 -ne $AntigravityExecutableSha256.ToUpperInvariant()) {
      Stop-Security 'SERVICE_BINARY_UNTRUSTED' 'SHA-256 AntigravityExecutable không khớp bằng chứng đã duyệt.'
    }
    Assert-ServiceLogonRightProvider
  }

  $ScriptTransaction.control_root = $resolvedControlRoot
  $ScriptTransaction.worktree_root = $resolvedWorkerRoot
  $ScriptTransaction.service_data_root = $resolvedServiceDataRoot
  $ScriptTransaction.manifest_path = $ManifestPathResolved
  if ($ServiceBinaryInfo) {
    $ScriptTransaction.service_binary_path = $ServiceBinaryInfo.path
    $ScriptTransaction.service_binary_sha256 = $ServiceBinaryInfo.sha256
  }
  if ($AntigravityBinaryInfo) {
    $ScriptTransaction.antigravity_executable_path = $AntigravityBinaryInfo.path
    $ScriptTransaction.antigravity_executable_sha256 = $AntigravityBinaryInfo.sha256
  }

  $plan = Get-Plan $resolvedControlRoot $resolvedWorkerRoot $resolvedServiceDataRoot $ManifestPathResolved
  if ($DryRun) {
    $plan | ConvertTo-Json -Depth 12
    return
  }

  Assert-Administrator

  $controlExists = Test-Path -LiteralPath $resolvedControlRoot
  $workerRootExists = Test-Path -LiteralPath $resolvedWorkerRoot
  $serviceDataExists = Test-Path -LiteralPath $resolvedServiceDataRoot
  if ($controlExists -or $workerRootExists -or $serviceDataExists) {
    $candidateMarkerPath = Join-Path $resolvedControlRoot $MarkerName
    if ($controlExists -and (Test-Path -LiteralPath $candidateMarkerPath -PathType Leaf)) {
      $existingMarker = Get-Content -Raw -LiteralPath $candidateMarkerPath | ConvertFrom-Json
      Assert-ExistingMarkerIdentity $existingMarker $resolvedControlRoot $resolvedWorkerRoot $resolvedServiceDataRoot
      if (@(Get-Field $existingMarker 'completed_steps') -contains 'PROVISIONED') {
        Assert-CompletedProvisioning $existingMarker $resolvedControlRoot $resolvedWorkerRoot $resolvedServiceDataRoot
        [ordered]@{
          status = 'ALREADY_PROVISIONED'
          provisioning_id = [string](Get-Field $existingMarker 'provisioning_id')
          marker = $candidateMarkerPath
        } | ConvertTo-Json -Depth 8
        return
      }
    }
    Stop-Security 'PARTIAL_PROVISIONING_DETECTED' 'Một hoặc nhiều resource đã tồn tại nhưng không có marker hoàn tất khớp chính xác; không nhận diện hoặc xóa resource cũ.'
  }

  $existing = Get-LocalUser -Name $WorkerAccount -ErrorAction SilentlyContinue
  if ($existing) {
    Stop-Security 'WORKER_ACCOUNT_UNMANAGED' "Worker account đã tồn tại nhưng chưa chứng minh do provisioning instance này tạo: $WorkerAccount"
  }

  New-EmptyDirectory $resolvedControlRoot
  $MarkerPath = Join-Path $resolvedControlRoot $MarkerName
  $ScriptTransaction.status = 'PARTIAL'
  Set-IsolatedAcl $resolvedControlRoot
  Add-Step 'CONTROL_ROOT_CREATED'
  Add-Step 'CONTROL_ROOT_ACL_APPLIED'

  $securePassword = Read-Host "Nhập password mới cho $WorkerIdentity (không được ghi log)" -AsSecureString
  New-LocalUser -Name $WorkerAccount -Password $securePassword -AccountNeverExpires -UserMayNotChangePassword -Description 'Dedicated non-admin Antigravity worker; service logon only' -ErrorAction Stop | Out-Null
  $ScriptTransaction.worker_sid = (Get-LocalUser -Name $WorkerAccount -ErrorAction Stop).SID.Value
  $ScriptTransaction.worker_account_created_by_provisioning = $true
  Add-Step 'ACCOUNT_CREATED'
  Assert-WorkerGroupMembership $WorkerAccount

  New-EmptyDirectory $resolvedWorkerRoot
  Add-Step 'WORKTREE_ROOT_CREATED'
  New-EmptyDirectory $resolvedServiceDataRoot
  Add-Step 'SERVICE_DATA_ROOT_CREATED'

  foreach ($child in @('ai-control', 'tasks', 'audit', 'operations', 'receipts')) {
    New-EmptyDirectory (Join-Path $resolvedControlRoot $child)
  }
  Add-Step 'CONTROL_LAYOUT_CREATED'

  Set-IsolatedAcl $resolvedControlRoot
  foreach ($child in @('ai-control', 'tasks', 'audit', 'operations', 'receipts')) {
    Set-IsolatedAcl (Join-Path $resolvedControlRoot $child)
  }
  Set-IsolatedAcl $resolvedWorkerRoot -AllowWorkerTraverse
  Set-IsolatedAcl $resolvedServiceDataRoot
  Add-Step 'ACL_APPLIED'

  $manifestObject = Get-Plan $resolvedControlRoot $resolvedWorkerRoot $resolvedServiceDataRoot $ManifestPathResolved
  $manifestObject.transaction.completed_steps = @($ScriptTransaction.completed_steps)
  $manifestObject.transaction.marker_path = $MarkerPath
  $manifestObject | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ManifestPathResolved -Encoding UTF8
  $ScriptTransaction.manifest_sha256 = Get-FileSha256 $ManifestPathResolved
  Add-Step 'MANIFEST_WRITTEN'

  # Keep the ownership manifest immutable by AIWorker until its hash is in the trusted marker.
  Set-IsolatedAcl $resolvedServiceDataRoot -AllowWorkerModify
  Add-Step 'WORKER_DATA_WRITE_GRANTED'

  if ($RegisterService) {
    # The current provider deliberately fails closed before reaching this branch.
    $credential = [PSCredential]::new($WorkerIdentity, $securePassword)
    New-Service -Name $ServiceName -DisplayName 'QQ Antigravity restricted worker (test)' -Description 'Restricted worker service; manual start only' -BinaryPathName ('"{0}"' -f $ServiceBinaryInfo.path) -Credential $credential -StartupType Manual -ErrorAction Stop | Out-Null
    $ScriptTransaction.service_created_by_provisioning = $true
    Add-Step 'SERVICE_CREATED'
  }

  $ScriptTransaction.status = 'PROVISIONED'
  Add-Step 'PROVISIONED'
  [ordered]@{
    status = 'PROVISIONED_TEST_BOUNDARY'
    provisioning_id = $ProvisioningId
    marker = $MarkerPath
    manifest = $ManifestPathResolved
    service_registered = [bool]$RegisterService
    effective_permission_self_test = 'NOT_RUN'
  } | ConvertTo-Json -Depth 8
} catch {
  $code = Get-SecurityErrorCode $_
  if ($MarkerPath -and (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
    try {
      $ScriptTransaction.status = 'PARTIAL'
      $ScriptTransaction.failure = [ordered]@{
        code = $code
        message = $_.Exception.Message
        at = (Get-Date).ToUniversalTime().ToString('o')
      }
      Write-TransactionMarker
    } catch {
      # Preserve the original failure; a missing/invalid marker is itself fail-closed.
    }
  }
  [ordered]@{
    status = 'FAILED'
    error_code = $code
    message = $_.Exception.Message
    provisioning_id = $ProvisioningId
    marker = $MarkerPath
  } | ConvertTo-Json -Depth 8
  exit 1
}
