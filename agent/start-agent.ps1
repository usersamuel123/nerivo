$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "Ollama non trovato. Installalo da https://ollama.com/download"
  exit 1
}
ollama pull gpt-oss:20b
if (-not (Test-Path ".venv")) { py -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
& ".\.venv\Scripts\python.exe" run_agent.py
