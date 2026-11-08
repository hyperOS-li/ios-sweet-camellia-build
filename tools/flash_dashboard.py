#!/usr/bin/env python3
"""Tiny local dashboard for the fastboot userdata/boot flash.

Serves 127.0.0.1:64775; reads the JSON written by tools/flash_progress.ps1.
"""
import http.server
import json
import pathlib
import socketserver

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROGRESS = ROOT / 'payload' / 'out' / 'n2' / 'flash_progress.json'
PORT = 64775

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Camellia flash</title>
<style>
body{background:#0b1020;color:#e8eefc;font-family:system-ui,Segoe UI,sans-serif;margin:0;padding:32px}
.card{max-width:780px;margin:0 auto;background:#121a33;border:1px solid #1e2a4a;border-radius:18px;padding:28px 32px;box-shadow:0 12px 40px #0008}
h1{font-size:18px;font-weight:600;color:#9fb0d0;margin:0 0 18px}
.big{font-size:54px;font-weight:800;letter-spacing:-1px}
.bar{height:26px;background:#1e2a4a;border-radius:13px;overflow:hidden;margin:14px 0}
.fill{height:100%;width:0;background:linear-gradient(90deg,#3b82f6,#22d3ee);transition:width .35s}
.row{display:flex;justify-content:space-between;margin-top:10px;color:#9fb0d0;font-size:15px}
.mono{font-family:ui-monospace,Consolas,monospace;color:#cfe0ff}
.note{margin-top:22px;color:#7f90b0;font-size:13px;line-height:1.6}
.dot{display:inline-block;width:8px;height:8px;border-radius:4px;background:#22c55e;margin-right:6px}
</style></head><body><div class="card">
<h1><span class="dot" id="dot"></span>Camellia · iOS root flash</h1>
<div class="big" id="pct">--%</div>
<div class="bar"><div class="fill" id="fill"></div></div>
<div class="row"><span id="bytes">--</span><span id="speed">--</span></div>
<div class="row"><span id="phase">--</span><span id="elapsed">--</span></div>
<div class="row"><span id="pid">--</span><span id="updated">--</span></div>
<p class="note" id="note">16 GiB userdata → boot 135 → reboot. 刷写过程中不要拔线、不要断电。</p>
<script>
async function tick(){
  try{
    const r = await fetch('/progress.json?t=' + Date.now(), {cache:'no-store'});
    const d = await r.json();
    const pct = d.total ? Math.min(100, 100 * d.read / d.total) : 0;
    document.getElementById('pct').textContent = pct.toFixed(1) + '%';
    document.getElementById('fill').style.width = pct + '%';
    document.getElementById('bytes').textContent =
      (d.read/1073741824).toFixed(2) + ' / ' + (d.total/1073741824).toFixed(2) + ' GiB';
    document.getElementById('speed').textContent = (d.speed/1048576).toFixed(1) + ' MiB/s';
    document.getElementById('phase').textContent = d.phase || '...';
    document.getElementById('elapsed').textContent = d.elapsedSec != null ? d.elapsedSec + ' s' : '';
    document.getElementById('pid').textContent = d.pid ? 'fastboot PID ' + d.pid : 'fastboot 未在运行（等 boot/reboot）';
    document.getElementById('updated').textContent = d.updated || '';
    document.getElementById('dot').style.background = d.running ? '#22c55e' : '#f59e0b';
  }catch(e){
    document.getElementById('note').textContent = '等待进度文件/服务…';
  }
}
setInterval(tick, 1000); tick();
</script></div></body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/':
            self._send(200, HTML.encode('utf-8'), 'text/html; charset=utf-8')
        elif path == '/progress.json':
            try:
                data = PROGRESS.read_bytes().lstrip(b'\xef\xbb\xbf')
            except FileNotFoundError:
                data = b'{}'
            self._send(200, data, 'application/json')
        elif path == '/favicon.ico':
            self._send(204, b'', 'image/x-icon')
        else:
            self._send(404, b'not found', 'text/plain')

    def log_message(self, *args):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == '__main__':
    print('dashboard: http://127.0.0.1:%d/' % PORT)
    Server(('127.0.0.1', PORT), Handler).serve_forever()
