param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') { $env:PSModulePath = "$PSHOME\Modules" }
if (-not $env:KATUV_SIGN_CERT_SHA1 -or $env:KATUV_SIGN_CERT_SHA1 -notmatch '^[0-9a-fA-F]{40}$') { throw 'Set KATUV_SIGN_CERT_SHA1 to the validated personal certificate thumbprint.' }
$tool = $env:KATUV_SIGNTOOL
if (-not $tool) { $tool = (Get-Command signtool.exe -ErrorAction Stop).Source }
$timestamp = 'http://time.certum.pl'
& $tool sign /sha1 $env:KATUV_SIGN_CERT_SHA1 /fd SHA256 /tr $timestamp /td SHA256 $Path
if ($LASTEXITCODE -ne 0) { throw 'Signing failed. Connect SimplySign Desktop and authorize the signing session.' }
& $tool verify /pa /all /v $Path
if ($LASTEXITCODE -ne 0) { throw 'Authenticode verification failed.' }
$signature = Get-AuthenticodeSignature -LiteralPath $Path
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $env:KATUV_SIGN_CERT_SHA1 -or -not $signature.TimeStamperCertificate) {
    throw 'A valid timestamped signature from the selected publisher is required.'
}
