param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Bundle,
    [Parameter(Mandatory=$true)][string]$Acceptance,
    [Parameter(Mandatory=$true)][string]$PublisherThumbprint
)
$ErrorActionPreference = 'Stop'
if ($PublisherThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'An independently selected publisher certificate thumbprint is required.' }
if ($Installer -match 'UNSIGNED' -or $Bundle -match 'UNSIGNED') { throw 'Internal unsigned candidates must never be published.' }
$signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $PublisherThumbprint -or -not $signature.TimeStamperCertificate) { throw 'Installer must have a valid timestamped publisher signature.' }
$record = Get-Content -LiteralPath $Acceptance -Raw -Encoding UTF8 | ConvertFrom-Json
$hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($record.installer_sha256 -ne $hash -or $record.second_computer -ne $true -or -not $record.tested_at -or -not $record.tester) { throw 'Acceptance must identify this exact signed installer and testing on another computer.' }
foreach ($check in @('windows10_x64','windows11_x64','standard_user','clean_install','upgrade_1_2','failed_upgrade','restart','busy_blocked','port_conflict','uninstall_preserves_projects','offline_models','hebrew_export','signed_uninstaller')) {
    if ($record.checks.$check -ne $true) { throw "Missing acceptance result: $check" }
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
    if ($manifest.signed -ne $true -or $manifest.source_dirty -ne $false -or $manifest.publisher_thumbprint -ne $PublisherThumbprint -or $record.version -ne $manifest.version) { throw 'Release provenance or signing status does not match.' }
    foreach ($file in $manifest.files.PSObject.Properties) {
        if ($file.Name -match '[/\\:]' -or $file.Name -in @('.', '..')) { throw 'Invalid manifest path.' }
        $path = Join-Path $manifestPath[0].DirectoryName $file.Name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw 'Release file hash mismatch.' }
        if ($file.Name.EndsWith('.ps1')) {
            $sig = Get-AuthenticodeSignature -LiteralPath $path
            if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Thumbprint -ne $PublisherThumbprint -or -not $sig.TimeStamperCertificate) { throw ('Script signature is invalid: ' + $file.Name) }
        }
    }
    Write-Output ('Public release checks passed: ' + $manifest.version + ' / ' + $hash)
} finally {
    $target = [IO.Path]::GetFullPath($temporary)
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Test-Path -LiteralPath $target) -and [IO.Path]::GetDirectoryName($target) -eq $parent -and [IO.Path]::GetFileName($target) -like 'katuv-signature-audit-*') { Remove-Item -LiteralPath $target -Recurse -Force }
}
