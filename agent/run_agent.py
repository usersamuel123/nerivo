import email as email_lib
import html
import imaplib
import json
import os
import re
import smtplib
import sqlite3
import time
import traceback
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB = ROOT / "agent.db"

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
INTERVAL = max(5, int(os.getenv("AGENT_INTERVAL_MINUTES", "15")))
MODE = os.getenv("AGENT_MODE", "guarded_autonomous")
NERIVO = os.getenv("NERIVO_BASE_URL", "").rstrip("/")

BREVO_KEY = os.getenv("BREVO_API_KEY", "").strip()
BREVO_SENDER_ID = int(os.getenv("BREVO_SENDER_ID", "0") or 0)
NOTIFY_EMAIL = os.getenv("LEAD_NOTIFICATION_EMAIL", "").strip()

STRIPE_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
PAYMENT_LINK = os.getenv(
    "NERIVO_PAYMENT_LINK",
    "https://buy.stripe.com/8x2cN79YZbTLgh9ggIc3m00",
).strip()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "usersamuel123/nerivo").strip()
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM_ID = os.getenv("VERCEL_TEAM_ID", "").strip()
VERCEL_PROJECT_ID = os.getenv("VERCEL_PROJECT_ID", "").strip()

IMAP_HOST = os.getenv("NERIVO_EMAIL_IMAP_HOST", "").strip()
SMTP_HOST = os.getenv("NERIVO_EMAIL_SMTP_HOST", "").strip()
EMAIL_USER = os.getenv("NERIVO_EMAIL_USER", "").strip()
EMAIL_PASSWORD = os.getenv("NERIVO_EMAIL_PASSWORD", "").strip()

OUTBOUND = os.getenv("OUTBOUND_EMAIL_ENABLED", "false").lower() == "true"
AUTO_DEPLOY = os.getenv("AUTO_DEPLOY_ENABLED", "false").lower() == "true"
AUTO_PR = os.getenv("AUTO_PR_ENABLED", "true").lower() == "true"
AUTO_BILLING = os.getenv("AUTO_BILLING_CHANGES_ENABLED", "false").lower() == "true"

FOLLOWUP_DAYS = [0, 3, 7, 14]
RENEWAL_DAYS = int(os.getenv("RENEWAL_REMINDER_DAYS", "7"))
FEEDBACK_DAYS = int(os.getenv("FEEDBACK_DAYS", "14"))
REACTIVATION_DAYS = int(os.getenv("REACTIVATION_DAYS", "21"))


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute(
        """create table if not exists events(
            id integer primary key, ts text, kind text, data text)"""
    )
    c.execute(
        """create table if not exists tasks(
            id integer primary key, created text, status text, title text, detail text)"""
    )
    c.execute(
        """create table if not exists lead_state(
            email text primary key,
            first_seen text,
            last_seen text,
            replied text default '',
            last_outbound text default '',
            last_inbound text default '',
            followup_step integer default 0,
            next_action_at text default '',
            status text default 'new',
            intent text default '',
            need text default '',
            objections text default '',
            customer_id text default '',
            subscription_id text default '',
            checkout_session_id text default '',
            paid_at text default '',
            activated_at text default '',
            last_payment_at text default '',
            current_period_end text default '',
            last_feedback_at text default '',
            last_support_at text default '',
            opted_out integer default 0,
            last_name text default '',
            agency text default '',
            source text default 'brevo')"""
    )
    c.commit()
    return c


def log(kind, data):
    c = db()
    c.execute(
        "insert into events(ts,kind,data) values(?,?,?)",
        (iso(), kind, json.dumps(data, ensure_ascii=False, default=str)),
    )
    c.commit()
    c.close()


def add_task(title, detail, status="pending"):
    c = db()
    c.execute(
        "insert into tasks(created,status,title,detail) values(?,?,?,?)",
        (iso(), status, title, detail),
    )
    c.commit()
    c.close()


def remember_lead(email, name="", agency="", source="brevo"):
    email = email.strip().lower()
    if not email:
        return
    c = db()
    c.execute(
        """insert into lead_state(email,first_seen,last_seen,last_name,agency,source)
           values(?,?,?,?,?,?)
           on conflict(email) do update set
             last_seen=excluded.last_seen,
             last_name=coalesce(nullif(excluded.last_name,''),lead_state.last_name),
             agency=coalesce(nullif(excluded.agency,''),lead_state.agency),
             source=coalesce(nullif(excluded.source,''),lead_state.source)""",
        (email, iso(), iso(), name, agency, source),
    )
    c.commit()
    c.close()


def get_lead(email):
    c = db()
    row = c.execute("select * from lead_state where email=?", (email.lower(),)).fetchone()
    c.close()
    return dict(row) if row else None


def update_lead(email, **fields):
    allowed = {
        "replied", "last_outbound", "last_inbound", "followup_step", "next_action_at",
        "status", "intent", "need", "objections", "customer_id", "subscription_id",
        "checkout_session_id", "paid_at", "activated_at", "last_payment_at",
        "current_period_end", "last_feedback_at", "last_support_at", "opted_out",
        "last_name", "agency", "source",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    c = db()
    sql = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"update lead_state set {sql} where email=?", [*fields.values(), email.lower()])
    c.commit()
    c.close()


def already_sent_key(email, key):
    c = db()
    row = c.execute(
        "select 1 from events where kind=? and data like ? limit 1",
        ("email_sent", f'%"{email.lower()}"%"{key}"%'),
    ).fetchone()
    c.close()
    return bool(row)


def http_check(url):
    if not url:
        return {"configured": False}
    try:
        r = requests.get(url, timeout=20)
        return {"configured": True, "status": r.status_code, "ok": r.ok}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:200]}


def ollama(messages):
    r = requests.post(
        f"{OLLAMA}/api/chat",
        json={"model": MODEL, "messages": messages, "stream": False},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def brevo_headers():
    return {
        "accept": "application/json",
        "api-key": BREVO_KEY,
        "content-type": "application/json",
    }


def brevo_contacts():
    if not BREVO_KEY:
        return {"configured": False, "contacts": []}
    r = requests.get(
        "https://api.brevo.com/v3/contacts",
        headers=brevo_headers(),
        params={"limit": 100, "offset": 0, "sort": "desc"},
        timeout=30,
    )
    if not r.ok:
        return {"configured": True, "ok": False, "status": r.status_code, "contacts": []}
    return {"configured": True, "ok": True, "contacts": r.json().get("contacts", [])}


def send_brevo_email(to_email, subject, body_html, key="", reply_to=None):
    if not OUTBOUND:
        return {"sent": False, "reason": "OUTBOUND_EMAIL_ENABLED=false"}
    if not (BREVO_KEY and BREVO_SENDER_ID and to_email):
        return {"sent": False, "reason": "Brevo non configurato"}
    if key and already_sent_key(to_email, key):
        return {"sent": False, "reason": "idempotency_key_already_sent", "key": key}

    payload = {
        "sender": {"id": BREVO_SENDER_ID},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": body_html,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}

    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers=brevo_headers(),
        json=payload,
        timeout=30,
    )
    result = {"sent": r.ok, "status": r.status_code, "key": key}
    if not r.ok:
        result["detail"] = r.text[:500]
    else:
        log("email_sent", {"email": to_email.lower(), "key": key, "subject": subject})
        update_lead(
            to_email,
            last_outbound=iso(),
            replied=iso() if key == "initial" else (get_lead(to_email) or {}).get("replied", ""),
        )
    return result


def send_smtp_email(to_email, subject, body_text, key=""):
    if not OUTBOUND:
        return {"sent": False, "reason": "OUTBOUND_EMAIL_ENABLED=false"}
    if not (SMTP_HOST and EMAIL_USER and EMAIL_PASSWORD):
        return {"sent": False, "reason": "SMTP non configurato"}
    if key and already_sent_key(to_email, key):
        return {"sent": False, "reason": "idempotency_key_already_sent"}

    msg = EmailMessage()
    msg["From"] = EMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
    log("email_sent", {"email": to_email.lower(), "key": key, "subject": subject})
    update_lead(to_email, last_outbound=iso())
    return {"sent": True, "transport": "smtp", "key": key}


def stripe_get(path, params=None):
    if not STRIPE_KEY:
        return None
    r = requests.get(
        f"https://api.stripe.com/v1/{path}",
        headers={"Authorization": f"Bearer {STRIPE_KEY}"},
        params=params or {},
        timeout=30,
    )
    if not r.ok:
        log("stripe_error", {"path": path, "status": r.status_code, "detail": r.text[:300]})
        return None
    return r.json()


def stripe_snapshot():
    if not STRIPE_KEY:
        return {"configured": False}
    out = {"configured": True}
    balance = stripe_get("balance")
    if balance:
        out["balance"] = balance.get("available", [])
    subs = stripe_get("subscriptions", {"status": "all", "limit": 100})
    if subs:
        data = subs.get("data", [])
        out["active_subscriptions"] = sum(s.get("status") == "active" for s in data)
        out["total_subscriptions"] = len(data)
    return out


def process_stripe():
    if not STRIPE_KEY:
        return {"configured": False}

    sessions = stripe_get(
        "checkout/sessions",
        {"limit": 100, "status": "complete", "expand[]": "data.subscription"},
    )
    if not sessions:
        return {"configured": True, "ok": False}

    paid = 0
    for s in sessions.get("data", []):
        if s.get("payment_status") != "paid":
            continue
        details = s.get("customer_details") or {}
        email = (details.get("email") or "").strip().lower()
        if not email:
            continue

        remember_lead(email, details.get("name", ""), "", "stripe")
        lead = get_lead(email) or {}
        if lead.get("checkout_session_id") == s.get("id") and lead.get("paid_at"):
            continue

        subscription = s.get("subscription")
        sub_id = subscription.get("id") if isinstance(subscription, dict) else subscription or ""
        customer_id = s.get("customer") or ""
        update_lead(
            email,
            customer_id=customer_id,
            subscription_id=sub_id,
            checkout_session_id=s.get("id", ""),
            paid_at=lead.get("paid_at") or iso(),
            last_payment_at=iso(),
            status="paid",
            activated_at=lead.get("activated_at", ""),
        )
        paid += 1
        log("payment_detected", {
            "email": email, "session_id": s.get("id"), "subscription_id": sub_id
        })

        if not lead.get("activated_at"):
            first = (details.get("name") or "ciao").split()[0]
            body = f"""<p>Ciao {html.escape(first)},</p>
<p>pagamento ricevuto: benvenuto in NERIVO.</p>
<p>Il tuo accesso è stato registrato. Il prossimo passo è completare l'onboarding e collegare il flusso lead della tua agenzia.</p>
<p><a href="{html.escape(NERIVO)}/workspace.html">Apri il workspace NERIVO</a></p>
<p>Se hai bisogno di assistenza, rispondi direttamente a questa email.</p>
<p>A presto,<br>NERIVO</p>"""
            result = send_brevo_email(email, "NERIVO — attivazione completata", body, "activation")
            if result.get("sent"):
                update_lead(email, activated_at=iso(), status="active")

    return {"configured": True, "ok": True, "paid_detected": paid}


def process_subscriptions():
    if not STRIPE_KEY:
        return {"configured": False}
    data = stripe_get("subscriptions", {"status": "all", "limit": 100})
    if not data:
        return {"configured": True, "ok": False}

    changed = 0
    for s in data.get("data", []):
        customer = s.get("customer")
        customer_obj = customer if isinstance(customer, dict) else {}
        email = ""
        if customer_obj:
            email = (customer_obj.get("email") or "").lower().strip()
        if not email and isinstance(customer, str):
            customer_data = stripe_get(f"customers/{customer}")
            email = ((customer_data or {}).get("email") or "").lower().strip()
        if not email:
            continue

        remember_lead(email, customer_obj.get("name", "") if customer_obj else "", "", "stripe")
        status = s.get("status", "")
        period_end = s.get("current_period_end")
        period_end_iso = (
            datetime.fromtimestamp(period_end, timezone.utc).isoformat()
            if period_end else ""
        )
        lead = get_lead(email) or {}
        update_lead(
            email,
            customer_id=s.get("customer", "") if isinstance(s.get("customer"), str) else "",
            subscription_id=s.get("id", ""),
            current_period_end=period_end_iso,
            status="active" if status == "active" else status,
        )
        changed += 1

        if status == "active" and period_end:
            days = (datetime.fromtimestamp(period_end, timezone.utc) - now()).days
            if 0 <= days <= RENEWAL_DAYS and not already_sent_key(email, "renewal_reminder"):
                body = "<p>Ciao,</p><p>il tuo rinnovo NERIVO è previsto a breve. Non devi fare nulla se il metodo di pagamento è attivo.</p><p>A presto,<br>NERIVO</p>"
                send_brevo_email(email, "NERIVO — rinnovo in arrivo", body, "renewal_reminder")
        elif status in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
            if not already_sent_key(email, f"billing_{status}"):
                body = "<p>Ciao,</p><p>abbiamo rilevato un problema con il rinnovo del tuo abbonamento NERIVO.</p><p>Controlla il metodo di pagamento nel portale cliente Stripe oppure rispondi a questa email per assistenza.</p><p>NERIVO</p>"
                send_brevo_email(email, "NERIVO — attenzione al rinnovo", body, f"billing_{status}")

    return {"configured": True, "ok": True, "subscriptions_processed": changed}


def parse_address(msg):
    value = msg.get("From", "")
    match = re.search(r"<([^>]+)>", value)
    return (match.group(1) if match else value).strip().lower()


def decode_subject(value):
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        (p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p)
        for p, enc in parts
    )


def extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")).lower():
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            return str(msg.get_payload())
    return ""


def process_inbound_email():
    if not (IMAP_HOST and EMAIL_USER and EMAIL_PASSWORD):
        return {"configured": False}

    count = 0
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select("INBOX")
        status, data = mail.search(None, "UNSEEN")
        if status != "OK":
            return {"configured": True, "ok": False}
        ids = data[0].split()[-50:]
        for msg_id in ids:
            status, raw = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            msg = email_lib.message_from_bytes(raw[0][1])
            sender = parse_address(msg)
            if not sender or sender == EMAIL_USER.lower():
                continue
            body = extract_text(msg).strip()[:6000]
            if not body:
                continue

            remember_lead(sender, "", "", "email")
            classification = ollama([
                {"role": "system", "content": (
                    "Classifica una risposta commerciale NERIVO in JSON valido con "
                    "intent, need, objection, opt_out, urgency. Non inventare."
                )},
                {"role": "user", "content": body},
            ])
            try:
                obj = json.loads(classification)
            except Exception:
                obj = {"intent": "unknown", "need": "", "objection": "", "opt_out": False, "urgency": "normal"}

            lead = get_lead(sender) or {}
            opt_out = bool(obj.get("opt_out"))
            update_lead(
                sender,
                last_inbound=iso(),
                replied=iso(),
                followup_step=99 if opt_out else lead.get("followup_step", 0),
                next_action_at="",
                status="opted_out" if opt_out else "engaged",
                intent=str(obj.get("intent", ""))[:200],
                need=str(obj.get("need", ""))[:500],
                objections=str(obj.get("objection", ""))[:500],
                opted_out=1 if opt_out else lead.get("opted_out", 0),
                last_support_at=iso() if "support" in str(obj.get("intent", "")).lower() else lead.get("last_support_at", ""),
            )
            log("inbound_email", {"email": sender, "subject": decode_subject(msg.get("Subject", "")), "classification": obj})

            if opt_out:
                count += 1
                continue

            prompt = f"""Sei l'assistente commerciale di NERIVO.
Rispondi a questa email in italiano, breve, professionale e umano.
Obiettivo: capire il bisogno e portare al prossimo passo senza pressione.
Se c'è un'obiezione, rispondi in modo concreto.
Se chiede assistenza, aiutalo.
Se è pronto ad acquistare, usa questo checkout: {PAYMENT_LINK}
Non inventare funzionalità, prezzi o risultati.
Email ricevuta:
{body}"""
            reply = ollama([{"role": "system", "content": prompt}])
            result = send_brevo_email(
                sender,
                "Re: " + decode_subject(msg.get("Subject", "NERIVO")),
                "<p>" + html.escape(reply).replace("\n", "<br>") + "</p>",
                f"inbound_reply_{msg_id.decode(errors='ignore')}",
            )
            if result.get("sent"):
                update_lead(sender, status="engaged")
            count += 1
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return {"configured": True, "ok": True, "processed": count}


def qualify_lead(email):
    lead = get_lead(email)
    if not lead or lead.get("opted_out"):
        return
    if lead.get("intent") and lead.get("need"):
        return

    context = json.dumps({
        "name": lead.get("last_name"),
        "agency": lead.get("agency"),
        "status": lead.get("status"),
        "source": lead.get("source"),
    }, ensure_ascii=False)
    result = ollama([
        {"role": "system", "content": (
            "Sei un qualificatore commerciale B2B. Restituisci JSON con "
            "intent, need, qualification, objection, next_step. Usa solo dati presenti; "
            "se mancano informazioni usa unknown."
        )},
        {"role": "user", "content": context},
    ])
    try:
        obj = json.loads(result)
    except Exception:
        obj = {"intent": "unknown", "need": "unknown", "qualification": "unknown", "objection": "", "next_step": "ask"}

    update_lead(
        email,
        intent=str(obj.get("intent", ""))[:200],
        need=str(obj.get("need", ""))[:500],
        objections=str(obj.get("objection", ""))[:500],
        status="qualified" if obj.get("qualification") in {"qualified", "high"} else "new",
    )
    log("lead_qualified", {"email": email, "result": obj})


def process_brevo_leads():
    data = brevo_contacts()
    if not data.get("ok"):
        return data

    seen = 0
    new_count = 0
    actions = []
    for c in data.get("contacts", []):
        email = str(c.get("email") or "").strip().lower()
        if not email:
            continue
        attrs = c.get("attributes") or {}
        name = str(attrs.get("FIRSTNAME") or attrs.get("NAME") or "").strip()
        agency = str(attrs.get("COMPANY") or attrs.get("AGENCY") or "").strip()
        existed = get_lead(email) is not None
        remember_lead(email, name, agency, "brevo")
        seen += 1
        if not existed:
            new_count += 1
            update_lead(email, status="new", next_action_at=iso())
        qualify_lead(email)

    return {"configured": True, "ok": True, "contacts_seen": seen, "new_leads": new_count, "actions": actions}


def process_sales_followups():
    c = db()
    rows = c.execute(
        """select * from lead_state
           where opted_out=0 and paid_at='' and status not in ('closed','opted_out','paid','active')"""
    ).fetchall()
    c.close()

    processed = 0
    sent = 0
    for row in rows:
        lead = dict(row)
        email = lead["email"]
        if not OUTBOUND:
            continue
        last_in = lead.get("last_inbound") or ""
        last_out = lead.get("last_outbound") or ""
        if last_in and (not last_out or last_in > last_out):
            continue

        first_seen = datetime.fromisoformat(lead["first_seen"])
        age = (now() - first_seen).days
        step = int(lead.get("followup_step") or 0)
        target = next((i for i, d in enumerate(FOLLOWUP_DAYS) if age >= d and i >= step), None)
        if target is None:
            continue
        if target > 0 and last_out:
            days_since = (now() - datetime.fromisoformat(last_out)).days
            if days_since < FOLLOWUP_DAYS[target] - FOLLOWUP_DAYS[target - 1]:
                continue

        first = (lead.get("last_name") or "ciao").split()[0]
        if target == 0:
            subject = "NERIVO — il prossimo passo per la tua agenzia"
            body = f"<p>Ciao {html.escape(first)},</p><p>grazie per aver mostrato interesse per NERIVO.</p><p>Se vuoi partire subito: <a href="{html.escape(PAYMENT_LINK)}">attiva NERIVO</a>.</p><p>Se invece vuoi prima capire se si adatta al tuo flusso, rispondi a questa email con come gestisci oggi i lead.</p><p>NERIVO</p>"
        elif target == 1:
            subject = "NERIVO — hai ancora una richiesta aperta"
            body = f"<p>Ciao {html.escape(first)},</p><p>riprendo la tua richiesta NERIVO. Se vuoi partire subito, puoi attivarlo qui: <a href="{html.escape(PAYMENT_LINK)}">checkout NERIVO</a>.</p><p>Se hai una domanda o un dubbio, rispondi pure.</p><p>NERIVO</p>"
        elif target == 2:
            subject = "NERIVO — una domanda veloce"
            body = f"<p>Ciao {html.escape(first)},</p><p>qual è il principale punto del tuo processo lead che vorresti automatizzare?</p><p>Se preferisci, puoi anche partire direttamente da qui: <a href="{html.escape(PAYMENT_LINK)}">attiva NERIVO</a>.</p><p>NERIVO</p>"
        else:
            subject = "NERIVO — chiudo qui per ora"
            body = "<p>Ciao,</p><p>non voglio riempirti la casella. Chiudo qui il follow-up; se vorrai riprendere il discorso, puoi rispondere a questa email.</p><p>NERIVO</p>"

        result = send_brevo_email(email, subject, body, f"followup_{target}")
        if result.get("sent"):
            update_lead(email, last_outbound=iso(), followup_step=target + 1, next_action_at="")
            if target == 3:
                update_lead(email, status="nurture")
            sent += 1
        processed += 1

    return {"processed": processed, "sent": sent, "outbound_enabled": OUTBOUND}


def process_customer_lifecycle():
    c = db()
    rows = c.execute(
        "select * from lead_state where paid_at<>'' and opted_out=0"
    ).fetchall()
    c.close()
    sent = 0
    for lead in map(dict, rows):
        email = lead["email"]
        paid_at = lead.get("paid_at") or ""
        if not paid_at:
            continue
        paid_dt = datetime.fromisoformat(paid_at)
        age = (now() - paid_dt).days

        if age >= FEEDBACK_DAYS and not lead.get("last_feedback_at"):
            body = "<p>Ciao,</p><p>come sta andando NERIVO nel tuo flusso lead?</p><p>Rispondi con una riga: cosa funziona e cosa vorresti migliorare.</p><p>NERIVO</p>"
            result = send_brevo_email(email, "NERIVO — come sta andando?", body, "feedback_request")
            if result.get("sent"):
                update_lead(email, last_feedback_at=iso())
                sent += 1

        if age >= 30 and not already_sent_key(email, "upsell_check"):
            log("upsell_candidate", {"email": email, "reason": "active_customer_30d"})
            update_lead(email, status="upsell_candidate")
            add_task("Upsell candidate", f"Cliente {email} attivo da {age} giorni: valutare modulo aggiuntivo.", "pending")

    return {"customers": len(rows), "lifecycle_emails_sent": sent}


def process_reactivation():
    c = db()
    rows = c.execute(
        """select * from lead_state
           where opted_out=0 and paid_at='' and status in ('nurture','new','qualified')"""
    ).fetchall()
    c.close()
    sent = 0
    for lead in map(dict, rows):
        email = lead["email"]
        seen = datetime.fromisoformat(lead["last_seen"])
        if (now() - seen).days < REACTIVATION_DAYS:
            continue
        if already_sent_key(email, "reactivation"):
            continue
        body = f"<p>Ciao,</p><p>riapro un attimo il discorso NERIVO: se vuoi automatizzare il follow-up dei tuoi lead, puoi partire qui: <a href="{html.escape(PAYMENT_LINK)}">attiva NERIVO</a>.</p><p>Se non ti interessa più, basta rispondere e non ti ricontatteremo.</p><p>NERIVO</p>"
        result = send_brevo_email(email, "NERIVO — riapriamo il discorso?", body, "reactivation")
        if result.get("sent"):
            sent += 1
    return {"reactivation_sent": sent}


def github_snapshot():
    if not GITHUB_TOKEN:
        return {"configured": False}
    h = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {GITHUB_TOKEN}"}
    out = {"configured": True, "repository": GITHUB_REPOSITORY}
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}", headers=h, timeout=20)
        out["repo_ok"] = r.ok
        if r.ok:
            repo = r.json()
            out["default_branch"] = repo.get("default_branch")
            out["open_issues"] = repo.get("open_issues_count")
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/commits", headers=h, params={"per_page": 5}, timeout=20)
        out["recent_commits_ok"] = r.ok
        if r.ok:
            out["recent_commits"] = [x.get("sha", "")[:7] for x in r.json()]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def vercel_snapshot():
    if not (VERCEL_TOKEN and VERCEL_PROJECT_ID and VERCEL_TEAM_ID):
        return {"configured": False}
    h = {"Authorization": f"Bearer {VERCEL_TOKEN}"}
    out = {"configured": True}
    try:
        r = requests.get(
            f"https://api.vercel.com/v9/projects/{VERCEL_PROJECT_ID}",
            headers=h, params={"teamId": VERCEL_TEAM_ID}, timeout=20
        )
        out["project_ok"] = r.ok
        if r.ok:
            out["project_name"] = r.json().get("name")
        r = requests.get(
            "https://api.vercel.com/v6/deployments",
            headers=h,
            params={"projectId": VERCEL_PROJECT_ID, "teamId": VERCEL_TEAM_ID, "limit": 5},
            timeout=20,
        )
        out["deployments_ok"] = r.ok
        if r.ok:
            out["deployments"] = [
                {"state": x.get("state"), "url": x.get("url")}
                for x in r.json().get("deployments", [])
            ]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def cycle():
    state = {
        "time": iso(),
        "mode": MODE,
        "model": MODEL,
        "site": http_check(NERIVO),
        "ollama": http_check(OLLAMA),
        "integrations": {
            "brevo": bool(BREVO_KEY),
            "stripe": bool(STRIPE_KEY),
            "github": bool(GITHUB_TOKEN),
            "vercel": bool(VERCEL_TOKEN and VERCEL_PROJECT_ID and VERCEL_TEAM_ID),
            "inbound_email": bool(IMAP_HOST and EMAIL_USER and EMAIL_PASSWORD),
            "outbound_email": OUTBOUND,
            "auto_deploy": AUTO_DEPLOY,
            "auto_pr": AUTO_PR,
            "auto_billing": AUTO_BILLING,
        },
    }

    state["inbound"] = process_inbound_email()
    state["lead_cycle"] = process_brevo_leads()
    state["sales"] = process_sales_followups()
    state["stripe"] = process_stripe()
    state["subscriptions"] = process_subscriptions()
    state["customer_lifecycle"] = process_customer_lifecycle()
    state["reactivation"] = process_reactivation()
    state["github"] = github_snapshot()
    state["vercel"] = vercel_snapshot()

    prompt = f"""Sei NERIVO Agent, operatore autonomo di un SaaS B2B italiano.
Stato verificato del ciclo:
{json.dumps(state, ensure_ascii=False, indent=2, default=str)}

Produci un report breve e fattuale con:
1. RISULTATO
2. FATTO AUTOMATICAMENTE
3. DA APPROVARE
4. METRICHE
5. PROSSIMO CICLO

Regola assoluta: non dichiarare mai eseguita un'azione che non compare nello stato come riuscita.
Se una integrazione non è configurata, dichiarala come non disponibile.
Non inventare lead, clienti, pagamenti, email o deploy.
Non proporre spam, scraping aggressivo, frodi o promesse di guadagno.
Le modifiche a prezzi Stripe, rimborsi, trasferimenti di denaro, cancellazioni distruttive e campagne massive restano manuali.
"""
    answer = ollama([
        {
            "role": "system",
            "content": (
                "Sei il reporting layer di NERIVO. Usa esclusivamente lo stato verificato. "
                "Sii conciso e non trasformare piani in azioni eseguite."
            ),
        },
        {"role": "user", "content": prompt},
    ])
    log("agent_cycle", {"answer": answer, "state": state})
    print("\n" + "=" * 72 + "\n" + answer + "\n" + "=" * 72, flush=True)


def main():
    db().close()
    log("startup", {"mode": MODE, "model": MODEL})
    print(f"NERIVO Agent avviato | model={MODEL} | mode={MODE}", flush=True)
    while True:
        try:
            cycle()
        except Exception as e:
            log("error", {"error": str(e), "trace": traceback.format_exc()})
            print("Agent error:", e, flush=True)
        time.sleep(INTERVAL * 60)


if __name__ == "__main__":
    main()
