param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Bundle,
    [string]$Acceptance,
    [string]$PublisherThumbprint,
    [switch]$Unsigned,
    [switch]$Preview,
    [string]$ExpectedCommit
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') { $env:PSModulePath = "$PSHOME\Modules" }
if ($Preview -and -not $Unsigned) { throw 'Preview publication requires explicit -Unsigned.' }
if (-not $Unsigned) {
    if ($Installer -match 'UNSIGNED' -or $Bundle -match 'UNSIGNED') { throw 'Unsigned publication requires explicit -Unsigned.' }
    if ($PublisherThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'An independently selected publisher certificate thumbprint is required.' }
} elseif ($PublisherThumbprint) { throw 'An unsigned release cannot claim a publisher certificate.' }
if (-not $Preview -and -not $Acceptance) { throw 'Stable releases require acceptance results for the exact installer on another computer.' }
if ($Preview -and $ExpectedCommit -notmatch '^[0-9a-f]{40}$') { throw 'Preview releases require the expected source commit.' }
foreach ($artifact in @($Installer, $Bundle)) {
    $digest = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksum = (Get-Content -LiteralPath ($artifact + '.sha256') -Raw -Encoding ASCII).Trim()
    if ($checksum -cne ($digest + '  ' + [IO.Path]::GetFileName($artifact))) { throw 'Artifact SHA-256 or filename mismatch.' }
}
$signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($Unsigned) {
    if ($signature.Status -ne 'NotSigned') { throw 'Expected an unsigned installer, not an invalid or unexpected signature.' }
} elseif ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $PublisherThumbprint -or -not $signature.TimeStamperCertificate) { throw 'Installer must have a valid timestamped publisher signature.' }
$hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $Preview) {
    $record = Get-Content -LiteralPath $Acceptance -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.installer_sha256 -ne $hash -or $record.second_computer -ne $true -or -not $record.tested_at -or -not $record.tester) { throw 'Acceptance must identify this exact installer and testing on another computer.' }
    $checks = @('windows10_x64','windows11_x64','standard_user','clean_install','upgrade_1_2','failed_upgrade','restart','busy_blocked','port_conflict','uninstall_preserves_projects','offline_models','hebrew_export')
    if (-not $Unsigned) { $checks += 'signed_uninstaller' }
    foreach ($check in $checks) {
        if ($record.checks.$check -ne $true) { throw "Missing acceptance result: $check" }
    }
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('katuv-signature-audit-' + [guid]::NewGuid().ToString('N'))
try {
    # Audit the archive before extraction, including traversal and file hashes.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead([IO.Path]::GetFullPath($Bundle))
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:|\\)') { throw 'Unsafe release archive path.' }
        }
    } finally { $zip.Dispose() }
    Expand-Archive -LiteralPath $Bundle -DestinationPath $temporary
    $manifestPath = @(Get-ChildItem -LiteralPath $temporary -Recurse -Filter release.json)
    if ($manifestPath.Count -ne 1) { throw 'Expected exactly one release manifest.' }
    $manifest = Get-Content -LiteralPath $manifestPath[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.signed -ne (-not $Unsigned) -or $manifest.source_dirty -ne $false -or $manifest.source_commit -notmatch '^[0-9a-f]{40}$') { throw 'Release provenance or signing status does not match.' }
    if ($ExpectedCommit -and $manifest.source_commit -ne $ExpectedCommit) { throw 'Unexpected source commit.' }
    if (-not $Preview -and $record.version -ne $manifest.version) { throw 'Acceptance version does not match.' }
    if ($Unsigned) {
        if ($manifest.publisher_thumbprint) { throw 'Unsigned manifest must not claim a publisher.' }
        $suffix = '-UNSIGNED'
    } else {
        if ($manifest.publisher_thumbprint -ne $PublisherThumbprint) { throw 'Unexpected publisher.' }
        $suffix = ''
    }
    $stem = 'Katuv-' + $manifest.version + '-Windows' + $suffix
    if ([IO.Path]::GetFileName($Installer) -cne ($stem + '.exe') -or [IO.Path]::GetFileName($Bundle) -cne ($stem + '.zip')) { throw 'Artifact names must identify version and signing status.' }
    foreach ($file in $manifest.files.PSObject.Properties) {
        if ($file.Name -match '[/\\:]' -or $file.Name -in @('.', '..')) { throw 'Invalid manifest path.' }
        $path = Join-Path $manifestPath[0].DirectoryName $file.Name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw 'Release file hash mismatch.' }
        if ($file.Name.EndsWith('.ps1') -and -not $Unsigned) {
            $sig = Get-AuthenticodeSignature -LiteralPath $path
            if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Thumbprint -ne $PublisherThumbprint -or -not $sig.TimeStamperCertificate) { throw ('Script signature is invalid: ' + $file.Name) }
        }
    }
    $channel = if ($Preview) { 'UNSIGNED PREVIEW ONLY' } elseif ($Unsigned) { 'UNSIGNED RELEASE' } else { 'SIGNED RELEASE' }
    Write-Output ($channel + ' checks passed: ' + $manifest.version + ' / ' + $hash)
} finally {
    $target = [IO.Path]::GetFullPath($temporary)
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Test-Path -LiteralPath $target) -and [IO.Path]::GetDirectoryName($target) -eq $parent -and [IO.Path]::GetFileName($target) -like 'katuv-signature-audit-*') { Remove-Item -LiteralPath $target -Recurse -Force }
}
