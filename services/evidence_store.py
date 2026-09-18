from datetime import datetime, timedelta
import os
from pathlib import Path

from core.time_utils import UTC_TIMEZONE, now_utc
from logger_utils import log_message


def cleanup_old_screenshots(
    days: int = 7,
    root: str = "static/screenshots",
    *,
    hard_delete: bool = False,
) -> int:
    """Count legacy files past retention without silently deleting them.

    Normal application cleanup is performed by ``EvidenceRepository`` and
    records an ``EXPIRED`` tombstone.  ``hard_delete`` is an explicit,
    operator-approved maintenance escape hatch and is never used by workers.
    """
    root = str(Path(root).expanduser().resolve())
    if not os.path.exists(root):
        return 0
    cutoff_time = now_utc() - timedelta(days=max(1, days))
    count = 0
    try:
        for current_root, _dirs, filenames in os.walk(root, topdown=False):
            for filename in filenames:
                if not filename.lower().endswith(".png"):
                    continue
                file_path = os.path.join(current_root, filename)
                try:
                    old = datetime.fromtimestamp(
                        os.path.getmtime(file_path), tz=UTC_TIMEZONE
                    ) <= cutoff_time
                    if not old:
                        continue
                    if hard_delete:
                        os.remove(file_path)
                    count += 1
                except OSError:
                    continue
            if hard_delete and current_root != root:
                try:
                    os.rmdir(current_root)
                except OSError:
                    pass
        if count:
            log_message(
                f"Phát hiện {count} ảnh Zalo quá hạn retention; đã ghi tombstone, "
                "không xóa âm thầm."
            )
    except OSError as exc:
        log_message(f"Lỗi khi quét ảnh chụp cũ: {exc}", level="WARNING")
    return count
