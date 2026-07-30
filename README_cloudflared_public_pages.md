# Cloudflared public pages

It now starts automatically when `start.py` starts the WebUI.

Manual fallback on the home master machine after the WebUI is already open on `127.0.0.1:8989`:

```text
scripts/windows/start_cloudflared_public_pages.bat
```

It starts two local whitelist gates:

```text
127.0.0.1:7979 -> remote_login.html only
127.0.0.1:6969 -> mobile_order.html only
```

Then it starts two cloudflared quick tunnels. Cloudflared quick tunnel external
URLs do not keep `:7979` or `:6969`; it prints two different
`https://xxxx.trycloudflare.com` URLs.

Send outsiders:

```text
login window URL -> remote_login.html
order window URL -> mobile_order.html
```

Blocked by the whitelist gate:

- `/`
- `/ui.html`
- `/api/accounts`
- rush/order/proxy/distributed control APIs
- any API not required by `remote_login.html` or `mobile_order.html`

No token is required in this mode.

Auto-start env switches:

```text
MT_CLOUDFLARED_AUTOSTART=0          disable all automatic cloudflared public tunnels
MT_CLOUDFLARED_PUBLIC_PAGES=0      disable only these public page tunnels
MT_PUBLIC_LOGIN_PORT=7979          local login whitelist port
MT_PUBLIC_ORDER_PORT=6969          local order whitelist port
MT_CLOUDFLARED_BIN=cloudflared.exe optional explicit cloudflared path
```
