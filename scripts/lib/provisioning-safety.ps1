Set-StrictMode -Version Latest

function Stop-Security {
  param(
    [Parameter(Mandatory = $true)][string]$Code,
    [Parameter(Mandatory = $true)][string]$Message,
    [hashtable]$Details = @{}
  )

  $payload = [ordered]@{
    code = $Code
    message = $Message
    details = $Details
  }
  $exception = [System.InvalidOperationException]::new(($payload | ConvertTo-Json -Compress -Depth 8))
  $exception.Data['security_code'] = $Code
  throw $exception
}

function Get-SecurityErrorCode {
  param([Parameter(Mandatory = $true)][System.Management.Automation.ErrorRecord]$ErrorRecord)

  if ($ErrorRecord.Exception.Data.Contains('security_code')) {
    return [string]$ErrorRecord.Exception.Data['security_code']
  }
  return 'PROVISIONING_FAILED'
}

function Get-Field {
  param(
    [Parameter(Mandatory = $true)][object]$Object,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    return $null
  }
  return $property.Value
}

function Write-JsonObjectPropertyAtomic {
  param(
    [Parameter(Mandatory = $true)][object]$Object,
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$PropertyName,
    [Parameter(Mandatory = $true)][object]$Value
  )

  $tempPath = "$Path.$([guid]::NewGuid().Guid).tmp"
  try {
    $Object | Add-Member -MemberType NoteProperty -Name $PropertyName -Value $Value -Force
    $Object | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tempPath -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $tempPath) {
      Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
  }
}

function Write-RollbackMarkerObject {
  param(
    [Parameter(Mandatory = $true)][object]$Marker,
    [Parameter(Mandatory = $true)][string]$Path,
    [bool]$ServiceRemoved,
    [bool]$WorkerAccountRemoved,
    [bool]$TestDataRemoved
  )

  Write-JsonObjectPropertyAtomic -Object $Marker -Path $Path -PropertyName 'rollback' -Value ([ordered]@{
    updated_at = (Get-Date).ToUniversalTime().ToString('o')
    service_removed = [bool]$ServiceRemoved
    worker_account_removed = [bool]$WorkerAccountRemoved
    test_data_removed = [bool]$TestDataRemoved
  })
}

function Convert-ToAbsolutePath {
  param([Parameter(Mandatory = $true)][string]$Value)

  if ([string]::IsNullOrWhiteSpace($Value) -or -not [IO.Path]::IsPathRooted($Value)) {
    Stop-Security 'PROVISIONING_PATH_ESCAPE' "Đường dẫn phải là absolute path: $Value"
  }
  $full = [IO.Path]::GetFullPath($Value)
  $root = [IO.Path]::GetPathRoot($full)
  if ([string]::Equals($full, $root, [StringComparison]::OrdinalIgnoreCase)) {
    return $root
  }
  return $full.TrimEnd('\')
}

function Test-SameOrInside {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Candidate
  )

  $rootText = (Convert-ToAbsolutePath $Root).TrimEnd('\')
  $candidateText = (Convert-ToAbsolutePath $Candidate).TrimEnd('\')
  return $candidateText -eq $rootText -or $candidateText.StartsWith($rootText + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Test-ReparsePoint {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return $false
  }
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-NoReparseInPath {
  param([Parameter(Mandatory = $true)][string]$Path)

  $cursor = Convert-ToAbsolutePath $Path
  while (-not [string]::IsNullOrWhiteSpace($cursor)) {
    if (Test-Path -LiteralPath $cursor) {
      if (Test-ReparsePoint $cursor) {
        Stop-Security 'PROVISIONING_PATH_ESCAPE' "Đường dẫn chứa reparse point/junction không được phép: $cursor"
      }
    }
    $parent = Split-Path -Parent $cursor
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) {
      break
    }
    $cursor = $parent
  }
}

function Assert-NoReparseTree {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    return
  }
  Assert-NoReparseInPath $Path
  try {
    $items = Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop
    foreach ($item in @($items)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Stop-Security 'PROVISIONING_PATH_ESCAPE' "Cây dữ liệu chứa reparse point/junction: $($item.FullName)"
      }
    }
  } catch {
    if ($_.Exception.Data.Contains('security_code')) {
      throw
    }
    Stop-Security 'PROVISIONING_PATH_ESCAPE' "Không thể kiểm tra reparse point trong: $Path"
  }
}

function Test-WritableRights {
  param([Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemRights]$Rights)

  # Do not include aggregate Read/Traverse/FullControl values in the mask.
  # FullControl overlaps every ordinary bit, so using it directly marks Read
  # and Traverse as writable. FullControl is still detected because it carries
  # the granular write/delete/change-owner bits below.
  $mask = [int64][System.Security.AccessControl.FileSystemRights]::WriteData
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::AppendData
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::WriteAttributes
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::Delete
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::ChangePermissions
  $mask = $mask -bor [int64][System.Security.AccessControl.FileSystemRights]::TakeOwnership
  return (([int64]$Rights -band $mask) -ne 0)
}

function Get-AclRules {
  param([Parameter(Mandatory = $true)][object]$Acl)

  if ($null -ne $Acl.PSObject.Properties['Access']) {
    return $Acl.Access
  }
  return $Acl.GetAccessRules($true, $true, [System.Security.Principal.NTAccount])
}

function Get-BroadWriteRules {
  param([Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemSecurity]$Acl)

  $rules = Get-AclRules $Acl
  return @(
    $rules | Where-Object {
      $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
      $_.IdentityReference.Value -match '(?i)(^|\\)(Everyone|Authenticated Users|Users|INTERACTIVE)$' -and
      (Test-WritableRights $_.FileSystemRights)
    }
  )
}

function Assert-SecureParent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$ErrorCode = 'ACL_BASELINE_UNSAFE'
  )

  $full = Convert-ToAbsolutePath $Path
  $parent = Split-Path -Parent $full
  if ([string]::IsNullOrWhiteSpace($parent) -or -not (Test-Path -LiteralPath $parent -PathType Container)) {
    Stop-Security 'ACL_BASELINE_UNSAFE' "$Label cần parent đã tồn tại để kiểm tra ACL: $parent"
  }
  Assert-NoReparseInPath $parent
  try {
    $acl = Get-Acl -LiteralPath $parent -ErrorAction Stop
  } catch {
    Stop-Security 'ACL_VERIFICATION_FAILED' "Không đọc được ACL của parent $parent"
  }
  $broad = @(Get-BroadWriteRules $acl)
  if ($broad.Count -gt 0) {
    Stop-Security 'ACL_BASELINE_UNSAFE' "$Label có parent cho phép principal broad ghi dữ liệu: $parent" @{
      parent = $parent
      identities = @($broad | ForEach-Object { $_.IdentityReference.Value })
    }
  }
  Assert-TrustedAclOwner -Acl $acl -Label "$Label parent" -ErrorCode $ErrorCode
  Assert-NoUntrustedWritableAces -Acl $acl -Label "$Label parent" -ErrorCode $ErrorCode
}

function Assert-TestRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $full = Convert-ToAbsolutePath $Value
  if ([string]::Equals($full, [IO.Path]::GetPathRoot($full), [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Security 'PROVISIONING_PATH_ESCAPE' "$Label không được là root của ổ đĩa"
  }
  if ((Split-Path -Leaf $full) -notmatch '(?i)(-Test|-Isolation)$') {
    Stop-Security 'PROVISIONING_PATH_ESCAPE' "$Label phải kết thúc bằng -Test hoặc -Isolation: $full"
  }
  foreach ($blocked in @($env:USERPROFILE, $env:WINDIR, $env:TEMP, $env:LOCALAPPDATA, $env:APPDATA)) {
    if ($blocked -and (Test-SameOrInside $blocked $full)) {
      Stop-Security 'PROVISIONING_PATH_ESCAPE' "$Label nằm trong thư mục người dùng/hệ thống bị chặn: $full"
    }
  }
  Assert-NoReparseInPath $full
  Assert-SecureParent $full $Label
  return $full
}

function Assert-ManifestPath {
  param(
    [Parameter(Mandatory = $true)][string]$ServiceDataRoot,
    [string]$ManifestPath
  )

  $root = Convert-ToAbsolutePath $ServiceDataRoot
  $candidate = if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    Join-Path $root 'provisioning-manifest.json'
  } else {
    Convert-ToAbsolutePath $ManifestPath
  }
  $parent = (Convert-ToAbsolutePath (Split-Path -Parent $candidate)).TrimEnd('\')
  if ($parent -ne $root.TrimEnd('\')) {
    Stop-Security 'PROVISIONING_PATH_ESCAPE' "Manifest chỉ được nằm trực tiếp trong SERVICE_DATA_ROOT: $candidate"
  }
  Assert-NoReparseInPath $parent
  return $candidate
}

function Assert-ExistingFile {
  param(
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label,
    [switch]$MachineScoped
  )

  $full = Convert-ToAbsolutePath $Value
  if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
    Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' "$Label không tồn tại: $full"
  }
  Assert-NoReparseInPath $full
  if ($MachineScoped) {
    foreach ($blocked in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP)) {
      if ($blocked -and (Test-SameOrInside $blocked $full)) {
        Stop-Security 'SERVICE_BINARY_UNTRUSTED' "$Label không được lấy từ profile QQ: $full"
      }
    }
  }
  return $full
}

function Get-FileSha256 {
  param([Parameter(Mandatory = $true)][string]$Path)

  try {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToUpperInvariant()
  } catch {
    Stop-Security 'PROVISIONING_MARKER_INVALID' "Không tính được SHA-256 của file: $Path"
  }
}

function Assert-ManifestHash {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedHash
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Stop-Security 'PROVISIONING_MARKER_INVALID' "Manifest đã biến mất; không thể chứng minh ownership: $Path"
  }
  Assert-NoReparseInPath $Path
  if ([string]::IsNullOrWhiteSpace($ExpectedHash) -or (Get-FileSha256 $Path) -ne $ExpectedHash.ToUpperInvariant()) {
    Stop-Security 'PROVISIONING_MARKER_INVALID' 'Manifest hash không khớp marker; không xóa file/root.'
  }
}

function Assert-AccountSidOwnership {
  param(
    [Parameter(Mandatory = $true)][string]$ExpectedName,
    [Parameter(Mandatory = $true)][string]$ActualName,
    [Parameter(Mandatory = $true)][string]$ExpectedSid,
    [Parameter(Mandatory = $true)][string]$ActualSid,
    [Parameter(Mandatory = $true)][bool]$CreatedByProvisioning
  )

  if (-not $CreatedByProvisioning) {
    Stop-Security 'ROLLBACK_OWNERSHIP_UNPROVEN' 'Marker không chứng minh account do provisioning instance này tạo.'
  }
  if ($ActualName -ne $ExpectedName) {
    Stop-Security 'WORKER_ACCOUNT_UNMANAGED' 'Account hiện tại không khớp tên trong marker.'
  }
  if ($ActualSid -ne $ExpectedSid) {
    Stop-Security 'WORKER_ACCOUNT_SID_MISMATCH' 'Account cùng tên nhưng SID hiện tại khác marker; không xóa.'
  }
}

function Assert-RollbackDeletionProof {
  param([object]$Proof)

  $requiredFields = @('status', 'exclusive_delete_lock', 'worker_stopped', 'active_handles', 'root_fingerprint_before', 'root_fingerprint_after')
  if ($null -eq $Proof) {
    Stop-Security 'ROLLBACK_SAFE_STOP_UNAVAILABLE' 'Chưa có bằng chứng độc quyền đủ mạnh để xóa cây dữ liệu; giữ marker.'
  }
  foreach ($field in $requiredFields) {
    if ($null -eq $Proof.PSObject.Properties[$field]) {
      Stop-Security 'ROLLBACK_SAFE_STOP_UNAVAILABLE' "Bằng chứng xóa thiếu trường bắt buộc: $field"
    }
  }
  try {
    $activeHandles = [int](Get-Field $Proof 'active_handles')
  } catch {
    Stop-Security 'ROLLBACK_SAFE_STOP_UNAVAILABLE' 'Bằng chứng xóa có số handle không hợp lệ.'
  }
  if ((Get-Field $Proof 'status') -ne 'VERIFIED_EXCLUSIVE_REMOVAL' -or
      (Get-Field $Proof 'exclusive_delete_lock') -ne $true -or
      (Get-Field $Proof 'worker_stopped') -ne $true -or
      $activeHandles -ne 0) {
    Stop-Security 'ROLLBACK_SAFE_STOP_UNAVAILABLE' 'Worker hoặc handle còn hoạt động; không xóa cây dữ liệu.'
  }
  $before = [string](Get-Field $Proof 'root_fingerprint_before')
  $after = [string](Get-Field $Proof 'root_fingerprint_after')
  if ([string]::IsNullOrWhiteSpace($before) -or $before -ne $after) {
    Stop-Security 'ROLLBACK_SAFE_STOP_UNAVAILABLE' 'Cây dữ liệu đã thay đổi sau kiểm tra; không xóa và giữ marker.'
  }
}

function Assert-RollbackDeletionPreconditions {
  param(
    [switch]$RemoveTestData,
    [switch]$RemoveWorkerAccount,
    [bool]$WorkerAccountPresent,
    [object]$RemovalSafetyProof = $null
  )

  if (-not $RemoveTestData) {
    return
  }
  if ($WorkerAccountPresent -and -not $RemoveWorkerAccount) {
    Stop-Security 'ROLLBACK_OWNERSHIP_UNPROVEN' 'Không xóa marker/root khi AIWorker còn tồn tại mà Owner chưa yêu cầu gỡ account.'
  }
  Assert-RollbackDeletionProof $RemovalSafetyProof
}

function Assert-TrustedServiceBinary {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$WorkerIdentity
  )

  $full = Convert-ToAbsolutePath $Path
  if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
    Stop-Security 'SERVICE_BINARY_UNTRUSTED' "Service executable không tồn tại: $full"
  }
  Assert-NoReparseInPath $full
  Assert-SecureParent $full 'SERVICE_BINARY' -ErrorCode 'SERVICE_BINARY_UNTRUSTED'
  foreach ($blocked in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP)) {
    if ($blocked -and (Test-SameOrInside $blocked $full)) {
      Stop-Security 'SERVICE_BINARY_UNTRUSTED' "Service executable nằm trong profile có thể bị QQ sửa: $full"
    }
  }
  $acl = Get-Acl -LiteralPath $full -ErrorAction Stop
  Assert-TrustedAclOwner -Acl $acl -Label 'SERVICE_BINARY' -ErrorCode 'SERVICE_BINARY_UNTRUSTED'
  Assert-NoUntrustedWritableAces -Acl $acl -Label 'SERVICE_BINARY' -WorkerIdentity $WorkerIdentity -ErrorCode 'SERVICE_BINARY_UNTRUSTED'
  return [pscustomobject]@{
    path = $full
    sha256 = Get-FileSha256 $full
  }
}

function Normalize-Identity {
  param([Parameter(Mandatory = $true)][string]$Value)

  $text = $Value.Trim()
  if ($text -match '^(?i)\.\\(.+)$') {
    $text = "$env:COMPUTERNAME\$($Matches[1])"
  } elseif ($text -notmatch '\\') {
    $text = "$env:COMPUTERNAME\$text"
  }
  return $text.ToUpperInvariant()
}

function Test-IdentityEquivalent {
  param(
    [Parameter(Mandatory = $true)][string]$Actual,
    [Parameter(Mandatory = $true)][string]$Expected
  )

  return (Normalize-Identity $Actual) -eq (Normalize-Identity $Expected)
}

function Get-DefaultTrustedAclIdentities {
  $identities = @(
    'NT AUTHORITY\SYSTEM',
    'BUILTIN\Administrators',
    'NT SERVICE\TrustedInstaller'
  )
  if (-not [string]::IsNullOrWhiteSpace($env:USERDOMAIN) -and -not [string]::IsNullOrWhiteSpace($env:USERNAME)) {
    $identities += "$env:USERDOMAIN\$env:USERNAME"
  }
  if (-not [string]::IsNullOrWhiteSpace($env:COMPUTERNAME) -and -not [string]::IsNullOrWhiteSpace($env:USERNAME)) {
    $identities += "$env:COMPUTERNAME\$env:USERNAME"
  }
  return @($identities | Select-Object -Unique)
}

function Test-IdentityInSet {
  param(
    [Parameter(Mandatory = $true)][string]$Actual,
    [string[]]$Expected = @()
  )

  foreach ($candidate in @($Expected)) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-IdentityEquivalent $Actual $candidate)) {
      return $true
    }
  }
  return $false
}

function Test-RightsSubset {
  param(
    [Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemRights]$Actual,
    [Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemRights]$Allowed
  )

  $unexpected = ([int64]$Actual) -band (-bnot ([int64]$Allowed))
  return $unexpected -eq 0
}

function Assert-TrustedAclOwner {
  param(
    [Parameter(Mandatory = $true)][object]$Acl,
    [Parameter(Mandatory = $true)][string]$Label,
    [string[]]$TrustedIdentities = @(),
    [string]$ErrorCode = 'ACL_VERIFICATION_FAILED'
  )

  $allowed = @(Get-DefaultTrustedAclIdentities) + @($TrustedIdentities)
  if (-not (Test-IdentityInSet ([string]$Acl.Owner) $allowed)) {
    Stop-Security $ErrorCode "$Label có owner không nằm trong allowlist tin cậy: $($Acl.Owner)"
  }
}

function Assert-NoUntrustedWritableAces {
  param(
    [Parameter(Mandatory = $true)][System.Security.AccessControl.FileSystemSecurity]$Acl,
    [Parameter(Mandatory = $true)][string]$Label,
    [string[]]$TrustedIdentities = @(),
    [string]$WorkerIdentity = '',
    [string]$ErrorCode = 'ACL_VERIFICATION_FAILED'
  )

  $allowed = @(Get-DefaultTrustedAclIdentities) + @($TrustedIdentities)
  $unexpected = @()
  foreach ($rule in @(Get-AclRules $Acl | Where-Object {
    $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
    (Test-WritableRights $_.FileSystemRights)
  })) {
    $identity = [string]$rule.IdentityReference.Value
    if ($WorkerIdentity -and (Test-IdentityEquivalent $identity $WorkerIdentity)) {
      $unexpected += "$identity (worker executable write)"
      continue
    }
    if (-not (Test-IdentityInSet $identity $allowed)) {
      $unexpected += "$identity ($($rule.FileSystemRights))"
    }
  }
  if ($unexpected.Count -gt 0) {
    Stop-Security $ErrorCode "$Label có writable ACE ngoài allowlist: $($unexpected -join ', ')"
  }
}

function Assert-WorkerGroupMembership {
  param([Parameter(Mandatory = $true)][string]$AccountName)

  try {
    $account = Get-LocalUser -Name $AccountName -ErrorAction Stop
    $workerSid = $account.SID.Value
    $allowed = @('S-1-5-32-545')
    $unexpected = @()
    $foundAllowed = $false
    foreach ($group in @(Get-LocalGroup)) {
      foreach ($member in @(Get-LocalGroupMember -Group $group.Name -ErrorAction Stop)) {
        if ($member.SID.Value -eq $workerSid) {
          if ($allowed -contains $group.SID.Value) {
            $foundAllowed = $true
          } else {
            $unexpected += "$($group.Name) [$($group.SID.Value)]"
          }
        }
      }
    }
  } catch {
    if ($_.Exception.Data.Contains('security_code')) {
      throw
    }
    Stop-Security 'ACL_VERIFICATION_FAILED' "Không thể xác minh group membership của $AccountName"
  }
  if ($unexpected.Count -gt 0) {
    Stop-Security 'WORKER_ACCOUNT_UNMANAGED' "AIWorker nằm trong group ngoài Users; không tự ý gỡ: $($unexpected -join ', ')"
  }
  if (-not $foundAllowed) {
    Stop-Security 'WORKER_ACCOUNT_UNMANAGED' "AIWorker không nằm trong group Users; không tự ý gỡ."
  }
}

function Get-LiveService {
  param([Parameter(Mandatory = $true)][string]$Name)

  try {
    $escaped = $Name.Replace("'", "''")
    return Get-CimInstance -ClassName Win32_Service -Filter "Name='$escaped'" -ErrorAction Stop | Select-Object -First 1
  } catch {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' "Không đọc được cấu hình live của service: $Name"
  }
}

function Get-ServiceImagePath {
  param([Parameter(Mandatory = $true)][object]$Service)

  $raw = [string](Get-Field $Service 'PathName')
  if ([string]::IsNullOrWhiteSpace($raw)) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Service không có PathName để xác minh'
  }
  $text = $raw.Trim()
  if ($text.StartsWith('"')) {
    $end = $text.IndexOf('"', 1)
    if ($end -lt 2) {
      Stop-Security 'SERVICE_CONFIG_MISMATCH' "PathName service không hợp lệ: $raw"
    }
    return Convert-ToAbsolutePath $text.Substring(1, $end - 1)
  }
  return Convert-ToAbsolutePath ($text -split '\s+', 2)[0]
}

function Assert-ServiceOwnership {
  param(
    [Parameter(Mandatory = $true)][object]$Marker,
    [Parameter(Mandatory = $true)][object]$Service
  )

  if ((Get-Field $Marker 'service_created_by_provisioning') -ne $true) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Marker không chứng minh service do provisioning này tạo'
  }
  $expectedPath = Convert-ToAbsolutePath ([string](Get-Field $Marker 'service_binary_path'))
  $actualPath = Get-ServiceImagePath $Service
  if ($actualPath -ne $expectedPath) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Service binary path không khớp marker' @{
      expected = $expectedPath
      actual = $actualPath
    }
  }
  $expectedHash = [string](Get-Field $Marker 'service_binary_sha256')
  if ([string]::IsNullOrWhiteSpace($expectedHash) -or (Get-FileSha256 $actualPath) -ne $expectedHash.ToUpperInvariant()) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Service binary hash không khớp marker'
  }
  $expectedAccount = [string](Get-Field $Marker 'service_account')
  $actualAccount = [string](Get-Field $Service 'StartName')
  if ([string]::IsNullOrWhiteSpace($expectedAccount) -or [string]::IsNullOrWhiteSpace($actualAccount) -or -not (Test-IdentityEquivalent $actualAccount $expectedAccount)) {
    Stop-Security 'SERVICE_CONFIG_MISMATCH' 'Service account không khớp marker'
  }
}

function Assert-ManagedAclObject {
  param(
    [Parameter(Mandatory = $true)][object]$Acl,
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$WorkerIdentity,
    [string[]]$TrustedIdentities = @(),
    [switch]$AllowWorkerWrite,
    [switch]$AllowWorkerTraverse
  )

  if (-not $Acl.AreAccessRulesProtected) {
    Stop-Security 'ACL_VERIFICATION_FAILED' "$Label vẫn nhận ACL inheritance: $Path"
  }
  Assert-TrustedAclOwner -Acl $Acl -Label $Label -TrustedIdentities $TrustedIdentities
  $allowed = @(Get-DefaultTrustedAclIdentities) + @($TrustedIdentities)
  $unexpected = @()
  foreach ($rule in @(Get-AclRules $Acl)) {
    $identity = [string]$rule.IdentityReference.Value
    if ($WorkerIdentity -and (Test-IdentityEquivalent $identity $WorkerIdentity)) {
      $rights = [System.Security.AccessControl.FileSystemRights]$rule.FileSystemRights
      $allowedWorkerRights = [System.Security.AccessControl.FileSystemRights]::Modify -bor [System.Security.AccessControl.FileSystemRights]::Synchronize
      $allowedTraverseRights = [System.Security.AccessControl.FileSystemRights]::Traverse -bor [System.Security.AccessControl.FileSystemRights]::Synchronize
      if ($AllowWorkerWrite -and $rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
          (Test-RightsSubset $rights $allowedWorkerRights)) {
        continue
      }
      if ($AllowWorkerTraverse -and $rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
          (Test-RightsSubset $rights $allowedTraverseRights)) {
        continue
      }
      $unexpected += "$identity ($($rule.AccessControlType): $rights)"
      continue
    }
    if (-not (Test-IdentityInSet $identity $allowed)) {
      $unexpected += "$identity ($($rule.AccessControlType): $($rule.FileSystemRights))"
    }
  }
  if ($unexpected.Count -gt 0) {
    Stop-Security 'ACL_VERIFICATION_FAILED' "$Label có ACE ngoài allowlist: $($unexpected -join ', ')"
  }
}

function Assert-ManagedAcl {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$WorkerIdentity,
    [string[]]$TrustedIdentities = @(),
    [switch]$AllowWorkerWrite,
    [switch]$AllowWorkerTraverse
  )

  $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
  Assert-ManagedAclObject -Acl $acl -Path $Path -Label $Label -WorkerIdentity $WorkerIdentity -TrustedIdentities $TrustedIdentities -AllowWorkerWrite:$AllowWorkerWrite -AllowWorkerTraverse:$AllowWorkerTraverse
}

function Assert-ManagedRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$WorkerIdentity,
    [string[]]$TrustedIdentities = @(),
    [switch]$AllowWorkerWrite,
    [switch]$AllowWorkerTraverse,
    [switch]$RequireExisting
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    if ($RequireExisting) {
      Stop-Security 'PROVISIONING_RESOURCE_MISMATCH' "$Label được marker ghi nhận nhưng không còn tồn tại: $Path"
    }
    return
  }
  Assert-NoReparseTree $Path
  Assert-ManagedAcl $Path $Label $WorkerIdentity -TrustedIdentities $TrustedIdentities -AllowWorkerWrite:$AllowWorkerWrite -AllowWorkerTraverse:$AllowWorkerTraverse
}
