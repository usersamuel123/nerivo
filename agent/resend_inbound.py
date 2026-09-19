import html
import json
import os
import sqlite3
from datetime import datetime, timezone

import requests

ROOT = __import__("pathlib").Path(__file__).resolve().parent
DB = ROOT / "agent.db"

RESEND_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_ADDRESS = os.getenv("RESEND_INBOUND_ADDRESS", "").strip().lower()
BREVO_KEY = os.getenv("BREVO_API_KEY", "").strip()
BREVO_SENDER_ID = int(os.getenv("BREVO_SENDER_ID", "0") or 0)
OUTBOUND = os.getenv("OUTBOUND_EMAIL_ENABLED", "false").lower() == "true"
PAYMENT_LINK = os.getenv("NERIVO_PAYMENT_LINK", "https://buy.stripe.com/8x2cN79YZbTLgh9ggIc3m00").strip()
OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
MAX_MESSAGES = max(1, int(os.getenv("RESEND_POLL_LIMIT", "25")))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""create table if not exists inbound_messages(
        email_id text primary key,
        received_at text not null,
        from_email text default '',
        subject text default '',
        body text default '',
        processed integer default 0,
        error text default ''
    )""")
    c.commit()
    return c


def _log(kind, data):
    c = _db()
    c.execute("create table if not exists events(id integer primary key, ts text, kind text, data text)")
    c.execute("insert into events(ts,kind,data) values(?,?,?)", (_now(), kind, json.dumps(data, ensure_ascii=False, default=str)))
    c.commit()
    c.close()


def _remember(email, name="", source="resend"):
    c = _db()
    c.execute("""create table if not exists lead_state(
        email text primary key, first_seen text, last_seen text, replied text default '',
        last_outbound text default '', last_inbound text default '', followup_step integer default 0,
        next_action_at text default '', status text default 'new', intent text default '',
        need text default '', objections text default '', customer_id text default '',
        subscription_id text default '', checkout_session_id text default '', paid_at text default '',
        activated_at text default '', last_payment_at text default '', current_period_end text default '',
        last_feedback_at text default '', last_support_at text default '', opted_out integer default 0,
        last_name text default '', agency text default '', source text default 'brevo'
    )""")
    existing = {r[1] for r in c.execute("pragma table_info(lead_state)").fetchall()}
    for col, definition in {
        "replied":"text default ''","last_outbound":"text default ''","last_inbound":"text default ''",
        "followup_step":"integer default 0","next_action_at":"text default ''","status":"text default 'new'",
        "intent":"text default ''","need":"text default ''","objections":"text default ''",
        "customer_id":"text default ''","subscription_id":"text default ''","checkout_session_id":"text default ''",
        "paid_at":"text default ''","activated_at":"text default ''","last_payment_at":"text default ''",
        "current_period_end":"text default ''","last_feedback_at":"text default ''","last_support_at":"text default ''",
        "opted_out":"integer default 0","last_name":"text default ''","agency":"text default ''",
        "source":"text default 'brevo'"
    }.items():
        if col not in existing:
            c.execute(f"alter table lead_state add column {col} {definition}")
    c.execute("""insert into lead_state(email,first_seen,last_seen,last_name,source)
                 values(?,?,?,?,?)
                 on conflict(email) do update set last_seen=excluded.last_seen,
                 last_name=case when excluded.last_name<>'' then excluded.last_name else lead_state.last_name end,
                 source=excluded.source""", (email.lower(), _now(), _now(), name, source))
    c.commit()
    c.close()


def _lead(email):
    c = _db()
    row = c.execute("select * from lead_state where email=?", (email.lower(),)).fetchone()
    c.close()
    return dict(row) if row else {}


def _update(email, **fields):
    allowed = {"replied","last_outbound","last_inbound","followup_step","next_action_at","status",
               "intent","need","objections","last_support_at","opted_out","last_name","source"}
    fields = {k:v for k,v in fields.items() if k in allowed}
    if not fields:
        return
    c = _db()
    sql = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"update lead_state set {sql} where email=?", [*fields.values(), email.lower()])
    c.commit()
    c.close()


def _resend(path, params=None):
    if not RESEND_KEY:
        return None
    r = requests.get("https://api.resend.com" + path,
                     headers={"Authorization": f"Bearer {RESEND_KEY}", "Accept": "application/json"},
                     params=params or {}, timeout=30)
    if not r.ok:
        _log("resend_api_error", {"path": path, "status": r.status_code, "detail": r.text[:300]})
        return None
    return r.json()


def _ollama(messages):
    r = requests.post(f"{OLLAMA}/api/chat",
                      json={"model": MODEL, "messages": messages, "stream": False},
                      timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _send(to_email, subject, text, key):
    if not OUTBOUND or not (BREVO_KEY and BREVO_SENDER_ID):
        return {"sent": False, "reason": "outbound_not_configured"}
    c = _db()
    c.execute("create table if not exists events(id integer primary key, ts text, kind text, data text)")
    existing = c.execute("select 1 from events where kind='email_sent' and data like ? limit 1",
                         (f'%"email": "{to_email.lower()}"%"key": "{key}"%',)).fetchone()
    c.close()
    if existing:
        return {"sent": False, "reason": "already_sent"}
    payload = {
        "sender": {"id": BREVO_SENDER_ID},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": "<p>" + html.escape(text).replace("\n", "<br>") + "</p>",
    }
    if RESEND_ADDRESS:
        payload["replyTo"] = {"email": RESEND_ADDRESS, "name": "NERIVO"}
    r = requests.post("https://api.brevo.com/v3/smtp/email",
                      headers={"api-key": BREVO_KEY, "content-type": "application/json", "accept": "application/json"},
                      json=payload, timeout=30)
    if r.ok:
        c = _db()
        c.execute("insert into events(ts,kind,data) values(?,?,?)",
                  (_now(), "email_sent", json.dumps({"email":to_email.lower(),"key":key,"subject":subject}, ensure_ascii=False)))
        c.commit(); c.close()
        _update(to_email, last_outbound=_now())
        return {"sent": True}
    return {"sent": False, "status": r.status_code, "detail": r.text[:300]}


def _already_processed(email_id):
    c = _db()
    row = c.execute("select processed from inbound_messages where email_id=?", (email_id,)).fetchone()
    c.close()
    return bool(row and row["processed"])


def _store(email_id, sender, subject, body):
    c = _db()
    c.execute("""insert into inbound_messages(email_id,received_at,from_email,subject,body,processed,error)
                 values(?,?,?,?,?,?,?)
                 on conflict(email_id) do update set from_email=excluded.from_email,
                 subject=excluded.subject, body=excluded.body""",
              (email_id, _now(), sender, subject, body, 0, ""))
    c.commit(); c.close()


def _mark(email_id, processed, error=""):
    c = _db()
    c.execute("update inbound_messages set processed=?,error=? where email_id=?", (1 if processed else 0, error[:1000], email_id))
    c.commit(); c.close()


def _process(email):
    email_id = str(email.get("id") or "").strip()
    if not email_id or _already_processed(email_id):
        return "duplicate"
    full = _resend("/emails/receiving/" + email_id)
    if not full:
        return "fetch_failed"
    item = full.get("data") or full
    sender = str(item.get("from") or email.get("from") or "").strip()
    subject = str(item.get("subject") or email.get("subject") or "").strip()[:300]
    body = str(item.get("text") or "").strip() or str(item.get("html") or "").strip()
    body = body[:12000]
    if "<" in body and ">" in body:
        body = __import__("re").sub(r"<[^>]+>", " ", body)
    if not sender or not body:
        _store(email_id, sender, subject, body)
        _mark(email_id, False, "missing_sender_or_body")
        return "invalid"
    if "<" in sender and ">" in sender:
        sender = sender.split("<",1)[1].split(">",1)[0].strip()
    sender = sender.lower()
    _store(email_id, sender, subject, body)
    _remember(sender, source="resend")
    lead = _lead(sender)
    try:
        raw = _ollama([
            {"role":"system","content":"Classifica email cliente NERIVO in JSON: intent, need, objection, opt_out, support, urgency. Non inventare."},
            {"role":"user","content":body}
        ])
        obj = json.loads(raw)
    except Exception:
        obj = {"intent":"unknown","need":"","objection":"","opt_out":False,"support":False,"urgency":"normal"}
    opt_out = bool(obj.get("opt_out"))
    _update(sender, last_inbound=_now(), status="opted_out" if opt_out else ("support" if obj.get("support") else "engaged"),
            intent=str(obj.get("intent",""))[:200], need=str(obj.get("need",""))[:500],
            objections=str(obj.get("objection",""))[:500], opted_out=1 if opt_out else lead.get("opted_out",0),
            followup_step=99 if opt_out else lead.get("followup_step",0),
            next_action_at="", last_support_at=_now() if obj.get("support") else lead.get("last_support_at",""))
    _log("inbound_email", {"email":sender,"email_id":email_id,"subject":subject,"classification":obj})
    if not opt_out:
        prompt = f"""Sei l'assistente NERIVO. Rispondi in italiano in modo breve, umano e concreto.
Gestisci anche richieste di supporto. Non inventare funzionalità, prezzi o risultati.
Se la persona vuole acquistare, usa questo checkout: {PAYMENT_LINK}
Email del cliente:
{body}"""
        reply = _ollama([{"role":"system","content":prompt}])
        result = _send(sender, "Re: " + (subject or "NERIVO"), reply, "resend_inbound_" + email_id)
        if result.get("sent"):
            _update(sender, status="engaged", replied=_now())
    _mark(email_id, True)
    return "processed"


def cycle():
    if not RESEND_KEY:
        return {"configured":False}
    data = _resend("/emails/receiving", {"limit": MAX_MESSAGES})
    if not data:
        return {"configured":True,"ok":False}
    items = data.get("data") or []
    counts = {"received":len(items),"processed":0,"duplicates":0,"failed":0}
    for item in items[:MAX_MESSAGES]:
        result = _process(item)
        if result == "processed": counts["processed"] += 1
        elif result == "duplicate": counts["duplicates"] += 1
        elif result != "invalid": counts["failed"] += 1
    return {"configured":True,"ok":True,**counts}
