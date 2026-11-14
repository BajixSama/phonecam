"""
PhoneCam Anywhere - use your phone's camera on your PC from ANY network (mobile data / different WiFi).

Setup (one time):  pip install aiohttp
Run:               python phonecam_anywhere.py

How it works: a small server runs on your PC together with a free Cloudflare "quick tunnel"
(no account or signup needed). The tunnel gives you a public https link that the phone can
open from anywhere. The link contains a random secret key, so nobody can connect without it.
The cloudflared binary is downloaded automatically by the script on first run.
"""
import hmac
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

from aiohttp import web, WSMsgType

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = secrets.token_urlsafe(9)
PHONE_URL = ""

viewers = set()
phones = set()


def authed(request):
    return hmac.compare_digest(request.query.get("key", ""), KEY)


# ------------------------------------------------------------------ server
async def ws_handler(request):
    if not authed(request):
        raise web.HTTPForbidden(text="bad key")
    role = request.query.get("role", "viewer")
    ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024, heartbeat=20)
    await ws.prepare(request)

    if role == "phone":
        phones.add(ws)
        for v in list(viewers):
            await v.send_str("phone_on")
    else:
        viewers.add(ws)
        await ws.send_str("phone_on" if phones else "phone_off")

    try:
        async for msg in ws:
            if role == "phone" and msg.type == WSMsgType.BINARY:
                for v in list(viewers):
                    try:
                        await v.send_bytes(msg.data)
                    except Exception:
                        viewers.discard(v)
    finally:
        phones.discard(ws)
        viewers.discard(ws)
        if role == "phone":
            for v in list(viewers):
                try:
                    await v.send_str("phone_off")
                except Exception:
                    pass
    return ws


def page(html, **kw):
    async def handler(request):
        if not authed(request):
            raise web.HTTPForbidden(text="Invalid link (key missing). Use the full link printed in the terminal.")
        out = html
        for k, v in kw.items():
            out = out.replace(k, v())
        return web.Response(text=out, content_type="text/html", headers={"Cache-Control": "no-store"})
    return handler


VIEWER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PhoneCam - PC Viewer</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"></script>
<style>
  :root{color-scheme:light dark;--bg:#fff;--fg:#111;--mut:#666;--card:#f2f2f2}
  @media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--card:#222}}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
  header{padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:var(--card)}
  .dot{width:10px;height:10px;border-radius:50%;background:#c33;display:inline-block}
  .dot.on{background:#2a2}
  button{padding:6px 12px;border:0;border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
  main{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#000;position:relative}
  canvas{max-width:100%;max-height:100%}
  #pair{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
        background:var(--bg);color:var(--fg);text-align:center;padding:20px}
  #pair.hide{display:none}
  #qr{background:#fff;padding:12px;border-radius:12px}
  #qr img{display:block}
  .url{font-family:monospace;font-size:13px;word-break:break-all;max-width:560px;background:var(--card);padding:8px 12px;border-radius:8px}
  .hint{color:var(--mut);font-size:13px}
</style></head><body>
<header>
  <b>PhoneCam</b>
  <span><span id="dot" class="dot"></span> <span id="st">Waiting for phone...</span></span>
  <button id="mir">Mirror</button>
  <button id="rot">Rotate</button>
  <button id="fs">Fullscreen</button>
  <button id="pairbtn">Show QR</button>
  <span class="hint" id="info"></span>
</header>
<main>
  <canvas id="c" width="1280" height="720"></canvas>
  <div id="pair">
    <h2 style="margin:0">Scan this QR code with your phone</h2>
    <div id="qr"></div>
    <div class="url" id="url"></div>
    <button id="copy">Copy link</button>
    <div class="hint">The phone can be on any network (mobile data or a different WiFi). This link is private, do not share it.</div>
  </div>
</main>
<script>
const PHONE_URL = "__PHONE_URL__";
const KEY = new URLSearchParams(location.search).get('key');
const $ = id => document.getElementById(id);
$('url').textContent = PHONE_URL;
try { const q = qrcode(0, 'M'); q.addData(PHONE_URL); q.make(); $('qr').innerHTML = q.createImgTag(6, 0); }
catch (e) { $('qr').style.display = 'none'; }
$('copy').onclick = () => navigator.clipboard.writeText(PHONE_URL);
$('pairbtn').onclick = () => $('pair').classList.toggle('hide');

const c = $('c'), ctx = c.getContext('2d');
let mirror = false, rot = 0, frames = 0;
function applyT(){
  c.style.transform = `scaleX(${mirror ? -1 : 1}) rotate(${rot}deg)`;
  if (rot % 180) { c.style.maxWidth = '100vh'; c.style.maxHeight = '100vw'; }
  else { c.style.maxWidth = '100%'; c.style.maxHeight = '100%'; }
}
$('mir').onclick = () => { mirror = !mirror; applyT(); };
$('rot').onclick = () => { rot = (rot + 90) % 360; applyT(); };
$('fs').onclick = () => document.documentElement.requestFullscreen();

function connect(){
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/ws?role=viewer&key=' + encodeURIComponent(KEY));
  ws.binaryType = 'arraybuffer';
  ws.onmessage = async (e) => {
    if (typeof e.data === 'string') {
      const on = e.data === 'phone_on';
      $('dot').className = 'dot' + (on ? ' on' : '');
      $('st').textContent = on ? 'Phone connected' : 'Waiting for phone...';
      if (on) $('pair').classList.add('hide'); else $('pair').classList.remove('hide');
      return;
    }
    const bmp = await createImageBitmap(new Blob([e.data]));
    if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; }
    ctx.drawImage(bmp, 0, 0); bmp.close(); frames++;
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
connect();
setInterval(() => { $('info').textContent = (c.width + 'x' + c.height) + '  ' + frames + ' fps'; frames = 0; }, 1000);
</script></body></html>
"""

PHONE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>PhoneCam - Phone</title>
<style>
  :root{color-scheme:light dark;--bg:#fff;--fg:#111;--card:#eee}
  @media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--card:#262626}}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif;padding:16px;
       padding-top:max(16px,env(safe-area-inset-top));padding-bottom:max(16px,env(safe-area-inset-bottom))}
  video{width:100%;border-radius:12px;background:#000;max-height:55vh}
  .row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  button,select{flex:1;padding:14px;border:0;border-radius:10px;background:var(--card);color:var(--fg);font-size:16px}
  button.p{background:#1a73e8;color:#fff}
  #st{margin-top:12px;font-size:14px;opacity:.85;line-height:1.5}
</style></head><body>
<video id="v" playsinline muted autoplay></video>
<div class="row">
  <button class="p" id="start">Start camera</button>
  <button id="flip">Front/Back</button>
</div>
<div class="row">
  <select id="q">
    <option value="1280,0.7,20" selected>720p - Best for mobile data</option>
    <option value="854,0.6,25">480p - Weak network</option>
    <option value="1920,0.75,15">1080p - Strong network</option>
    <option value="2560,0.8,12">1440p - Very strong network</option>
    <option value="3840,0.85,10">4K - Max (needs very fast upload)</option>
  </select>
</div>
<div id="st">Ready</div>
<script>
const v = document.getElementById('v'), st = document.getElementById('st');
const KEY = new URLSearchParams(location.search).get('key');
const c = document.createElement('canvas'), ctx = c.getContext('2d');
let ws, stream, facing = 'environment', running = false, wake = null;
let bytesSent = 0, mbps = 0, curQ = 0.7, connected = false;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function connect(){
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws?role=phone&key=' + encodeURIComponent(KEY));
  ws.onopen = () => { connected = true; render(); };
  ws.onclose = () => { connected = false; render(); setTimeout(connect, 1000); };
}
function render(extra){
  st.innerHTML = (connected ? 'Connected to PC' : 'Reconnecting...') +
    (running ? '<br>Streaming ' + v.videoWidth + 'x' + v.videoHeight + ' | upload ' + mbps.toFixed(1) + ' Mbps | JPEG q' + Math.round(curQ * 100) : '') +
    (extra ? '<br>' + extra : '');
}
async function startCam(){
  if (stream) stream.getTracks().forEach(t => t.stop());
  const [w] = document.getElementById('q').value.split(',').map(Number);
  stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: facing, width: { ideal: w }, height: { ideal: Math.round(w * 9 / 16) } }, audio: false });
  v.srcObject = stream; await v.play();
  const qmax = Number(document.getElementById('q').value.split(',')[1]);
  curQ = qmax;
}
async function loop(){
  let lastSize = 300000;
  while (running) {
    const [, qmax, fps] = document.getElementById('q').value.split(',').map(Number);
    const t0 = performance.now();
    if (ws && ws.readyState === 1 && v.videoWidth) {
      if (ws.bufferedAmount > lastSize * 1.5) {
        // network is slow: skip the frame and lower quality (adaptive)
        curQ = Math.max(0.35, curQ - 0.05);
        await sleep(30);
        continue;
      }
      if (ws.bufferedAmount === 0 && curQ < qmax) curQ = Math.min(qmax, curQ + 0.01);
      c.width = v.videoWidth; c.height = v.videoHeight;
      ctx.drawImage(v, 0, 0);
      const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', curQ));
      lastSize = Math.max(blob.size, 50000);
      bytesSent += blob.size;
      ws.send(blob);
    }
    await sleep(Math.max(0, 1000 / fps - (performance.now() - t0)));
  }
}
setInterval(() => { mbps = bytesSent * 8 / 1e6; bytesSent = 0; render(); }, 1000);

document.getElementById('start').onclick = async () => {
  try {
    await startCam();
    if (!running) { running = true; loop(); }
    document.getElementById('start').textContent = 'Streaming...';
    try { wake = await navigator.wakeLock.request('screen'); } catch (e) {}
    render();
  } catch (e) { render('Camera error: ' + e.message); }
};
document.getElementById('flip').onclick = async () => {
  facing = facing === 'environment' ? 'user' : 'environment';
  if (running) await startCam();
};
document.getElementById('q').onchange = async () => { if (running) await startCam(); };
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && running) { try { wake = await navigator.wakeLock.request('screen'); } catch (e) {} }
});
connect();
</script></body></html>
"""


# ------------------------------------------------------------------ tunnel
def find_cloudflared():
    found = shutil.which("cloudflared")
    if found:
        return found
    exe = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    local = os.path.join(HERE, exe)
    if os.path.exists(local):
        return local

    system, mach = platform.system(), platform.machine().lower()
    arch = "arm64" if ("arm" in mach or "aarch64" in mach) else "amd64"
    base = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    print("Downloading cloudflared (first run only)...")
    try:
        if system == "Windows":
            urllib.request.urlretrieve(base + f"cloudflared-windows-{arch}.exe", local)
        elif system == "Linux":
            urllib.request.urlretrieve(base + f"cloudflared-linux-{arch}", local)
            os.chmod(local, os.stat(local).st_mode | stat.S_IEXEC)
        elif system == "Darwin":
            tgz = os.path.join(HERE, "cloudflared.tgz")
            urllib.request.urlretrieve(base + f"cloudflared-darwin-{arch}.tgz", tgz)
            with tarfile.open(tgz) as t:
                t.extract("cloudflared", HERE)
            os.remove(tgz)
            os.chmod(local, os.stat(local).st_mode | stat.S_IEXEC)
        else:
            return None
    except Exception as e:
        print("Download fail:", e)
        return None
    return local


def start_tunnel():
    exe = find_cloudflared()
    if not exe:
        return None, None
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    url = None
    deadline = time.time() + 60
    pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = pat.search(line)
        if m:
            url = m.group(0)
            break

    def drain():
        for _ in proc.stdout:
            pass
    threading.Thread(target=drain, daemon=True).start()
    return proc, url


def main():
    global PHONE_URL
    print("Starting tunnel, this can take 10-20 seconds...")
    proc, url = start_tunnel()
    if not url:
        print("\nCould not create the tunnel. Check your internet connection or install cloudflared manually:")
        print("https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)
    PHONE_URL = f"{url}/phone?key={KEY}"

    app = web.Application()
    app.router.add_get("/", page(VIEWER_HTML, __PHONE_URL__=lambda: PHONE_URL))
    app.router.add_get("/phone", page(PHONE_HTML))
    app.router.add_get("/ws", ws_handler)

    print("\n=== PhoneCam Anywhere is running ===")
    print(f"Open on PC   : http://localhost:{PORT}/?key={KEY}   (a QR code is shown there)")
    print(f"Open on phone: {PHONE_URL}\n")
    print("Press Ctrl+C to stop.\n")
    try:
        web.run_app(app, host="127.0.0.1", port=PORT, print=None)
    finally:
        proc.terminate()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
