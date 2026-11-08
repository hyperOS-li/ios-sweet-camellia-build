#!/usr/bin/env python3
"""Create one commit on a GitHub branch through the Git Data API.

This checkout has no local clone of the CI repository, and every change under
xnu/patches/** triggers an Actions build.  Doing the whole change as a single
commit keeps it to one build (and one 10-20 min wait) instead of one per file.

Token: $env:GH_TOKEN, else <workspace>/.gh_token, else <project>/.gh_token.

Usage:
  gh_commit.py --message "diag: ..." \
      --add xnu/patches/0195-camellia-panic-print-guarded.patch=xnu\\patches\\0195-camellia-panic-print-guarded.patch \
      --del xnu/patches/diag/0188-camellia-stall-forever.patch \
      --wait

  --add REPO_PATH=LOCAL_PATH   upload LOCAL_PATH at REPO_PATH
  --del REPO_PATH              delete REPO_PATH
  --wait                       poll the run triggered by this commit
  --timeout SECONDS            wait budget for --wait (default 2700)
  --dry-run                    resolve the base commit/tree only

Exit code: 0 = commit created (and run succeeded with --wait); 1 = failure.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(ROOT)
API = 'https://api.github.com'
DEFAULT_REPO = 'hyperOS-li/ios-sweet-camellia-build'


def read_token():
    tok = os.environ.get('GH_TOKEN', '').strip()
    if tok:
        return tok
    for cand in (os.path.join(WORKSPACE, '.gh_token'), os.path.join(ROOT, '.gh_token')):
        if os.path.isfile(cand):
            with open(cand, 'r', encoding='utf-8') as f:
                return f.read().strip()
    raise SystemExit('no token: set GH_TOKEN or put .gh_token next to the project')


TOKEN = read_token()


def api(method, path, body=None):
    data = None
    headers = {
        'Authorization': 'Bearer ' + TOKEN,
        'User-Agent': 'dsh-gh-commit',
        'Accept': 'application/vnd.github+json',
    }
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    last = None
    for attempt in range(4):
        req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            return json.loads(raw.decode('utf-8')) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:2000]
            if exc.code < 500:
                raise SystemExit('%s %s -> HTTP %d\n%s' % (method, path, exc.code, detail))
            last = 'HTTP %d %s' % (exc.code, detail[:200])
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(2 + attempt * 3)
    raise SystemExit('%s %s failed after retries: %s' % (method, path, last))


def parse_args(argv):
    opts = {'repo': DEFAULT_REPO, 'branch': 'main', 'message': '', 'adds': [],
            'dels': [], 'wait': False, 'timeout': 2700, 'dry_run': False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--add', '--del', '--message', '--repo', '--branch', '--timeout'):
            if i + 1 >= len(argv):
                raise SystemExit('%s needs a value' % a)
            v = argv[i + 1]
            i += 2
        elif a == '--wait':
            opts['wait'] = True
            i += 1
            continue
        elif a == '--dry-run':
            opts['dry_run'] = True
            i += 1
            continue
        else:
            raise SystemExit('unknown argument: %s' % a)
        if a == '--add':
            if '=' not in v:
                raise SystemExit('--add wants REPO_PATH=LOCAL_PATH, got %r' % v)
            repo_path, local = v.split('=', 1)
            if not os.path.isfile(local):
                raise SystemExit('local file not found: %s' % local)
            opts['adds'].append((repo_path.strip('/'), local))
        elif a == '--del':
            opts['dels'].append(v.strip('/'))
        elif a == '--message':
            opts['message'] = v
        elif a == '--repo':
            opts['repo'] = v
        elif a == '--branch':
            opts['branch'] = v
        elif a == '--timeout':
            opts['timeout'] = int(v)
    if not opts['message']:
        raise SystemExit('--message is required')
    if not opts['adds'] and not opts['dels']:
        raise SystemExit('nothing to do: pass --add and/or --del')
    return opts


def wait_for_run(repo, sha, timeout):
    print('waiting for the Actions run of %s ...' % sha[:12])
    deadline = time.time() + timeout
    run = None
    while time.time() < deadline:
        js = api('GET', '/repos/%s/actions/runs?head_sha=%s&per_page=5' % (repo, sha))
        runs = js.get('workflow_runs') or []
        if runs:
            run = runs[0]
            break
        time.sleep(5)
    if run is None:
        print('no run appeared for that commit')
        return 1
    print('run id=%s status=%s' % (run['id'], run['status']))
    while time.time() < deadline:
        js = api('GET', '/repos/%s/actions/runs/%s' % (repo, run['id']))
        print('  %s  status=%s conclusion=%s' % (time.strftime('%H:%M:%S'),
                                                  js['status'], js['conclusion']))
        if js['status'] == 'completed':
            print('run %s: %s' % (js['id'], js['conclusion']))
            print(js['html_url'])
            return 0 if js['conclusion'] == 'success' else 1
        time.sleep(20)
    print('timeout waiting for run %s' % run['id'])
    return 1


def main(argv):
    o = parse_args(argv)
    repo, branch = o['repo'], o['branch']
    ref = api('GET', '/repos/%s/git/ref/heads/%s' % (repo, branch))
    parent = ref['object']['sha']
    base_tree = api('GET', '/repos/%s/git/commits/%s' % (repo, parent))['tree']['sha']
    print('base commit=%s tree=%s' % (parent[:12], base_tree[:12]))

    if o['dry_run']:
        print('dry run: %d add, %d delete' % (len(o['adds']), len(o['dels'])))
        return 0

    entries = []
    for repo_path, local in o['adds']:
        with open(local, 'rb') as f:
            blob = api('POST', '/repos/%s/git/blobs' % repo, {
                'content': base64.b64encode(f.read()).decode('ascii'),
                'encoding': 'base64'})
        print('blob %s <- %s' % (blob['sha'][:12], local))
        entries.append({'path': repo_path, 'mode': '100644',
                        'type': 'blob', 'sha': blob['sha']})
    for repo_path in o['dels']:
        print('delete %s' % repo_path)
        entries.append({'path': repo_path, 'mode': '100644',
                        'type': 'blob', 'sha': None})
    tree = api('POST', '/repos/%s/git/trees' % repo,
               {'base_tree': base_tree, 'tree': entries})
    print('tree %s' % tree['sha'][:12])
    commit = api('POST', '/repos/%s/git/commits' % repo, {
        'message': o['message'], 'tree': tree['sha'], 'parents': [parent]})
    print('commit %s' % commit['sha'])
    api('PATCH', '/repos/%s/git/refs/heads/%s' % (repo, branch),
        {'sha': commit['sha'], 'force': False})
    print('branch %s updated; https://github.com/%s/commit/%s' % (branch, repo, commit['sha']))
    if o['wait']:
        return wait_for_run(repo, commit['sha'], o['timeout'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
