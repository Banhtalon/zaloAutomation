import json
import asyncio
from google import genai
from google.genai import types
import PIL.Image
from logger_utils import logger
from .config import Config

class OCRProcessor:
    def __init__(self):
        # Khởi tạo Client theo chuẩn SDK mới của Google
        api_key = Config.GEMINI_API_KEY
        if not api_key:
            logger.warning("⚠️ Chưa cấu hình GEMINI_API_KEY trong file .env!")
            self.client = None
        else:
            self.client = genai.Client(api_key=api_key)

    async def process_file_async(self, image_path):
        logger.info(f"🤖 Đang gửi ảnh {image_path} cho AI xử lý (bất đồng bộ)...")
        if not self.client:
            logger.error("❌ Không thể xử lý vì thiếu GEMINI_API_KEY.")
            raise RuntimeError("Thiếu GEMINI_API_KEY.")
            
        try:
            # Đọc ảnh trong thread pool để không làm nghẽn Event Loop
            img = await asyncio.to_thread(PIL.Image.open, image_path)
            
            prompt = """
            Đây là ảnh chụp màn hình phần mềm quản lý lớp học.
            Hãy tìm mã lớp học (ở góc trên cùng) và danh sách tên học viên.
            TUYỆT ĐỐI bỏ qua các thành phần giao diện như: 'Default', 'Class site', 'Running', các icon mũi tên.
            Trích xuất và trả về MỘT MẢNG JSON, mỗi phần tử gồm 2 key: "class_code" và "name".
            """
            
            # Gọi API bằng Client không đồng bộ (aio)
            response = await self.client.aio.models.generate_content(
                model=Config.GEMINI_MODEL,
                contents=[prompt, img],  # type: ignore[arg-type]
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            
            if not response.text:
                raise ValueError("AI không trả về nội dung OCR.")
            data = json.loads(response.text)
            if not isinstance(data, list):
                raise ValueError("AI không trả về một mảng học viên.")
            logger.info(f"✅ AI trích xuất thành công {len(data)} học viên (bất đồng bộ)!")
            img.close()
            return data
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi nhờ AI xử lý (bất đồng bộ): {e}")
            if "img" in locals():
                img.close()
            raise
