import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DB = ROOT / "agent.db"
REPO = os.getenv("GITHUB_REPOSITORY", "usersamuel123/nerivo").strip()
TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
TEAM = os.getenv("VERCEL_TEAM_ID", "").strip()
PROJECT = os.getenv("VERCEL_PROJECT_ID", "").strip()
BASE_URL = os.getenv("NERIVO_BASE_URL", "").rstrip("/")
AUTO_ENGINEERING = os.getenv("AUTO_ENGINEERING_ENABLED", "true").lower() == "true"
AUTO_MERGE = os.getenv("AUTO_ENGINEERING_MERGE_ENABLED", "true").lower() == "true"
COOLDOWN_MINUTES = max(15, int(os.getenv("ENGINEERING_COOLDOWN_MINUTES", "30")))
POST_DEPLOY_TIMEOUT = max(120, int(os.getenv("POST_DEPLOY_VERIFY_SECONDS", "420")))
MAX_FILES = 5
MAX_FILE_CHARS = 50000
ALLOWED_PREFIXES = ("api/", "agent/", "app.js", "styles.css", "index.html", "workspace.html", "activation.html")
BLOCKED_PREFIXES = (".github/", ".gitignore", "privacy.html", "terms.html", "cookies.html", "vercel.json")


def _db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("create table if not exists engineering_state(key text primary key,value text)")
    c.execute("create table if not exists events(id integer primary key,ts text,kind text,data text)")
    c.commit()
    return c


def _set_state(key, value):
    c = _db()
    c.execute(
        "insert into engineering_state(key,value) values(?,?) "
        "on conflict(key) do update set value=excluded.value",
        (key, str(value)),
    )
    c.commit()
    c.close()


def _get_state(key, default=""):
    c = _db()
    row = c.execute("select value from engineering_state where key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default


def _log(kind, data):
    c = _db()
    c.execute(
        "insert into events(ts,kind,data) values(?,?,?)",
        (datetime.now(timezone.utc).isoformat(), kind, json.dumps(data, ensure_ascii=False, default=str)),
    )
    c.commit()
    c.close()


def gh_headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2026-03-10",
    }


def gh(method, path, **kwargs):
    r = requests.request(
        method,
        f"https://api.github.com{path}",
        headers=gh_headers(),
        timeout=30,
        **kwargs,
    )
    if not r.ok:
        raise RuntimeError(f"GitHub {method} {path}: HTTP {r.status_code} {r.text[:500]}")
    return r.json() if r.text else {}


def repo_info():
    return gh("GET", f"/repos/{REPO}")


def latest_main_sha():
    repo = repo_info()
    ref = gh("GET", f"/repos/{REPO}/git/ref/heads/{repo['default_branch']}")
    return ref["object"]["sha"]


def latest_ci(main_sha):
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/runs",
        params={"per_page": 10, "branch": "main"},
    )
    runs = data.get("workflow_runs", [])
    for run in runs:
        if run.get("name") == "NERIVO CI" and run.get("head_sha") == main_sha:
            return run
    return None


def _vercel_list(limit=20):
    token = os.getenv("VERCEL_TOKEN", "").strip()
    if not (TEAM and PROJECT and token):
        return []
    r = requests.get(
        "https://api.vercel.com/v6/deployments",
        headers={"Authorization": f"Bearer {token}"},
        params={"projectId": PROJECT, "teamId": TEAM, "limit": limit},
        timeout=30,
    )
    if not r.ok:
        return []
    return r.json().get("deployments", [])


def _deployment_id(d):
    return d.get("uid") or d.get("id") or ""


def _deployment_commit(d):
    meta = d.get("meta") or {}
    git = d.get("gitSource") or {}
    return (
        meta.get("githubCommitSha")
        or meta.get("gitlabCommitSha")
        or meta.get("bitbucketCommitSha")
        or git.get("sha")
        or ""
    )


def latest_vercel():
    deployments = _vercel_list(10)
    for d in deployments:
        if d.get("target") == "production":
            return d
    return deployments[0] if deployments else None


def site_health(base_url=BASE_URL):
    if not base_url:
        return {"ok": False, "reason": "NERIVO_BASE_URL missing"}
    try:
        r = requests.get(base_url + "/api/health", timeout=20)
        return {"ok": r.ok, "status": r.status_code, "body": r.text[:1000]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def snapshot():
    main_sha = latest_main_sha()
    ci = latest_ci(main_sha)
    vercel = latest_vercel()
    health = site_health()
    return {
        "repo": REPO,
        "main_sha": main_sha,
        "ci": {
            "status": ci.get("status") if ci else None,
            "conclusion": ci.get("conclusion") if ci else None,
            "name": ci.get("name") if ci else None,
            "sha": ci.get("head_sha") if ci else None,
        },
        "vercel": {
            "id": _deployment_id(vercel),
            "state": vercel.get("state") or vercel.get("readyState"),
            "target": vercel.get("target"),
            "url": vercel.get("url"),
            "commit": _deployment_commit(vercel),
        } if vercel else None,
        "site_health": health,
    }


def needs_repair(state):
    ci = state.get("ci") or {}
    vercel = state.get("vercel") or {}
    health = state.get("site_health") or {}
    return (
        ci.get("conclusion") == "failure"
        or (vercel and vercel.get("target") == "production" and vercel.get("state") in {"ERROR", "CANCELED"})
        or not health.get("ok")
    )


def _fingerprint(state):
    stable = {
        "ci": {
            "conclusion": (state.get("ci") or {}).get("conclusion"),
            "sha": (state.get("ci") or {}).get("sha"),
        },
        "vercel": {
            "state": (state.get("vercel") or {}).get("state"),
            "commit": (state.get("vercel") or {}).get("commit"),
        },
        "health": {
            "status": (state.get("site_health") or {}).get("status"),
            "error": (state.get("site_health") or {}).get("error"),
            "reason": (state.get("site_health") or {}).get("reason"),
        },
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:20]


def _cooldown(fp):
    last_fp = _get_state("last_failure_fingerprint")
    last_at = _get_state("last_failure_at")
    if fp != last_fp or not last_at:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last_at)
        return age < timedelta(minutes=COOLDOWN_MINUTES)
    except Exception:
        return False


def _record_failure(fp, reason):
    _set_state("last_failure_fingerprint", fp)
    _set_state("last_failure_at", datetime.now(timezone.utc).isoformat())
    _set_state("last_failure_reason", reason)


def ollama(prompt):
    r = requests.post(
        f"{OLLAMA}/api/chat",
        json={
            "model": MODEL,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Sei l'ingegnere automatico di NERIVO. Proponi solo modifiche "
                        "minime e reversibili. Non toccare segreti, pagamenti, prezzi, "
                        "dati personali, workflow CI, policy legali o configurazione "
                        "Vercel. Restituisci SOLO JSON valido."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        },
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def parse_plan(raw):
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"{.*}", raw, re.S)
        if not match:
            raise ValueError("LLM did not return JSON")
        return json.loads(match.group(0))


def validate_plan(plan):
    edits = plan.get("edits")
    if not isinstance(edits, list) or not edits or len(edits) > MAX_FILES:
        raise ValueError("invalid edit count")
    for edit in edits:
        path = str(edit.get("path", ""))
        content = str(edit.get("content", ""))
        if not path.startswith(ALLOWED_PREFIXES) or path.startswith(BLOCKED_PREFIXES):
            raise ValueError(f"path not allowed: {path}")
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"file too large: {path}")
        low = content.lower()
        if any(x in low for x in ("brevo_api_key", "stripe_secret_key", "github_token", "vercel_token")):
            raise ValueError(f"possible secret in {path}")


def run_tests():
    commands = [
        [os.environ.get("PYTHON", "python"), "-m", "py_compile", "agent/run_agent.py", "agent/engineering.py", "agent/recovery.py", "agent/growth.py"],
        ["node", "--check", "app.js"],
        ["node", "--check", "api/demo.js"],
        ["node", "--check", "api/health.js"],
        ["node", "--check", "api/inbound.js"],
    ]
    for cmd in commands:
        result = subprocess.run(
            cmd,
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            return False, (result.stdout + "
" + result.stderr)[-5000:]
    return True, "local tests passed"


def create_branch(branch):
    repo = repo_info()
    sha = gh("GET", f"/repos/{REPO}/git/ref/heads/{repo['default_branch']}")["object"]["sha"]
    try:
        gh(
            "POST",
            f"/repos/{REPO}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
    except RuntimeError as e:
        if "Reference already exists" not in str(e):
            raise
    return sha


def write_file(path, content, branch, message):
    import base64

    data = gh("GET", f"/repos/{REPO}/contents/{path}", params={"ref": branch})
    gh(
        "PUT",
        f"/repos/{REPO}/contents/{path}",
        json={
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "sha": data["sha"],
            "branch": branch,
        },
    )


def create_pr(branch, title, body):
    return gh(
        "POST",
        f"/repos/{REPO}/pulls",
        json={
            "title": title,
            "head": branch,
            "base": "main",
            "body": body,
            "draft": False,
        },
    )


def wait_for_ci(sha, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = gh(
            "GET",
            f"/repos/{REPO}/actions/runs",
            params={"head_sha": sha, "per_page": 20},
        ).get("workflow_runs", [])
        relevant = [r for r in runs if r.get("name") == "NERIVO CI"]
        if relevant:
            run = relevant[0]
            if run.get("status") == "completed":
                return run.get("conclusion") == "success", run
        time.sleep(15)
    return False, {"timeout": True}


def merge_pr(number, sha):
    return gh(
        "PUT",
        f"/repos/{REPO}/pulls/{number}/merge",
        json={
            "sha": sha,
            "merge_method": "squash",
            "commit_title": "auto: verified engineering repair",
            "commit_message": "Automatically merged after local and GitHub CI verification.",
        },
    )


def wait_for_production(commit_sha, timeout=POST_DEPLOY_TIMEOUT):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        deployments = _vercel_list(20)
        production = [d for d in deployments if d.get("target") == "production"]
        candidate = production[0] if production else None
        if candidate:
            last = candidate
            state = candidate.get("state") or candidate.get("readyState")
            commit = _deployment_commit(candidate)
            if state in {"ERROR", "CANCELED"} and commit == commit_sha:
                return {
                    "ok": False,
                    "reason": "production_deployment_failed",
                    "deployment": _deployment_id(candidate),
                    "state": state,
                    "commit": commit,
                }
            if state == "READY" and commit == commit_sha:
                health = site_health()
                if health.get("ok"):
                    return {
                        "ok": True,
                        "deployment": _deployment_id(candidate),
                        "state": state,
                        "commit": commit,
                        "health": health,
                    }
                return {
                    "ok": False,
                    "reason": "production_health_failed",
                    "deployment": _deployment_id(candidate),
                    "state": state,
                    "commit": commit,
                    "health": health,
                }
        time.sleep(15)
    return {
        "ok": False,
        "reason": "production_verification_timeout",
        "deployment": _deployment_id(last) if last else None,
        "state": (last or {}).get("state") if last else None,
        "commit": _deployment_commit(last) if last else None,
    }


def rollback_to_previous(bad_deployment_id):
    if not (VERCEL_TOKEN and TEAM and PROJECT):
        return {"ok": False, "reason": "vercel_not_configured"}
    deployments = _vercel_list(20)
    candidates = [
        d for d in deployments
        if d.get("target") == "production"
        and d.get("state") == "READY"
        and _deployment_id(d) != bad_deployment_id
    ]
    if not candidates:
        return {"ok": False, "reason": "no_ready_rollback_candidate"}
    candidate = candidates[0]
    candidate_id = _deployment_id(candidate)
    try:
        r = requests.post(
            f"https://api.vercel.com/v9/projects/{PROJECT}/rollback/{candidate_id}",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            params={"teamId": TEAM},
            json={},
            timeout=30,
        )
        if not r.ok:
            return {
                "ok": False,
                "reason": "rollback_request_failed",
                "status": r.status_code,
                "body": r.text[:500],
                "candidate": candidate_id,
            }
        for _ in range(10):
            time.sleep(3)
            health = site_health()
            if health.get("ok"):
                _set_state("last_rollback_at", datetime.now(timezone.utc).isoformat())
                _log("engineering_rollback", {
                    "bad_deployment": bad_deployment_id,
                    "restored_deployment": candidate_id,
                    "restored_commit": _deployment_commit(candidate),
                })
                return {
                    "ok": True,
                    "candidate": candidate_id,
                    "commit": _deployment_commit(candidate),
                    "health": health,
                }
        return {"ok": False, "reason": "rollback_health_failed", "candidate": candidate_id}
    except Exception as e:
        return {"ok": False, "reason": "rollback_exception", "error": str(e)[:300]}


def repair_once():
    if not (AUTO_ENGINEERING and TOKEN):
        return {"enabled": False}

    state = snapshot()
    if not needs_repair(state):
        return {"enabled": True, "action": "none", "state": state}

    fp = _fingerprint(state)
    if _cooldown(fp):
        return {
            "enabled": True,
            "action": "cooldown",
            "fingerprint": fp,
            "cooldown_minutes": COOLDOWN_MINUTES,
            "state": state,
        }

    _record_failure(fp, "verified production/CI failure")
    context = []
    for path in ("agent/run_agent.py", "api/health.js", "api/demo.js", "api/inbound.js"):
        try:
            content, _ = fetch_file(path)
            context.append(f"FILE {path}\n{content[:20000]}")
        except Exception:
            pass

    prompt = (
        "Stato verificato:\n"
        + json.dumps(state, ensure_ascii=False, indent=2)
        + "\n\nCodice rilevante:\n"
        + "\n\n".join(context)
        + "\n\nGenera un piano JSON con {title,summary,edits}. "
        "Ogni edit deve avere path e content completo. "
        "Correggi solo la causa più probabile del guasto verificato. "
        "Non introdurre nuove feature."
    )
    plan = parse_plan(ollama(prompt))
    validate_plan(plan)

    branch = "agent/auto-repair-" + str(int(time.time()))
    base_sha = create_branch(branch)
    for edit in plan["edits"]:
        write_file(
            edit["path"],
            edit["content"],
            branch,
            f"auto: {plan.get('title', 'repair')}",
        )

    ok, detail = run_tests()
    if not ok:
        _log("engineering_repair_blocked", {"reason": "local_tests", "fingerprint": fp})
        return {
            "enabled": True,
            "action": "blocked_local_tests",
            "detail": detail,
            "branch": branch,
            "base_sha": base_sha,
            "fingerprint": fp,
        }

    head = gh("GET", f"/repos/{REPO}/git/ref/heads/{branch}")["object"]["sha"]
    pr = create_pr(
        branch,
        "auto: " + str(plan.get("title", "engineering repair"))[:100],
        str(plan.get("summary", "Automated repair"))
        + "\n\nAutomated local checks passed.",
    )
    ci_ok, ci = wait_for_ci(head)
    if not ci_ok:
        _log("engineering_repair_blocked", {
            "reason": "github_ci",
            "fingerprint": fp,
            "pr": pr.get("number"),
        })
        return {
            "enabled": True,
            "action": "blocked_ci",
            "pr": pr.get("number"),
            "ci": ci,
            "fingerprint": fp,
        }

    merged = (
        merge_pr(pr["number"], head)
        if AUTO_MERGE
        else {"merged": False, "reason": "AUTO_ENGINEERING_MERGE_ENABLED=false"}
    )
    if not merged.get("merged"):
        return {
            "enabled": True,
            "action": "pr_created",
            "pr": pr.get("number"),
            "merge": merged,
            "ci": ci,
            "fingerprint": fp,
        }

    merged_sha = merged.get("sha") or ""
    verification = wait_for_production(merged_sha)
    if verification.get("ok"):
        _set_state("last_success_at", datetime.now(timezone.utc).isoformat())
        _set_state("last_success_fingerprint", fp)
        _set_state("last_failure_fingerprint", "")
        _log("engineering_repair_success", {
            "fingerprint": fp,
            "pr": pr.get("number"),
            "merge_sha": merged_sha,
            "verification": verification,
        })
        return {
            "enabled": True,
            "action": "merged_and_verified",
            "pr": pr.get("number"),
            "merge": merged,
            "ci": ci,
            "verification": verification,
            "fingerprint": fp,
        }

    rollback = rollback_to_previous(verification.get("deployment"))
    _log("engineering_repair_postdeploy_failed", {
        "fingerprint": fp,
        "pr": pr.get("number"),
        "merge_sha": merged_sha,
        "verification": verification,
        "rollback": rollback,
    })
    return {
        "enabled": True,
        "action": "merged_rollback" if rollback.get("ok") else "merged_postdeploy_failed",
        "pr": pr.get("number"),
        "merge": merged,
        "ci": ci,
        "verification": verification,
        "rollback": rollback,
        "fingerprint": fp,
    }


def fetch_file(path, ref="main"):
    import base64

    data = gh("GET", f"/repos/{REPO}/contents/{path}", params={"ref": ref})
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def main():
    result = repair_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
