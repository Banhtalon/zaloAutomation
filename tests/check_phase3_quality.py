import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


required_files = {
    "core/container.py",
    "core/settings.py",
    "api/dependencies.py",
    "services/runtime.py",
    "services/execution_mode.py",
    "services/bcnb_safety.py",
    "services/bcnb_actions.py",
    "services/zalo_safety.py",
    "services/zalo_discovery_service.py",
    "services/observability.py",
    "api/health_routes.py",
    "PHASE3_MAINTAINABILITY.md",
    "templates/index.html",
    "templates/partials/sidebar.html",
    "templates/partials/header.html",
    "templates/partials/dashboard.html",
    "templates/partials/googleform.html",
    "templates/partials/groups.html",
    "templates/partials/logs.html",
    "templates/partials/lms_modal.html",
    "templates/partials/image_modal.html",
    "static/css/app.css",
    "static/js/tailwind-config.js",
    "static/js/state.js",
    "static/js/groups.js",
    "static/js/jobs.js",
    "static/js/googleform.js",
    "static/js/lms.js",
    "static/js/modals.js",
    "tests/check_template_js.js",
    "requirements-dev.txt",
    "pyproject.toml",
    "CHECK_QUALITY.ps1",
    "QUALITY.bat",
}
missing = sorted(path for path in required_files if not (ROOT / path).exists())
if missing:
    raise SystemExit(f"missing-phase3-files={missing}")

zalo = read("services/zalo_service.py")
discovery = read("services/zalo_discovery_service.py")
lms = read("services/lms_service.py")
google = read("services/googleform_service.py")
dispatcher = read("services/job_dispatcher.py")
plan = read("services/lms_plan.py")
main = read("main.py")
container_source = read("core/container.py")
health_routes = read("api/health_routes.py")
bcnb_actions = read("services/bcnb_actions.py")
index_template = read("templates/index.html")
partial_names = [
    "sidebar.html",
    "header.html",
    "dashboard.html",
    "googleform.html",
    "groups.html",
    "logs.html",
    "lms_modal.html",
    "image_modal.html",
]
template_source = index_template + "\n" + "\n".join(
    read(f"templates/partials/{name}") for name in partial_names
)
app_css = read("static/css/app.css")
dev_requirements = read("requirements-dev.txt")
tool_config = read("pyproject.toml")
quality_script = read("CHECK_QUALITY.ps1")

invariants = {
    "zalo-live-config-mode": "Config.BCNB_DRY_RUN" not in zalo,
    "google-live-config-mode": "Config.MODE" not in google,
    "dispatcher-persisted-mode": dispatcher.count("mode_from_job(job)") >= 3,
    "lms-digest-binds-mode": '"dry_run": bool(plan.get("dry_run", True))' in plan,
    "zalo-chat-fail-closed": "open_chat_matches(group_name, header_text)" in zalo
    and "Không xác minh được nhóm Zalo đang mở" in zalo,
    "shared-bcnb-week-selection": "select_last_week_checked" in discovery
    and "select_last_week_checked" in lms,
    "service-size-budget": len(zalo.splitlines()) <= 520
    and len(discovery.splitlines()) <= 300,
    "app-factory": "def create_app(" in main and "app = create_app()" in main,
    "single-container-dispatcher": "self.dispatcher = JobDispatcher(" in container_source,
    "metrics-off-event-loop": "asyncio.to_thread" in health_routes,
    "template-facade-budget": len(index_template.splitlines()) <= 120,
    "template-partials-wired": all(
        f'{{% include "partials/{name}" %}}' in index_template
        for name in partial_names
    ),
    "no-inline-template-assets": "<style" not in template_source.lower()
    and re.search(r"<script(?![^>]*\bsrc=)[^>]*>", template_source, re.I) is None,
    "viewport-motion-baseline": "h-screen" not in template_source
    and not re.search(r"duration-(?:[3-9]\d\d|\d{4,})", template_source)
    and "prefers-reduced-motion" in app_css,
    "accessible-icon-controls": 'aria-label="Đóng cửa sổ LMS"' in template_source
    and 'aria-label="Đóng ảnh báo cáo"' in template_source
    and 'aria-label="Làm mới danh sách lớp tạm ẩn"' in template_source
    and 'aria-label="Làm mới lịch sử nộp Form"' in template_source,
    "fixed-safe-area": template_source.count("safe-area-modal") >= 2
    and "safe-area-inset-top" in app_css
    and "viewport-fit=cover" in index_template,
    "atomic-upload-clone": "create_many_claimed" in read("repositories/upload_repository.py")
    and "_copy_verified" in read("services/upload_store.py")
    and "reconcile_orphans" in read("core/container.py"),
    "bcnb-server-readback": "await page.reload" in read("services/bcnb_safety.py")
    and "verify_checkbox_persisted" in bcnb_actions
    and "execute_verified_checkbox_action" in zalo
    and "execute_verified_checkbox_action" in lms,
    "dev-tools-pinned": all(
        dependency in dev_requirements
        for dependency in ("coverage==7.15.0", "mypy==2.2.0", "ruff==0.15.20")
    ),
    "full-typecheck-config": 'files = [' in tool_config
    and "check_untyped_defs = true" in tool_config
    and "explicit_package_bases = true" in tool_config,
    "branch-coverage-gate": "branch = true" in tool_config
    and "fail_under = 70.0" in tool_config,
    "single-quality-entrypoint": all(
        command in quality_script
        for command in (
            '"ruff", "check", "."',
            '"-m", "mypy"',
            '"-m", "coverage", "run"',
            '"tests\\check_phase3_quality.py"',
            '"tests\\check_phase4_quality.py"',
            '"tests\\check_phase5_quality.py"',
            '"-m", "tests.check_app_smoke"',
        )
    ),
}
failed = sorted(name for name, passed in invariants.items() if not passed)
if failed:
    raise SystemExit(f"phase3-invariant-failed={failed}")

production_roots = [
    ROOT / "api",
    ROOT / "classifiers",
    ROOT / "db",
    ROOT / "googleform_autosubmit",
    ROOT / "models",
    ROOT / "repositories",
    ROOT / "services",
]
bare_except = 0
for folder in production_roots:
    for path in folder.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        bare_except += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.type is None
        )
if bare_except:
    raise SystemExit(f"bare-except-budget-exceeded={bare_except}>0")

for path in (ROOT / "api").glob("*_routes.py"):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = getattr(node, "value", None)
        if isinstance(value, ast.Call):
            name = getattr(value.func, "id", "") or getattr(value.func, "attr", "")
            if name.endswith("Repository") or name == "UploadStore":
                raise SystemExit(f"module-global-runtime={path.name}:{name}")

print("phase3-quality-gate-ok")
