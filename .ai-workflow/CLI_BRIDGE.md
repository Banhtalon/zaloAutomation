# Phục hồi hồ sơ bridge cũ

Việc mới dùng [Direct](DIRECT_GEMINI.md) và [mẫu cấu hình hiện hành](BRIDGE_CONFIG.direct.example.json). Trang này chỉ dành cho hồ sơ bridge đã khóa trước đợt tinh gọn vận hành. Quy tắc chung nằm ở [canonical](V10_CANONICAL_SPEC.md).

## Trình tự khi gặp hồ sơ cũ

1. Đọc task, lock, config, packet và trạng thái đã lưu; đối chiếu commit mã đã ghi và số lượt còn lại. Thiếu hồ sơ hoặc kết quả lần trước chưa rõ thì dừng, không chạy lại.
2. Dùng `status`, `report` hoặc `inspect` để xem bằng chứng. Nếu trạng thái yêu cầu đối soát, dùng `reconcile` hoặc `recover` theo kết quả thực tế; không sửa tay số lượt, lock hay trạng thái.
3. Chỉ dùng `resume` trên đúng hồ sơ đã khóa sau khi đã đối soát. Lệnh `pilot`, `run`, `quota-drill`, `activate` của bridge không còn khởi tạo đường mới.
4. Nếu bản mã hiện hành không thể đọc hồ sơ cũ, dùng commit trước đợt này `fc5c5bc587714244ed71f1da956fb9fc8fd673f0` trong một worktree riêng. Giữ nguyên packet, tạo bản sao để thử trước khi tiếp tục bản thật. Không sao chép dữ liệu đăng nhập vào packet.

## Lệnh tham khảo

```text
node scripts/bridge.mjs status <run-packets>
node scripts/bridge.mjs report <run-packets> --audience lead --format json
node scripts/bridge.mjs inspect <bridge-config.json> <frozen-task.json> <run-packets>
node scripts/bridge.mjs reconcile <bridge-config.json> <frozen-task.json> <repo> <run-packets>
node scripts/bridge.mjs recover <bridge-config.json> <frozen-task.json> <repo> <run-packets> <claim-owner> <reason>
node scripts/bridge.mjs resume <bridge-config.json> <frozen-task.json> <repo> <run-packets>
```

`recover` cần tên chủ quyền xử lý đúng như hồ sơ claim và lý do cụ thể; lệnh ghi trạng thái chặn khi không thể xác nhận thao tác trước; không phải lệnh chạy tiếp. Với hồ sơ Direct, ưu tiên [lệnh Direct](DIRECT_GEMINI.md) và chỉ dùng các lệnh `inspect`, `reconcile`, `recover` của bridge khi trạng thái yêu cầu. Các mẫu `BRIDGE_CONFIG.controlled.example.json` và `BRIDGE_CONFIG.example.json` là bản lịch sử, không dùng cho việc mới.
