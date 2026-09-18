"""Fail-closed checks for enabling external production mutations.

The application can inspect Zalo/BCNB and run Google Form workflows in
dry-run mode without a public deployment.  Any mode that can mutate an
external system must opt in explicitly and satisfy the web-auth, transport,
allowlist and credential controls in this module before a worker is created.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any
from urllib.parse import urlparse


_LOOPBACK_NAMES = {"localhost", "testserver", "testclient"}
LOCAL_PRODUCTION_ACK_VALUE = "I_UNDERSTAND_LOCAL_BROAD_WRITES"


@dataclass(frozen=True, slots=True)
class ProductionReadinessReport:
    """Sanitized, serializable result of the production preflight."""

    required: bool
    ready: bool
    checks: tuple[dict[str, Any], ...]
    blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "ready": self.ready,
            "checks": [dict(item) for item in self.checks],
            "blockers": list(self.blockers),
        }


class ProductionReadinessError(RuntimeError):
    """Raised before the worker is constructed when production is unsafe."""

    def __init__(self, report: ProductionReadinessReport):
        self.report = report
        blockers = ", ".join(report.blockers) or "unknown"
        super().__init__(f"Production preflight failed: {blockers}")


def _is_loopback_host(value: str) -> bool:
    host = str(value or "").strip().lower().strip("[]")
    if not host or host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_public_host(value: str) -> bool:
    """Reject loopback/private/reserved hosts and local-only DNS names."""

    host = str(value or "").strip().lower().strip("[]")
    if not host or _is_loopback_host(host):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and not host.endswith((".local", ".localhost", ".internal"))
    return not (
        address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _public_host_allowlisted(hosts: tuple[str, ...]) -> bool:
    """Require an explicit non-loopback host; wildcard hosts are unsafe."""

    return any(
        item.strip() not in {"", "*"} and _is_public_host(item)
        for item in hosts
    )


def _loopback_host_allowlisted(hosts: tuple[str, ...]) -> bool:
    """Keep the local-production escape hatch unreachable from the LAN."""

    normalized = tuple(item.strip() for item in hosts if item.strip())
    return bool(normalized) and all(
        item not in {"*"} and _is_loopback_host(item)
        for item in normalized
    )


def _public_https_origin(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme.lower() == "https"
        and bool(hostname)
        and _is_public_host(hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _https_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)


def _check(
    checks: list[dict[str, Any]],
    blockers: list[str],
    name: str,
    ok: bool,
    detail: str,
) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        blockers.append(name)


def production_readiness_report(
    settings: Any,
    *,
    form_url: str = "",
    bcnb_url: str = "",
    bcnb_username: str = "",
    bcnb_password: str = "",
    gemini_api_key: str | None = None,
    local_production_ack: str = "",
) -> ProductionReadinessReport:
    """Return a sanitized preflight report without exposing secret values.

    Dry-run configurations intentionally pass without public web-auth or
    external credentials.  The checks below only run when at least one
    external mutating mode is enabled.
    """

    google_form_production = str(
        getattr(settings, "google_form_mode", "dry-run")
    ).lower() == "production"
    bcnb_production = not bool(getattr(settings, "bcnb_dry_run", True))
    required = google_form_production or bcnb_production
    if not required:
        return ProductionReadinessReport(
            required=False,
            ready=True,
            checks=(
                {
                    "name": "external_mutations_disabled",
                    "ok": True,
                    "detail": "Google Form va BCNB dang o dry-run.",
                },
            ),
            blockers=(),
        )

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    auth_mode = str(getattr(settings, "auth_mode", "local")).strip().lower()
    ack_value = str(
        local_production_ack
        or getattr(settings, "local_production_ack", "")
    ).strip()
    if auth_mode == "local":
        # Personal deployments may remain on the loopback-only local auth mode,
        # but only after an explicit high-friction acknowledgement.  This does
        # not narrow /run-scan: the current endpoint is a broad batch operation,
        # so the acknowledgement says so explicitly.
        _check(
            checks,
            blockers,
            "LOCAL_PRODUCTION_ACK_REQUIRED",
            ack_value == LOCAL_PRODUCTION_ACK_VALUE,
            "Local production can ghi nhieu dong; phai dat LOCAL_PRODUCTION_ACK dung chuoi xac nhan.",
        )
        _check(
            checks,
            blockers,
            "LOCAL_HOST_ALLOWLIST_LOOPBACK_ONLY",
            _loopback_host_allowlisted(
                tuple(getattr(settings, "auth_allowed_hosts", ()))
            ),
            "Local production chi duoc bind voi allowlist loopback; khong mo ra LAN.",
        )
    else:
        _check(
            checks,
            blockers,
            "AUTH_MODE_WEB_REQUIRED",
            auth_mode == "web",
            "AUTH_MODE phai la web khi bat ky mutation production nao duoc bat.",
        )
        _check(
            checks,
            blockers,
            "AUTH_SECURE_COOKIE_REQUIRED",
            bool(getattr(settings, "auth_secure_cookie", False)),
            "Cookie phien web phai co Secure tren deployment HTTPS.",
        )
        _check(
            checks,
            blockers,
            "AUTH_USERS_REQUIRED",
            bool(getattr(settings, "auth_users", ())),
            "Phai co it nhat mot user da hash PBKDF2.",
        )
        _check(
            checks,
            blockers,
            "PUBLIC_HOST_ALLOWLIST_REQUIRED",
            _public_host_allowlisted(
                tuple(getattr(settings, "auth_allowed_hosts", ()))
            ),
            "Allowlist phai co hostname public cu the; khong dung wildcard/loopback.",
        )
        _check(
            checks,
            blockers,
            "HTTPS_ORIGIN_ALLOWLIST_REQUIRED",
            any(
                _public_https_origin(item)
                for item in tuple(getattr(settings, "auth_allowed_origins", ()))
            ),
            "Allowlist origin phai co it nhat mot origin HTTPS public.",
        )

    if google_form_production:
        _check(
            checks,
            blockers,
            "GOOGLE_FORM_URL_REQUIRED",
            _https_url(form_url),
            "FORM_URL phai la URL HTTPS hop le khi GOOGLE_FORM_MODE=production.",
        )
    if bcnb_production:
        _check(
            checks,
            blockers,
            "BCNB_URL_HTTPS_REQUIRED",
            _https_url(bcnb_url),
            "BCNB_URL phai la URL HTTPS hop le khi BCNB production.",
        )
        _check(
            checks,
            blockers,
            "BCNB_CREDENTIALS_REQUIRED",
            bool(str(bcnb_username).strip()) and bool(str(bcnb_password).strip()),
            "BCNB username/password phai duoc cau hinh; gia tri khong duoc ghi vao report.",
        )
        _check(
            checks,
            blockers,
            "GEMINI_API_KEY_REQUIRED",
            bool(str(gemini_api_key or "").strip()),
            "GEMINI_API_KEY phai duoc cau hinh cho luong LMS production.",
        )

    return ProductionReadinessReport(
        required=True,
        ready=not blockers,
        checks=tuple(checks),
        blockers=tuple(blockers),
    )


def assert_production_ready(
    settings: Any,
    *,
    form_url: str = "",
    bcnb_url: str = "",
    bcnb_username: str = "",
    bcnb_password: str = "",
    gemini_api_key: str | None = None,
    local_production_ack: str = "",
) -> ProductionReadinessReport:
    """Run the preflight and raise a sanitized error when it is not ready."""

    report = production_readiness_report(
        settings,
        form_url=form_url,
        bcnb_url=bcnb_url,
        bcnb_username=bcnb_username,
        bcnb_password=bcnb_password,
        gemini_api_key=gemini_api_key,
        local_production_ack=local_production_ack,
    )
    if report.required and not report.ready:
        raise ProductionReadinessError(report)
    return report
