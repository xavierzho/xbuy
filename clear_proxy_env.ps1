# Clear proxy-related User environment variables (restart terminals after run)
$names = @('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy')
foreach ($n in $names) {
    $v = [Environment]::GetEnvironmentVariable($n, 'User')
    if ($v) { Write-Host "Remove User $n" }
    [Environment]::SetEnvironmentVariable($n, $null, 'User')
}
Write-Host 'Done. Close and reopen PowerShell / IDE / rush app.'
