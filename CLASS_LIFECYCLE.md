# Vòng đời lớp và chuyển cấp

## Quy tắc nghiệp vụ

- Có mặt trên BCNB là bằng chứng lớp đang hoạt động.
- Vắng trên BCNB chỉ chuyển lớp sang `PAUSED_OR_TRANSITIONING`; thời gian vắng
  1, 2, 3 tuần hoặc lâu hơn không tự động chứng minh lớp đã kết thúc.
- BCNB hiện không cung cấp ngày kết thúc lớp riêng. Khi trạng thái lớp trên
  BCNB là `Finished`, app tự chuyển đúng nhóm Zalo đã ghép duy nhất sang
  `ENDED_CONFIRMED`; `ended_at` là thời điểm app quan sát `Finished` lần đầu.
  Trạng thái mâu thuẫn, mã mơ hồ hoặc lớp vẫn có trong lịch tuần đều bị bỏ qua.
- Người quản lý vẫn có thể xác nhận thủ công khi BCNB không có evidence
  `Finished`.
- Lớp đã xác nhận kết thúc được chuyển sang `ARCHIVED` sau 7 ngày tính từ
  `ended_at`. Bản ghi được giữ làm dấu vết, không bị xóa vật lý.
- Nếu mã lớp xuất hiện lại trên BCNB, lớp trở về `ACTIVE` từ mọi trạng thái.

## Chuỗi chuyển cấp tự động

Các chuỗi đã được khai báo trong parser/discovery:

- Lập trình: `SB → SA → SI → GB → GA → GI → PTB → PTA → PTI → JSB → JSA → JSI → CSB → CSA → CSI`.
- Robotics: `ROB4B → ROB4A → ROB4I → PREB → PREA → PREI → ARMB → ARMA → ARMI → SEMIB → SEMIA → SEMII`.

Số khóa được giữ ổn định trong chuỗi, ví dụ `GB02 → GA02 → GI02` hoặc
`PTA11 → PTI11`. Khóa so sánh mới gồm cơ sở + roadmap + số cohort; khóa
legacy trong SQLite (`|PT|`, `|G|`, `|ROB4|`...) vẫn được giữ để không phá dữ
liệu cũ. Hai lớp cùng số nhưng khác cơ sở/chương trình không bị ghép nhầm.

Robotics không còn bị loại khỏi parser; các chuyển tiếp `ROB4 → PRE → ARM →
SEMI` vẫn fail-closed nếu BCNB/Zalo chưa đồng thời xác minh được mã và tên
nhóm. Không dùng số ngày nghỉ cố định để kết luận lớp đã kết thúc.

## Dữ liệu

- `groups` lưu thêm mã lớp, khóa chuỗi và số buổi gần nhất.
- `class_cohorts` lưu trạng thái hiện tại của một khóa xuyên suốt các cấp.
- `class_code_history` lưu lịch sử mã và tên nhóm Zalo.
- `ignored_groups` là danh sách tạm nghỉ/chờ chuyển cấp hoặc đã xác nhận kết
  thúc; bản ghi lưu trữ vẫn là tombstone để lần dò sau không tự thêm lại.

## Vận hành và hoàn tác

- Migration chỉ bổ sung cột/bảng, không xóa dữ liệu cũ.
- Ứng dụng chạy lưu trữ khi khởi động và mỗi 24 giờ. `GET` danh sách luôn chỉ
  đọc, không chạy maintenance ngầm.
- Bản sao trước migration nằm trong `.backups/` với tiền tố
  `database-before-class-lifecycle-`.
- Có thể hoàn tác mã nguồn và phục hồi bản sao SQLite nêu trên nếu cần.
