# Kế hoạch hoàn thiện Zalo Automation

> Roadmap hiện hành từ 2026-09-18. Trạng thái thực tế nằm trong
> `PROJECT_STATE.md`; lịch sử quyết định nằm trong `PROJECT_DECISIONS.md`.

## Mục tiêu hoàn thành

Dự án chỉ được coi là hoàn thành khi một người vận hành có thể chạy từng luồng
với bằng chứng rõ ràng, phục hồi được khi lỗi và không vô tình ghi sai dữ liệu
bên ngoài.

## Thứ tự ưu tiên

### P0 — Ổn định mốc nguồn

- [x] Chuẩn hóa tài liệu trung tâm theo source hiện tại.
- [x] `CHECK_QUALITY.ps1 -Isolated` đạt ngày 2026-09-18: 525 test, coverage
  77,1%, exit `0`.
- [ ] Review toàn bộ thay đổi sản phẩm đang chưa commit.
- [ ] Tạo checkpoint Git được Owner chấp nhận; không commit secret/runtime.

Hoàn thành khi: quality gate xanh, diff đã được review và có điểm quay lại rõ
ràng. Không yêu cầu chạy trình duyệt hoặc dịch vụ thật.

### P1 — Nghiệm thu local/dry-run

- [ ] Health/readiness và khởi động lại ứng dụng ổn định.
- [ ] Discovery tạo snapshot đúng hai tuần và danh sách gap có thể review.
- [ ] Zalo scan dry-run xử lý đủ target; target thiếu đồng bộ giữ trạng thái cần
  xem xét thay vì kết luận âm tính.
- [ ] Google Form OCR và dry-run hoạt động với ảnh đại diện đã loại dữ liệu nhạy cảm.
- [ ] LMS analysis tạo plan đúng; token hết hạn/dùng lại bị từ chối.
- [ ] Backup SQLite và restore drill trên bản sao tạm thành công.
- [ ] Sửa các chuỗi log tiếng Việt bị lỗi mã hóa và thêm một kiểm tra nhỏ để
  tránh tái diễn.

Hoàn thành khi: mỗi luồng có lệnh/job mới, kết quả, timestamp và giới hạn bằng
chứng. Không dùng test xanh thay cho kiểm tra hành vi nhìn thấy được.

### P2 — Canary ghi thật có giới hạn

- [ ] Owner chọn chính xác một target và duyệt bản xem trước mới.
- [ ] Tạo backup trước khi chạy và xác minh rollback.
- [ ] Chạy đúng một canary; không chuyển batch dry-run thành quyền ghi hàng loạt.
- [ ] Kiểm tra read-back và external-action audit sau thao tác.
- [ ] Nếu outcome `UNKNOWN`, dừng và kiểm tra thủ công; không retry.

Hoàn thành khi: target thật được xác minh bằng read-back, không có audit treo và
Owner chấp nhận kết quả. Mỗi hệ thống ngoài cần gate riêng.

### P3 — Staging/production nếu thực sự cần

- [ ] Chỉ thực hiện khi Owner yêu cầu truy cập qua mạng hoặc vận hành lâu dài.
- [ ] Dùng một replica, HTTPS reverse proxy, web auth, Secure cookie và allowlist.
- [ ] Lưu secret ngoài Git và chạy smoke chỉ-đọc qua URL staging.
- [ ] Có quy trình cập nhật, giám sát, backup và quay lại bản trước.

Hoàn thành khi: staging được nghiệm thu riêng. Local acceptance không tự mở
quyền staging hoặc production.

## Điều kiện dừng

Dừng ngay và báo Owner nếu gặp một trong các trường hợp:

- target, tuần, ngày hoặc identity mơ hồ;
- snapshot/plan hết hạn hoặc khác dữ liệu vừa đọc lại;
- profile Zalo bị process khác giữ;
- lịch sử Zalo chưa đồng bộ;
- audit `INTENT_RECORDED`/`UNKNOWN` chưa được xử lý;
- backup hoặc restore drill thất bại;
- phát hiện secret/dữ liệu thật sắp được đưa vào Git;
- thay đổi ngoài phạm vi task so với baseline.

## Cách quay lại

- Tài liệu: dùng Git hoặc bản ZIP được tạo trước lần chuẩn hóa.
- Source: chỉ quay lại bằng commit/checkpoint đã xác định; không dùng
  `reset --hard` trên working tree đang có thay đổi.
- Database: dùng backup đã kiểm tra integrity và restore drill; không chép đè
  database đang chạy.
- Hệ thống ngoài: dựa trên read-back/audit và thao tác phục hồi được Owner duyệt.
