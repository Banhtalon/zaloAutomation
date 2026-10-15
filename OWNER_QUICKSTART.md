# Bắt đầu nhanh cho Owner và Codex

Đây là bộ điều phối AI của dự án. Quy tắc workflow duy nhất nằm ở [`V10_CANONICAL_SPEC.md`](.ai-workflow/V10_CANONICAL_SPEC.md); trang này chỉ tóm tắt cách dùng, không tạo thêm quy tắc.

## Owner cần gửi

- Kết quả sản phẩm muốn có.
- Phần nào không được thay đổi.
- Cách kiểm tra để biết là đạt.
- Nếu có: giới hạn chi phí, quyền tài khoản, dữ liệu thật hoặc thời điểm phát hành.

## Luồng mặc định

Bắt đầu từ [`HANDOFF.md`](HANDOFF.md). Với tác vụ mới, Lead/Codex tự lập hồ sơ và chạy Direct: worker thực hiện → chương trình tự chạy kiểm tra → reviewer độc lập kiểm tra → kiểm tra sản phẩm (Product Check) nếu tác vụ yêu cầu → chờ Owner dùng thử và nghiệm thu. Nếu Direct đã biết không phù hợp, Lead ghi lý do và dùng ngoại lệ được quy định trong canonical. Owner không cần chọn AI nào, cách chạy nào, đọc hồ sơ kỹ thuật hay tự chạy lệnh.

## Khi xem kết quả

Owner xem bản dùng thử theo hướng dẫn Lead và nói rõ đạt hay chưa đạt. `DONE`/`COMPLETED` chỉ có nghĩa workflow đã hoàn tất trong vùng riêng; chưa có nghĩa mã đã vào nhánh chính hoặc đã phát hành. Gộp vào dự án chính và phát hành là các bước riêng, phải xác minh mốc mã đích.

## Khi có lỗi hoặc bị chặn

Lead sẽ nêu nguyên nhân, vị trí/bằng chứng, cách xử lý và người cần làm tiếp bằng tiếng Việt. Owner chỉ cần quyết định khi vấn đề liên quan đến hành vi sản phẩm, chi phí, quyền/tài khoản hoặc dữ liệu thật.

## Link cần dùng

- [`HANDOFF.md`](HANDOFF.md): điểm bắt đầu và packet hiện hành.
- [`OWNER_GUIDE.md`](.ai-workflow/OWNER_GUIDE.md): vai trò và trạng thái dành cho Owner.
- [`OWNER_QUICK_PROMPTS.md`](.ai-workflow/prompts/OWNER_QUICK_PROMPTS.md): câu mẫu giao việc, phản hồi và nghiệm thu.
- [`V10_CANONICAL_SPEC.md`](.ai-workflow/V10_CANONICAL_SPEC.md): quy tắc đầy đủ khi cần tra cứu.

## Áp dụng cho Zalo Automation

Bộ flow đã được nhập riêng với mã ứng dụng. Trạng thái kiểm chứng và điều kiện còn thiếu nằm trong [PROJECT_STATE.md](PROJECT_STATE.md); AI tiếp quản đọc [HANDOFF.md](HANDOFF.md). Cài bộ flow không tự cho phép quét hay ghi Zalo/BCNB/Form/LMS thật.
