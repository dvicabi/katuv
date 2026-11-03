$ErrorActionPreference = 'Stop'

function Set-KatuvBytes([string]$Path, [byte[]]$Bytes) {
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}

function Set-KatuvText([string]$Path, [string]$Text) {
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($true))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}

function Get-KatuvMutexName([string]$InstallRoot) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($InstallRoot).TrimEnd('\').ToLowerInvariant())) }
    finally { $sha.Dispose() }
    return 'Local\Katuv-' + ([BitConverter]::ToString($digest).Replace('-', ''))
}

function Get-KatuvRuntime([string]$InstallRoot) {
    $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $runtime = (Get-Content -LiteralPath (Join-Path $root 'active-runtime.txt') -Raw -Encoding UTF8).Trim()
    $runtime = [IO.Path]::GetFullPath($runtime)
    if ([IO.Path]::GetDirectoryName($runtime) -ne $root -or [IO.Path]::GetFileName($runtime) -notmatch '^runtime-[0-9]') {
        throw 'נתיב סביבת העבודה אינו תקין. הריצו שוב את המתקין.'
    }
    if ((Get-Item -LiteralPath $runtime).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'נתיב סביבת העבודה אינו תיקייה מקומית רגילה.' }
    return $runtime
}

function Get-KatuvServers([string]$InstallRoot) {
    $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $prefix = $root + '\'
    $servers = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -match '-m\s+autocaption\.web(?:\s|$)'
    })
    $runtimes = @{}
    foreach ($server in $servers) {
        $scripts = [IO.Path]::GetDirectoryName($server.ExecutablePath)
        $runtime = [IO.Path]::GetDirectoryName($scripts)
        if ([IO.Path]::GetFileName($scripts) -eq 'Scripts' -and [IO.Path]::GetDirectoryName($runtime) -eq $root -and [IO.Path]::GetFileName($runtime) -match '^runtime-[0-9]') {
            $runtimes[[int]$server.ProcessId] = [IO.Path]::GetFileName($runtime)
        }
    }
    foreach ($server in $servers) {
        $runtimeName = $runtimes[[int]$server.ProcessId]
        # Windows venv python.exe launches a child from the managed base Python.
        # Attribute that child to its verified venv parent, never to its own base directory.
        if (-not $runtimeName) { $runtimeName = $runtimes[[int]$server.ParentProcessId] }
        $server | Add-Member -NotePropertyName KatuvRuntimeName -NotePropertyValue $runtimeName -Force
    }
    return $servers
}

function Get-KatuvServerUrl($Server) {
    $port = 8765
    if ($Server.CommandLine -match '--port\s+(\d+)') { $port = [int]$Matches[1] }
    return "http://127.0.0.1:$port"
}

function Assert-KatuvIdle([string]$InstallRoot, [switch]$Maintenance) {
    $servers = @(Get-KatuvServers $InstallRoot)
    $entered = @()
    try {
        foreach ($server in $servers) {
            if (-not $server.KatuvRuntimeName) { throw 'לא ניתן לזהות את סביבת השרת הפתוח. סגרו אותו באופן מסודר ונסו שוב.' }
            $url = Get-KatuvServerUrl $server
            try { $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 4 }
            catch { throw 'היישום פתוח אך אינו מגיב. סגרו אותו באופן מסודר ונסו שוב; לא הופסק תהליך לא מזוהה.' }
            if ($health.app -ne 'katuv') { throw 'הכתובת משמשת יישום אחר. סגרו את כתוב ונסו שוב.' }
            if ($health.installation_id) {
                $identity = (Get-Content -LiteralPath (Join-Path $InstallRoot 'installation-id.txt') -Raw -Encoding UTF8).Trim()
                $runtimeName = $server.KatuvRuntimeName
                if ($health.installation_id -ne $identity -or $health.runtime_id -ne $runtimeName) {
                    throw 'השרת הפתוח שייך להתקנה אחרת. סגרו אותו באופן מסודר ונסו שוב.'
                }
                if ($health.busy) { throw 'יש עיבוד סרטון או הכנת מודלים פעילים. המתינו לסיום ונסו שוב.' }
                if ($Maintenance) {
                    $null = Invoke-RestMethod -Uri "$url/api/system/maintenance" -Method Post -ContentType 'application/json' -Body '{"enabled":true}' -TimeoutSec 4
                    $entered += $url
                }
            } else {
                $projects = Invoke-RestMethod -Uri "$url/api/projects" -TimeoutSec 4
                $preparation = Invoke-RestMethod -Uri "$url/api/system/preparation" -TimeoutSec 4
                if ($preparation.state -eq 'running' -or @($projects | Where-Object { $_.state -in @('aligning','verifying','exporting') }).Count) {
                    throw 'יש עיבוד סרטון או הכנת מודלים פעילים. המתינו לסיום ונסו שוב.'
                }
            }
        }
    } catch {
        foreach ($url in $entered) { try { $null = Invoke-RestMethod -Uri "$url/api/system/maintenance" -Method Post -ContentType 'application/json' -Body '{"enabled":false}' -TimeoutSec 3 } catch {} }
        throw
    }
    return $servers
}

function Stop-KatuvServers($Servers) {
    foreach ($server in $Servers) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ProcessId)"
        if ($current -and $current.ExecutablePath -eq $server.ExecutablePath -and $current.CreationDate -eq $server.CreationDate) {
            Stop-Process -Id $server.ProcessId -ErrorAction Stop
            Wait-Process -Id $server.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        }
    }
}

function Remove-KatuvRuntime([string]$InstallRoot, [string]$Runtime) {
    $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $target = [IO.Path]::GetFullPath($Runtime).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($target) -ne $root -or [IO.Path]::GetFileName($target) -notmatch '^runtime-[0-9]') {
        throw 'Refusing to remove a directory outside the installation runtime allowlist.'
    }
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to remove a redirected runtime.' }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
