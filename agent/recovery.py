import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

ROOT = __import__("pathlib").Path(__file__).resolve().parent
DB = ROOT / "agent.db"
BASE_URL = os.getenv("NERIVO_BASE_URL", "").rstrip("/")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM = os.getenv("VERCEL_TEAM_ID", "").strip()
VERCEL_PROJECT = os.getenv("VERCEL_PROJECT_ID", "").strip()
COOLDOWN_MINUTES = max(15, int(os.getenv("ENGINEERING_COOLDOWN_MINUTES", "30")))


def _now():
    return datetime.now(timezone.utc)


def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("create table if not exists recovery_state(key text primary key,value text)")
    c.execute("create table if not exists events(id integer primary key,ts text,kind text,data text)")
    c.commit()
    return c


def _set(k, v):
    c = _db()
    c.execute("insert into recovery_state(key,value) values(?,?) on conflict(key) do update set value=excluded.value", (k, str(v)))
    c.commit(); c.close()


def _get(k, default=""):
    c = _db()
    row = c.execute("select value from recovery_state where key=?", (k,)).fetchone()
    c.close()
    return row["value"] if row else default


def _log(kind, data):
    c = _db()
    c.execute("insert into events(ts,kind,data) values(?,?,?)", (_now().isoformat(), kind, json.dumps(data, ensure_ascii=False, default=str)))
    c.commit(); c.close()


def _health():
    if not BASE_URL:
        return {"configured": False}
    try:
        r = requests.get(BASE_URL + "/api/health", timeout=20)
        return {"configured": True, "ok": r.ok, "status": r.status_code, "body": r.text[:1000]}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:300]}


def _deployment():
    if not (VERCEL_TOKEN and VERCEL_TEAM and VERCEL_PROJECT):
        return {"configured": False}
    try:
        r = requests.get(
            "https://api.vercel.com/v6/deployments",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            params={"projectId": VERCEL_PROJECT, "teamId": VERCEL_TEAM, "limit": 5},
            timeout=20,
        )
        if not r.ok:
            return {"configured": True, "ok": False, "status": r.status_code}
        items = r.json().get("deployments", [])
        if not items:
            return {"configured": True, "ok": True, "deployment": None}
        d = items[0]
        return {"configured": True, "ok": True, "deployment": {"state": d.get("state"), "target": d.get("target"), "url": d.get("url")}}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:300]}


def _recent_failures():
    c = _db()
    since = (_now() - timedelta(hours=1)).isoformat()
    rows = c.execute("select kind,data from events where ts>=? and kind in ('error','engineering_error','growth_error','resend_inbound_error') order by id desc limit 50", (since,)).fetchall()
    c.close()
    fingerprints = {}
    for row in rows:
        raw = f"{row['kind']}|{row['data']}"
        fp = hashlib.sha256(raw.encode()).hexdigest()[:16]
        fingerprints[fp] = fingerprints.get(fp, 0) + 1
    return sorted(fingerprints.items(), key=lambda x:x[1], reverse=True)[:5]


def cycle():
    health = _health()
    deployment = _deployment()
    failures = _recent_failures()
    state = {"health": health, "deployment": deployment, "repeated_failures": failures, "cooldown_minutes": COOLDOWN_MINUTES}

    if not health.get("ok", True):
        _set("last_unhealthy_at", _now().isoformat())
        _log("recovery_alert", {"reason":"production_health_failed","health":health,"deployment":deployment})
        state["action"] = "engineering_cycle_required"
    elif failures and failures[0][1] >= 3:
        _log("recovery_alert", {"reason":"repeated_error_fingerprint","fingerprint":failures[0][0],"count":failures[0][1]})
        state["action"] = "cooldown_repeated_failure"
    else:
        state["action"] = "healthy"

    _set("last_recovery_check", _now().isoformat())
    _log("recovery_check", state)
    return {"enabled":True,"ok":health.get("ok", True),"action":state["action"],"health":health,"deployment":deployment,"repeated_failures":failures}
