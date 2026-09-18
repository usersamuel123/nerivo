# NERIVO Agent Node

Local autonomous operations layer for NERIVO.

## Purpose
Run a local agent on the owner's Windows PC. The agent keeps a durable task queue, calls a local Ollama model, performs bounded business/engineering tasks, and writes an audit log.

## First model
Recommended starting model: `gpt-oss:20b`. Ollama lists it at about 14 GB with 128K context and native tool/agentic capabilities, designed for local use. It is a reasonable first benchmark for a 16 GB GPU; actual performance depends on the AMD driver/runtime.

## Safety model
- Read-only analysis by default.
- No bank/card/credential handling.
- No irreversible actions.
- External messages are disabled until the operator explicitly enables outbound email.
- Code/deploy actions are represented as proposals until enabled.
- Every action is logged locally.

## Run
1. Install Ollama.
2. Pull the selected model.
3. Create the virtual environment and install requirements.
4. Copy `.env.example` to `.env`.
5. Start `python run_agent.py`.

The agent is intentionally conservative on first boot. Autonomy can be increased after observing its logs.
