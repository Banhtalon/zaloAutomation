from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


required_files = {
    "services/external_gateways.py",
    "services/zalo_discovery_service.py",
    "tests/test_external_gateways.py",
    "tests/test_lms_service.py",
    "tests/test_googleform_submit_service.py",
    "tests/test_basic_repositories.py",
    "tests/test_evidence_store.py",
    "PHASE4_EXTERNAL_BOUNDARIES.md",
}
missing = sorted(path for path in required_files if not (ROOT / path).exists())
if missing:
    raise SystemExit(f"missing-phase4-files={missing}")

runtime = read("services/runtime.py")
external = read("services/external_gateways.py")
zalo = read("services/zalo_service.py")
discovery = read("services/zalo_discovery_service.py")
lms = read("services/lms_service.py")
google = read("services/googleform_service.py")
submitter = read("googleform_autosubmit/form_submitter.py")
tool_config = read("pyproject.toml")
quality_script = read("CHECK_QUALITY.ps1")

orchestration = zalo + discovery + lms + google
invariants = {
    "runtime-owns-external-gateways": "external: ExternalGateways" in runtime
    and "default_factory=ExternalGateways.production" in runtime,
    "external-sdk-boundary": "async_playwright" not in orchestration
    and "genai.Client" not in orchestration
    and "PIL.Image" not in orchestration,
    "managed-browser-lifecycle": "async def persistent_page" in external
    and "async def form_batch" in external
    and external.count("await context.close()") == 1
    and external.count("await browser.close()") == 1,
    "zalo-gateway-wiring": zalo.count("external.browser.persistent_page") == 1
    and discovery.count("selected.browser.persistent_page") == 1,
    "lms-gateway-wiring": lms.count("external.browser.persistent_page") == 2
    and "external.lms_ocr" in lms,
    "google-gateway-wiring": "external.browser.form_batch" in google
    and "runtime.external.student_ocr_factory()" in google,
    "ambiguous-submit-is-unknown": "SubmissionOutcomeUnknownError" in submitter
    and '"UNKNOWN"' in google,
    "selector-data-not-interpolation": "filter(has_text=question)" in submitter
    and "radio_locator = question_container.get_by_role(" in submitter
    and '"radio",' in submitter
    and "name=value" in submitter
    and "exact=True" in submitter
    and 'get_attribute("aria-checked")' in submitter
    and "filter(has_text=value)" not in submitter
    and "contains(., '{question}')" not in submitter
    and "data-value='{value}'" not in submitter,
    "google-submit-needs-dom-confirmation": "wait_for_url" not in submitter
    and "Câu trả lời của bạn đã được ghi lại" in submitter,
    "safe-dialog-policy": 'dialog.type in {"alert", "confirm"}' in lms
    and "await dialog.dismiss()" in lms,
    "coverage-gate-raised": "branch = true" in tool_config
    and "fail_under = 70.0" in tool_config,
    "phase4-gate-wired": '"tests\\check_phase4_quality.py"' in quality_script,
}

failed = sorted(name for name, passed in invariants.items() if not passed)
if failed:
    raise SystemExit(f"phase4-invariant-failed={failed}")

print("phase4-quality-gate-ok")
