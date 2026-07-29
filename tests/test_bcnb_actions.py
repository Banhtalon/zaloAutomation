import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services.bcnb_actions import execute_verified_checkbox_action


class BcnbActionTests(unittest.IsolatedAsyncioTestCase):
    def make_dependencies(self):
        checkbox = AsyncMock()
        checkbox.is_checked.return_value = False
        confirm = AsyncMock()
        confirm.count.return_value = 0
        page = MagicMock()
        page.locator.return_value.first = confirm
        audit_repo = AsyncMock()
        audit_repo.record_intent.return_value = (
            {"id": 7, "outcome": "INTENT_RECORDED"},
            True,
        )
        job_repo = AsyncMock()
        return page, checkbox, audit_repo, job_repo

    async def execute(self, persisted):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        readback = AsyncMock()
        if isinstance(persisted, BaseException):
            readback.side_effect = persisted
        else:
            readback.return_value = persisted
        with (
            patch("services.bcnb_actions.verify_checkbox_persisted", readback),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            result = await execute_verified_checkbox_action(
                page=page,
                checkbox=checkbox,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=3,
                action_key="test-action",
                action_type="BCNB_REPORT_TICK",
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
                execution_mode="production",
                reason={"test": True},
            )
        return result, audit_repo

    async def test_verified_is_recorded_only_after_server_readback(self):
        result, audit_repo = await self.execute(True)
        self.assertEqual(result, "VERIFIED")
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"], "VERIFIED"
        )

    async def test_unchecked_server_readback_is_failed(self):
        with self.assertRaises(RuntimeError):
            await self.execute(False)

    async def test_unreadable_server_state_is_unknown_not_verified(self):
        with self.assertRaises(RuntimeError):
            await self.execute(RuntimeError("reload failed"))
        # Exercise again to inspect the terminal audit call without hiding the exception.
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        with (
            patch(
                "services.bcnb_actions.verify_checkbox_persisted",
                AsyncMock(side_effect=RuntimeError("reload failed")),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(RuntimeError):
                await execute_verified_checkbox_action(
                    page=page,
                    checkbox=checkbox,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=3,
                    action_key="test-action",
                    action_type="BCNB_REPORT_TICK",
                    class_code="VT-JSB14",
                    column_kind="report",
                    week=27,
                    year=2026,
                    lesson="14",
                    execution_mode="production",
                    reason={"test": True},
                )
        outcomes = [call.kwargs["outcome"] for call in audit_repo.complete_action.await_args_list]
        self.assertEqual(outcomes, ["UNKNOWN"])

    async def test_evidence_path_without_sha256_is_rejected_before_intent(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        with self.assertRaises(ValueError):
            await execute_verified_checkbox_action(
                page=page,
                checkbox=checkbox,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=3,
                action_key="test-action",
                action_type="BCNB_REPORT_TICK",
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
                execution_mode="dry-run",
                reason={"test": True},
                evidence_path="/static/proof.png",
            )
        audit_repo.record_intent.assert_not_awaited()

    async def test_sha256_is_written_directly_to_bcnb_audit(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        await execute_verified_checkbox_action(
            page=page,
            checkbox=checkbox,
            audit_repo=audit_repo,
            job_repo=job_repo,
            job_id=3,
            action_key="test-action",
            action_type="BCNB_REPORT_TICK",
            class_code="VT-JSB14",
            column_kind="report",
            week=27,
            year=2026,
            lesson="14",
            execution_mode="dry-run",
            reason={"test": True},
            evidence_path="/static/proof.png",
            evidence_sha256="B" * 64,
        )
        self.assertEqual(
            audit_repo.record_intent.await_args.kwargs["evidence_sha256"],
            "B" * 64,
        )

    async def test_already_checked_production_state_is_audited_without_clicking(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        checkbox.is_checked.return_value = True
        result = await execute_verified_checkbox_action(
            page=page,
            checkbox=checkbox,
            audit_repo=audit_repo,
            job_repo=job_repo,
            job_id=3,
            action_key="lesson-14-observation",
            action_type="BCNB_REPORT_TICK",
            class_code="VT-JSB14",
            column_kind="report",
            week=27,
            year=2026,
            lesson="14",
            execution_mode="production",
            reason={"lesson_14_exemption": True},
        )
        self.assertEqual(result, "VERIFIED")
        checkbox.check.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs,
            {"outcome": "VERIFIED", "after_state": {"checked": True}},
        )


if __name__ == "__main__":
    unittest.main()
