import json, os, sqlite3, time, traceback, html
from datetime import datetime, timezone
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "usersamuel123/nerivo").strip()
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM_ID = os.getenv("VERCEL_TEAM_ID", "").strip()
VERCEL_PROJECT_ID = os.getenv("VERCEL_PROJECT_ID", "").strip()
OUTBOUND = os.getenv("OUTBOUND_EMAIL_ENABLED", "false").lower() == "true"
AUTO_DEPLOY = os.getenv("AUTO_DEPLOY_ENABLED", "false").lower() == "true"
AUTO_PR = os.getenv("AUTO_PR_ENABLED", "true").lower() == "true"
AUTO_BILLING = os.getenv("AUTO_BILLING_CHANGES_ENABLED", "false").lower() == "true"
PAYMENT_LINK = os.getenv("NERIVO_PAYMENT_LINK", "https://buy.stripe.com/8x2cN79YZbTLgh9ggIc3m00")

def db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists events(id integer primary key, ts text, kind text, data text)")
    c.execute("create table if not exists tasks(id integer primary key, created text, status text, title text, detail text)")
    c.execute("create table if not exists lead_state(email text primary key, first_seen text, last_seen text, replied text default '', last_name text, agency text)")
    c.commit()
    return c

def log(kind, data):
    c = db()
    c.execute("insert into events(ts,kind,data) values(?,?,?)",
              (datetime.now(timezone.utc).isoformat(), kind, json.dumps(data, ensure_ascii=False)))
    c.commit()
    c.close()

def remember_lead(email, name="", agency=""):
    now = datetime.now(timezone.utc).isoformat()
    c = db()
    c.execute("""insert into lead_state(email,first_seen,last_seen,last_name,agency)
                 values(?,?,?,?,?)
                 on conflict(email) do update set last_seen=excluded.last_seen,
                 last_name=coalesce(nullif(excluded.last_name,''),lead_state.last_name),
                 agency=coalesce(nullif(excluded.agency,''),lead_state.agency)""",
              (email, now, now, name, agency))
    c.commit()
    c.close()

def already_replied(email):
    c = db()
    row = c.execute("select replied from lead_state where email=?", (email,)).fetchone()
    c.close()
    return bool(row and row[0])

def mark_replied(email):
    c = db()
    c.execute("update lead_state set replied=? where email=?",
              (datetime.now(timezone.utc).isoformat(), email))
    c.commit()
    c.close()

def ollama(messages):
    r = requests.post(f"{OLLAMA}/api/chat",
                       json={"model": MODEL, "messages": messages, "stream": False},
                       timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]

def http_check(url):
    if not url:
        return {"configured": False}
    try:
        r = requests.get(url, timeout=20)
        return {"configured": True, "status": r.status_code, "ok": r.ok}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:200]}

def brevo_headers():
    return {"accept":"application/json","api-key":BREVO_KEY,"content-type":"application/json"}

def brevo_contacts():
    if not BREVO_KEY:
        return {"configured":False, "contacts":[]}
    r = requests.get("https://api.brevo.com/v3/contacts", headers=brevo_headers(),
                     params={"limit":50,"offset":0,"sort":"desc"}, timeout=30)
    if not r.ok:
        return {"configured":True, "ok":False, "status":r.status_code, "contacts":[]}
    return {"configured":True,"ok":True,"contacts":r.json().get("contacts",[])}

def send_brevo_email(to_email, subject, body_html):
    if not OUTBOUND:
        return {"sent":False,"reason":"OUTBOUND_EMAIL_ENABLED=false"}
    if not (BREVO_KEY and BREVO_SENDER_ID and to_email):
        return {"sent":False,"reason":"Brevo sender/email non configurati"}
    r = requests.post("https://api.brevo.com/v3/smtp/email", headers=brevo_headers(),
                      json={"sender":{"id":BREVO_SENDER_ID},"to":[{"email":to_email}],
                            "subject":subject,"htmlContent":body_html}, timeout=30)
    return {"sent":r.ok,"status":r.status_code,"detail":"" if r.ok else r.text[:500]}

def stripe_snapshot():
    if not STRIPE_KEY:
        return {"configured":False}
    h={"Authorization":f"Bearer {STRIPE_KEY}"}
    out={"configured":True}
    try:
        r=requests.get("https://api.stripe.com/v1/balance",headers=h,timeout=20)
        out["balance_ok"]=r.ok
        if r.ok: out["balance"]=r.json().get("available",[])
        r=requests.get("https://api.stripe.com/v1/subscriptions",
                       headers=h,params={"status":"all","limit":100},timeout=20)
        out["subscriptions_ok"]=r.ok
        if r.ok:
            subs=r.json().get("data",[])
            out["active_subscriptions"]=sum(1 for s in subs if s.get("status")=="active")
            out["total_subscriptions"]=len(subs)
    except Exception as e:
        out["error"]=str(e)[:200]
    return out

def github_snapshot():
    if not GITHUB_TOKEN:
        return {"configured":False}
    h={"Accept":"application/vnd.github+json","Authorization":f"Bearer {GITHUB_TOKEN}"}
    out={"configured":True,"repository":GITHUB_REPOSITORY}
    try:
        r=requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}",headers=h,timeout=20)
        out["repo_ok"]=r.ok
        if r.ok:
            repo=r.json()
            out["default_branch"]=repo.get("default_branch")
            out["open_issues"]=repo.get("open_issues_count")
        r=requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/commits",
                       headers=h,params={"per_page":5},timeout=20)
        out["recent_commits_ok"]=r.ok
        if r.ok: out["recent_commits"]=[x.get("sha","")[:7] for x in r.json()]
    except Exception as e:
        out["error"]=str(e)[:200]
    return out

def vercel_snapshot():
    if not (VERCEL_TOKEN and VERCEL_PROJECT_ID and VERCEL_TEAM_ID):
        return {"configured":False}
    h={"Authorization":f"Bearer {VERCEL_TOKEN}"}
    out={"configured":True}
    try:
        r=requests.get(f"https://api.vercel.com/v9/projects/{VERCEL_PROJECT_ID}",
                       headers=h,params={"teamId":VERCEL_TEAM_ID},timeout=20)
        out["project_ok"]=r.ok
        if r.ok: out["project_name"]=r.json().get("name")
        r=requests.get("https://api.vercel.com/v6/deployments",headers=h,
                       params={"projectId":VERCEL_PROJECT_ID,"teamId":VERCEL_TEAM_ID,"limit":5},timeout=20)
        out["deployments_ok"]=r.ok
        if r.ok:
            out["deployments"]=[{"state":x.get("state"),"url":x.get("url")} for x in r.json().get("deployments",[])]
    except Exception as e:
        out["error"]=str(e)[:200]
    return out

def process_leads():
    data=brevo_contacts()
    if not data.get("ok"):
        return data
    new=[]
    for c in data.get("contacts",[]):
        email=str(c.get("email") or "").strip().lower()
        if not email: continue
        attrs=c.get("attributes") or {}
        name=str(attrs.get("FIRSTNAME") or attrs.get("NAME") or "").strip()
        agency=str(attrs.get("COMPANY") or attrs.get("AGENCY") or "").strip()
        remember_lead(email,name,agency)
        if not already_replied(email):
            new.append({"email":email,"name":name,"agency":agency,"createdAt":c.get("createdAt")})
    actions=[]
    for lead in new[:10]:
        if OUTBOUND:
            first=lead["name"].split()[0] if lead["name"] else "ciao"
            subject="NERIVO — prossimo passo per la tua richiesta demo"
            body=f"""<p>Ciao {html.escape(first)},</p>
<p>grazie per aver richiesto una demo di NERIVO.</p>
<p>Il prossimo passo è una breve verifica del flusso della tua agenzia. Se preferisci partire subito, trovi qui l'accesso Founding:</p>
<p><a href="{html.escape(PAYMENT_LINK)}">Attiva NERIVO — €99 + €29/mese</a></p>
<p>Se vuoi prima parlarne, rispondi a questa email con il canale e l'orario che preferisci.</p>
<p>A presto,<br>NERIVO</p>"""
            result=send_brevo_email(lead["email"],subject,body)
            if result.get("sent"): mark_replied(lead["email"])
            actions.append({"lead":lead,"email":result})
        else:
            actions.append({"lead":lead,"email":{"sent":False,"reason":"outbound_disabled"}})
    return {"configured":True,"ok":True,"contacts_seen":len(data.get("contacts",[])),
            "new_leads":len(new),"actions":actions}

SYSTEM = """Sei NERIVO Agent, operatore autonomo di un SaaS italiano.
Obiettivo: far crescere prodotto e ricavi in modo sostenibile e verificabile.
Puoi automatizzare analisi, monitoraggio, classificazione lead, follow-up transazionali autorizzati,
report, test e lavoro reversibile sul repository. Non inventare dati. Non fare spam, frodi,
impersonificazione, scraping aggressivo o promesse di guadagno. Non effettuare rimborsi,
trasferimenti di denaro, cambi prezzo, cancellazioni distruttive o impegni legali.
Per attività non consentite produci una richiesta di approvazione. Usa solo integrazioni realmente
presenti. Ogni azione deve essere registrata nel log."""

def cycle():
    state={"time":datetime.now().astimezone().isoformat(),"mode":MODE,"model":MODEL,
           "site":http_check(NERIVO),"ollama":http_check(OLLAMA),
           "brevo":{"configured":bool(BREVO_KEY),"sender_id":BREVO_SENDER_ID,"outbound":OUTBOUND},
           "stripe":stripe_snapshot(),"github":github_snapshot(),"vercel":vercel_snapshot()}
    state["lead_cycle"]=process_leads()
    prompt=f"""Esegui il ciclo operativo NERIVO usando solo lo stato verificato qui sotto.
{json.dumps(state,ensure_ascii=False,indent=2)}
Dai un piano operativo molto breve:
1. RISULTATO
2. FATTO AUTOMATICAMENTE
3. DA APPROVARE
4. METRICA
5. PROSSIMO CICLO
Non dichiarare di aver eseguito azioni che non compaiono nello stato. Priorità: lead, conversione,
retention, affidabilità, prodotto. Se OUTBOUND_EMAIL_ENABLED è false, non fingere invii."""
    answer=ollama([{"role":"system","content":SYSTEM},{"role":"user","content":prompt}])
    log("agent_cycle",{"answer":answer,"state":state})
    print("\n"+"="*72+"\n"+answer+"\n"+"="*72,flush=True)

def main():
    db().close()
    log("startup",{"mode":MODE,"model":MODEL})
    print(f"NERIVO Agent avviato | model={MODEL} | mode={MODE}")
    while True:
        try: cycle()
        except Exception as e:
            log("error",{"error":str(e),"trace":traceback.format_exc()})
            print("Agent error:",e,flush=True)
        time.sleep(INTERVAL*60)

if __name__=="__main__":
    main()
