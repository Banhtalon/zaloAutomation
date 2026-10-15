# Adoption and migration

[Quy tắc workflow v10](V10_CANONICAL_SPEC.md) là nguồn điều phối cho dự án đã chuyển đổi.

## Template release

Tag chú thích `v9.1.2` trỏ tới `c68750f80d276534867287ffe02689f346be8b8d`. Entry point, scripts và tests ở checkout hiện tại dùng v10.

## v10.1.0-rc.2

Checkout hiện tại đã có Controlled Delegation V2 và cầu nối CLI tuần tự. Task v10 cũ
tiếp tục dùng policy đã đóng băng; task mới khởi tạo từ template hoặc `workflow.mjs init`
dùng `CONTROLLED_DELEGATION_V1`. Khi task contract chọn V2, bridge configuration cần có
worker Gemini, fallback Luna và các binding reviewer/senior tương ứng. Packet đang chạy
giữ nguyên policy; thay đổi phạm vi hoặc contract tạo revision mới. `pilot`, `run` và
`resume` trả Owner report cùng Lead data trong JSON khi checkpoint báo cáo được lưu.

## New project

Copy `.ai-workflow/`, `AGENTS.md`, `GEMINI.md`, `HANDOFF.md`, `OWNER_QUICKSTART.md`, `scripts/` và `mcp/` vào namespace workflow trống. Thư mục `mcp/` là cầu nối Python mà Direct dùng để gọi worker; nếu bỏ sót, task sẽ dừng ngay khi khởi động. `HANDOFF.md` là điểm bắt đầu mà `BOOTSTRAP.md` yêu cầu đọc.

Nếu project nhận chưa có `requirements.txt`, copy file này. Nếu đã có file đó, giữ nguyên các dependency hiện có và thêm dòng `mcp==2.2.0`; không chép đè để tránh làm mất dependency của project. Nếu project đã ghim một phiên bản MCP khác, dừng để xử lý xung đột trước khi chạy workflow.

Tạo môi trường Python riêng cho project và cài đúng dependency đã ghim; không nâng hoặc hạ MCP trong Python dùng chung của máy:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from mcp import Client; from mcp.server.mcpserver import MCPServer; print('MCP 2.2.0 OK')"
.\.venv\Scripts\Activate.ps1
```

Sau khi kích hoạt `.venv`, chạy các lệnh `prepare`, `check`, `run` và các gate có gọi worker trong cùng cửa sổ PowerShell. Nếu lệnh được chạy từ một tiến trình không dùng cửa sổ đã kích hoạt, đặt `worker.command[0]` trong config Direct thành đường dẫn tuyệt đối tới `.venv/Scripts/python.exe` của project. Thêm `.workflow-local/` và `.venv/` vào ignore file, cấu hình gates theo dự án nhận, rồi tạo project profile và task v10.

## Existing project

Ghi lại task/version/head tại thời điểm chuyển đổi, requirements hiện có, gates và live-write boundaries. Tạo contract v10 tại baseline đã chọn và lưu evidence mới theo canonical spec.

## Khôi phục v9

Tag `v9.1.2` giữ bản lưu v9. Tạo working directory riêng để xem hoặc phục hồi:

```text
git worktree add ..\qq-ai-workflow-v9 v9.1.2
```

Working directory này giữ checkout hiện tại nguyên vẹn. Việc chuyển đổi sau đó dùng task và evidence mới.
