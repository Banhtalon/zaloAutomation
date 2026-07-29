from datetime import datetime, timedelta
import os

from logger_utils import log_message


def cleanup_old_screenshots(days: int = 14, root: str = "static/screenshots") -> int:
    if not os.path.exists(root):
        return 0
    cutoff_time = datetime.now() - timedelta(days=max(1, days))
    count = 0
    try:
        for current_root, _dirs, filenames in os.walk(root, topdown=False):
            for filename in filenames:
                if not filename.endswith(".png"):
                    continue
                file_path = os.path.join(current_root, filename)
                if datetime.fromtimestamp(os.path.getmtime(file_path)) < cutoff_time:
                    try:
                        os.remove(file_path)
                        count += 1
                    except OSError:
                        continue
            if current_root != root:
                try:
                    os.rmdir(current_root)
                except OSError:
                    pass
        if count:
            log_message(
                f"Đã tự động dọn dẹp {count} ảnh chụp màn hình Zalo cũ "
                f"(hơn {days} ngày)."
            )
    except OSError as exc:
        log_message(f"Lỗi khi dọn dẹp ảnh chụp cũ: {exc}", level="WARNING")
    return count
