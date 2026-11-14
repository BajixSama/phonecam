# PhoneCam

Use your phone's camera on your PC, wirelessly. No phone app, no Bluetooth, no USB.
The phone just opens a web page in its browser.

Two versions:

| File | Works on | Notes |
|---|---|---|
| `phonecam_anywhere.py` | **Any network** (mobile data, different WiFi) | Uses a free Cloudflare quick tunnel. Recommended. |
| `phonecam.py` | Same WiFi only | Fully local, self-signed HTTPS certificate (browser shows a warning you must accept). |

## Quick start

```bash
pip install aiohttp
python phonecam_anywhere.py      # on Windows: py phonecam_anywhere.py
```

1. Open the **PC link** printed in the terminal in your PC browser. A QR code appears.
2. Scan the QR code with your phone (or open the **phone link**).
3. Tap **Start camera** on the phone. The video shows up on the PC.

The first run downloads `cloudflared` automatically (Windows, Linux, macOS).

## Features

- Front/back camera switch
- Quality presets: 480p, 720p, 1080p, 1440p, 4K
- Adaptive JPEG quality when the network is slow
- Live upload speed and real camera resolution shown on the phone
- Mirror, rotate and fullscreen on the PC viewer
- Random secret key in the link, so only people with the link can connect

## Using it as a webcam in Zoom / Meet / Discord

A browser page cannot register itself as a camera device. Use [OBS Studio](https://obsproject.com/):
add the PC viewer page as a *Browser source* (or use *Window capture*), then click **Start Virtual Camera**.

## Limitations

- Video is sent as JPEG frames over WebSocket. Quality and latency are below real video codecs (H.264/WebRTC).
- 4K needs a very fast upload link (roughly 100 Mbps at 10 fps). On mobile data use 720p or lower.
- iPhone Safari does not provide 4K to web pages, 1080p at most.
- No audio yet.
- Cloudflare quick tunnels are meant for testing and can be limited or stopped at any time.

## Security

Anyone who has the full phone link (including `?key=...`) can stream into your viewer, and the
viewer link shows whatever is streamed. Do not share the links. A new key and URL are generated every run.

## License

MIT
