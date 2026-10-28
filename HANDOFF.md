# Bàn giao hiện hành — Zalo Automation

[Canonical v10](.ai-workflow/V10_CANONICAL_SPEC.md) là nguồn quy tắc flow AI.
Yêu cầu hiện tại của Owner và ranh giới an toàn dự án trong [AGENTS.md](AGENTS.md)
được ưu tiên. Đọc PROJECT_SUMMARY.md, PROJECT_STATE.md và PROJECT_DECISIONS.md
trước source. DEC-027 là lịch sử gỡ flow; Owner yêu cầu chuyển lại ngày 2026-10-03.

## Trạng thái cài đặt

- Nguồn main: 3056879dc47d9a3b2846b99c1178b9fd8b553b8f; canonical 10.1.0-rc.9.
- Project profile: [.ai-workflow/PROJECT_PROFILE.json](.ai-workflow/PROJECT_PROFILE.json).
- Bộ công cụ đã nhập; trạng thái thực thi/capability và kiểm tra mới xem PROJECT_STATE.md.
- Không có packet lịch sử được chuyển. .workflow-local/ là dữ liệu cục bộ bị Git bỏ qua.
- Chưa khởi tạo task trên repo thật: mã đang sửa chưa được commit và Owner cấm commit
  trong đợt chuyển đổi này. Không coi HEAD cũ là bản chứa công việc hiện tại.
- Các model trong template là yêu cầu cấu hình, không chứng minh đã gọi hay còn quota.

## Luồng việc mới

Gemini thực hiện → controller chạy gate → Luna Max review độc lập → Product Check
nếu áp dụng → Owner nghiệm thu → chờ quyền gộp/phát hành riêng.
Đọc [Direct](.ai-workflow/DIRECT_GEMINI.md) và dùng ngoại lệ ASSISTED theo canonical
trước dispatch nếu Direct đã biết không phù hợp. Check/review vẫn chỉ đọc.

Lead soạn contract và scope riêng cho đúng công việc, khai báo gate source đã đọc,
giới hạn read/write và các dữ liệu thật phải loại trừ. Không gọi worker/reviewer
hoặc chạy Zalo/BCNB/Form/LMS thật trong việc cài bộ flow này.
Candidate chưa rõ, trùng, thiếu hoặc không khớp phải giữ review theo contract Zalo.

## Môi trường và kiểm tra

Ứng dụng tiếp tục dùng venv/Scripts/python.exe và requirements.txt.
Flow dùng .venv/Scripts/python.exe và requirements-workflow.txt riêng.
Hướng dẫn MIGRATION.md của bộ mẫu được thích nghi ở đây để giữ nguyên dependency
ứng dụng; không cài flow vào Python toàn máy hoặc venv của ứng dụng.

Cấu hình Direct theo dự án: [.ai-workflow/BRIDGE_CONFIG.zalo.direct.example.json](.ai-workflow/BRIDGE_CONFIG.zalo.direct.example.json).
Đây là template, chưa phải task runnable: Lead thay write_paths/gate_paths bằng
đúng file của nhiệm vụ và tạo contract mới; không mở quyền cả repository.
local_trial_root ở ngoài repo; không chép auth/settings hay browser profile sang đó.

Các gate source/local: node --test scripts/lib/*.test.mjs; Python flow chạy
mcp/test_antigravity_server.py; powershell -NoProfile -ExecutionPolicy Bypass
-File scripts/provisioning-safety.test.ps1; gate ứng dụng CHECK_QUALITY.ps1 -Isolated.
Gate đủ của ứng dụng không thay bằng test flow. Product Check cần tiêu chí và
fixture riêng khi có thay đổi nhìn thấy được, không suy ra acceptance từ test xanh.

Owner xem [bắt đầu nhanh](OWNER_QUICKSTART.md). Lead vận hành công cụ và trình bày
kết quả tiếng Việt; Owner quyết định sản phẩm, chi phí, tài khoản, dữ liệu thật,
nghiệm thu và phát hành.


## Cập nhật Flow 2026-10-22 (DEC-036 — Chuẩn Hàng Không)

1. **Lead** trực tiếp code từng hạng mục trong `UPGRADE_PROGRESS.md`.
2. **Test**: Chạy unit test mục tiêu + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated`.
3. **Review độc lập**: `"<prompt>" | codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh" -s read-only -`.
4. **Báo cáo**: Phong cách điện tín / chuẩn hàng không (ngắn gọn, tối ưu token tối đa).
