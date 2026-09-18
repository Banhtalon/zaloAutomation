"""Static Phase 9B staging contract gate; no network or external mutation."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "deploy" / "staging"


def main() -> int:
    required = (
        STAGING / "Caddyfile.example",
        STAGING / ".env.staging.example",
        STAGING / "README.md",
        ROOT / "scripts" / "phase9b_browser_smoke.py",
    )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise AssertionError(f"Missing Phase 9B artifacts: {missing}")

    caddy = (STAGING / "Caddyfile.example").read_text(encoding="utf-8")
    env = (STAGING / ".env.staging.example").read_text(encoding="utf-8")
    readme = (STAGING / "README.md").read_text(encoding="utf-8")
    checks = {
        "proxy_loopback": "reverse_proxy 127.0.0.1:8000" in caddy,
        "proxy_hardening": all(
            marker in caddy
            for marker in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy")
        ),
        "web_auth": "AUTH_MODE=web" in env and "AUTH_SECURE_COOKIE=true" in env,
        "public_allowlist": bool(re.search(r"AUTH_ALLOWED_HOSTS=\S+\.\S+", env))
        and "AUTH_ALLOWED_ORIGINS=https://" in env,
        "mutations_disabled": "GOOGLE_FORM_MODE=dry-run" in env and "BCNB_DRY_RUN=true" in env,
        "single_replica_documented": "single replica" in readme.lower(),
        "human_canary_gate": "human" in readme.lower() and "canary" in readme.lower(),
        "no_plaintext_secret": "password=" not in env.lower() and "gemini_api_key=" not in env.lower(),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise AssertionError(f"Phase 9B staging contract failed: {failures}")
    print(
        "PHASE 9B STAGING CONTRACT PASS: "
        + ", ".join(name for name, passed in checks.items() if passed)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
