from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


required_files = {
    "api/errors.py",
    "services/zalo_discovery_service.py",
    "services/zalo_discovery_matching.py",
    "tests/test_api_errors.py",
    "tests/test_api_routes_unit.py",
    "tests/test_job_dispatcher_unit.py",
    "tests/test_zalo_discovery_service.py",
    "PHASE5_SERVICE_HARDENING.md",
}
missing = sorted(path for path in required_files if not (ROOT / path).exists())
if missing:
    raise SystemExit(f"missing-phase5-files={missing}")

zalo = read("services/zalo_service.py")
discovery = read("services/zalo_discovery_service.py")
discovery_matching = read("services/zalo_discovery_matching.py")
dispatcher = read("services/job_dispatcher.py")
job_repository = read("repositories/job_repository.py")
api_errors = read("api/errors.py")
api_routes = "\n".join(
    path.read_text(encoding="utf-8-sig")
    for path in (ROOT / "api").glob("*_routes.py")
)
tool_config = read("pyproject.toml")
quality_script = read("CHECK_QUALITY.ps1")

invariants = {
    "discovery-extracted": "async def _run_auto_discovery" in discovery
    and "async def _run_auto_discovery" not in zalo
    and len(zalo.splitlines()) <= 520
    and len(discovery.splitlines()) <= 300,
    "discovery-compatibility-export": (
        "run_auto_discovery as run_auto_discovery" in zalo
        and "from services.zalo_discovery_service import run_auto_discovery"
        in dispatcher
    ),
    "discovery-route-contract": '"ZALO_DISCOVERY"' in read("api/zalo_routes.py")
    and '{"version": 2}' in read("api/zalo_routes.py"),
    "discovery-gateway-owned": "selected.browser.persistent_page()" in discovery
    and "async_playwright" not in discovery,
    "discovery-short-code-fails-closed": "find_bcnb_match" in discovery
    and "len(alias_codes.get(alias, set())) == 1" in discovery_matching
    and "class_code_matches_group" in discovery_matching,
    "stable-api-error-contract": '"code": code' in api_errors
    and '"message": message' in api_errors
    and "error_type=type(exc).__name__" in api_errors,
    "api-errors-do-not-leak-exceptions": "detail=str(exc)" not in api_routes
    and '"error": str(exc)' not in api_routes,
    "typed-job-not-found": "class JobNotFoundError(JobStateError)" in job_repository
    and "except JobNotFoundError" in read("api/job_routes.py"),
    "dispatcher-hides-internal-errors": "str(exc)" not in dispatcher
    and "Worker dừng an toàn do lỗi nội bộ." in dispatcher,
    "coverage-gate-raised": "branch = true" in tool_config
    and "fail_under = 70.0" in tool_config,
    "phase5-gate-wired": '"tests\\check_phase5_quality.py"' in quality_script,
}

failed = sorted(name for name, passed in invariants.items() if not passed)
if failed:
    raise SystemExit(f"phase5-invariant-failed={failed}")

print("phase5-quality-gate-ok")
