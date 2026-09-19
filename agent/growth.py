import base64
import html
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import requests

ROOT=__import__("pathlib").Path(__file__).resolve().parent
DB=ROOT/"agent.db"
GITHUB_TOKEN=os.getenv("GITHUB_TOKEN","").strip()
REPO=os.getenv("GITHUB_REPOSITORY","usersamuel123/nerivo").strip()
MODEL=os.getenv("OLLAMA_MODEL","gpt-oss:20b")
OLLAMA=os.getenv("OLLAMA_BASE_URL","http://127.0.0.1:11434").rstrip("/")
CONTENT_ENABLED=os.getenv("AUTO_CONTENT_ENABLED","true").lower()=="true"
CONTENT_INTERVAL_DAYS=max(1,int(os.getenv("CONTENT_INTERVAL_DAYS","3")))
BASE_URL=os.getenv("NERIVO_BASE_URL","").rstrip("/")

def _db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.execute("create table if not exists growth_state(key text primary key,value text)");c.commit();return c
def _state(k,d=""):
 c=_db();r=c.execute("select value from growth_state where key=?",(k,)).fetchone();c.close();return r["value"] if r else d
def _set(k,v):
 c=_db();c.execute("insert into growth_state(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(k,str(v)));c.commit();c.close()
def _log(k,d):
 c=_db();c.execute("insert into events(ts,kind,data) values(?,?,?)",(datetime.now(timezone.utc).isoformat(),k,json.dumps(d,ensure_ascii=False)));c.commit();c.close()
def metrics_snapshot():
 c=_db()
 out={"leads":c.execute("select count(*) n from lead_state").fetchone()["n"],"customers":c.execute("select count(*) n from lead_state where paid_at<>''").fetchone()["n"],"qualified":c.execute("select count(*) n from lead_state where status='qualified'").fetchone()["n"],"engaged":c.execute("select count(*) n from lead_state where status in ('engaged','active')").fetchone()["n"]}
 rows=c.execute("select kind,count(*) n from events group by kind order by n desc limit 30").fetchall();c.close();out["events"]={r["kind"]:r["n"] for r in rows};return out
def _ollama(prompt):
 r=requests.post(f"{OLLAMA}/api/chat",json={"model":MODEL,"messages":[{"role":"system","content":"Sei il motore growth di NERIVO. Restituisci solo JSON valido. Non inventare dati."},{"role":"user","content":prompt}],"stream":False},timeout=300);r.raise_for_status();return r.json()["message"]["content"]
def _json(s):
 try:return json.loads(s)
 except Exception:return {}
def _headers():return {"Accept":"application/vnd.github+json","Authorization":f"Bearer {GITHUB_TOKEN}","Content-Type":"application/json"}
def _file(path):
 r=requests.get(f"https://api.github.com/repos/{REPO}/contents/{path}",headers=_headers(),params={"ref":"main"},timeout=30);return r.json() if r.ok else None
def _put(path,content,message,branch,sha=None):
 p={"message":message,"content":base64.b64encode(content.encode()).decode(),"branch":branch}
 if sha:p["sha"]=sha
 r=requests.put(f"https://api.github.com/repos/{REPO}/contents/{path}",headers=_headers(),json=p,timeout=30)
 return {"ok":r.ok,"status":r.status_code,"json":r.json() if r.text else {}}
def _get_ref(branch="main"):
 r=requests.get(f"https://api.github.com/repos/{REPO}/git/ref/heads/{branch}",headers=_headers(),timeout=30);return r.json() if r.ok else None
def _create_branch(branch):
 base=_get_ref("main")
 if not base:return None
 r=requests.post(f"https://api.github.com/repos/{REPO}/git/refs",headers=_headers(),json={"ref":f"refs/heads/{branch}","sha":base["object"]["sha"]},timeout=30)
 return base["object"]["sha"] if r.ok else None
def _create_pr(branch,title,body):
 r=requests.post(f"https://api.github.com/repos/{REPO}/pulls",headers=_headers(),json={"title":title,"head":branch,"base":"main","body":body},timeout=30)
 return r.json() if r.ok else {"error":r.text[:500],"status":r.status_code}
def _merge_pr(number,sha):
 r=requests.put(f"https://api.github.com/repos/{REPO}/pulls/{number}/merge",headers=_headers(),json={"sha":sha,"merge_method":"squash"},timeout=30)
 return r.json() if r.text else {"merged":r.ok}
def _slug(v):
 s=v.lower().replace("à","a").replace("è","e").replace("é","e").replace("ì","i").replace("ò","o").replace("ù","u")
 return re.sub(r"-+","-",re.sub(r"[^a-z0-9\s-]","",s).replace(" ","-")).strip("-")[:70]
def _pending_content_pr():
 v=_state("pending_content_pr")
 if not v:return None
 try:return json.loads(v)
 except Exception:return None
def _set_pending(v):
 _set("pending_content_pr",json.dumps(v,ensure_ascii=False) if v else "")
def _finish_pending():
 pending=_pending_content_pr()
 if not pending:return {"action":"none"}
 number=pending.get("pr")
 if not number:return {"action":"cleared"}
 r=requests.get(f"https://api.github.com/repos/{REPO}/pulls/{number}",headers=_headers(),timeout=30)
 if not r.ok:return {"action":"pending","pr":number,"status":"unavailable"}
 pr=r.json()
 if pr.get("merged"):
  _set_pending(None);_log("content_merged",{"pr":number,"branch":pending.get("branch")});return {"action":"merged","pr":number}
 if pr.get("state")=="closed":
  _set_pending(None);return {"action":"closed","pr":number}
 sha=pr.get("head",{}).get("sha")
 if not sha:return {"action":"pending","pr":number,"status":"no_head"}
 runs=requests.get(f"https://api.github.com/repos/{REPO}/actions/runs",headers=_headers(),params={"head_sha":sha,"per_page":20},timeout=30)
 relevant=[x for x in runs.json().get("workflow_runs",[]) if x.get("name")=="NERIVO CI"] if runs.ok else []
 if not relevant:return {"action":"pending","pr":number,"status":"waiting_ci"}
 run=relevant[0]
 if run.get("status")!="completed":return {"action":"pending","pr":number,"status":"ci_"+str(run.get("status"))}
 if run.get("conclusion")!="success":
  _set_pending(None);_log("content_ci_failed",{"pr":number,"conclusion":run.get("conclusion")});return {"action":"blocked","pr":number,"status":"ci_"+str(run.get("conclusion"))}
 merged=_merge_pr(number,sha)
 if merged.get("merged"):
  _set_pending(None);_set("last_content_at",datetime.now(timezone.utc).isoformat());_log("content_merged",{"pr":number,"branch":pending.get("branch")});return {"action":"merged","pr":number}
 return {"action":"merge_failed","pr":number,"detail":merged}
def generate_and_publish():
 if not CONTENT_ENABLED:return {"enabled":False,"action":"disabled"}
 if not GITHUB_TOKEN:return {"enabled":True,"action":"waiting","reason":"missing_github_token"}
 pending=_finish_pending()
 if pending.get("action") in {"pending","blocked","merge_failed"}:return {"enabled":True,**pending}
 last=_state("last_content_at")
 if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).days<CONTENT_INTERVAL_DAYS:return {"enabled":True,"action":"waiting","reason":"interval"}
 m=metrics_snapshot()
 obj=_json(_ollama("Crea un articolo SEO originale in italiano per agenzie immobiliari. Dati verificati: "+json.dumps(m,ensure_ascii=False)+". Restituisci JSON con title,summary,slug,body_html. 700-1100 parole, utile e concreto, nessuna statistica inventata o promessa di risultato. body_html ammette solo p,h2,h3,ul,li,strong,em,a. Argomenti: lead, follow-up, risposta rapida, qualificazione o automazione."))
 title=str(obj.get("title","")).strip()[:140];summary=str(obj.get("summary","")).strip()[:300];body=str(obj.get("body_html","")).strip();slug=_slug(str(obj.get("slug") or title))
 if not title or not body or not slug:return {"enabled":True,"action":"failed","reason":"invalid_model_output"}
 if "<script" in body.lower() or "javascript:" in body.lower():return {"enabled":True,"action":"blocked","reason":"unsafe_html"}
 path=f"content/posts/{slug}.html"
 if _file(path):return {"enabled":True,"action":"waiting","reason":"slug_exists"}
 page=f'<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} — NERIVO</title><meta name="description" content="{html.escape(summary)}"><link rel="stylesheet" href="/styles.css"></head><body><main style="max-width:820px;margin:0 auto;padding:64px 24px"><p><a href="/blog.html">← Risorse</a></p><h1>{html.escape(title)}</h1><p>{html.escape(summary)}</p><article>{body}</article></main></body></html>'
 current=_file("content/index.json");posts=json.loads(base64.b64decode(current["content"]).decode()) if current else [];posts.insert(0,{"slug":slug,"title":title,"summary":summary,"date":datetime.now(timezone.utc).date().isoformat()})
 branch="agent/content-"+str(int(datetime.now(timezone.utc).timestamp()))
 if not _create_branch(branch):return {"enabled":True,"action":"failed","reason":"branch_creation_failed"}
 a=_put(path,page,f"content: publish {slug}",branch);b=_put("content/index.json",json.dumps(posts,ensure_ascii=False,indent=2)+"\n","content: update index",branch,current.get("sha") if current else None)
 if not (a["ok"] and b["ok"]):return {"enabled":True,"action":"partial_failure","branch":branch,"writes":[a,b]}
 head=_get_ref(branch)
 if not head:return {"enabled":True,"action":"failed","reason":"branch_head_unavailable","branch":branch}
 pr=_create_pr(branch,f"content: {title[:90]}",f"Automated SEO content generated from verified NERIVO metrics.\n\nSlug: {slug}")
 if not pr.get("number"):return {"enabled":True,"action":"pr_created_failed","branch":branch,"pr":pr}
 _set_pending({"pr":pr["number"],"branch":branch,"slug":slug});_log("content_pr_created",{"slug":slug,"branch":branch,"pr":pr["number"]})
 return {"enabled":True,"action":"pr_created","slug":slug,"branch":branch,"pr":pr["number"],"url":f"{BASE_URL}/content/posts/{slug}.html"}
def decision_cycle():
 m=metrics_snapshot();d=[]
 if m["leads"]==0:d.append("acquisition_attention")
 if m["leads"] and m["customers"]==0:d.append("conversion_attention")
 if m["qualified"]>0 and m["customers"]==0:d.append("followup_attention")
 _log("growth_decisions",{"metrics":m,"decisions":d});return {"metrics":m,"decisions":d}
def cycle():
 d=decision_cycle()
 try:
  from resend_inbound import cycle as resend_cycle
  inbound=resend_cycle()
 except Exception as e:
  inbound={"configured":True,"ok":False,"error":str(e)[:500]};_log("resend_inbound_error",inbound)
 try:
  from recovery import cycle as recovery_cycle
  recovery=recovery_cycle()
 except Exception as e:
  recovery={"enabled":True,"ok":False,"error":str(e)[:500]};_log("recovery_error",recovery)
 return {"metrics":d["metrics"],"decisions":d["decisions"],"inbound":inbound,"recovery":recovery,"content":generate_and_publish()}
