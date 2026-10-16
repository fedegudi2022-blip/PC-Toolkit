<#
.SYNOPSIS
    Pasa la suite completa y cuenta lo que pasó, repitiéndola si el proceso se murió sin informe.

.DESCRIPTION
    La suite imprime el resultado de todas las comprobaciones de una vez, al final. Si el proceso
    se muere a la mitad —un cierre duro, una excepción del sistema— el registro acaba en el bloque
    que estuviera corriendo: sin un `[FALLO]` que mirar, sin recuento y sin saber por dónde iba.
    Eso ya pasó en el servidor dos veces, con el código de dos versiones distintas, así que no es
    del código: es del proceso.

    Volver a lanzar la comprobación cuesta minuto y medio y **no gasta ninguna versión**: la
    etiqueta, los ejecutables y los hashes están hechos y siguen siendo los mismos. Así que:

    * Un fallo **con informe** (la suite llegó al final y alguna comprobación no pasó) se cuenta
      tal cual y no se repite: repetirlo no arreglaría nada.
    * Una **muerte sin informe** se anota en voz alta —queda como aviso en la página del flujo,
      para no tapar el problema— y se vuelve a intentar, hasta `-Intentos` veces.

    Entre intentos se recoge lo que la pasada interrumpida pudo dejar a medias: las dos tareas del
    Programador que crea la propia suite y los procesos de la aplicación que hubiera lanzado la
    tarea programada. Sin eso, la repetición mediría un equipo distinto de la primera vez. Esa
    limpieza solo se hace en el servidor (`GITHUB_ACTIONS`): en un equipo de verdad no se tocan
    los procesos de nadie.

.PARAMETER Intentos
    Cuántas veces se puede lanzar la suite como mucho (por defecto 3).

.PARAMETER Titulo
    Título de las anotaciones de fallo que se ven en la página del flujo.

.EXAMPLE
    pwsh tools/pasada_de_la_suite.ps1
    Pasa la suite y falla igual que ella, repitiendo solo las muertes sin informe.
#>
param(
    [int]$Intentos = 3,
    [string]$Titulo = 'Fallo de la suite'
)

$ErrorActionPreference = 'Continue'

$base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$registro = Join-Path $base 'suite.log'
$intentos = [Math]::Max(1, $Intentos)
$codigo = 1
$sinInforme = 0

function Clear-Leftovers {
    # Lo que la pasada interrumpida pudo dejar a medias. Solo en el servidor: aquí no hay nada
    # de nadie más que se pueda romper, y en un equipo de verdad sí.
    if ($env:GITHUB_ACTIONS -ne 'true') { return }
    foreach ($tarea in @('\PCToolkit\Limpieza PC Toolkit', '\PCToolkit\Mantenimiento PC Toolkit')) {
        & schtasks /Delete /TN $tarea /F 2>&1 | Out-Null
    }
    # Los procesos que lanza la tarea programada (`python … app.py --limpieza`). Se mira la línea
    # de órdenes a propósito: parar todos los `python` del servidor sería de más.
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'app\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

for ($intento = 1; $intento -le $intentos; $intento++) {
    if ($intento -gt 1) {
        Write-Host "== Se vuelve a pasar la suite (intento $intento de $intentos) =="
        Clear-Leftovers
        Start-Sleep -Seconds 5
    }
    # Sin búfer (`-u`): si el proceso se muere a mitad, en el registro queda todo lo que había
    # contado hasta ese momento en vez de perderse el último bloque dentro del búfer.
    python -u tests/smoke_test.py 2>&1 | Tee-Object -FilePath $registro
    $codigo = $LASTEXITCODE
    if ($codigo -eq 0) { exit 0 }

    # ¿La suite llegó a dar su informe? Si lo dio, la pasada terminó y los fallos son suyos.
    $informe = @(
        Select-String -Path $registro -Pattern '^\d+/\d+ comprobaciones|^Fallos: ' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Line.Trim() }
    )
    if ($informe.Count -gt 0) { break }
    if ($intento -gt 1) {
        # El intento que acaba de morir se guarda: el registro del siguiente lo pisa.
        Copy-Item -LiteralPath $registro -Destination (Join-Path $base "suite_intento$($intento - 1).log") -Force -ErrorAction SilentlyContinue
    }
    $sinInforme++
    $cola = @(Get-Content -LiteralPath $registro -Tail 5 -ErrorAction SilentlyContinue) |
        ForEach-Object { $_.Trim() } | Where-Object { $_ }
    Write-Host "::warning title=La suite murió sin dar su informe::Intento $intento de $intentos. Últimas líneas: $($cola -join ' | ')"
}

# --- Lo que se cuenta -------------------------------------------------------------
# El cierre del recuento es el titular; si además hay fallos o excepciones, se anotan. Cuando no
# hay ni una cosa ni la otra, la pasada murió muda y se dicen las últimas líneas del registro,
# que son las que dicen por dónde iba.
$destacadas = @(
    Select-String -Path $registro -Pattern '\[FALLO\]|Traceback|Fatal' -ErrorAction SilentlyContinue |
        Select-Object -First 30 | ForEach-Object { $_.Line.Trim() } | Where-Object { $_ }
)
if (-not $destacadas) {
    $destacadas = @(Get-Content -LiteralPath $registro -Tail 10 -ErrorAction SilentlyContinue) |
        ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
$cierre = @(
    Select-String -Path $registro -Pattern '^\d+/\d+ comprobaciones|^Fallos: ' -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Line.Trim() }
)
$textos = @($cierre + $destacadas) | Select-Object -Unique | Select-Object -First 40

$resumen = @()
if ($sinInforme -gt 0) {
    $cuales = if ($sinInforme -eq 1) { 'un intento' } else { "$sinInforme intentos" }
    $resumen += "La suite se murió sin dar su informe en $cuales de $intentos; esto es lo que dijo la pasada que sí terminó."
}
$resumen += $textos

if ($resumen.Count -eq 0) {
    '### La suite falló sin dejar ni una línea de salida' |
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Encoding utf8
    Write-Host "::error title=$Titulo::El registro quedó vacío: la suite no llegó a escribir nada."
} else {
    '### Fallos de la suite' | Add-Content -Path $env:GITHUB_STEP_SUMMARY -Encoding utf8
    $resumen | ForEach-Object { '- ' + $_ } | Add-Content -Path $env:GITHUB_STEP_SUMMARY -Encoding utf8
    foreach ($linea in $resumen) {
        $texto = $linea -replace '%', '%25' -replace "`r", '%0D' -replace "`n", '%0A'
        Write-Host "::error title=$Titulo::$texto"
    }
}
if (Test-Path -LiteralPath $registro) {
    # El informe entero, plegado al final del resumen: en un servidor el registro no se puede
    # abrir desde otro equipo, y hay fallos que solo se ven con la lista completa delante.
    $cuerpo = @('', '<details><summary>Las comprobaciones de esta pasada</summary>', '', '```')
    $cuerpo += @(Get-Content -LiteralPath $registro -Encoding utf8)
    $cuerpo += @('```', '</details>')
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($cuerpo -join "`n") -Encoding utf8
}
exit $codigo
