import unittest
from unittest.mock import AsyncMock, patch

from services.zalo_scan_evidence import (
    attach_zalo_sync_result,
    ensure_zalo_history_synced,
)


class FakeSyncAction:
    def __init__(self, *, click_error: Exception | None = None):
        self.click_error = click_error
        self.click_calls = 0

    async def click(self, **_kwargs):
        self.click_calls += 1
        if self.click_error is not None:
            raise self.click_error


class FakeSyncPage:
    def __init__(self):
        self.reload = AsyncMock()
        self.wait_for_selector = AsyncMock()


class ZaloHistorySyncTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_decision_is_attached_to_class_evidence(self):
        result = {"evidence": {"images": {}}}
        sync_result = {
            "decision": "SYNC_VERIFIED",
            "verified": True,
            "attempted": True,
        }

        attached = attach_zalo_sync_result(result, sync_result)

        self.assertIs(attached, result)
        self.assertEqual(
            attached["evidence"]["zalo_sync"]["decision"],
            "SYNC_VERIFIED",
        )
        self.assertIsNot(attached["evidence"]["zalo_sync"], sync_result)

    def test_banner_returning_during_class_scan_overrides_session_success(self):
        result = {
            "evidence": {
                "classifier_result": {"decision": "ZALO_SYNC_INCOMPLETE"},
            }
        }
        sync_result = {
            "decision": "SYNC_VERIFIED",
            "verified": True,
            "reload_verified": True,
        }

        attached = attach_zalo_sync_result(result, sync_result)

        self.assertFalse(attached["evidence"]["zalo_sync"]["verified"])
        self.assertEqual(
            attached["evidence"]["zalo_sync"]["decision"],
            "SYNC_LOST_DURING_SCAN",
        )

    async def test_absent_banner_is_already_verified_without_reload(self):
        page = FakeSyncPage()
        ensure_not_cancelled = AsyncMock()

        with patch(
            "services.zalo_scan_evidence._zalo_sync_is_incomplete",
            AsyncMock(return_value=False),
        ):
            result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=ensure_not_cancelled,
                poll_interval_seconds=0,
            )

        self.assertTrue(result["verified"])
        self.assertFalse(result["attempted"])
        self.assertEqual(result["decision"], "SYNC_NOT_REQUIRED")
        page.reload.assert_not_awaited()

    async def test_click_wait_reload_and_stable_absence_are_verified(self):
        page = FakeSyncPage()
        action = FakeSyncAction()
        ensure_not_cancelled = AsyncMock()

        with (
            patch(
                "services.zalo_scan_evidence._zalo_sync_is_incomplete",
                AsyncMock(side_effect=[True, False, False, False, False]),
            ),
            patch(
                "services.zalo_scan_evidence._visible_zalo_sync_action",
                AsyncMock(return_value=action),
            ),
        ):
            result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=ensure_not_cancelled,
                wait_timeout_seconds=1,
                poll_interval_seconds=0,
                verify_observations=3,
            )

        self.assertTrue(result["verified"])
        self.assertTrue(result["attempted"])
        self.assertTrue(result["reload_verified"])
        self.assertEqual(result["decision"], "SYNC_VERIFIED")
        self.assertEqual(action.click_calls, 1)
        page.reload.assert_awaited_once()
        page.wait_for_selector.assert_awaited_once()

    async def test_banner_reappearing_after_reload_fails_closed(self):
        page = FakeSyncPage()
        action = FakeSyncAction()

        with (
            patch(
                "services.zalo_scan_evidence._zalo_sync_is_incomplete",
                AsyncMock(side_effect=[True, False, True]),
            ),
            patch(
                "services.zalo_scan_evidence._visible_zalo_sync_action",
                AsyncMock(return_value=action),
            ),
        ):
            result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=AsyncMock(),
                wait_timeout_seconds=1,
                poll_interval_seconds=0,
                verify_observations=3,
            )

        self.assertFalse(result["verified"])
        self.assertEqual(result["decision"], "SYNC_REAPPEARED")
        page.reload.assert_awaited_once()

    async def test_banner_timeout_fails_closed_without_reload(self):
        page = FakeSyncPage()
        action = FakeSyncAction()

        with (
            patch(
                "services.zalo_scan_evidence._zalo_sync_is_incomplete",
                AsyncMock(return_value=True),
            ),
            patch(
                "services.zalo_scan_evidence._visible_zalo_sync_action",
                AsyncMock(return_value=action),
            ),
        ):
            result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=AsyncMock(),
                wait_timeout_seconds=0,
                poll_interval_seconds=0,
            )

        self.assertFalse(result["verified"])
        self.assertEqual(result["decision"], "SYNC_TIMEOUT")
        page.reload.assert_not_awaited()

    async def test_click_failure_is_explicit_and_fails_closed(self):
        page = FakeSyncPage()
        action = FakeSyncAction(click_error=RuntimeError("selector changed"))

        with (
            patch(
                "services.zalo_scan_evidence._zalo_sync_is_incomplete",
                AsyncMock(return_value=True),
            ),
            patch(
                "services.zalo_scan_evidence._visible_zalo_sync_action",
                AsyncMock(return_value=action),
            ),
        ):
            result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=AsyncMock(),
                poll_interval_seconds=0,
            )

        self.assertFalse(result["verified"])
        self.assertEqual(result["decision"], "SYNC_CLICK_FAILED")
        self.assertIn("selector changed", result["reason"])


if __name__ == "__main__":
    unittest.main()
