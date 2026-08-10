param([int]$Port = 8010, [string]$ExpectedVersion = "")

$deadline = (Get-Date).AddSeconds(45)
do {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok" -and (!$ExpectedVersion -or $health.version -eq $ExpectedVersion)) {
            Write-Host "API disponible en puerto $Port."
            exit 0
        }
    } catch {
        Start-Sleep -Milliseconds 750
    }
} while ((Get-Date) -lt $deadline)

Write-Error "La API no respondió en http://127.0.0.1:$Port/health dentro de 45 segundos."
exit 1
