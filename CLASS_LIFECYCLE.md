# Vòng đời lớp và chuyển cấp

## Quy tắc nghiệp vụ

- Có mặt trên BCNB là bằng chứng lớp đang hoạt động.
- Vắng trên BCNB chỉ chuyển lớp sang `PAUSED_OR_TRANSITIONING`; thời gian vắng
  1, 2, 3 tuần hoặc lâu hơn không tự động chứng minh lớp đã kết thúc.
- Lớp chỉ chuyển sang `ENDED_CONFIRMED` khi người quản lý xác nhận.
- Lớp đã xác nhận kết thúc được chuyển sang `ARCHIVED` sau 14 ngày tính từ
  `ended_at`. Bản ghi được giữ làm dấu vết, không bị xóa vật lý.
- Nếu mã lớp xuất hiện lại trên BCNB, lớp trở về `ACTIVE` từ mọi trạng thái.

## Chuỗi chuyển cấp tự động

Các chuỗi đã được xác nhận:

- App: `PTB → PTA → PTI`
- Game: `GB → GA → GI`
- Scratch: `SB → SA → SI`

Số khóa được giữ ổn định trong chuỗi, ví dụ `GB02 → GA02 → GI02`. Khóa nhận
diện còn chứa cơ sở/chương trình từ mã BCNB, vì vậy hai lớp cùng số ở hai cơ
sở khác nhau không bị ghép nhầm.

Robotics chưa được tự động nối cấp. Các nhánh `ARM`, `PRE`, `SEMI` chỉ được bật
sau khi thứ tự chuyển cấp được xác nhận bằng nghiệp vụ thực tế.

## Dữ liệu

- `groups` lưu thêm mã lớp, khóa chuỗi và số buổi gần nhất.
- `class_cohorts` lưu trạng thái hiện tại của một khóa xuyên suốt các cấp.
- `class_code_history` lưu lịch sử mã và tên nhóm Zalo.
- `ignored_groups` là danh sách tạm nghỉ/chờ chuyển cấp hoặc đã xác nhận kết
  thúc; bản ghi lưu trữ vẫn là tombstone để lần dò sau không tự thêm lại.

## Vận hành và hoàn tác

- Migration chỉ bổ sung cột/bảng, không xóa dữ liệu cũ.
- Ứng dụng chạy lưu trữ khi khởi động, khi đọc danh sách và mỗi 24 giờ.
- Bản sao trước migration nằm trong `.backups/` với tiền tố
  `database-before-class-lifecycle-`.
- Có thể hoàn tác mã nguồn và phục hồi bản sao SQLite nêu trên nếu cần.
