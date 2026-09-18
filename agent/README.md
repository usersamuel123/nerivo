# NERIVO Agent Node

Local autonomous operations layer for NERIVO.

## Recommended starting model
Ollama currently lists gpt-oss:20b at about 14 GB with a 128K context window and native agentic/tool capabilities. Its published MXFP4 format is designed to fit systems with about 16 GB memory. Actual speed and GPU utilization depend on the AMD driver/runtime, so NERIVO should benchmark the local machine before increasing autonomy.

## One-time install
Open PowerShell in this folder and run:
Set-ExecutionPolicy -Scope Process Bypass
./install.ps1

The installer installs Ollama through winget if needed, downloads gpt-oss:20b, creates a Python environment, installs dependencies, creates a Windows logon task, and starts the agent.

## Configuration
Copy .env.example to .env. Keep secrets only in this local file. Never commit it.
Start with AGENT_MODE=supervised.

## Owner brief
python owner-brief.py

## Safety
No bank/card/credential handling. No irreversible actions. No outbound email by default. Every cycle is logged to SQLite.
