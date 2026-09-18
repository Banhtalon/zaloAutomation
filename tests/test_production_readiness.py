from types import SimpleNamespace
import unittest

from core.production_readiness import (
    LOCAL_PRODUCTION_ACK_VALUE,
    ProductionReadinessError,
    assert_production_ready,
    production_readiness_report,
)
from core.settings import AppSettings
from core.container import AppContainer


def _settings(**overrides):
    values = {
        "google_form_mode": "production",
        "bcnb_dry_run": False,
        "auth_mode": "web",
        "auth_secure_cookie": True,
        "auth_users": (("admin", "Admin", "pbkdf2_sha256$310000$hash"),),
        "auth_allowed_hosts": ("automation.example.com",),
        "auth_allowed_origins": ("https://automation.example.com",),
        "local_production_ack": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ProductionReadinessTests(unittest.TestCase):
    def test_dry_run_does_not_require_public_deployment(self):
        report = production_readiness_report(
            _settings(
                google_form_mode="dry-run",
                bcnb_dry_run=True,
                auth_mode="local",
                auth_secure_cookie=False,
                auth_users=(),
                auth_allowed_hosts=("localhost",),
                auth_allowed_origins=(),
            )
        )
        self.assertFalse(report.required)
        self.assertTrue(report.ready)
        self.assertEqual(report.blockers, ())

    def test_production_requires_all_external_controls(self):
        report = production_readiness_report(
            _settings(
                auth_mode="local",
                auth_secure_cookie=False,
                auth_users=(),
                auth_allowed_hosts=("127.0.0.1",),
                auth_allowed_origins=(),
            ),
            form_url="http://form.example.com",
            bcnb_url="http://bcnb.example.com",
            bcnb_username="",
            bcnb_password="",
            gemini_api_key=None,
        )
        self.assertTrue(report.required)
        self.assertFalse(report.ready)
        self.assertIn("LOCAL_PRODUCTION_ACK_REQUIRED", report.blockers)
        self.assertIn("GOOGLE_FORM_URL_REQUIRED", report.blockers)
        self.assertIn("BCNB_CREDENTIALS_REQUIRED", report.blockers)
        self.assertNotIn("secret", repr(report).lower())

    def test_local_production_opt_in_passes_on_loopback_only(self):
        report = assert_production_ready(
            _settings(
                google_form_mode="dry-run",
                bcnb_dry_run=False,
                auth_mode="local",
                auth_secure_cookie=False,
                auth_users=(),
                auth_allowed_hosts=("127.0.0.1", "localhost", "::1"),
                auth_allowed_origins=(),
                local_production_ack=LOCAL_PRODUCTION_ACK_VALUE,
            ),
            bcnb_url="https://teachingmindx.top/check_nhanxet.php",
            bcnb_username="configured",
            bcnb_password="configured",
            gemini_api_key="configured",
        )
        self.assertTrue(report.required)
        self.assertTrue(report.ready)
        self.assertEqual(report.blockers, ())
        self.assertIn(
            "LOCAL_PRODUCTION_ACK_REQUIRED",
            {item["name"] for item in report.checks},
        )

    def test_local_production_opt_in_rejects_public_host(self):
        report = production_readiness_report(
            _settings(
                google_form_mode="dry-run",
                bcnb_dry_run=False,
                auth_mode="local",
                auth_allowed_hosts=("127.0.0.1", "automation.example.com"),
                local_production_ack=LOCAL_PRODUCTION_ACK_VALUE,
            ),
            bcnb_url="https://teachingmindx.top/check_nhanxet.php",
            bcnb_username="configured",
            bcnb_password="configured",
            gemini_api_key="configured",
        )
        self.assertIn("LOCAL_HOST_ALLOWLIST_LOOPBACK_ONLY", report.blockers)

    def test_private_hosts_and_origins_are_not_production_allowlists(self):
        report = production_readiness_report(
            _settings(
                auth_allowed_hosts=("10.0.0.4",),
                auth_allowed_origins=("https://10.0.0.4",),
            ),
            form_url="https://forms.example.com/viewform",
            bcnb_url="https://teachingmindx.top/check_nhanxet.php",
            bcnb_username="configured",
            bcnb_password="configured",
            gemini_api_key="configured",
        )
        self.assertIn("PUBLIC_HOST_ALLOWLIST_REQUIRED", report.blockers)
        self.assertIn("HTTPS_ORIGIN_ALLOWLIST_REQUIRED", report.blockers)

    def test_valid_production_configuration_passes(self):
        report = assert_production_ready(
            _settings(),
            form_url="https://forms.example.com/viewform",
            bcnb_url="https://teachingmindx.top/check_nhanxet.php",
            bcnb_username="configured",
            bcnb_password="configured",
            gemini_api_key="configured",
        )
        self.assertTrue(report.required)
        self.assertTrue(report.ready)
        self.assertEqual(report.blockers, ())

    def test_container_rejects_production_before_worker_creation(self):
        base = AppSettings.from_config()
        settings = base.with_overrides(
            google_form_mode="production",
            bcnb_dry_run=False,
            auth_mode="local",
            auth_secure_cookie=False,
            local_production_ack="",
        )
        with self.assertRaises(ProductionReadinessError) as context:
            AppContainer(settings)
        self.assertIn("LOCAL_PRODUCTION_ACK_REQUIRED", str(context.exception))
        self.assertNotIn("BCNB_PASSWORD", str(context.exception))


if __name__ == "__main__":
    unittest.main()
