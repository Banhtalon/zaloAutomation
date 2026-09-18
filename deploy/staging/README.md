# Staging riêng tư — tùy chọn

This directory is a deployment contract, not an automatic deployer. It keeps
the application behind a TLS reverse proxy and leaves all external mutations
disabled until a separate human canary approval.

## Host requirements

- One host/replica only (single replica) while web sessions and the Zalo
  profile are local.
- A real DNS hostname with a valid TLS certificate. Do not expose Uvicorn on
  the public interface.
- A secret-managed environment file based on `.env.staging.example`; never put
  the PBKDF2 hash, BCNB password or Gemini key in Git or logs.
- A verified SQLite backup before each staging rollout.

## Start the app behind Caddy

```powershell
# Load the secret-managed values in the service environment first.
& .\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# In another process, after replacing staging.example.com in Caddyfile:
caddy run --config deploy/staging/Caddyfile.example --adapter caddyfile
```

Verify through the proxy, not the Uvicorn port:

```powershell
Invoke-WebRequest https://staging.example.com/api/health/live
Invoke-WebRequest https://staging.example.com/api/health/ready
```

## Smoke and gate commands

The local smoke starts an isolated dry-run app, authenticates with an ephemeral
test user, checks the desktop/mobile UI and writes screenshots plus SHA-256
metadata under `runtime/release-evidence/phase9b-browser-smoke/`.

```powershell
& .\venv\Scripts\python.exe scripts\phase9b_browser_smoke.py
& .\venv\Scripts\python.exe tests\check_phase9b_quality.py
```

For a real staging URL, provide credentials through the process environment
only and keep the run read-only:

```powershell
$env:PHASE9B_SMOKE_USERNAME = 'staging-reviewer'
$env:PHASE9B_SMOKE_PASSWORD = '<read from secret manager>'
& .\venv\Scripts\python.exe scripts\phase9b_browser_smoke.py `
  --base-url https://staging.example.com --external
```

External smoke không bấm discovery, scan, LMS, BCNB hoặc Google Form. Nó không
phải production canary và không thay thế gate phê duyệt/read-back trong
`../../PROJECT_COMPLETION_PLAN.md`.
