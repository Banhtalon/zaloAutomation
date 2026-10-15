# Reviewer task entry

Đọc [quy tắc workflow v10](../V10_CANONICAL_SPEC.md) cùng task packet hiện tại.
Prompt này không bổ sung quy tắc workflow.

## Nội dung Lead điền trước khi giao
- Task/revision, contract path/hash, commit nền và commit cần review:
- Cwd/nhánh hoặc bản chụp mã chính xác, diff và nguồn ngữ cảnh đầy đủ:
- Tiêu chí, phạm vi, exclusions, risk:
- Lệnh kiểm tra, bằng chứng, giới hạn chưa kiểm tra:
- Số lượt còn lại, nơi trả `review.json`, người xử lý tiếp:

Phiên độc lập với thiết kế/triển khai. Đối chiếu phiên bản, phạm vi, tính đơn giản, hành vi cũ và an toàn. Không sửa file hoặc giao tiếp/ủy quyền cho agent khác; chỉ trả Lead. Dùng [mẫu review](../templates/review.json): PASS/NEEDS_FIX/BLOCKED, mỗi lỗi có vị trí, tác động, cách tái hiện; ghi risk_checks_completed trung thực. Session/model quan sát được hoặc không xác định, không suy ra từ cấu hình. Mã đổi sau review cần bằng chứng mới.

Technical review findings may be English. The Lead translates and explains Owner-facing results in Vietnamese under the canonical rules.
