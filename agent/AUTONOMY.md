# NERIVO Agent — Revenue Operating System

The local agent runs a guarded autonomous commercial loop on the owner's PC.

## Automatic lifecycle

1. Lead capture and durable CRM state
2. Deduplication
3. AI qualification: intent, need, objection and next step
4. One-to-one sales replies when inbound email is configured
5. Scheduled follow-ups with idempotency and stop conditions
6. Stripe checkout/payment detection
7. Customer activation email and lifecycle state
8. Subscription/renewal monitoring
9. Failed-renewal recovery email
10. Customer feedback request
11. Upsell candidate detection
12. Lead reactivation
13. Commercial metrics and audit log
14. NERIVO/Ollama/GitHub/Vercel health monitoring

## Communications

Brevo is the primary outbound transport. Optional IMAP/SMTP enables the agent to read and answer inbound customer email. Outbound sending remains disabled until `OUTBOUND_EMAIL_ENABLED=true`.

The agent never sends another sales follow-up after a detected inbound reply. Explicit opt-out is stored and stops automated outreach.

## Stripe

The agent may read checkout sessions, customers and subscriptions to detect:
- completed payment
- active subscription
- upcoming renewal
- past-due/unpaid subscription

It may send customer communications based on those verified states.

It does **not** autonomously:
- change prices
- change billing configuration
- issue refunds
- make payouts/transfers
- move money

## Product operations

GitHub and Vercel health are monitored. Repository scope is restricted to `GITHUB_REPOSITORY`.

The local engineering loop can automatically repair verified technical failures: it inspects CI/Vercel/health state, asks the local Ollama model for a minimal patch, restricts editable paths, runs local checks, opens a pull request, waits for `NERIVO CI`, and can merge automatically when CI passes. Vercel's Git integration can then deploy the merged `main` commit.

The engineering loop does not modify workflows, Vercel configuration, legal pages, secrets, billing configuration or destructive customer data. It only acts on a concrete detected failure; it does not invent failures.

## Accounting

Italian fiscal accounting and invoicing remain with the owner's commercialista. NERIVO only keeps commercial/payment records needed for operations.

## Safety

- never expose secrets
- never invent tool results
- never claim an action happened without a successful API result
- no bulk promotional campaigns
- no aggressive scraping
- no destructive customer-data deletion
- no legal commitments
- keep an audit event for material actions

Secrets belong only in the local `.env` file and must never be committed.
