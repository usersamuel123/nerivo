import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPO = os.getenv('GITHUB_REPOSITORY', 'usersamuel123/nerivo').strip()
TOKEN = os.getenv('GITHUB_TOKEN', '').strip()
OLLAMA = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/')
MODEL = os.getenv('OLLAMA_MODEL', 'gpt-oss:20b')
TEAM = os.getenv('VERCEL_TEAM_ID', '').strip()
PROJECT = os.getenv('VERCEL_PROJECT_ID', '').strip()
AUTO_ENGINEERING = os.getenv('AUTO_ENGINEERING_ENABLED', 'true').lower() == 'true'
AUTO_MERGE = os.getenv('AUTO_ENGINEERING_MERGE_ENABLED', 'true').lower() == 'true'
MAX_FILES = 5
MAX_FILE_CHARS = 50000
ALLOWED_PREFIXES = ('api/', 'agent/', 'app.js', 'styles.css', 'index.html', 'workspace.html', 'activation.html')
BLOCKED_PREFIXES = ('.github/', '.gitignore', 'privacy.html', 'terms.html', 'cookies.html', 'vercel.json')

def gh_headers():
    return {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {TOKEN}', 'X-GitHub-Api-Version': '2026-03-10'}

def gh(method, path, **kwargs):
    r = requests.request(method, f'https://api.github.com{path}', headers=gh_headers(), timeout=30, **kwargs)
    if not r.ok:
        raise RuntimeError(f'GitHub {method} {path}: HTTP {r.status_code} {r.text[:500]}')
    return r.json() if r.text else {}

def repo_info():
    return gh('GET', f'/repos/{REPO}')

def latest_ci():
    data = gh('GET', f'/repos/{REPO}/actions/runs', params={'per_page': 10, 'branch': 'main'})
    runs = data.get('workflow_runs', [])
    return runs[0] if runs else None

def latest_vercel():
    token = os.getenv('VERCEL_TOKEN', '').strip()
    if not (TEAM and PROJECT and token):
        return None
    r = requests.get('https://api.vercel.com/v6/deployments', headers={'Authorization': f'Bearer {token}'}, params={'projectId': PROJECT, 'teamId': TEAM, 'limit': 5}, timeout=30)
    if not r.ok:
        return {'ok': False, 'status': r.status_code}
    deployments = r.json().get('deployments', [])
    return deployments[0] if deployments else None

def site_health(base_url):
    if not base_url:
        return {'ok': False, 'reason': 'NERIVO_BASE_URL missing'}
    try:
        r = requests.get(base_url + '/api/health', timeout=20)
        return {'ok': r.ok, 'status': r.status_code, 'body': r.text[:1000]}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:300]}

def snapshot():
    ci = latest_ci()
    vercel = latest_vercel()
    health = site_health(os.getenv('NERIVO_BASE_URL', '').strip())
    return {'repo': REPO, 'ci': {'status': ci.get('status') if ci else None, 'conclusion': ci.get('conclusion') if ci else None, 'name': ci.get('name') if ci else None, 'sha': ci.get('head_sha') if ci else None}, 'vercel': {'state': vercel.get('state'), 'target': vercel.get('target'), 'url': vercel.get('url')} if vercel else None, 'site_health': health}

def needs_repair(state):
    ci = state.get('ci') or {}
    vercel = state.get('vercel') or {}
    health = state.get('site_health') or {}
    return ci.get('conclusion') == 'failure' or (vercel and vercel.get('target') == 'production' and vercel.get('state') in {'ERROR', 'CANCELED'}) or not health.get('ok')

def ollama(prompt):
    r = requests.post(f'{OLLAMA}/api/chat', json={'model': MODEL, 'stream': False, 'messages': [{'role': 'system', 'content': 'Sei l\'ingegnere automatico di NERIVO. Proponi solo modifiche minime e reversibili. Non toccare segreti, pagamenti, prezzi, dati personali, workflow CI, policy legali o configurazione Vercel. Restituisci SOLO JSON valido.'}, {'role': 'user', 'content': prompt}]}, timeout=300)
    r.raise_for_status()
    return r.json()['message']['content']

def parse_plan(raw):
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r'\{.*\}', raw, re.S)
        if not match:
            raise ValueError('LLM did not return JSON')
        return json.loads(match.group(0))

def validate_plan(plan):
    edits = plan.get('edits')
    if not isinstance(edits, list) or not edits or len(edits) > MAX_FILES:
        raise ValueError('invalid edit count')
    for edit in edits:
        path = str(edit.get('path', ''))
        content = str(edit.get('content', ''))
        if not path.startswith(ALLOWED_PREFIXES) or path.startswith(BLOCKED_PREFIXES):
            raise ValueError(f'path not allowed: {path}')
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f'file too large: {path}')
        low = content.lower()
        if any(x in low for x in ('brevo_api_key', 'stripe_secret_key', 'github_token', 'vercel_token')):
            raise ValueError(f'possible secret in {path}')

def run_tests():
    commands = [[os.environ.get('PYTHON', 'python'), '-m', 'py_compile', 'agent/run_agent.py', 'agent/engineering.py'], ['node', '--check', 'app.js'], ['node', '--check', 'api/demo.js'], ['node', '--check', 'api/health.js']]
    for cmd in commands:
        result = subprocess.run(cmd, cwd=ROOT.parent, capture_output=True, text=True, timeout=120)
        if result.returncode:
            return False, (result.stdout + '\n' + result.stderr)[-5000:]
    return True, 'local tests passed'

def create_branch(branch):
    repo = repo_info()
    sha = gh('GET', f'/repos/{REPO}/git/ref/heads/{repo["default_branch"]}')['object']['sha']
    try:
        gh('POST', f'/repos/{REPO}/git/refs', json={'ref': f'refs/heads/{branch}', 'sha': sha})
    except RuntimeError as e:
        if 'Reference already exists' not in str(e):
            raise
    return sha

def write_file(path, content, branch, message):
    import base64
    data = gh('GET', f'/repos/{REPO}/contents/{path}', params={'ref': branch})
    gh('PUT', f'/repos/{REPO}/contents/{path}', json={'message': message, 'content': base64.b64encode(content.encode()).decode(), 'sha': data['sha'], 'branch': branch})

def create_pr(branch, title, body):
    return gh('POST', f'/repos/{REPO}/pulls', json={'title': title, 'head': branch, 'base': 'main', 'body': body, 'draft': False})

def wait_for_ci(sha, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = gh('GET', f'/repos/{REPO}/actions/runs', params={'head_sha': sha, 'per_page': 20}).get('workflow_runs', [])
        relevant = [r for r in runs if r.get('name') == 'NERIVO CI']
        if relevant:
            run = relevant[0]
            if run.get('status') == 'completed':
                return run.get('conclusion') == 'success', run
        time.sleep(15)
    return False, {'timeout': True}

def merge_pr(number, sha):
    return gh('PUT', f'/repos/{REPO}/pulls/{number}/merge', json={'sha': sha, 'merge_method': 'squash', 'commit_title': 'auto: verified engineering repair', 'commit_message': 'Automatically merged after local and GitHub CI verification.'})

def repair_once():
    if not (AUTO_ENGINEERING and TOKEN):
        return {'enabled': False}
    state = snapshot()
    if not needs_repair(state):
        return {'enabled': True, 'action': 'none', 'state': state}
    context = []
    for path in ('agent/run_agent.py', 'api/health.js', 'api/demo.js'):
        try:
            content, _ = fetch_file(path)
            context.append(f'FILE {path}\n{content[:20000]}')
        except Exception:
            pass
    prompt = 'Stato verificato:\n' + json.dumps(state, ensure_ascii=False, indent=2) + '\n\nCodice rilevante:\n' + '\n\n'.join(context) + '\n\nGenera un piano JSON con {title,summary,edits}. Ogni edit deve avere path e content completo. Correggi solo la causa più probabile del guasto verificato. Non introdurre nuove feature.'
    plan = parse_plan(ollama(prompt))
    validate_plan(plan)
    branch = 'agent/auto-repair-' + str(int(time.time()))
    base_sha = create_branch(branch)
    for edit in plan['edits']:
        write_file(edit['path'], edit['content'], branch, f'auto: {plan.get("title", "repair")}')
    ok, detail = run_tests()
    if not ok:
        return {'enabled': True, 'action': 'blocked_local_tests', 'detail': detail, 'branch': branch, 'base_sha': base_sha}
    head = gh('GET', f'/repos/{REPO}/git/ref/heads/{branch}')['object']['sha']
    pr = create_pr(branch, 'auto: ' + str(plan.get('title', 'engineering repair'))[:100], str(plan.get('summary', 'Automated repair')) + '\n\nAutomated local checks passed.')
    ci_ok, ci = wait_for_ci(head)
    if not ci_ok:
        return {'enabled': True, 'action': 'blocked_ci', 'pr': pr.get('number'), 'ci': ci}
    merged = merge_pr(pr['number'], head) if AUTO_MERGE else {'merged': False, 'reason': 'AUTO_ENGINEERING_MERGE_ENABLED=false'}
    return {'enabled': True, 'action': 'merged' if merged.get('merged') else 'pr_created', 'pr': pr.get('number'), 'merge': merged, 'ci': ci}

def fetch_file(path, ref='main'):
    import base64
    data = gh('GET', f'/repos/{REPO}/contents/{path}', params={'ref': ref})
    return base64.b64decode(data['content']).decode('utf-8'), data['sha']

def main():
    result = repair_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()