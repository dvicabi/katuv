param([string]$InstallRoot=$PSScriptRoot, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$mutex = [Threading.Mutex]::new($false, (Get-KatuvMutexName $InstallRoot))
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'מתבצעת הפעלה או התקנה. המתינו לסיום ונסו שוב.' }
    $servers = @(Assert-KatuvIdle $InstallRoot -Maintenance:(-not $CheckOnly))
    if ($CheckOnly) { exit 0 }
    $runtimes = @()
    foreach ($name in @('active-runtime.txt', 'previous-runtime.txt')) {
        $pointer = Join-Path $InstallRoot $name
        if (Test-Path -LiteralPath $pointer) {
            $target = [IO.Path]::GetFullPath((Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim())
            if ([IO.Path]::GetDirectoryName($target) -ne $InstallRoot -or [IO.Path]::GetFileName($target) -notmatch '^runtime-[0-9]') { throw 'נתיב סביבת עבודה אינו תקין; ההסרה הופסקה.' }
            $runtimes += $target
        }
    }
    foreach ($folder in Get-ChildItem -LiteralPath $InstallRoot -Directory -Filter 'runtime-*') {
        if (Test-Path -LiteralPath (Join-Path $folder.FullName '.katuv-owned')) { $runtimes += $folder.FullName }
    }
    foreach ($runtime in $runtimes) {
        if ((Test-Path -LiteralPath $runtime) -and ((Get-Item -LiteralPath $runtime).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'סביבת עבודה מפנה לתיקייה אחרת; ההסרה הופסקה.' }
    }
    Stop-KatuvServers $servers
    foreach ($name in @('active-runtime.txt','previous-runtime.txt')) {
        $path = Join-Path $InstallRoot $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    foreach ($runtime in ($runtimes | Select-Object -Unique)) { Remove-KatuvRuntime $InstallRoot $runtime }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $path = Join-Path $folder 'כתוב.lnk'
        if (Test-Path -LiteralPath $path) {
            $shortcut = $shell.CreateShortcut($path)
            if ($shortcut.Arguments.Contains((Join-Path $InstallRoot 'Start-Katuv.ps1'))) { Remove-Item -LiteralPath $path -Force }
        }
    }
    # Projects, shared model caches and downloaded dependency caches are deliberately retained.
    Write-Output 'היישום הוסר. הפרויקטים ומטמוני ההורדה נשמרו להתקנה חוזרת.'
} catch { Write-Output $_.Exception.Message; exit 1 }
finally { if ($locked) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
