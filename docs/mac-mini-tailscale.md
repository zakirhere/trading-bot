# Mac Mini + Tailscale Deployment Notes

Goal: run `tradebot` on an always-on Mac mini at home and access the dashboard privately without exposing it to the public internet.

## Recommended Network Setup

Use Tailscale private tailnet access.

Do not expose the dashboard with router port forwarding, DDNS, static IP, or Tailscale Funnel.

Tailscale gives each signed-in device a private network identity. With MagicDNS enabled, the Mac mini can be reached by name from other devices in the same tailnet.

Example:

```text
http://mac-mini:8787
```

or by Tailscale IP:

```text
http://100.x.y.z:8787
```

For a friendly name such as `zakir-tradebot.com`, point a private DNS record at
the Mac mini's Tailscale `100.x.y.z` address. Keep the hostname useful only from
devices signed into the same tailnet.

Examples:

```bash
ssh zakir@zakir-tradebot.com
open vnc://zakir-tradebot.com
```

Dashboard:

```text
http://zakir-tradebot.com:8787
```

## Why Tailscale

- Avoids CGNAT/static-IP/DDNS problems.
- No public dashboard exposure.
- Works from laptop or phone as long as the device is signed into the same tailnet.
- Good fit for private access to the tradebot dashboard.

## Setup Later

1. Install Tailscale on the Mac mini.
2. Install Tailscale on laptop/phone.
3. Sign into the same Tailscale account.
4. Enable MagicDNS in the Tailscale admin console if needed.
5. Clone the repo on the Mac mini.
6. Create `.env` with Alpaca paper credentials and optional Slack webhook.
7. Install the persistent service:

```bash
deploy/macos/install-service.sh
```

8. Open the dashboard from another Tailscale device.

## Dashboard Binding

The dashboard binds to:

```text
SERVICE_HOST=127.0.0.1
SERVICE_PORT=8787
```

That is local-only. For direct dashboard access from another Tailscale device,
set this in `.env` on the Mac mini:

```bash
SERVICE_HOST=100.x.y.z
SERVICE_PORT=8787
```

Use the Mac mini's own Tailscale IP for `SERVICE_HOST`, not the public home IP.

Avoid binding publicly unless the machine is protected by firewall rules and the dashboard has authentication.
