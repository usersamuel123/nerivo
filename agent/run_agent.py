import json, os, sqlite3, time, traceback
from datetime import datetime, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/".env")
DB=ROOT/"agent.db"
OLLAMA=os.getenv("OLLAMA_BASE_URL","http://127.0.0.1:11434").rstrip("/")
MODEL=os.getenv("OLLAMA_MODEL","gpt-oss:20b")
INTERVAL=max(5,int(os.getenv("AGENT_INTERVAL_MINUTES","30")))
MODE=os.getenv("AGENT_MODE","guarded_autonomous")
NERIVO=os.getenv("NERIVO_BASE_URL","").rstrip("/")

def db():
    c=sqlite3.connect(DB)
    c.execute("create table if not exists events(id integer primary key, ts text, kind text, data text)")
    c.execute("create table if not exists tasks(id integer primary key, created text, status text, title text, detail text)")
    c.commit(); return c

def log(kind,data):
    c=db()
    c.execute("insert into events(ts,kind,data) values(?,?,?)",
              (datetime.now(timezone.utc).isoformat(),kind,json.dumps(data,ensure_ascii=False)))
    c.commit(); c.close()

def ollama(messages):
    r=requests.post(f"{OLLAMA}/api/chat",
                    json={"model":MODEL,"messages":messages,"stream":False},
                    timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]

def http_check(url):
    if not url: return {"configured":False}
    try:
        r=requests.get(url,timeout=20)
        return {"configured":True,"status":r.status_code,"ok":r.ok}
    except Exception as e:
        return {"configured":True,"ok":False,"error":str(e)[:200]}

SYSTEM="""Sei NERIVO Agent, l'operatore autonomo di un piccolo SaaS italiano.
Il tuo obiettivo è aumentare ricavi e valore del prodotto in modo sostenibile.
Lavora in modo proattivo: osserva, analizza, pianifica e porta avanti il maggior numero
possibile di attività reversibili. Non inventare dati e non dichiarare di aver usato strumenti
che non sono collegati. Niente spam, frodi, scraping aggressivo, impersonificazione o promesse
ingannevoli. Non gestire denaro, credenziali, contratti, cancellazioni o comunicazioni esterne
irreversibili senza approvazione. Per azioni rischiose produci una richiesta di approvazione.
Ogni ciclo deve scegliere le attività con il maggior impatto economico verificabile."""

def snapshot():
    return {
        "time":datetime.now().astimezone().isoformat(),
        "mode":MODE,
        "model":MODEL,
        "nerivo_base_url":NERIVO,
        "site":http_check(NERIVO),
        "ollama":http_check(OLLAMA),
    }

def cycle():
    state=snapshot()
    prompt=f"""Esegui un ciclo operativo NERIVO.
Stato verificato:
{json.dumps(state,ensure_ascii=False,indent=2)}

Costruisci un piano breve e prioritario.
Formato:
1. RISULTATO DA OTTENERE
2. AZIONI AUTOMATICHE SICURE
3. AZIONI CHE RICHIEDONO APPROVAZIONE
4. METRICA DA MISURARE
5. PROSSIMA VERIFICA

Se un'integrazione non è presente, indica esattamente quale integrazione serve invece di fingere
di poter leggere email, Stripe, Brevo, GitHub o dati privati."""
    answer=ollama([{"role":"system","content":SYSTEM},{"role":"user","content":prompt}])
    log("agent_cycle",{"answer":answer,"state":state})
    print("\n"+"="*72+"\n"+answer+"\n"+"="*72,flush=True)

def main():
    db().close()
    log("startup",snapshot())
    print(f"NERIVO Agent avviato | model={MODEL} | mode={MODE}")
    while True:
        try: cycle()
        except Exception as e:
            log("error",{"error":str(e),"trace":traceback.format_exc()})
            print("Agent error:",e,flush=True)
        time.sleep(INTERVAL*60)

if __name__=="__main__":
    main()
