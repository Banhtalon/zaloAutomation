import unicodedata
import re
from datetime import datetime, timedelta


def _date_without_year(day: int, month: int, today: datetime) -> datetime:
    candidate = datetime(today.year, month, day)
    if candidate.date() > today.date():
        candidate = datetime(today.year - 1, month, day)
    return candidate


def parse_zalo_date_header(date_text: str, today: datetime) -> datetime | None:
    text = date_text.strip().lower()
    if "hôm nay" in text:
        return today
    if "hôm qua" in text:
        return today - timedelta(days=1)
    
    # Định dạng dd/mm/yyyy hoặc dd/mm
    matches = re.findall(r'\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b', text)
    if matches:
        match = matches[-1]
        try:
            day = int(match[0])
            month = int(match[1])
            if match[2]:
                year = int(match[2])
                if year < 100:
                    year += 2000
                return datetime(year, month, day)
            return _date_without_year(day, month, today)
        except (TypeError, ValueError):
            return None
    return None

def check_is_report_and_extract_date(
    raw_content: str,
    group_name: str,
    today: datetime,
) -> tuple[bool, datetime | None]:
    # 1. Chuẩn hóa tiếng Việt và đưa về chữ thường
    text = unicodedata.normalize('NFC', raw_content).lower().replace('\xa0', ' ')
    
    # 2. Hệ thống tính điểm phân loại (Scoring System)
    score = 0.0
    
    # Category 1: Lời chào (Trọng số 1.0)
    greetings = ["chào ba mẹ", "chào bố mẹ", "chào phụ huynh", "chào quý phụ huynh", "kính gửi phụ huynh", "kính gửi quý phụ huynh", "thân gửi phụ huynh"]
    if any(g in text for g in greetings):
        score += 1.0
        
    # Category 2: Các đầu mục báo cáo CHÍNH (Trọng số 2.0)
    strong_headers = ["nội dung", "nhận xét", "mã lớp: vt", "bài tập về nhà", "btvn", "nhiệm vụ về nhà", "nhiệm vụ khi về nhà"]
    for h in strong_headers:
        if h in text:
            score += 2.0
            
    # Category 3: Các đầu mục PHỤ (thường có cả trong điểm danh) (Trọng số 1.0)
    weak_headers = ["tình hình lớp", "sĩ số", "vắng", "buổi số", "buổi học"]
    for h in weak_headers:
        if h in text:
            score += 1.0
            
    # Category 4: Từ khóa báo cáo/tổng kết rõ ràng (Trọng số 3.0)
    report_keywords = ["báo cáo lớp", "báo cáo buổi học", "tổng kết buổi", "tổng kết lớp", "update tình hình", "update lớp", "update sau buổi"]
    if any(rk in text for rk in report_keywords):
        score += 3.0
        
    # Category 5: Khớp mã lớp học (Trọng số 1.0)
    group_name_upper = group_name.upper()
    parts = [p.strip().lower() for p in group_name_upper.split('-') if len(p.strip()) >= 3]
    for part in parts:
        if part == "mindx":
            continue  # Bỏ qua chữ mindx vì quá phổ biến (tên người gửi)
        if part in text:
            score += 1.0
            break
            
    # Penalty: Trừ điểm nếu là tin nhắn điểm danh đơn thuần
    if "điểm danh" in text and "nội dung" not in text and "nhận xét" not in text:
        score -= 2.0
            
    # Phân loại: Ngưỡng >= 4.0 điểm được coi là báo cáo
    is_report = score >= 4.0
    
    if not is_report:
        return False, None
        
    # 3. Trích xuất ngày tháng thông minh
    msg_date = None
    
    # Mức 1: Định dạng dd/mm/yyyy hoặc dd/mm/yy hoặc yyyy-mm-dd
    # Dùng \2 để đảm bảo 2 dấu phân cách phải giống nhau (tránh nhầm chuỗi như 3-09/06/2026 thành 3-09/06)
    date_matches = re.findall(r'(?<!\d)(\d{1,2})([/.-])(\d{1,2})\2(\d{2,4})(?!\d)', raw_content)
    if date_matches:
        # Lấy ngày đầu tiên xuất hiện (thường là ngày của buổi học hiện tại, tránh lấy ngày của buổi học kế tiếp ở cuối tin nhắn)
        match = date_matches[0]
        try:
            day = int(match[0])
            month = int(match[2])
            year = int(match[3])
            if year < 100:
                year += 2000
            msg_date = datetime(year, month, day)
        except (TypeError, ValueError):
            pass
            
    # Mức 2: Định dạng ngày/tháng không kèm năm (Ví dụ: 09/05 hoặc 9-5)
    # Cần lọc kỹ để tránh nhầm với sĩ số (ví dụ: 7/8 học sinh)
    if not msg_date:
        dm_matches = []
        for sep in ['/', '-', '.']:
            pattern = r'(?<!\d)(\d{1,2})' + re.escape(sep) + r'(\d{1,2})(?!\d)'
            matches = re.findall(pattern, raw_content)
            if matches:
                dm_matches = [(m[0], m[1], sep) for m in matches]
                break
                
        for dm in dm_matches:
            try:
                day = int(dm[0])
                month = int(dm[1])
                sep = dm[2]
                if 1 <= month <= 12 and 1 <= day <= 31:
                    # Tìm ngữ cảnh xung quanh để xác nhận đây là ngày tháng chứ không phải sĩ số
                    idx = raw_content.find(f"{dm[0]}{sep}{dm[1]}")
                    context_before = raw_content[max(0, idx-15):idx].lower()
                    if "ngày" in context_before or "nộp" in context_before or dm[0].startswith('0') or dm[1].startswith('0'):
                        msg_date = _date_without_year(day, month, today)
                        break
            except (TypeError, ValueError):
                pass
                
    # Mức 3: Định dạng chữ "ngày d tháng m"
    if not msg_date:
        text_match = re.search(r'ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})', raw_content, re.IGNORECASE)
        if text_match:
            try:
                day = int(text_match.group(1))
                month = int(text_match.group(2))
                msg_date = _date_without_year(day, month, today)
            except (TypeError, ValueError):
                pass
                
    return True, msg_date
