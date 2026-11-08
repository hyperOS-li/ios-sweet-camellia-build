#!/usr/bin/env python3
"""Local progress dashboard for the camellia XNU port.

Serves http://127.0.0.1:8765/ (or the next free port) and refreshes the
status JSON every 10 seconds from the GitHub API and local build artifacts.
The GitHub token is read from GH_TOKEN and never included in any response.
"""
import http.server
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'payload', 'out')
REPO = 'hyperOS-li/ios-sweet-camellia'
TOKEN = os.environ.get('GH_TOKEN', '')
ZIP = os.path.join(OUT, 'xnu110.zip')
TOTAL = 60031977

_run_cache = {'run': None, 'ts': 0}


def _refresh_loop():
    while True:
        _run_cache['run'] = gh_run()
        _run_cache['ts'] = time.time()
        time.sleep(30)


def gh_run():
    if not TOKEN:
        return {'error': 'no token'}
    req = urllib.request.Request(
        'https://api.github.com/repos/%s/actions/runs?per_page=1' % REPO,
        headers={'Authorization': 'Bearer ' + TOKEN,
                 'User-Agent': 'dsh-progress',
                 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.load(r)
        runs = data.get('workflow_runs') or []
        if not runs:
            return None
        run = runs[0]
        return {
            'id': run.get('id'),
            'status': run.get('status'),
            'conclusion': run.get('conclusion'),
            'sha': (run.get('head_sha') or '')[:10],
            'created': run.get('created_at'),
            'url': run.get('html_url'),
        }
    except Exception as exc:  # noqa: BLE001
        return {'error': str(exc)}


def status():
    zip_size = os.path.getsize(ZIP) if os.path.exists(ZIP) else 0
    qemu = None
    p = os.path.join(OUT, 'progress.json')
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                qemu = json.load(f)
        except Exception:  # noqa: BLE001
            qemu = None
    return {
        'time': time.strftime('%H:%M:%S'),
        'phase': 'M2 · 0109 QEMU 验证（跳过 dtrace + 网络块）',
        'run': _run_cache['run'],
        'run_ts': _run_cache['ts'],
        'download': {
            'size': zip_size,
            'total': TOTAL,
            'percent': round(zip_size * 100.0 / TOTAL, 1) if TOTAL else 0,
            'extracted': os.path.isdir(os.path.join(OUT, 'xnu110')),
        },
        'qemu': qemu,
        'milestones': [
            {'name': 'M1 LK → 自写代码 / 显示 / WDT', 'state': 'done'},
            {'name': 'M2 XNU 启动 + 日志等待 root', 'state': 'doing'},
            {'name': 'M3 MTK UFS + iOS 根', 'state': 'todo'},
            {'name': 'M4 SpringBoard / 设置', 'state': 'todo'},
            {'name': 'M5 触摸', 'state': 'todo'},
        ],
        'patches': [
            {'id': '0082', 'note': 'CAMELLIA machine config（sweet flags）'},
            {'id': '0083', 'note': 'MASTER.arm64.camellia'},
            {'id': '0084', 'note': 'CAMELLIA.h + board_config'},
            {'id': '0086', 'note': 'make 级 SWEET||CAMELLIA'},
            {'id': '0087', 'note': '早期 FB 用 boot_args Video'},
            {'id': '0088', 'note': '早期 console / 最终别名'},
            {'id': '0089', 'note': 'soc_base=0 仍读 timebase'},
            {'id': '0090', 'note': 'fbcon probe + stride 4352'},
            {'id': '0091', 'note': 'bootstrap KB 标记'},
            {'id': '0092', 'note': 'SWEET 跳过 PE_lockdown_iokit'},
            {'id': '0093', 'note': 'vm_set_restrictions / kperf 标记'},
            {'id': '0094', 'note': 'SWEET 跳过 kperf_init_early'},
            {'id': '0095', 'note': 'bsd_init / bsd_autoconf 标记'},
            {'id': '0096', 'note': 'bsd_init 细标记（hfs/pthread/domaininit/autoconf）'},
            {'id': '0097', 'note': 'hfs 之后逐调用标记'},
            {'id': '0098', 'note': 'SWEET 跳过 mcache_init'},
            {'id': '0099', 'note': 'SWEET 跳过 mbinit'},
            {'id': '0100', 'note': 'SWEET 跳过 DLIL SUM16 自检'},
            {'id': '0101', 'note': 'dlil/socketinit/domaininit 标记'},
            {'id': '0102', 'note': 'SWEET 跳过 domaininit/iptap_init'},
            {'id': '0103', 'note': 'domaininit 后逐调用标记'},
            {'id': '0104', 'note': 'SWEET 跳过 flow_divert/skywalk/necp/netagent'},
            {'id': '0105', 'note': 'SWEET 跳过 sysctl_mib_init'},
            {'id': '0106', 'note': 'SWEET 跳过 kminit'},
            {'id': '0107', 'note': 'SWEET 跳过 pseudo_init 循环'},
            {'id': '0108', 'note': 'dtrace/loopattach/ether/net/inittodr 标记'},
            {'id': '0109', 'note': 'SWEET 跳过 dtrace + 网络块'},
        ],
        'timeline': [
            'QEMU 里 XNU 22.6 启动，日志到 AppleCLCD',
            '0090：fbcon 注册，stride 修 4352',
            '0091：KB 标记定位到 PE_lockdown_iokit 之后',
            '0092：跳过 IOKit lockdown，仍停在 after lockdown',
            '0093：标记定位到 kperf_init_early 卡住',
            '0094：跳过 kperf → bsd_init 开始，HFS 注册 30.4s 后停住',
            '0095：bsd_init start 出现，但 HFS 后无 mountroot loop',
            '0096：after hfs_register 出现，无 after pthread_init',
            '0097：定位到 mcache_init 卡住',
            '0098：跳过 mcache → 又卡在 mbinit',
            '0099：跳过 mbinit → 过 pthread，DLIL SUM16 自检重启',
            '0100：跳过 SUM16 → 仍在 dlil_init 后 MACH Reboot',
            '0101：定位到 domaininit',
            '0102：跳过 domaininit → 又卡在 flow_divert/skywalk/necp/netagent',
            '0103：逐个加标记',
            '0104：跳过 netext → 卡在 sysctl_mib_init',
            '0105：跳过 sysctl_mib → 到 bsd_autoconf，卡在 kminit',
            '0106：跳过 kminit → 卡在 pseudo_init 循环',
            '0107：跳过 pseudo_init → IOKitBSDInit 返回 0',
            '0108：定位到 dtrace_postinit',
            '0109：跳过 dtrace + 网络块，等 QEMU',
        ],
    }


HTML = '''<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>camellia · XNU 移植进度</title>
<style>
:root{color-scheme:dark}
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0e1116;color:#e7e9ee;margin:0;padding:26px}
h1{font-size:22px;margin:0 0 6px}
.sub{color:#8b95a7;font-size:13px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.card{background:#161b23;border:1px solid #232b38;border-radius:12px;padding:16px}
.card h2{font-size:13px;color:#8b95a7;margin:0 0 10px;font-weight:600;letter-spacing:.04em}
.big{font-size:24px;font-weight:700}
.ok{color:#4ade80}.bad{color:#f87171}.wait{color:#facc15}
ul{margin:0;padding-left:18px}li{margin:4px 0;font-size:13px;color:#c7cedb}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#1f2937;color:#cbd5e1;margin-right:6px}
.done{background:#14532d;color:#86efac}.doing{background:#713f12;color:#fde68a}.todo{background:#1f2937;color:#94a3b8}
.bar{height:8px;background:#1f2937;border-radius:999px;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:#3b82f6;width:0}
a{color:#93c5fd}
</style></head><body>
<h1>红米 Note 10 5G（camellia）· XNU 移植进度</h1>
<div class="sub">自动刷新 · <span id="time">--</span> · 仅本机可见</div>
<div class="grid">
<div class="card"><h2>当前阶段</h2><div class="big" id="phase">--</div><div class="sub" style="margin-top:8px" id="phase2"></div></div>
<div class="card"><h2>最新 CI run</h2><div class="big" id="run">--</div><div class="sub" id="run2"></div></div>
<div class="card"><h2>kernel 下载</h2><div class="big" id="dl">--</div><div class="bar"><i id="bar"></i></div><div class="sub" id="dl2"></div></div>
<div class="card"><h2>QEMU 判据</h2><div class="big" id="qemu">--</div><div class="sub" id="qemu2"></div></div>
<div class="card"><h2>里程碑</h2><ul id="ms"></ul></div>
<div class="card"><h2>补丁</h2><ul id="patches"></ul></div>
<div class="card" style="grid-column:1/-1"><h2>最近时间线</h2><ul id="timeline"></ul></div>
</div>
<script>
async function tick(){
  try{
    const s = await (await fetch('/status.json?t=' + Date.now(), {cache:'no-store'})).json();
    document.getElementById('time').textContent = s.time;
    document.getElementById('phase').textContent = s.phase;
    const r = s.run;
    const runEl = document.getElementById('run');
    if(!r){ runEl.textContent='等待'; runEl.className='big wait'; }
    else if(r.error){ runEl.textContent='API 错误'; runEl.className='big bad'; document.getElementById('run2').textContent=r.error; }
    else {
      runEl.textContent = r.status + (r.conclusion? ' / '+r.conclusion : '');
      runEl.className = 'big ' + (r.conclusion==='success'?'ok':r.conclusion==='failure'?'bad':'wait');
      document.getElementById('run2').innerHTML = '#'+r.id+' · '+r.sha+' · <a href="'+r.url+'" target="_blank">打开</a>';
    }
    const d = s.download;
    document.getElementById('dl').textContent = d.percent + '%';
    document.getElementById('bar').style.width = d.percent + '%';
    document.getElementById('dl2').textContent = (d.size/1048576).toFixed(1)+' / '+(d.total/1048576).toFixed(1)+' MB' + (d.extracted?' · 已解压':'');
    const q = s.qemu;
    const qEl = document.getElementById('qemu');
    if(!q){ qEl.textContent='等待 QEMU 结果'; qEl.className='big wait'; document.getElementById('qemu2').textContent=''; }
    else { qEl.textContent=q.verdict||'已运行'; qEl.className='big '+(q.ok?'ok':'bad'); document.getElementById('qemu2').textContent=q.detail||''; }
    document.getElementById('ms').innerHTML = s.milestones.map(m=>'<li><span class="pill '+m.state+'">'+m.state+'</span>'+m.name+'</li>').join('');
    document.getElementById('patches').innerHTML = s.patches.map(p=>'<li><b>'+p.id+'</b> · '+p.note+'</li>').join('');
    document.getElementById('timeline').innerHTML = s.timeline.map(t=>'<li>'+t+'</li>').join('');
  }catch(e){ document.getElementById('time').textContent='连接失败'; }
}
tick(); setInterval(tick, 10000);
</script></body></html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):  # noqa: N802
        if self.path in ('/', '/index.html'):
            self._send(HTML.encode('utf-8'), 'text/html; charset=utf-8')
        elif self.path.startswith('/status.json'):
            self._send(json.dumps(status(), ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):  # quiet
        pass


def main():
    port = 8765
    for candidate in range(8765, 8771):
        try:
            httpd = http.server.ThreadingHTTPServer(('127.0.0.1', candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    else:
        print('no free port', file=sys.stderr)
        sys.exit(1)
    print('PROGRESS_URL=http://127.0.0.1:%d/' % port, flush=True)
    threading.Thread(target=_refresh_loop, daemon=True).start()
    httpd.serve_forever()


if __name__ == '__main__':
    main()
