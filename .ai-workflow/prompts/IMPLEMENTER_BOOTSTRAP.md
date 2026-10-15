# Implementer task entry

Đọc [quy tắc workflow v10](../V10_CANONICAL_SPEC.md) cùng task packet hiện tại.
Prompt này không bổ sung quy tắc workflow.

## Nội dung Lead điền trước khi giao
- Task/phần việc, revision, mục tiêu và hành vi mong muốn:
- Cwd, nhánh, commit nền, contract path/hash:
- File được sửa; file chỉ được đọc; ngoài phạm vi:
- Tiêu chí đạt; lệnh kiểm tra đầy đủ, cwd, timeout, kết quả đạt:
- Người chạy kiểm tra (Direct: controller, ASSISTED: Lead):
- Số lượt còn lại và điều kiện dừng:
- Packet, đường dẫn trả `implementer-result.md`, người xử lý tiếp:

Trả [mẫu kết quả](../templates/implementer-result.md), không tự duyệt. Direct worker giữ giới hạn công cụ, không tự chạy shell/test khi controller được giao chạy.

Technical handoff text may be English. The Lead translates and explains Owner-facing results in Vietnamese under the canonical rules.
