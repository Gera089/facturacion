# crear_remision.ps1
# Enviar remisión a la API de FastAPI

$apiUrl = "http://127.0.0.1:8000/sae/remision/crear"

# Construimos el objeto en PowerShell y luego lo convertimos a JSON
$data = @{
    empresa   = "EZA2007"
    cliente   = "100475"
    productos = @(
        @{
            cip      = "150"
            cantidad = 2
            precio   = 1270.59
        },
        @{
            cip      = "66"
            cantidad = 3
            precio   = 1588
        }
    )
}

$json = $data | ConvertTo-Json -Depth 5

Write-Host "Enviando solicitud a la API..."

try {
    $response = Invoke-RestMethod -Uri $apiUrl -Method POST -ContentType "application/json" -Body $json
    Write-Host "Respuesta API:"
    $response | Format-List
}
catch {
    Write-Host "Error al hacer la solicitud REST:"
    Write-Host $_.Exception.Message
}