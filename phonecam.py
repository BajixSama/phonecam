"""
PhoneCam - use your phone's camera on your PC over WiFi (no app, no Bluetooth).

Setup (one time):  pip install aiohttp cryptography
Run:               python phonecam.py
"""
import asyncio
import datetime
import ipaddress
import os
import socket
import ssl
import sys

from aiohttp import web, WSMsgType

PORT = 8443
HERE = os.path.dirname(os.path.abspath(__file__))


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def make_cert(ip):
    """Self-signed HTTPS cert (phone browsers only allow camera access over HTTPS)."""
    cert_path = os.path.join(HERE, f"cert_{ip}.pem")
    key_path = os.path.join(HERE, f"key_{ip}.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phonecam")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address(ip)),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


viewers = set()
phones = set()


async def ws_handler(request):
    role = request.query.get("role", "viewer")
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024, heartbeat=15)
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
                        # (frames are relayed as-is to every connected viewer)
                        if v._writer is None or not v.closed:
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


VIEWER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PhoneCam - PC Viewer</title>
<style>
  :root{color-scheme:light dark;--bg:#fff;--fg:#111;--mut:#666;--card:#f2f2f2}
  @media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--card:#222}}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
  header{padding:12px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:var(--card)}
  header b{font-size:18px}
  .url{font-family:monospace;font-size:16px;padding:4px 10px;border-radius:6px;background:var(--bg)}
  .dot{width:10px;height:10px;border-radius:50%;background:#c33;display:inline-block}
  .dot.on{background:#2a2}
  button{padding:6px 12px;border:0;border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
  main{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#000}
  canvas{max-width:100%;max-height:100%;object-fit:contain}
  .hint{color:var(--mut);font-size:13px}
</style></head><body>
<header>
  <b>PhoneCam</b>
  <span><span id="dot" class="dot"></span> <span id="st">Waiting for phone...</span></span>
  <span>Open on your phone: <span class="url" id="url"></span></span>
  <button id="mir">Mirror</button>
  <button id="rot">Rotate</button>
  <button id="fs">Fullscreen</button>
  <span class="hint" id="fps"></span>
</header>
<main><canvas id="c" width="1280" height="720"></canvas></main>
<script>
const PHONE_URL = "__PHONE_URL__";
document.getElementById('url').textContent = PHONE_URL;
const c = document.getElementById('c'), ctx = c.getContext('2d');
let mirror = false, rot = 0, frames = 0;
function applyT(){
  c.style.transform = `scaleX(${mirror?-1:1}) rotate(${rot}deg)`;
  if (rot % 180) { c.style.maxWidth = '100vh'; c.style.maxHeight = '100vw'; }
  else { c.style.maxWidth = '100%'; c.style.maxHeight = '100%'; }
}
document.getElementById('mir').onclick = () => { mirror = !mirror; applyT(); };
document.getElementById('rot').onclick = () => { rot = (rot + 90) % 360; applyT(); };
document.getElementById('fs').onclick = () => document.documentElement.requestFullscreen();
function connect(){
  const ws = new WebSocket('wss://' + location.host + '/ws?role=viewer');
  ws.binaryType = 'arraybuffer';
  ws.onmessage = async (e) => {
    if (typeof e.data === 'string') {
      const on = e.data === 'phone_on';
      document.getElementById('dot').className = 'dot' + (on ? ' on' : '');
      document.getElementById('st').textContent = on ? 'Phone connected' : 'Waiting for phone...';
      return;
    }
    const bmp = await createImageBitmap(new Blob([e.data]));
    if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; }
    ctx.drawImage(bmp, 0, 0); bmp.close(); frames++;
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
connect();
setInterval(() => { document.getElementById('fps').textContent = frames + ' fps'; frames = 0; }, 1000);
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
  video{width:100%;border-radius:12px;background:#000;max-height:60vh}
  .row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  button,select{flex:1;padding:14px;border:0;border-radius:10px;background:var(--card);color:var(--fg);font-size:16px}
  button.p{background:#1a73e8;color:#fff}
  #st{margin-top:12px;font-size:14px;opacity:.8}
</style></head><body>
<video id="v" playsinline muted autoplay></video>
<div class="row">
  <button class="p" id="start">Start camera</button>
  <button id="flip">Front/Back</button>
</div>
<div class="row">
  <select id="q">
    <option value="3840,0.85,10">4K - Max quality (strong WiFi)</option>
    <option value="2560,0.8,15">1440p - High quality</option>
    <option value="1920,0.75,20">1080p - Full HD</option>
    <option value="1280,0.7,25">720p - Balanced</option>
    <option value="854,0.6,25">480p - Smooth / weak WiFi</option>
  </select>
</div>
<div id="st">Ready</div>
<script>
const v = document.getElementById('v'), st = document.getElementById('st');
const c = document.createElement('canvas'), ctx = c.getContext('2d');
let ws, stream, facing = 'environment', running = false, wake = null;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function connect(){
  ws = new WebSocket('wss://' + location.host + '/ws?role=phone');
  ws.onopen = () => st.textContent = 'Connected to PC' + (running ? ' - streaming' : '');
  ws.onclose = () => { st.textContent = 'Reconnecting...'; setTimeout(connect, 1000); };
}
async function startCam(){
  if (stream) stream.getTracks().forEach(t => t.stop());
  const [w] = document.getElementById('q').value.split(',').map(Number);
  stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: facing, width: { ideal: w }, height: { ideal: Math.round(w * 9 / 16) } }, audio: false });
  v.srcObject = stream; await v.play();
  st.textContent = 'Camera: ' + v.videoWidth + 'x' + v.videoHeight;
}
async function loop(){
  while (running) {
    const [, q, fps] = document.getElementById('q').value.split(',').map(Number);
    const t0 = performance.now();
    if (ws.readyState === 1 && ws.bufferedAmount < 4000000 && v.videoWidth) {
      c.width = v.videoWidth; c.height = v.videoHeight;
      ctx.drawImage(v, 0, 0);
      const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', q));
      ws.send(blob);
    }
    await sleep(Math.max(0, 1000 / fps - (performance.now() - t0)));
  }
}
document.getElementById('start').onclick = async () => {
  try {
    await startCam();
    if (!running) { running = true; loop(); }
    document.getElementById('start').textContent = 'Streaming...';
    try { wake = await navigator.wakeLock.request('screen'); } catch (e) {}
    st.textContent = 'Streaming ' + v.videoWidth + 'x' + v.videoHeight;
  } catch (e) { st.textContent = 'Camera error: ' + e.message; }
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


def main():
    ip = local_ip()
    cert, key = make_cert(ip)
    phone_url = f"https://{ip}:{PORT}/phone"

    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(
        text=VIEWER_HTML.replace("__PHONE_URL__", phone_url), content_type="text/html"))
    app.router.add_get("/phone", lambda r: web.Response(text=PHONE_HTML, content_type="text/html"))
    app.router.add_get("/ws", ws_handler)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)

    print("\n=== PhoneCam is running ===")
    print(f"Open on PC   : https://localhost:{PORT}")
    print(f"Open on phone: {phone_url}")
    print("(Your browser will show a 'Not secure' warning -> Advanced -> Proceed. This is expected.)\n")
    web.run_app(app, host="0.0.0.0", port=PORT, ssl_context=ctx, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
