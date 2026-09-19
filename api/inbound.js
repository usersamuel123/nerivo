import crypto from "node:crypto";

export const config = {
  api: { bodyParser: false },
};

function header(req, name) {
  const value = req.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value || "";
}

async function rawBody(req) {
  if (Buffer.isBuffer(req.body)) return req.body.toString("utf8");
  if (typeof req.body === "string") return req.body;
  const chunks = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

function verifyResend(payload, req) {
  const secret = (process.env.RESEND_WEBHOOK_SECRET || "").trim();
  const id = header(req, "svix-id");
  const timestamp = header(req, "svix-timestamp");
  const signature = header(req, "svix-signature");
  if (!secret || !id || !timestamp || !signature) return false;

  const ts = Number(timestamp);
  if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > 300) return false;

  const secretBytes = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  const signed = id + "." + timestamp + "." + payload;
  const expected = crypto.createHmac("sha256", secretBytes).update(signed).digest("base64");

  return signature.split(" ").some((part) => {
    const value = part.includes(",") ? part.split(",")[1] : part;
    if (!value) return false;
    const a = Buffer.from(value);
    const b = Buffer.from(expected);
    return a.length === b.length && crypto.timingSafeEqual(a, b);
  });
}

function resendEvent(payload, req) {
  try {
    const event = JSON.parse(payload || "{}");
    if (event?.type !== "email.received" || !event?.data?.email_id) return null;
    return verifyResend(payload, req) ? event : null;
  } catch {
    return null;
  }
}

function brevoSecretOk(req) {
  const expected = process.env.INBOUND_WEBHOOK_SECRET || process.env.BREVO_INBOUND_WEBHOOK_SECRET || "";
  if (!expected) return false;
  return (header(req, "x-nerivo-webhook-secret") || header(req, "x-webhook-secret")) === expected;
}

async function notify(subject, text) {
  if (!(process.env.LEAD_NOTIFICATION_EMAIL && process.env.BREVO_API_KEY && process.env.BREVO_SENDER_ID)) return;
  await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: { "api-key": process.env.BREVO_API_KEY, "content-type": "application/json" },
    body: JSON.stringify({
      sender: { id: Number(process.env.BREVO_SENDER_ID) },
      to: [{ email: process.env.LEAD_NOTIFICATION_EMAIL }],
      subject: "[NERIVO INBOUND] " + subject,
      textContent: text,
    }),
  });
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ ok: false });

  try {
    const payload = await rawBody(req);
    const resend = resendEvent(payload, req);

    if (resend) {
      const emailId = resend.data.email_id;
      const subject = String(resend.data.subject || "").slice(0, 300);
      await notify(subject, "Resend inbound ricevuto. email_id=" + emailId + "\nFrom: " + (resend.data.from || "") + "\nTo: " + (resend.data.to || []).join(", ") + "\nSubject: " + subject);
      return res.status(200).json({ ok: true, provider: "resend", email_id: emailId });
    }

    if (!brevoSecretOk(req)) return res.status(401).json({ ok: false });

    let parsed;
    try { parsed = JSON.parse(payload || "{}"); } catch { return res.status(400).json({ ok: false }); }
    const items = Array.isArray(parsed.items) ? parsed.items : [parsed];
    let accepted = 0;
    for (const item of items.slice(0, 20)) {
      const from = typeof item.From === "string" ? item.From : (item.From?.Address || "");
      const subject = String(item.Subject || "").slice(0, 300);
      const text = String(item.ExtractedMarkdownMessage || item.RawTextBody || "").slice(0, 10000);
      if (!from || !text) continue;
      await notify(subject, "From: " + from + "\nSubject: " + subject + "\n\n" + text);
      accepted++;
    }
    return res.status(200).json({ ok: true, accepted, provider: "brevo" });
  } catch (error) {
    return res.status(400).json({ ok: false, error: "invalid_payload" });
  }
}
