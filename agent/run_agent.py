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
MODE=os.getenv("AGENT_MODE","supervised")
NERIVO=os.getenv("NERIVO_BASE_URL","").rstrip("/")

def db():
    c=sqlite3.connect(DB)
    c.execute("create table if not exists events(id integer primary key, ts text, kind text, data text)")
    c.execute("create table if not exists tasks(id integer primary key, created text, status text, title text, detail text)")
    c.commit(); return c

def log(kind,data):
    c=db(); c.execute("insert into events(ts,kind,data) values(?,?,?)",(datetime.now(timezone.utc).isoformat(),kind,json.dumps(data,ensure_ascii=False))); c.commit(); c.close()

def ollama(messages):
    r=requests.post(f"{OLLAMA}/api/chat",json={"model":MODEL,"messages":messages,"stream":False},timeout=180)
    r.raise_for_status()
    return r.json()["message"]["content"]

SYSTEM="""Sei NERIVO Agent, l'operatore autonomo di un piccolo SaaS italiano.
Obiettivo: far crescere NERIVO in modo sostenibile, senza spam, frodi, manipolazione o promesse ingannevoli.
Priorità: clienti paganti, qualità del prodotto, retention, sicurezza e riduzione del lavoro umano.
Non inventare dati. Quando non puoi verificare qualcosa, dichiaralo.
Non eseguire azioni finanziarie irreversibili, cancellazioni, accesso a credenziali, contratti o invio massivo di messaggi.
In modalità supervised proponi azioni; non eseguirle.
Rispondi con: situazione, priorità, azioni proposte, rischi, prossima verifica."""

def snapshot():
    return {
      "time":datetime.now().astimezone().isoformat(),
      "mode":MODE,
      "model":MODEL,
      "nerivo_base_url":NERIVO,
      "database":str(DB)
    }

def cycle():
    state=snapshot()
    prompt=f"""Esegui un ciclo operativo.
Stato tecnico disponibile:
{json.dumps(state,ensure_ascii=False,indent=2)}

Non hai ancora accesso automatico a caselle email, Stripe o GitHub in questo processo.
Quindi non fingere di averli controllati.
Analizza ciò che è noto e prepara le 3 attività più utili che l'orchestratore dovrebbe eseguire quando gli strumenti saranno collegati."""
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

if __name__=="__main__": main()
