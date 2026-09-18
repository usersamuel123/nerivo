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

## Go-live checklist

1. Configure `BREVO_API_KEY` in Vercel.
2. Finalize the legal/privacy information before actively collecting prospect data.
3. Verify the Stripe business/tax settings for the Italian business.
4. Connect a custom domain only after the NERIVO name/domain clearance decision.
5. Run an end-to-end demo-form test and a Stripe test purchase/operational check as appropriate.

## Architecture

`index.html` + `styles.css` + `app.js` provide the frontend.

`api/demo.js` validates the demo request server-side and, when `BREVO_API_KEY` is configured, creates/updates the contact in Brevo and sends an internal notification.

The public frontend never receives the Brevo key.
