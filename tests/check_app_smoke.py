import os
import tempfile

from core.settings import AppSettings
from main import create_app


with tempfile.TemporaryDirectory() as temp_dir:
    settings = AppSettings.from_config().with_overrides(
        database_path=os.path.join(temp_dir, "smoke.db"),
        upload_root=os.path.join(temp_dir, "uploads"),
        log_dir=os.path.join(temp_dir, "logs"),
        min_free_disk_bytes=1,
        bcnb_dry_run=True,
        google_form_mode="dry-run",
    )

    from fastapi.testclient import TestClient
    app = create_app(settings)

    with TestClient(app) as client:
        dashboard = client.get("/")
        if dashboard.status_code != 200:
            raise SystemExit("dashboard-smoke-failed")
        dashboard_html = dashboard.text
        for dom_id in (
            "results-table",
            "zalo-preflight",
            "zalo-preflight-status",
            "zalo-preflight-checks",
            "zalo-dashboard-plan-brief",
            "tab-specs",
            "specs-evidence-table-body",
            "zalo-review-queue",
            "zalo-review-queue-items",
            "paste-area",
            "groups-table-body",
            "full-log-terminal",
            "lmsModal",
            "image-modal",
        ):
            if f'id="{dom_id}"' not in dashboard_html:
                raise SystemExit(f"dashboard-dom-contract-missing={dom_id}")
        for asset in (
            "/static/css/app.css",
            "/static/js/state.js",
            "/static/js/jobs.js",
            "/static/js/googleform.js",
            "/static/js/lms.js",
        ):
            if client.get(asset).status_code != 200:
                raise SystemExit(f"dashboard-asset-missing={asset}")
        if client.get("/api/jobs/latest").status_code != 200:
            raise SystemExit("jobs-api-smoke-failed")
        if client.get("/api/health/live").status_code != 200:
            raise SystemExit("liveness-smoke-failed")
        ready = client.get("/api/health/ready")
        if ready.status_code != 200 or not ready.json().get("ready"):
            raise SystemExit("readiness-smoke-failed")
        preflight = client.get("/api/zalo-scan-preflight")
        if preflight.status_code != 200 or preflight.json().get("status") not in {"GREEN", "YELLOW", "RED"}:
            raise SystemExit("zalo-preflight-smoke-failed")
        metrics = client.get("/api/metrics/jobs")
        if metrics.status_code != 200 or "jobs" not in metrics.json():
            raise SystemExit("metrics-smoke-failed")
        if client.post(
            "/auto-discover", headers={"Origin": "https://untrusted.example"}
        ).status_code != 403:
            raise SystemExit("cross-site-write-protection-failed")
        paths = client.get("/openapi.json").json().get("paths", {})
        required = {
            "/": {"get"},
            "/auto-discover": {"post"},
            "/run-scan": {"post"},
            "/api/zalo-scan-preflight": {"get"},
            "/api/groups": {"get"},
            "/api/logs": {"get"},
            "/api/uploads": {"post"},
            "/api/googleform/run-ocr": {"post"},
            "/api/googleform/mappings": {"get", "post"},
            "/api/googleform/submit": {"post"},
            "/api/lms/analyze": {"post"},
            "/api/lms/confirm": {"post"},
            "/api/jobs/latest": {"get"},
            "/api/jobs/{job_id}": {"get"},
            "/api/jobs/{job_id}/cancel": {"post"},
            "/api/jobs/{job_id}/retry": {"post"},
            "/api/jobs/{job_id}/events": {"get"},
            "/api/jobs/{job_id}/audit": {"get"},
            "/api/health/live": {"get"},
            "/api/health/ready": {"get"},
            "/api/metrics/jobs": {"get"},
            "/api/auth/login": {"post"},
            "/api/auth/me": {"get"},
            "/api/auth/logout": {"post"},
            "/api/evidence/{job_id}/{filename}": {"get"},
        }
        missing = set(required) - set(paths)
        if missing:
            raise SystemExit(f"missing-phase3-routes={sorted(missing)}")
        wrong_methods = {
            path: sorted(methods - set(paths[path]))
            for path, methods in required.items()
            if methods - set(paths[path])
        }
        if wrong_methods:
            raise SystemExit(f"missing-phase3-methods={wrong_methods}")

print("phase3-app-smoke-ok")
