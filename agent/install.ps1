$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
Write-Host "=== NERIVO Agent installer ==="
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "winget non disponibile. Installa/aggiorna App Installer di Windows." }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "Installazione Ollama..."
  winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
}
Write-Host "Scarico il modello locale..."
ollama pull gpt-oss:20b
if (-not (Test-Path ".venv")) { py -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$task="NERIVO Agent"
$python=(Resolve-Path ".venv\Scripts\python.exe").Path
$script=(Resolve-Path "run_agent.py").Path
$action=New-ScheduledTaskAction -Execute $python -Argument "`"$script`""
$trigger=New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Description "NERIVO local autonomous agent" -Force | Out-Null
Write-Host "Installazione completata. Avvio agente..."
Start-ScheduledTask -TaskName $task
