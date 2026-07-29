# Giai đoạn 5 — Service Hardening & Error Contracts

## Mục tiêu

Giai đoạn 5 giảm rủi ro thay đổi ở các luồng nền quan trọng mà không đổi schema
SQLite, route thành công hay payload job đã lưu. Phạm vi gồm Auto Discovery,
vòng đời browser gateway, dispatcher và lỗi công khai của API.

## Thay đổi chính

- Tách Auto Discovery khỏi `services/zalo_service.py` sang
  `services/zalo_discovery_service.py`.
- Giữ re-export `run_auto_discovery` để mã tích hợp cũ tiếp tục hoạt động.
- Dispatcher gọi trực tiếp service mới; route `/auto-discover`, loại job
  `ZALO_DISCOVERY` và payload `{"version": 2}` không đổi.
- Discovery tiếp tục dùng browser gateway do `WorkerRuntime` sở hữu và luôn nhả
  browser lock/context khi thành công, lỗi hoặc bị hủy.
- Discovery ghép được mã nhóm rút gọn như `PTI09` với mã BCNB đầy đủ
  `VT-C4K-PTI09` khi alias là duy nhất; alias trùng giữa nhiều mã sẽ fail-closed.
- API dùng lỗi công khai dạng `{"code": "...", "message": "..."}`. Chi tiết
  exception, đường dẫn, thông tin database và secret không được trả về client
  hoặc ghi vào `scan_jobs.message`.
- `JobNotFoundError` thay cho việc dò chuỗi thông báo để phân biệt HTTP 404 và
  xung đột trạng thái HTTP 409.

## Hợp đồng an toàn

1. Discovery fail-closed nếu BCNB/Zalo không trả dữ liệu, tỷ lệ ghép dưới 50%,
   hoặc số nhóm mới giảm bất thường.
2. Không gọi SDK Playwright trực tiếp trong orchestration service.
3. Lỗi không dự kiến trả HTTP 500 với mã `INTERNAL_ERROR`.
4. Lỗi upload/confirmation/state đã biết dùng mã ổn định và không lộ nội dung
   exception gốc.
5. Không có migration database trong giai đoạn này.

## Kiểm thử và chất lượng

- Contract test cho launch/context/page/init-script cleanup của browser gateway.
- Test Auto Discovery với gateway giả, fail-closed, persistence và nhả lock.
- Test hồi quy mã rút gọn PTI09 và trường hợp hai mã BCNB có cùng alias.
- Test toàn bộ 6 nhánh job dispatcher, retry, recovery, shutdown, cancel và lỗi.
- Test route thành công cùng hợp đồng lỗi đã làm sạch.
- Cổng bắt buộc: Ruff, mypy, 105 test, branch coverage tối thiểu 70%, invariant
  Giai đoạn 3/4/5, schema, app smoke, frontend contract và compile.

## Triển khai và khôi phục

- Triển khai cục bộ trước, giữ `BCNB_DRY_RUN=true` và Google Form ở dry-run.
- Kiểm tra `/health/ready`, tạo một job Discovery thử và xác nhận timeline kết thúc
  ở trạng thái cuối trước khi bật thao tác production.
- Checkpoint trước thay đổi:
  `.backups/zaloAutomation-v4.0.0-before-phase5-20260715-143423.zip`
- SHA-256:
  `9ADE87326748CA4D4071D6C14734D0955D3AB355BA2DC9FFEBBDF3E069531E3C`
- Bản backup chứa `.env`, database và browser profile, phải được xem là dữ liệu
  bí mật. Khi rollback, dừng ứng dụng, lưu riêng dữ liệu phát sinh sau checkpoint,
  rồi phục hồi toàn bộ archive.
