# NERIVO Agent — required integrations

Put secrets ONLY in the local `agent/.env` file.

Required for full autonomous operation:
- BREVO_API_KEY
- BREVO_SENDER_ID
- LEAD_NOTIFICATION_EMAIL
- STRIPE_SECRET_KEY (live mode only when you intentionally enable sales automation)
- GITHUB_TOKEN (repository-scoped token for the NERIVO repo)
- VERCEL_TOKEN
- VERCEL_TEAM_ID / VERCEL_PROJECT_ID if needed by the Vercel API
- NERIVO_EMAIL_IMAP_HOST / NERIVO_EMAIL_SMTP_HOST
- NERIVO_EMAIL_USER / NERIVO_EMAIL_PASSWORD

Never commit `.env`.

## Operating policy

The agent may automatically:
- read and classify incoming NERIVO leads;
- draft and, after outbound is explicitly enabled, send one-to-one transactional/customer-service replies;
- schedule follow-ups;
- read Stripe revenue/subscription state;
- maintain a product backlog;
- create branches, commits and pull requests;
- run tests;
- deploy only according to the configured production policy;
- generate daily/weekly owner briefs.

The agent must request owner approval for:
- refunds, payouts or money transfers;
- changing Stripe pricing or billing terms;
- legal/contractual commitments;
- deleting customer data;
- bulk promotional outreach;
- actions outside the NERIVO repository;
- credential changes.

No system should promise revenue or guarantee outcomes.
