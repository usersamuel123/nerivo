# NERIVO Agent — autonomy policy

The local agent operates continuously on the owner's PC.

Automatic loops:
- health checks for NERIVO and local Ollama
- Brevo lead discovery and durable lead state
- one-to-one follow-up only when OUTBOUND_EMAIL_ENABLED=true
- Stripe revenue/subscription read-only metrics
- GitHub repository health/recent commits
- Vercel project/deployment health
- SQLite audit log
- local LLM operational prioritization

Safety gates:
- no bank/card credentials
- no refunds, payouts or money transfers
- no Stripe price/billing changes
- no destructive customer-data deletion
- no bulk promotional campaigns
- repository scope limited by GITHUB_REPOSITORY
- no invented tool results

Outbound is disabled by default. If enabled, the current implementation sends one-to-one follow-up to leads captured by the NERIVO demo flow and includes the existing Founding payment link. Use a verified sender and comply with applicable privacy and e-mail rules.

Keep all secrets only in the local agent .env file and never commit it.
