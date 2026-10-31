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

Các track và thứ tự lên cấp tự động đã được Owner xác nhận (`DEC-039` / D3) và khai báo trong `services/class_lifecycle.py` (`PROGRESSION_LEVELS`):

- **Lập trình (Coding)**:
  - Track `S`: `SB → SA → SI`
  - Track `G`: `GB → GA → GI`
  - Track `PT`: `PTB → PTA → PTI`
  - Track `JS`: `JSB → JSA → JSI`
  - Track `CS`: `CSB → CSA → CSI`
- **Robotics**:
  - Track `ROB4`: `ROB4B → ROB4A → ROB4I`
  - Track `PRE`: `PREB → PREA → PREI`
  - Track `ARM`: `ARMB → ARMA → ARMI`
  - Track `SEMI`: `SEMIB → SEMIA → SEMII`

### Quy tắc parse và so khớp

- Parser so khớp **toàn bộ token** (`fullmatch`) theo thứ tự độ dài giảm dần:
  `ROB4 | SEMI | PRE | ARM | PT | JS | CS | G | S` kèm cấp `B | A | I` và số cohort `2–3` chữ số (`\d{2,3}`).
- Bỏ qua các token hậu tố đứng sau token mã cấp (ví dụ `-HB` trong `VT-JSI12-HB`, `VT-CSA04-HB`); `center_code` lấy từ các token đứng trước token mã cấp (ví dụ `VT`, `VT-C4K`, `VT-ROB`, `VT-KIND`).
- Chỉ tự động nối chuỗi lên cấp `B → A → I` **trong cùng một track** và cùng `center_code` + `cohort_number` (`cohort_key = "{center_code}|{track}|{cohort_number}"`, ví dụ `VT|JS|17`, `VT-ROB|PRE|18`).
- Chuyển tiếp **giữa hai track khác nhau** (ví dụ `PREI16 → ARMB16`, `SI → GB`) **không** tự động nối; các dòng này giữ ở trạng thái cần kiểm tra thủ công (`TRANSITION_REVIEW` / `MISSING_ZALO_GROUP`).
- **Tương thích dữ liệu legacy (`VT-J|S|NN`, `VT-C|S|NN`)**:
  - Trước Wave 5, regex `.search` cũ đọc nhầm `JSB17` / `CSA03` thành track `S` với center `VT-J` / `VT-C`.
  - Hệ thống **không** tự động migrate `database.db` ngầm. Thay vào đó, ở mọi đường đọc và ghi (`GroupRepository`), các key legacy `VT-J|S|NN` và `VT-C|S|NN` được ánh xạ về key chuẩn `VT|JS|NN` và `VT|CS|NN` khi và chỉ khi mã lớp đầy đủ đã lưu (`class_code` / `current_class_code`) xác minh đúng danh tính lớp và không có va chạm alias. Nếu phát hiện va chạm (nhiều cohort cũ ứng với một key mới hoặc ngược lại, hoặc `class_code` thực sự thuộc center `VT-J` track `S`), hệ thống **fail-closed**: không tự gộp và đánh dấu `alias_collision` / `needs_review`.

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
