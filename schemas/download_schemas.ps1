# Descarga los XSD oficiales del SII en subcarpetas por familia y aplica el
# parche del decimal que libxml2 no acepta. Ejecutar desde la raíz del proyecto:
#   powershell -ExecutionPolicy Bypass -File schemas\download_schemas.ps1

$ErrorActionPreference = "Stop"
$base = "https://www.sii.cl/factura_electronica/factura_mercado/"
$map = @{
  "schema_dte.zip"   = "dte"      # DTE, EnvioDTE, SiiTypes, xmldsignature
  "schema_iecv.zip"  = "iecv"     # LibroCV + Lce*
  "schema_ic.zip"    = "response" # RespuestaEnvioDTE
  "schema19983.zip"  = "receipts" # EnvioRecibos, Recibos (Ley 19.983)
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($zip in $map.Keys) {
  $dest = Join-Path $here $map[$zip]
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  $tmp = Join-Path $here $zip
  Invoke-WebRequest -Uri "$base$zip" -OutFile $tmp -TimeoutSec 60 -UseBasicParsing
  Expand-Archive -Path $tmp -DestinationPath $dest -Force
  Remove-Item $tmp
  Write-Host "OK: $zip -> $($map[$zip])"
}

# RespuestaEnvioDTE necesita SiiTypes y xmldsignature (no vienen en su zip).
Copy-Item (Join-Path $here "dte\SiiTypes_v10.xsd")       (Join-Path $here "response\") -Force
Copy-Item (Join-Path $here "dte\xmldsignature_v10.xsd")  (Join-Path $here "response\") -Force

# Parche: libxml2 rechaza el decimal de 34 dígitos de LceSiiTypes.
$lce = Join-Path $here "iecv\LceSiiTypes_v10.xsd"
(Get-Content $lce -Raw) -replace '999999999999999999999999999999\.9999', '999999999999999999.9999' |
  Set-Content $lce -Encoding UTF8

Write-Host "Esquemas listos en schemas/ (dte, iecv, response, receipts)."
