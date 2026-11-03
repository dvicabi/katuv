param([switch]$NoBrowser, [ValidateRange(1024,65535)][int]$Port=8765, [switch]$ConsoleErrors)
$ErrorActionPreference = 'Stop'
$locked = $false; $mutex = $null
try {
    . (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
    $mutex = [Threading.Mutex]::new($false, (Get-KatuvMutexName $PSScriptRoot))
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'היישום מופעל, מתעדכן או מוסר כעת. המתינו לסיום ופתחו שוב.' }
    $env:AUTOCAPTION_HOME = $PSScriptRoot
    $env:AUTOCAPTION_DATA_DIR = Join-Path $PSScriptRoot 'projects'
    $env:AUTOCAPTION_INSTALLATION_ID = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installation-id.txt') -Raw -Encoding UTF8).Trim()
    $runtime = Get-KatuvRuntime $PSScriptRoot
    $python = Join-Path $runtime 'Scripts\python.exe'
    $version = (Get-Content -LiteralPath (Join-Path $runtime 'release-version.txt') -Raw -Encoding UTF8).Trim()
    $url = "http://127.0.0.1:$Port"
    function Get-Health { try { return Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2 } catch { return $null } }
    function Assert-Identity($Health) {
        if ($Health.app -ne 'katuv' -or $Health.installation_id -ne $env:AUTOCAPTION_INSTALLATION_ID -or
            $Health.runtime_id -ne (Split-Path $runtime -Leaf) -or $Health.app_version -ne $version) {
            throw "הפורט $Port תפוס על ידי יישום או התקנה אחרים. סגרו אותם או הפעילו את כתוב עם הפרמטר -Port ומספר אחר."
        }
        if ($Health.maintenance) { throw 'היישום מתעדכן כעת. פתחו אותו לאחר סיום העדכון.' }
    }
    $health = Get-Health
    if ($null -ne $health) { Assert-Identity $health }
    else {
        $probe = [Net.Sockets.TcpClient]::new()
        try {
            $connecting = $probe.ConnectAsync('127.0.0.1', $Port)
            try { $null = $connecting.Wait(500) } catch {}
            if ($probe.Connected) { throw "הפורט $Port תפוס. סגרו את היישום המשתמש בו או הפעילו עם -Port ומספר אחר." }
        } finally { $probe.Dispose() }
        $process = Start-Process -FilePath $python -ArgumentList @('-I','-X','utf8','-m','autocaption.web','--port',"$Port") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.out.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server.err.log')
        $deadline = (Get-Date).AddSeconds(90)
        do {
            Start-Sleep -Milliseconds 300
            $process.Refresh()
            if ($process.HasExited -or (Get-Date) -gt $deadline) { throw 'לא הצלחנו להפעיל את היישום. בדקו את server.err.log בתיקיית ההתקנה.' }
            $health = Get-Health
        } while ($null -eq $health)
        Assert-Identity $health
    }
    if (-not $NoBrowser) { Start-Process $url }
} catch {
    if ($ConsoleErrors) { Write-Output $_.Exception.Message }
    else {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($_.Exception.Message, 'כתוב - שגיאת הפעלה') | Out-Null
    }
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
