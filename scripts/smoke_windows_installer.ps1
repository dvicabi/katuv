param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = 'Stop'
if (-not $env:RUNNER_TEMP) { throw 'Run this destructive lifecycle test only on a disposable CI runner.' }
$root = Join-Path ([IO.Path]::GetFullPath($env:RUNNER_TEMP)) ('Katuv בדיקת התקנה ' + [guid]::NewGuid().ToString('N'))
function Install-Candidate {
    $process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="' + $root + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        if (Test-Path -LiteralPath (Join-Path $root 'installation.log')) { Get-Content -LiteralPath (Join-Path $root 'installation.log') -Tail 40 }
        throw "Installer failed: $($process.ExitCode)"
    }
}
function Start-Candidate {
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $root 'Start-Katuv.ps1') -Port 18765 -NoBrowser -ConsoleErrors
    if ($LASTEXITCODE -ne 0) { throw 'Installed launcher failed' }
}
Install-Candidate
$first = (Get-Content -LiteralPath (Join-Path $root 'active-runtime.txt') -Raw -Encoding UTF8).Trim()
Start-Candidate
$url = 'http://127.0.0.1:18765'
$health = Invoke-RestMethod "$url/api/health"
if ($health.app_version -ne '1.3.0' -or $health.busy) { throw 'Unexpected installed health' }
$project = Invoke-RestMethod "$url/api/demo" -Method Post -ContentType 'application/json' -Body '{}'
$projectFile = Join-Path $root ('projects\' + $project.id + '\project.json')
$before = (Get-FileHash -LiteralPath $projectFile -Algorithm SHA256).Hash
$sentinel = Join-Path $root 'cache\retained-test.txt'
Set-Content -LiteralPath $sentinel -Value 'Model/download cache must survive uninstall' -Encoding UTF8
Install-Candidate
$second = (Get-Content -LiteralPath (Join-Path $root 'active-runtime.txt') -Raw -Encoding UTF8).Trim()
$previous = (Get-Content -LiteralPath (Join-Path $root 'previous-runtime.txt') -Raw -Encoding UTF8).Trim()
if ($first -eq $second -or $previous -ne $first -or -not (Test-Path -LiteralPath $first)) { throw 'Repair did not retain a separate previous runtime' }
if ((Get-FileHash -LiteralPath $projectFile -Algorithm SHA256).Hash -ne $before) { throw 'Repair modified the project' }
Start-Candidate
Start-Candidate
$health2 = Invoke-RestMethod "$url/api/health"
if ($health2.installation_id -ne $health.installation_id -or $health2.runtime_id -ne (Split-Path $second -Leaf)) { throw 'Repair changed installation identity or launched an old runtime' }
$uninstaller = @(Get-ChildItem -LiteralPath $root -Filter 'unins*.exe')
if ($uninstaller.Count -ne 1) { throw 'Expected a registered Inno uninstaller' }
$process = Start-Process -FilePath $uninstaller[0].FullName -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Uninstaller failed: $($process.ExitCode)" }
if ((Test-Path -LiteralPath (Join-Path $root 'active-runtime.txt')) -or (Test-Path -LiteralPath $first) -or (Test-Path -LiteralPath $second)) { throw 'Uninstaller left an active runtime' }
if ((Get-FileHash -LiteralPath $projectFile -Algorithm SHA256).Hash -ne $before -or -not (Test-Path -LiteralPath $sentinel)) { throw 'Uninstaller modified projects or deleted retained caches' }
Write-Output 'EXE lifecycle passed: Hebrew path, install, repair, previous runtime, repeat launch, uninstall and project/cache retention.'
