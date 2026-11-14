---
trigger: always_on
description: Always-on offline Graphify AST workflow for codebase navigation and post-edit synchronization.
---

# Quy tắc bắt buộc sử dụng Graphify (`graphify-out/graph.json`)

Dự án duy trì đồ thị tri thức mã nguồn cục bộ tại `graphify-out/graph.json` (AST thuần túy). Mọi AI agent và subagent làm việc trên repository này phải tuân thủ:

1. **Tra cứu đồ thị trước khi đọc tràn lan mã nguồn**:
   - Khi `graphify-out/graph.json` tồn tại, luôn chạy `graphify query "<question>"`, `graphify explain "<symbol>"` hoặc `graphify path "<A>" "<B>"` trước khi grep diện rộng hoặc đọc hàng loạt tệp code.
   - Chỉ mở đọc trực tiếp các tệp/dòng mã nguồn đã được đồ thị khoanh vùng; chỉ đọc `graphify-out/GRAPH_REPORT.md` khi cần rà soát kiến trúc tổng thể.
   - Truyền quy tắc tra cứu này vào prompt của mọi subagent khảo sát mã nguồn.

2. **Đồng bộ đồ thị sau khi sửa code**:
   - Sau khi thêm, sửa hoặc xóa tệp mã nguồn trong phiên làm việc, bắt buộc chạy cập nhật AST cục bộ:
     ```powershell
     $env:GRAPHIFY_BACKEND = "none"; graphify update .
     ```
   - Khi cần dựng lại toàn bộ đồ thị mã nguồn từ đầu, chỉ chạy:
     ```powershell
     $env:GRAPHIFY_BACKEND = "none"; graphify extract . --code-only; graphify cluster-only .
     ```

3. **Ranh giới an toàn & Offline 100% (Bắt buộc)**:
   - **Giữ nguyên `.graphifyignore`**: Không xóa hoặc nới lỏng các mẫu chặn dữ liệu thật, phiên trình duyệt (`zalo_session/`), SQLite DB (`*.db*`, `*.sqlite*`), `.env*`, `runtime/`, `evidence/`, uploads, screenshots và `.backups/`.
   - **Offline 100%**: Luôn đặt `$env:GRAPHIFY_BACKEND="none"` và chỉ dùng chế độ `--code-only` (AST cục bộ). Tuyệt đối không gọi API bên ngoài, không gửi mã nguồn hay tài liệu dự án lên bất kỳ dịch vụ LLM/cloud nào qua Graphify.
