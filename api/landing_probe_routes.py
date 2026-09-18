from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse


router = APIRouter()

_ROUTE_PATH = "/api/internal/zalo/landing-probe/one-shot"
_STRUCTURAL_ROUTE_PATH = "/api/internal/zalo/structural-observation/one-shot"


def _retired_payload(*, phase: str, diagnostic_code: str) -> dict[str, object]:
    return {
        "phase": phase,
        "status": "RETIRED",
        "diagnostic_code": diagnostic_code,
        "attempt_consumed": False,
        "gateway_invoked": False,
        "probe": None,
        "observation": None,
    }


@router.post(_ROUTE_PATH)
async def run_landing_probe(request: Request):
    """Retired: diagnostics now run through the independent CLI boundary."""

    del request
    return JSONResponse(
        status_code=410,
        content=_retired_payload(
            phase="LANDING_PROBE_ONE_SHOT_RUNNER",
            diagnostic_code="LANDING_PROBE_ROUTE_RETIRED",
        ),
    )


@router.post(_STRUCTURAL_ROUTE_PATH)
async def run_structural_observation(request: Request):
    """Retired: diagnostics now run through the independent CLI boundary."""

    del request
    return JSONResponse(
        status_code=410,
        content=_retired_payload(
            phase="STRUCTURAL_OBSERVATION_ONE_SHOT_RUNNER",
            diagnostic_code="STRUCTURAL_OBSERVATION_ROUTE_RETIRED",
        ),
    )

