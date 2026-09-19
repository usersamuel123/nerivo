import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = __import__("pathlib").Path(__file__).resolve().parent
DB = ROOT / "agent.db"
BASE_URL = os.getenv("NERIVO_BASE_URL", "").rstrip("/")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM = os.getenv("VERCEL_TEAM_ID", "").strip()
VERCEL_PROJECT = os.getenv("VERCEL_PROJECT_ID", "").strip()
COOLDOWN_MINUTES = max(15, int(os.getenv("ENGINEERING_COOLDOWN_MINUTES", "30")))
HEALTH_FAILURE_THRESHOLD = max(2, int(os.getenv("HEALTH_FAILURE_THRESHOLD", "2")))
ROLLBACK_COOLDOWN_MINUTES = max(30, int(os.getenv("ROLLBACK_COOLDOWN_MINUTES", "60")))


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
    c.execute(
        "insert into recovery_state(key,value) values(?,?) "
        "on conflict(key) do update set value=excluded.value",
        (k, str(v)),
    )
    c.commit()
    c.close()


def _get(k, default=""):
    c = _db()
    row = c.execute("select value from recovery_state where key=?", (k,)).fetchone()
    c.close()
    return row["value"] if row else default


def _log(kind, data):
    c = _db()
    c.execute(
        "insert into events(ts,kind,data) values(?,?,?)",
        (_now().isoformat(), kind, json.dumps(data, ensure_ascii=False, default=str)),
    )
    c.commit()
    c.close()


def _health():
    if not BASE_URL:
        return {"configured": False}
    try:
        r = requests.get(BASE_URL + "/api/health", timeout=20)
        return {
            "configured": True,
            "ok": r.ok,
            "status": r.status_code,
            "body": r.text[:1000],
        }
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:300]}


def _deployments(limit=10):
    if not (VERCEL_TOKEN and VERCEL_TEAM and VERCEL_PROJECT):
        return {"configured": False, "deployments": []}
    try:
        r = requests.get(
            "https://api.vercel.com/v6/deployments",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            params={
                "projectId": VERCEL_PROJECT,
                "teamId": VERCEL_TEAM,
                "limit": limit,
            },
            timeout=20,
        )
        if not r.ok:
            return {"configured": True, "ok": False, "status": r.status_code, "deployments": []}
        return {
            "configured": True,
            "ok": True,
            "deployments": r.json().get("deployments", []),
        }
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:300], "deployments": []}


def _deployment():
    data = _deployments(5)
    items = data.get("deployments", [])
    if not items:
        return {"configured": data.get("configured", False), "ok": data.get("ok", True), "deployment": None}
    d = items[0]
    return {
        "configured": data.get("configured", False),
        "ok": data.get("ok", True),
        "deployment": {
            "id": d.get("uid") or d.get("id"),
            "state": d.get("state"),
            "readyState": d.get("readyState"),
            "target": d.get("target"),
            "url": d.get("url"),
            "meta": d.get("meta") or {},
        },
    }


def _commit_of(deployment):
    meta = deployment.get("meta") or {}
    return (
        meta.get("githubCommitSha")
        or meta.get("gitlabCommitSha")
        or meta.get("bitbucketCommitSha")
        or (deployment.get("gitSource") or {}).get("sha")
        or ""
    )


def _rollback_candidate(bad_id=""):
    data = _deployments(20)
    items = [
        d for d in data.get("deployments", [])
        if d.get("target") == "production" and d.get("state") == "READY"
    ]
    for d in items:
        did = d.get("uid") or d.get("id")
        if did and did != bad_id:
            return d
    return None


def _rollback(candidate_id):
    if not (VERCEL_TOKEN and VERCEL_TEAM and VERCEL_PROJECT and candidate_id):
        return {"ok": False, "reason": "vercel_not_configured"}
    try:
        r = requests.post(
            f"https://api.vercel.com/v9/projects/{VERCEL_PROJECT}/rollback/{candidate_id}",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            params={"teamId": VERCEL_TEAM},
            json={},
            timeout=30,
        )
        if not r.ok:
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        return {"ok": True, "status": r.status_code, "candidate": candidate_id}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _self_heal_production(health, deployment):
    if not VERCEL_TOKEN or not VERCEL_TEAM or not VERCEL_PROJECT:
        return {"action": "unavailable", "reason": "vercel_not_configured"}
    if health.get("ok"):
        _set("health_failure_count", 0)
        return {"action": "not_needed"}

    failures = int(_get("health_failure_count", "0") or "0") + 1
    _set("health_failure_count", failures)

    current = deployment.get("deployment") or {}
    current_id = current.get("id", "")
    current_state = current.get("state") or current.get("readyState")
    current_commit = _commit_of(current)
    now = _now()
    last_rollback = _get("last_rollback_at")
    if last_rollback:
        try:
            age = (now - datetime.fromisoformat(last_rollback)).total_seconds() / 60
            if age < ROLLBACK_COOLDOWN_MINUTES:
                return {
                    "action": "rollback_cooldown",
                    "failure_count": failures,
                    "current_state": current_state,
                }
        except Exception:
            pass

    if failures < HEALTH_FAILURE_THRESHOLD and current_state not in {"ERROR", "CANCELED"}:
        return {
            "action": "observe",
            "failure_count": failures,
            "current_state": current_state,
            "current_commit": current_commit,
        }

    candidate = _rollback_candidate(current_id)
    if not candidate:
        return {
            "action": "rollback_unavailable",
            "failure_count": failures,
            "current_state": current_state,
        }

    candidate_id = candidate.get("uid") or candidate.get("id")
    result = _rollback(candidate_id)
    if not result.get("ok"):
        _log("recovery_alert", {
            "reason": "rollback_failed",
            "current": current_id,
            "candidate": candidate_id,
            "result": result,
        })
        return {"action": "rollback_failed", "failure_count": failures, "result": result}

    _set("last_rollback_at", now.isoformat())
    _set("health_failure_count", 0)
    _log("recovery_rollback", {
        "reason": "production_health_failed",
        "from_deployment": current_id,
        "to_deployment": candidate_id,
        "to_commit": _commit_of(candidate),
        "current_state": current_state,
    })

    for _ in range(4):
        time.sleep(3)
        checked = _health()
        if checked.get("ok"):
            _set("last_recovery_success", _now().isoformat())
            return {
                "action": "rolled_back_and_verified",
                "from_deployment": current_id,
                "to_deployment": candidate_id,
            }

    return {
        "action": "rolled_back_health_still_failed",
        "from_deployment": current_id,
        "to_deployment": candidate_id,
    }


def _recent_failures():
    c = _db()
    since = (_now() - timedelta(hours=1)).isoformat()
    rows = c.execute(
        "select kind,data from events where ts>=? and kind in "
        "('error','engineering_error','growth_error','resend_inbound_error') "
        "order by id desc limit 50",
        (since,),
    ).fetchall()
    c.close()
    fingerprints = {}
    for row in rows:
        raw = f"{row['kind']}|{row['data']}"
        fp = hashlib.sha256(raw.encode()).hexdigest()[:16]
        fingerprints[fp] = fingerprints.get(fp, 0) + 1
    return sorted(fingerprints.items(), key=lambda x: x[1], reverse=True)[:5]


def cycle():
    health = _health()
    deployment = _deployment()
    failures = _recent_failures()
    self_heal = _self_heal_production(health, deployment)

    state = {
        "health": health,
        "deployment": deployment,
        "repeated_failures": failures,
        "cooldown_minutes": COOLDOWN_MINUTES,
        "self_heal": self_heal,
    }

    if self_heal.get("action") in {"rolled_back_and_verified", "rollback_failed", "rolled_back_health_still_failed"}:
        state["action"] = self_heal["action"]
    elif not health.get("ok", True):
        _set("last_unhealthy_at", _now().isoformat())
        _log(
            "recovery_alert",
            {
                "reason": "production_health_failed",
                "health": health,
                "deployment": deployment,
            },
        )
        state["action"] = "engineering_cycle_required"
    elif failures and failures[0][1] >= 3:
        _log(
            "recovery_alert",
            {
                "reason": "repeated_error_fingerprint",
                "fingerprint": failures[0][0],
                "count": failures[0][1],
            },
        )
        state["action"] = "cooldown_repeated_failure"
    else:
        state["action"] = "healthy"

    _set("last_recovery_check", _now().isoformat())
    _log("recovery_check", state)
    return {
        "enabled": True,
        "ok": health.get("ok", True),
        "action": state["action"],
        "health": health,
        "deployment": deployment,
        "self_heal": self_heal,
        "repeated_failures": failures,
    }
