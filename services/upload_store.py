import asyncio
import base64
import binascii
import hashlib
import io
import os
import re
import shutil
import uuid
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

import PIL.Image

from googleform_autosubmit.config import Config
from core.time_utils import now_utc, utc_iso
from logger_utils import log_message
from repositories.upload_repository import UploadClaimError, UploadRepository


ALLOWED_PURPOSES = {"googleform_ocr", "lms_analysis"}
UPLOAD_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def _copy_verified(
    source: Path,
    pending: Path,
    expected_size: int,
    expected_sha256: str,
) -> None:
    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as source_file, pending.open("xb") as target_file:
            while chunk := source_file.read(1024 * 1024):
                target_file.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            target_file.flush()
            os.fsync(target_file.fileno())
    except BaseException:
        with suppress(OSError):
            pending.unlink(missing_ok=True)
        raise
    if copied != expected_size or digest.hexdigest() != expected_sha256:
        with suppress(OSError):
            pending.unlink(missing_ok=True)
        raise UploadClaimError("Nội dung upload đã thay đổi sau khi được lưu.")


def _reconcile_upload_files(root: Path, live_paths: set[str]) -> dict:
    deleted = 0
    errors = []
    for namespace in ("staging", "jobs"):
        folder = root / namespace
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in live_paths:
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                errors.append(f"{relative}: {exc}")
        directories = sorted(
            (path for path in folder.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            with suppress(OSError):
                directory.rmdir()

    missing = sorted(
        relative for relative in live_paths if not (root / relative).is_file()
    )
    return {
        "deleted_orphans": deleted,
        "missing_live": len(missing),
        "missing_paths": missing,
        "errors": errors,
    }


class UploadValidationError(ValueError):
    pass


class UploadStore:
    def __init__(self, root: str | Path | None = None, repository=None, lock=None):
        self.root = Path(root or Config.UPLOAD_ROOT).resolve()
        self.repository = repository or UploadRepository()
        self._lock = lock or asyncio.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise UploadValidationError("Đường dẫn upload không hợp lệ.")
        candidate = (self.root / relative_path).resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise UploadValidationError("Đường dẫn upload vượt ngoài vùng lưu trữ.")
        return candidate

    @staticmethod
    def validate_upload_id(upload_id: str) -> str:
        if not isinstance(upload_id, str) or not UPLOAD_ID_PATTERN.fullmatch(upload_id):
            raise UploadValidationError("Mã upload không hợp lệ.")
        return upload_id

    @staticmethod
    def decode_image(image_data: str) -> tuple[bytes, str, str]:
        if not isinstance(image_data, str):
            raise UploadValidationError("Dữ liệu ảnh không hợp lệ.")
        encoded = image_data.split(",", 1)[1] if "," in image_data else image_data
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UploadValidationError("Dữ liệu ảnh base64 không hợp lệ.") from exc
        if not raw:
            raise UploadValidationError("Ảnh rỗng.")
        if len(raw) > Config.MAX_UPLOAD_BYTES:
            raise UploadValidationError("Ảnh vượt quá giới hạn dung lượng.")

        try:
            with PIL.Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                image_format = (image.format or "").upper()
                if width <= 0 or height <= 0 or width * height > Config.MAX_IMAGE_PIXELS:
                    raise UploadValidationError("Kích thước điểm ảnh vượt quá giới hạn an toàn.")
                image.verify()
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError("Nội dung tải lên không phải ảnh hợp lệ.") from exc

        formats = {
            "PNG": (".png", "image/png"),
            "JPEG": (".jpg", "image/jpeg"),
            "WEBP": (".webp", "image/webp"),
        }
        if image_format not in formats:
            raise UploadValidationError("Định dạng ảnh không được hỗ trợ.")
        extension, mime_type = formats[image_format]
        return raw, extension, mime_type

    async def save(self, image_data: str, purpose: str) -> dict:
        if purpose not in ALLOWED_PURPOSES:
            raise UploadValidationError("Mục đích upload không hợp lệ.")
        raw, extension, mime_type = self.decode_image(image_data)
        upload_id = uuid.uuid4().hex
        relative = Path("staging") / upload_id / f"input{extension}"
        path = self._resolve(relative.as_posix())
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            await self.repository.create(
                upload_id=upload_id,
                purpose=purpose,
                stored_path=relative.as_posix(),
                mime_type=mime_type,
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            raise
        return {
            "upload_id": upload_id,
            "purpose": purpose,
            "size_bytes": len(raw),
            "mime_type": mime_type,
        }

    async def claim(self, upload_ids: list[str], purpose: str, job_id: int) -> list[str]:
        if purpose not in ALLOWED_PURPOSES:
            raise UploadValidationError("Mục đích upload không hợp lệ.")
        if not upload_ids or len(upload_ids) > 20:
            raise UploadValidationError("Cần từ 1 đến 20 ảnh cho mỗi job.")
        clean_ids = [self.validate_upload_id(value) for value in upload_ids]
        if len(set(clean_ids)) != len(clean_ids):
            raise UploadValidationError("Danh sách upload bị trùng.")

        async with self._lock:
            records = await self.repository.get_many(clean_ids)
            if len(records) != len(clean_ids):
                raise UploadClaimError("Một hoặc nhiều upload không tồn tại.")
            copies: dict[str, tuple[Path, Path]] = {}
            new_paths: dict[str, str] = {}
            try:
                for record in records:
                    if record["purpose"] != purpose:
                        raise UploadClaimError("Upload không đúng mục đích sử dụng.")
                    if record["state"] != "STAGED" or record["job_id"] is not None:
                        raise UploadClaimError("Upload đã thuộc về một job khác.")
                    source = self._resolve(record["stored_path"])
                    if not source.is_file():
                        raise UploadClaimError("File upload không còn tồn tại.")
                    extension = source.suffix.lower()
                    relative = Path("jobs") / str(job_id) / "inputs" / f"{record['upload_id']}{extension}"
                    destination = self._resolve(relative.as_posix())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    copies[record["upload_id"]] = (source, destination)
                    new_paths[record["upload_id"]] = relative.as_posix()

                await self.repository.claim(
                    upload_ids=clean_ids,
                    purpose=purpose,
                    job_id=job_id,
                    new_paths=new_paths,
                )
            except Exception:
                for _source, destination in copies.values():
                    destination.unlink(missing_ok=True)
                raise

            for source, _destination in copies.values():
                source.unlink(missing_ok=True)
                try:
                    source.parent.rmdir()
                except OSError:
                    pass
            return clean_ids

    async def paths_for_job(self, job_id: int, purpose: str) -> list[str]:
        records = await self.repository.list_for_job(job_id, purpose)
        paths = []
        for record in records:
            path = self._resolve(record["stored_path"])
            if not path.is_file():
                raise UploadClaimError("Input của job không còn tồn tại.")
            paths.append(str(path))
        if not paths:
            raise UploadClaimError("Job không có input ảnh hợp lệ.")
        return paths

    async def clone_job_uploads(self, source_job_id: int, new_job_id: int) -> list[str]:
        async with self._lock:
            records = await self.repository.list_for_job(source_job_id)
            plans: list[dict] = []
            pending_paths: list[Path] = []
            final_paths: list[Path] = []
            try:
                for record in records:
                    source = self._resolve(record["stored_path"])
                    if not source.is_file():
                        raise UploadClaimError("Input cũ đã hết thời gian lưu; không thể retry.")
                    new_id = uuid.uuid4().hex
                    relative = Path("jobs") / str(new_job_id) / "inputs" / f"{new_id}{source.suffix.lower()}"
                    destination = self._resolve(relative.as_posix())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    pending = destination.with_name(
                        f"{destination.name}.pending-{uuid.uuid4().hex}"
                    )
                    plans.append(
                        {
                            "upload_id": new_id,
                            "purpose": record["purpose"],
                            "stored_path": relative.as_posix(),
                            "mime_type": record["mime_type"],
                            "size_bytes": record["size_bytes"],
                            "sha256": record["sha256"],
                            "job_id": new_job_id,
                            "source": source,
                            "pending": pending,
                            "destination": destination,
                        }
                    )

                for plan in plans:
                    await asyncio.to_thread(
                        _copy_verified,
                        plan["source"],
                        plan["pending"],
                        plan["size_bytes"],
                        plan["sha256"],
                    )
                    pending_paths.append(plan["pending"])

                for plan in plans:
                    os.replace(plan["pending"], plan["destination"])
                    pending_paths.remove(plan["pending"])
                    final_paths.append(plan["destination"])

                await self.repository.create_many_claimed(
                    [
                        {
                            key: plan[key]
                            for key in (
                                "upload_id",
                                "purpose",
                                "stored_path",
                                "mime_type",
                                "size_bytes",
                                "sha256",
                                "job_id",
                            )
                        }
                        for plan in plans
                    ]
                )
            except BaseException:
                for path in pending_paths + final_paths:
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
                raise
            return [plan["upload_id"] for plan in plans]

    async def cleanup_expired(self, retention_hours: int | None = None) -> int:
        hours = retention_hours if retention_hours is not None else Config.UPLOAD_RETENTION_HOURS
        cutoff = now_utc() - timedelta(hours=max(1, hours))
        records = await self.repository.list_expired(utc_iso(cutoff))
        deleted = 0
        async with self._lock:
            for record in records:
                try:
                    path = self._resolve(record["stored_path"])
                    path.unlink(missing_ok=True)
                    await self.repository.mark_deleted(record["upload_id"])
                    deleted += 1
                except (OSError, UploadValidationError):
                    continue
        return deleted

    async def reconcile_orphans(self) -> dict:
        """Remove crash leftovers without hiding database rows whose files vanished."""
        async with self._lock:
            live_paths = set(await self.repository.list_live_paths())
            result = await asyncio.to_thread(
                _reconcile_upload_files,
                self.root,
                live_paths,
            )
        if result["deleted_orphans"]:
            log_message(
                f"Đã dọn {result['deleted_orphans']} file upload mồ côi.",
                event="upload.reconcile.deleted",
            )
        if result["missing_live"]:
            log_message(
                f"Phát hiện {result['missing_live']} upload còn DB nhưng thiếu file; "
                "worker sẽ dừng fail-closed khi sử dụng.",
                level="ERROR",
                event="upload.reconcile.missing",
            )
        for error in result["errors"]:
            log_message(
                f"Không dọn được upload mồ côi: {error}",
                level="WARNING",
                event="upload.reconcile.error",
            )
        return result
