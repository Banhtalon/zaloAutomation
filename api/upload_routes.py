from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import get_container, require_operator
from api.errors import public_error
from core.container import AppContainer
from repositories.upload_repository import UploadClaimError
from services.upload_store import UploadValidationError


router = APIRouter()


class UploadRequest(BaseModel):
    image_data: str
    purpose: Literal["googleform_ocr", "lms_analysis"]


@router.post("/api/uploads", dependencies=[Depends(require_operator)])
async def create_upload(
    req: UploadRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        return await container.upload_store.save(req.image_data, req.purpose)
    except UploadValidationError as exc:
        raise public_error(
            400,
            "UPLOAD_INVALID",
            "Dữ liệu tải lên không hợp lệ.",
        ) from exc
    except UploadClaimError as exc:
        raise public_error(
            409,
            "UPLOAD_CONFLICT",
            "Tệp tải lên không còn sẵn sàng.",
        ) from exc
