<#
  采集 Windows 宿主机机器指纹（与 backend/core/common/licensing/fingerprint.py v2 算法一致）。
  主分量优先级：MachineGuid → CIM 整机 UUID → 物理网卡 MAC → 主机名。
  指纹 = SHA256("gtapp-machine-fingerprint:v2|<kind>|<value>")，64 位小写 hex。

  用法：
    . deploy\windows\get_host_fingerprint.ps1
    $fp = Get-GtHostFingerprint
    $code = Format-GtMachineCode $fp

  注意：这是给 Docker/容器部署注入宿主机指纹用的。原生 Windows 部署不要写
  GTAPP_HOST_FINGERPRINT，让 Python 侧读 MachineGuid 并维护 machine_anchor.json。
#>
$script:GtVirtualHints = @(
  'virtual', 'hyper-v', 'vmware', 'virtualbox', 'vbox', 'tailscale', 'zerotier',
  'vpn', 'tap', 'tun', 'bluetooth', 'wan miniport', 'loopback', 'npcap', 'wintun',
  'wireguard', 'cisco anyconnect', 'fortinet', 'openvpn'
)

function Normalize-GtUuid {
  param([string]$Value)
  $text = ([string]$Value).Trim().Trim('"').Trim('{').Trim('}').Trim().ToLower()
  if (-not $text) { return '' }
  $bogus = @(
    '00000000-0000-0000-0000-000000000000',
    'ffffffff-ffff-ffff-ffff-ffffffffffff',
    '03000200-0400-0500-0006-000700080009',
    'ffffffff-ffff-ffff-ffff-fffffffffffe'
  )
  if ($bogus -contains $text) { return '' }
  if ($text.Length -lt 16) { return '' }
  return $text
}

function Get-GtMachineGuid {
  $subPath = 'SOFTWARE\Microsoft\Cryptography'
  foreach ($view in @('Registry64', 'Registry32')) {
    $base = $null
    $sub = $null
    try {
      $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::LocalMachine,
        [Microsoft.Win32.RegistryView]::$view
      )
      $sub = $base.OpenSubKey($subPath)
      if ($null -ne $sub) {
        $value = $sub.GetValue('MachineGuid')
        $clean = Normalize-GtUuid ([string]$value)
        if ($clean) { return $clean }
      }
    } catch {
      # 视图不可用/权限不足时继续尝试下一个视图
    } finally {
      if ($null -ne $sub) { $sub.Dispose() }
      if ($null -ne $base) { $base.Dispose() }
    }
  }
  return ''
}

function Get-GtCimUuid {
  try {
    $product = Get-CimInstance -ClassName Win32_ComputerSystemProduct -ErrorAction Stop
    $clean = Normalize-GtUuid ([string]$product.UUID)
    if ($clean) { return $clean }
  } catch {}
  try {
    $product = Get-WmiObject -Class Win32_ComputerSystemProduct -ErrorAction Stop
    $clean = Normalize-GtUuid ([string]$product.UUID)
    if ($clean) { return $clean }
  } catch {}
  return ''
}

function Get-GtPhysicalMacs {
  $macs = @()
  try {
    foreach ($nic in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
      if ($nic.NetworkInterfaceType -ne [System.Net.NetworkInformation.NetworkInterfaceType]::Ethernet -and
          $nic.NetworkInterfaceType -ne [System.Net.NetworkInformation.NetworkInterfaceType]::Wireless80211) {
        continue
      }
      $label = (([string]$nic.Name) + ' ' + ([string]$nic.Description)).ToLower()
      $virtual = $false
      foreach ($hint in $script:GtVirtualHints) {
        if ($label.Contains($hint)) { $virtual = $true; break }
      }
      if ($virtual) { continue }
      $bytes = @($nic.GetPhysicalAddress().GetAddressBytes())
      if ($bytes.Count -ne 6) { continue }
      if (($bytes | Where-Object { $_ -ne 0 }).Count -eq 0) { continue }
      $parts = foreach ($b in $bytes) { '{0:x2}' -f [int]$b }
      $mac = ($parts -join ':')
      if ($mac -and $mac -ne '00:00:00:00:00:00') { $macs += $mac }
    }
  } catch {}
  return @($macs | Sort-Object -Unique)
}

function Get-GtHostFingerprintDetail {
  $guid = Get-GtMachineGuid
  if ($guid) { return @{ kind = 'machine-guid'; value = $guid } }

  $uuid = Get-GtCimUuid
  if ($uuid) { return @{ kind = 'cim-uuid'; value = $uuid } }

  $macs = @(Get-GtPhysicalMacs)
  if ($macs.Count -gt 0) {
    $joined = (($macs | Sort-Object) -join ',')
    return @{ kind = 'nic-mac'; value = $joined }
  }

  $hostName = ''
  try { $hostName = [System.Net.Dns]::GetHostName() } catch {}
  if (-not $hostName) { $hostName = $env:COMPUTERNAME }
  return @{ kind = 'hostname'; value = ([string]$hostName).Trim() }
}

function Get-GtHostFingerprint {
  $detail = Get-GtHostFingerprintDetail
  $payload = 'gtapp-machine-fingerprint:v2|' + $detail.kind + '|' + $detail.value
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $hash = $sha.ComputeHash($bytes)
    return ([BitConverter]::ToString($hash) -replace '-', '').ToLower()
  } finally {
    $sha.Dispose()
  }
}

function Format-GtMachineCode([string]$Fingerprint) {
  $raw = ($Fingerprint -replace '-', '').Trim().ToLower()
  if ($raw.Length -lt 20) { return '' }
  $prefix = $raw.Substring(0, 20).ToUpper()
  $parts = @()
  for ($i = 0; $i -lt $prefix.Length; $i += 5) {
    $len = [Math]::Min(5, $prefix.Length - $i)
    $parts += $prefix.Substring($i, $len)
  }
  return ($parts -join '-')
}
