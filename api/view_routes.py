from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from api.dependencies import require_viewer
from fastapi.templating import Jinja2Templates
from logger_utils import get_recent_logs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

@router.get("/")
async def index(request: Request):
    # Dữ liệu kết quả sẽ được poll từ API frontend
    asset_version = request.app.state.container.instance_id
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": asset_version},
    )
    response.headers["Cache-Control"] = "no-store"
    return response

@router.get("/api/logs", dependencies=[Depends(require_viewer)])
async def get_logs(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    level: str | None = None,
    job_id: int | None = Query(default=None, ge=1),
):
    records = get_recent_logs(
        after=after,
        limit=limit,
        level=level,
        job_id=job_id,
    )
    logs = [f"[{item['timestamp'][11:19]}] {item['message']}" for item in records]
    return {
        "logs": logs,
        "records": records,
        "cursor": records[-1]["seq"] if records else after,
    }

@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)
