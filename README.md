# NERIVO

SaaS revenue automation for Italian real-estate agencies.

## Current MVP

- Premium static landing page
- Real Stripe checkout for the Founding offer
- Founding offer: €99 activation + €29/month
- Demo-request API at `/api/demo`
- Brevo integration prepared server-side
- Honeypot anti-spam field
- No Brevo credentials committed to the repository

## Required production secret

The Vercel project must have this environment variable:

`BREVO_API_KEY`

Set it as a **server-side secret** for Preview/Production. Never put the value in `index.html`, `app.js`, GitHub, or client-side JavaScript.

## Stripe

The current Founding checkout is hosted by Stripe. Payment Link:

`https://buy.stripe.com/8x2cN79YZbTLgh9ggIc3m00`

The site does not expose Stripe secret credentials.

## Production checklist

1. Configure the Brevo server-side secrets in Vercel for Production.
2. Legal pages contain the supplied Italian business identity data; operational legal review remains separate from deployment.
3. Verify Stripe live checkout and Italian tax/business settings.
4. Keep the public Vercel URL until the NERIVO domain is cleared and configured.
5. Run the demo form and health endpoint end-to-end.
6. Keep the local autonomous agent secrets only in `agent/.env`; never commit them.

## Stack

GitHub + Vercel + Brevo + Stripe + local Ollama agent. Cloudflare is not required by the NERIVO stack.

## Architecture

`index.html` + `styles.css` + `app.js` provide the frontend.

`api/demo.js` validates the demo request server-side and, when `BREVO_API_KEY` is configured, creates/updates the contact in Brevo and sends an internal notification.

The public frontend never receives the Brevo key.

## Deployment check

A fresh deployment should expose `/api/health`, which reports only whether the three required environment variables are present. It never returns their values.
