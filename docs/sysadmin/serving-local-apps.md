# Sysadmin — Serving Local Apps from SPYBALLOON

**Problem:** Apps running on SPYBALLOON (native Ubuntu) need to be accessible from FIRMAMENT's browser (Windows, connected via Tailscale).

**This has been a recurring issue.** Documenting the solution here so we stop re-debugging it.

## Network Setup

| Machine | LAN IP | Tailscale IP | Role |
|---------|--------|-------------|------|
| SPYBALLOON | 192.168.0.197 | 100.122.177.91 | Server (Ubuntu, runs apps) |
| FIRMAMENT | 192.168.0.x | 100.75.112.14 | Client (Windows, has browser/display) |

## How to Access

From FIRMAMENT's browser: `http://100.122.177.91:<PORT>`

## Requirements (ALL must be true)

1. **App must be listening on 0.0.0.0** (not 127.0.0.1)
   - `HTTPServer(("0.0.0.0", port), Handler)` — correct
   - `HTTPServer(("127.0.0.1", port), Handler)` — WRONG, only localhost

2. **Port must be open in UFW**
   - Check: `sudo ufw status | grep <PORT>`
   - Add: `sudo ufw allow <PORT>/tcp comment "App Name"`
   - The `tailscale0` allow-all rule SHOULD cover this but doesn't always work

3. **App must still be running when you load the page**
   - Run in a tmux window, NOT with `nohup &` (process exits when stdin closes)
   - Or use `--keep-dashboard` style flag to hold after completion

## Currently Open Ports

| Port | App | Added |
|------|-----|-------|
| 22 | SSH | default |
| 3100 | Constellation Dashboard | 2026-03-20 |
| 3457 | CCR | 2026-03-20 |
| 9876 | QA Agent Dashboard | 2026-03-22 |
| 11434 | Ollama | 2026-03-20 |
| 50052 | llama-rpc (shared inference) | 2026-03-21 |

## Common Failures

### "Page doesn't load"
1. Is the app running? `ss -tlnp | grep <PORT>`
2. Is UFW allowing it? `sudo ufw status | grep <PORT>`
3. Is it bound to 0.0.0.0? `ss -tlnp | grep <PORT>` shows `0.0.0.0:PORT` not `127.0.0.1:PORT`

### "App finishes before I can see it"
Run in a tmux window so it stays alive:
```bash
tmux new-window -n QA-DASH
tmux send-keys -t QA-DASH "cd ~/projects/qa-agent && PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright xvfb-run --auto-servernum python3 qa_agent.py https://site.com --dashboard-port 9876" Enter
```
The dashboard keeps running after the test completes (Ctrl+C to stop).

### "Works from SPYBALLOON, not from FIRMAMENT"
The Tailscale `allow all on tailscale0` UFW rule is unreliable. Always add an explicit port rule:
```bash
sudo ufw allow 9876/tcp comment "QA Agent Dashboard"
```
