param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Katuv'), [switch]$NoLaunch, [switch]$NoShortcuts)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw 'נדרש מחשב Windows עם מעבד Intel או AMD של 64 סיביות.' }
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
if ((Get-Item -LiteralPath $InstallRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'תיקיית ההתקנה אינה יכולה להיות קישור לתיקייה אחרת.' }
$mutex = [Threading.Mutex]::new($false, (Get-KatuvMutexName $InstallRoot))
$locked = $false; $transcribing = $false; $candidate = $null; $pointerChanged = $false
$previousActive = $null; $previousFiles = @{}; $changedFiles = @()
$activeFile = Join-Path $InstallRoot 'active-runtime.txt'
$logPath = Join-Path $InstallRoot 'installation.log'
function Run-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "התקנת הרכיבים נכשלה (קוד $LASTEXITCODE). בדקו אינטרנט ומקום פנוי. יומן: $logPath" }
}
function Progress([int]$Percent, [string]$Message) { Write-Output "KATUV_PROGRESS|$Percent|$Message" }
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'התקנה או הסרה כבר מתבצעות. המתינו לסיומן.' }
    Start-Transcript -Path $logPath -Append | Out-Null; $transcribing = $true
    Progress 2 'בודקים את חבילת ההתקנה ואת המחשב…'
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.version -notmatch '^\d+\.\d+\.\d+$' -or $manifest.wheel -notmatch '^autocaption-[0-9.]+-py3-none-any\.whl$') { throw 'תיאור חבילת ההתקנה אינו תקין.' }
    foreach ($file in $manifest.files.PSObject.Properties) {
        if ($file.Name -match '[/\\:]' -or $file.Name -in @('.', '..')) { throw 'נתיב לא תקין בחבילת ההתקנה.' }
        $path = Join-Path $PSScriptRoot $file.Name
        if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() -ne $file.Value) { throw "קובץ התקנה חסר או פגום: $($file.Name). הורידו את החבילה שוב." }
    }
    $null = Assert-KatuvIdle $InstallRoot
    if (Test-Path -LiteralPath $activeFile) { $previousActive = Get-KatuvRuntime $InstallRoot }
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($InstallRoot))
    if ($drive.AvailableFreeSpace -lt 10GB) { throw 'נדרשים לפחות 10 GB פנויים להתקנה. פנו מקום והריצו שוב.' }
    $toolRoot = Join-Path $InstallRoot 'installer'
    New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
    $spec = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'downloads.json') -Raw | ConvertFrom-Json).uv_windows
    $uvFolder = Join-Path $toolRoot 'uv-0.12.3'; $uv = Join-Path $uvFolder 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        Progress 8 'מורידים את מנהל ההתקנה…'
        $archive = Join-Path $toolRoot 'uv.zip'
        Invoke-WebRequest -UseBasicParsing -Uri $spec.url -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $spec.sha256) { throw 'ההורדה לא עברה בדיקת תקינות. נסו שוב.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $uvFolder -Force
        if (-not (Test-Path -LiteralPath $uv)) { throw 'מנהל ההתקנה לא נמצא בחבילה.' }
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallRoot 'python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $toolRoot 'python-bin'
    $env:UV_CACHE_DIR = Join-Path $InstallRoot 'cache'
    $env:AUTOCAPTION_HOME = $InstallRoot
    $env:AUTOCAPTION_DATA_DIR = Join-Path $InstallRoot 'projects'
    $env:PYTHONUTF8 = '1'
    Progress 15 'מכינים סביבת עבודה חדשה…'
    Run-Checked $uv @('python', 'install', '3.11')
    # Never modify the active environment, including a same-version repair.
    $candidate = Join-Path $InstallRoot ('runtime-' + $manifest.version + '-' + [guid]::NewGuid().ToString('N'))
    Run-Checked $uv @('venv', '--python', '3.11', $candidate)
    Set-KatuvText (Join-Path $candidate '.katuv-owned') 'Katuv runtime'
    $python = Join-Path $candidate 'Scripts\python.exe'
    Progress 25 'מתקינים את מנועי העברית והווידאו. ההורדה עשויה לקחת כמה דקות…'
    Run-Checked $uv @('pip', 'install', '--python', $python, '--torch-backend=auto', '-r', (Join-Path $PSScriptRoot 'requirements-windows.txt'))
    Run-Checked $uv @('pip', 'install', '--python', $python, '--no-deps', (Join-Path $PSScriptRoot $manifest.wheel))
    Run-Checked $uv @('pip', 'check', '--python', $python)
    Progress 80 'בודקים את סביבת העבודה ואת רכיב הווידאו…'
    Run-Checked $python @('-I', '-X', 'utf8', '-m', 'autocaption.install_check', '--expected-version', $manifest.version)
    Set-KatuvText (Join-Path $candidate 'release-version.txt') $manifest.version
    $support = Join-Path $candidate 'katuv-launcher'
    New-Item -ItemType Directory -Path $support -Force | Out-Null
    $supportNames = @('Start-Katuv.ps1', 'Katuv.Common.ps1', 'Uninstall-Katuv.ps1')
    foreach ($name in $supportNames) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $support -Force }
    $servers = @(Assert-KatuvIdle $InstallRoot -Maintenance)
    Stop-KatuvServers $servers
    $identityPath = Join-Path $InstallRoot 'installation-id.txt'
    if (-not (Test-Path -LiteralPath $identityPath)) { Set-KatuvText $identityPath ([guid]::NewGuid().ToString('N')) }
    foreach ($name in $supportNames) {
        $target = Join-Path $InstallRoot $name
        $previousFiles[$name] = if (Test-Path -LiteralPath $target) { [IO.File]::ReadAllBytes($target) } else { $null }
        $changedFiles += $name
        Set-KatuvBytes $target ([IO.File]::ReadAllBytes((Join-Path $support $name)))
    }
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
            [IO.Directory]::CreateDirectory($folder) | Out-Null
            $shortcut = $shell.CreateShortcut((Join-Path $folder 'כתוב.lnk'))
            $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
            $shortcut.Arguments = '-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + (Join-Path $InstallRoot 'Start-Katuv.ps1') + '"'
            $shortcut.WorkingDirectory = $InstallRoot; $shortcut.Description = 'כתוב - הסטודיו לכתוביות בעברית'; $shortcut.Save()
        }
    }
    if ($previousActive) { Set-KatuvText (Join-Path $InstallRoot 'previous-runtime.txt') $previousActive }
    Progress 95 'מפעילים את הגרסה החדשה…'
    Set-KatuvText $activeFile $candidate
    $pointerChanged = $true
    Progress 100 'ההתקנה הסתיימה. ביישום בחרו הכנת המחשב להורדת המודלים.'
} catch {
    Write-Output ('KATUV_ERROR|' + $_.Exception.Message)
    if ($pointerChanged) {
        if ($previousActive) { Set-KatuvText $activeFile $previousActive }
        else { Remove-Item -LiteralPath $activeFile -Force }
    }
    foreach ($name in $changedFiles) {
        $target = Join-Path $InstallRoot $name
        if ($null -ne $previousFiles[$name]) { Set-KatuvBytes $target $previousFiles[$name] }
        elseif (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    }
    if ($candidate) { Remove-KatuvRuntime $InstallRoot $candidate }
    exit 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
if (-not $NoLaunch) { & (Join-Path $InstallRoot 'Start-Katuv.ps1') }
