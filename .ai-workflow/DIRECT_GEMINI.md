# Gọi Gemini trực tiếp

Điều phối chuẩn bị yêu cầu rồi chạy script; không mở Luna chỉ để chạy lệnh. Worker dùng Gemini 3.8 Flash High qua MCP Antigravity đã đăng nhập để thực hiện (Gemini implement), controller tự chạy kiểm tra/gate (controller tests), reviewer độc lập bắt buộc là OpenAI Codex model `gpt-6-luna` ở reasoning effort `max` (Luna Max independent review), sau đó dừng tại checkpoint chờ Owner nghiệm thu (Owner acceptance). Luna chỉ làm reviewer độc lập, tuyệt đối không làm worker hay runner. Không cài thêm dịch vụ hoặc thư viện ngoài các dependency đã ghim trong `requirements.txt`. Task Direct đã khóa với Luna 5.6 tiếp tục theo cấu hình cũ.

## Môi trường Python bắt buộc

Worker MCP dùng `mcp==2.2.0`. Không dùng MCP trong Python dùng chung của máy vì ứng dụng khác có thể cần phiên bản khác. Đứng tại thư mục gốc project và chạy một lần:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from mcp import Client; from mcp.server.mcpserver import MCPServer; print('MCP 2.2.0 OK')"
.\.venv\Scripts\Activate.ps1
```

Giữ cửa sổ PowerShell đã kích hoạt `.venv` trong suốt thời gian chạy `prepare`, `check`, `run` và các gate gọi worker. Nếu PowerShell không cho phép lệnh kích hoạt, dùng môi trường trong cửa sổ hiện tại bằng lệnh sau rồi chạy tiếp:

```powershell
$env:Path = (Resolve-Path ".venv\Scripts").Path + ";" + $env:Path
```

Lệnh kiểm tra nhanh trước khi chạy Direct:

```powershell
python -c "from mcp import Client; from mcp.server.mcpserver import MCPServer; print('MCP 2.2.0 OK')"
```

Nếu lệnh này lỗi, dừng tại bước chuẩn bị môi trường; không đổi import trong `mcp/antigravity_server.py` và không cài đè MCP trong Python dùng chung.

## Quy trình

Trước dispatch, Lead đối chiếu đầy đủ nguồn review, bộ lọc và kích thước prompt. Nếu Direct đã biết không phù hợp, xem ngoại lệ ASSISTED tại [canonical](V10_CANONICAL_SPEC.md#current-entry-and-owner-workflow). Lead ghi lý do và thông báo; Owner không phải chọn cách chạy. Không dùng ngoại lệ để chạy lại Direct đã thất bại, vượt ngân sách hoặc chưa rõ kết quả.

1. Dùng task `qq.workflow.task.v10.1` và config `qq.bridge.v2` hiện có; chốt repo chính, base SHA, mục tiêu, tiêu chí, exact `allowed_paths`/`write_paths` và gates. Owner chỉ cần mô tả công việc. Điều phối soạn JSON.
2. Bắt đầu từ [mẫu cấu hình Direct](BRIDGE_CONFIG.direct.example.json). Thay `write_paths` bằng đúng các file được giao, `gate_paths` bằng file bảo vệ cho các lệnh kiểm tra, và `local_trial_root` bằng thư mục tuyệt đối bên ngoài repo và packet. Với việc giao diện mới, thêm `product_check.command` và `preflight` như đoạn dưới. Hai thư mục mẫu có nội dung tối thiểu: một kết quả đúng, một kết quả sai có thể xảy ra; script gate nằm trong repo và dùng đường dẫn tương đối trong lệnh. `good_target_url` phải đúng URL mà script Product Check trả về khi đứng tại thư mục mẫu đúng. `prepare` tự chạy phép kiểm tra trên cả hai mẫu và xác nhận đủ định dạng Product Check **trước khi tạo packet**, không gọi AI. `check` và `run` thử lại trước dispatch; nếu mẫu sai, sửa phần chuẩn bị rồi mới chạy. Task đã prepare từ trước không bị ép thêm bước này. Nếu Gemini cần biết giao diện hiện có trước khi sửa, sau khi kiểm tra các file đó, thêm `review_context_paths` và một `worker.context` ngắn gồm `base_sha`, `paths` (phải nằm trong `review_context_paths`) và `summary`; phần tóm tắt này chỉ được chèn vào yêu cầu Gemini, còn `write_paths`/`allowed_paths` vẫn là giới hạn sửa. Config worker: `transport:mcp`, `server:antigravity_worker`, `provider:mcp`, `command:[C:/project/.venv/Scripts/python.exe,mcp/antigravity_server.py]`, `model:gemini-3.8-flash-high`, `local_trial:true`, `local_trial_root` là thư mục tuyệt đối nằm ngoài repo/packet, `skip_permissions:false`. Thay `C:/project` bằng đường dẫn thật của project trước khi `prepare`; phần tử thứ hai vẫn là `mcp/antigravity_server.py`. Reviewer: `provider:openai`, `cli:codex`, `command:[codex]`, `model:gpt-6-luna`, `effort:max`. Billing `SUBSCRIPTION_ONLY`; không chấp nhận `fallback_reviewer`.

```json
{
  "product_check": { "command": ["node", "tests/browser-check.mjs"] },
  "preflight": {
    "good_cwd": "C:/project/.workflow-local/fixtures/good",
    "bad_cwd": "C:/project/.workflow-local/fixtures/bad",
    "good_target_url": "file:///C:/project/.workflow-local/fixtures/good/page.html"
  }
}
```
3. Chạy `prepare`; lệnh này không gọi model. `run` tự kiểm tra điều kiện trước khi gọi AI, vì vậy chỉ chạy `check` riêng khi cần chẩn đoán. `prepare` tạo task/lock và helper dưới `.workflow-local/direct/<task_id>`, packet bên ngoài repo, giữ nguyên trạng thái paused/stopped. Chỉ hỗ trợ repo chính, không dùng một worktree làm repo nguồn. Thêm `.workflow-local/` vào ignore của repo trước khi chuẩn bị.
4. Chạy `run` đúng một lần cho lần bắt đầu mới. Task Direct mới có một lượt Gemini làm đầu và tối đa ba lượt Gemini sửa trong cùng tiến trình; tối đa bốn lượt Luna review. Dừng ngay khi đạt yêu cầu hoặc khi gặp trạng thái cần Điều phối xử lý. Task đã khóa với ngân sách cũ giữ một lượt sửa. Worker Gemini thực hiện (Gemini implement), controller tự chạy gate (controller tests), và OpenAI Codex Luna Max kiểm tra độc lập (Luna Max independent review). Worker chỉ dùng file tools trên file được giao.
5. Đọc JSON kết quả trước; khi lỗi mới mở evidence. Với tác vụ có Product Check, review đạt mới chuyển sang chạy lệnh kiểm tra sản phẩm đã đóng băng. Thiếu công cụ, hết thời gian hoặc kết quả chưa hợp lệ dừng tại `PRODUCT_CHECK_WAIT`; sau khi xử lý nguyên nhân, Điều phối dùng `verify-product` để chỉ chạy lại bước này, không gọi thêm worker/reviewer. `WAITING_FOR_CHECKPOINT` nghĩa là mọi kiểm tra bắt buộc đã đạt và đang chờ Owner nghiệm thu.
6. Sau khi Owner nói rõ nghiệm thu, Điều phối chạy `accept ... Owner`. Script ghi checkpoint/completion; không tự chép file về repo nguồn hoặc commit. Muốn chuyển kết quả phải so baseline và chỉ chuyển file đã duyệt. Sau đó lưu mốc khôi phục kèm mã nguồn, config không bí mật và evidence; không lưu settings/auth.

## Context ngắn chuẩn

Worker chỉ nhận mục tiêu, tiêu chí nghiệm thu, đúng `allowed_paths`/`write_paths` và một tóm tắt hiện trạng đã kiểm tra. Nếu cần context nguồn hiện có, dùng đúng dạng `worker.context: {base_sha, paths, summary}`; `base_sha` phải khớp task, `paths` phải nằm trong `review_context_paths`, và `summary` phải ngắn, không chứa thông tin nhạy cảm, tối đa 4.096 ký tự. Context không mở rộng quyền đọc hoặc quyền sửa của worker.

Reviewer nhận hợp đồng rút gọn của task, file hiện tại đã thay đổi, diff, nguồn gate/context/Product Check đã khai báo và bằng chứng test. Nguồn trùng lặp của file đang sửa được bỏ khỏi phần base; file bị xóa/đổi tên vẫn giữ base. Controller giữ bản đầy đủ và hash để đối chiếu; prompt reviewer tối đa 256 KiB. Báo cáo ghi `full_source_bytes`, `compact_source_bytes`, `prompt_bytes` và `omitted_base_paths`.

Nếu reviewer yêu cầu sửa, phần hướng dẫn chuyển lại cho worker cũng là context ngắn: tối đa 3.500 ký tự, không chứa thông tin nhạy cảm. Quá dài, có dấu hiệu bí mật hoặc sai định dạng thì dừng để Lead xử lý, không tự cắt nội dung và không gọi thêm worker.

Trước `prepare`, Lead chép các mục sau vào ghi chú/checklist chuẩn bị tác vụ ở ngoài đường dẫn output mà `prepare` sẽ tạo. Đánh dấu `[x]` sau khi ghi hành động, kết quả hoặc giới hạn kiểm tra bên cạnh mục tương ứng; kèm ghi chú này vào hồ sơ tác vụ sau `prepare`:

- [ ] Hành động và kết quả quan sát được khớp yêu cầu Owner.
- [ ] Với việc giao diện mới, mẫu đúng làm mọi gate PASS và Product Check trả đủ `evidence`; ít nhất một gate bắt mẫu sai. `prepare` xác nhận trước khi tạo packet, không tốn lượt AI.
- [ ] Phép kiểm tra bắt được một kết quả sai có thể xảy ra; với biểu mẫu, thử đầu vào thiếu/sai khi phù hợp.
- [ ] Không tự thêm điều kiện đúng từng chữ hoặc dấu câu mà Owner chưa yêu cầu.
- [ ] Nếu không kiểm tra tự động được, ghi bước kiểm tra tay và giới hạn của nó.
- [ ] Với nút/trang trong trình duyệt, chờ trang đích và phần tử cần xem hiện xong rồi mới kết luận thiếu; thử trường hợp tải chậm nếu liên quan. Không dùng một lần `isVisible()` ngay sau khi bấm làm bằng chứng duy nhất.
- [ ] Với giao diện dùng CSS/ảnh từ mạng, xem trang ở kích thước điện thoại và máy tính khi tài nguyên đó không tải; kiểm bố cục dễ đọc, không chỉ kiểm “không tràn ngang”.
- [ ] Nội dung mô tả hành vi sản phẩm lấy từ yêu cầu Owner hoặc nguồn có sẵn; phần chưa rõ viết chung, không tự thêm luật chơi.

Sau `run`, nếu gate báo lỗi nhưng nguồn có đúng phần bị báo thiếu, Lead đối chiếu ảnh/trình duyệt và mã gate trước khi coi đó là lỗi Gemini. Khi chưa phân biệt được, giữ `BLOCKED` và hồ sơ hiện tại; không gửi Gemini sửa theo một kết quả kiểm tra chưa xác minh. Trước khi báo Owner nghiệm thu giao diện, Lead xem ảnh thực tế của mã cuối và ghi giới hạn còn lại.

## Cú pháp PowerShell

`status` và `accept` trả thêm `owner_message` tiếng Việt, giữ nguyên `status`. `COMPLETED` là hoàn tất trong vùng riêng, chưa xác nhận đưa vào dự án chính. Lead ghi commit đích vào checklist sau khi được phép gộp và xác minh.

Đứng tại thư mục mã nguồn Trạm Điều Phối AI. Các biến bên dưới do Điều phối điền bằng đường dẫn thực tế; Owner không phải tự sửa lệnh. Hai file đầu vào dùng đúng schema đang có, không thêm schema yêu cầu mới.

```powershell
node scripts/direct.mjs prepare $repo $taskJson $configJson $newOutput
# Chỉ khi cần chẩn đoán: node scripts/direct.mjs check "$newOutput/prepared.json"
node scripts/direct.mjs run "$newOutput/prepared.json"
# Chỉ khi cần xem lại trạng thái: node scripts/direct.mjs status "$newOutput/prepared.json"
node scripts/direct.mjs verify-product "$newOutput/prepared.json" # Chỉ khi trạng thái PRODUCT_CHECK_WAIT
# Chỉ sau xác nhận nghiệm thu của Owner:
node scripts/direct.mjs accept "$newOutput/prepared.json" Owner
```

Mỗi lần chạy tạo báo cáo UUID riêng và một `dispatch.json` chống chạy lặp. Báo cáo chứa model/session, gate, số dispatch có bằng chứng, cleanup, vị trí evidence và các bộ đếm token do provider thật sự trả về; trường nào provider không trả thì để null/unavailable. Số lần gọi nội bộ provider không biết thì ghi unknown, không suy đoán token tiết kiệm từ kích thước prompt. Báo cáo cũng ghi kích thước nguồn đầy đủ và bản rút gọn gửi Luna để có thể đối chiếu mức giảm dữ liệu. Tổng dispatch tối đa không bao gồm lượt review mã công cụ, phải ghi hai ngân sách riêng.
Trong báo cáo, `attempts`/`worker_attempts_completed` là tổng lượt Gemini, `repairs` là số lượt Gemini đã sửa sau lượt đầu, còn `change_requests` là số lần Luna yêu cầu sửa; yêu cầu cuối có thể chạm trần mà không tạo thêm lượt Gemini.

Chỉ một tác vụ được ghi quyền tài khoản trên máy tại một thời điểm. Đóng cưỡng bức có thể bỏ qua cleanup: giữ packet, xác minh worker đã dừng, đối chiếu đúng quyền tạm; không ghi đè toàn bộ settings hoặc tự chạy lại. Không sửa thư mục RESTORE. Các runner R2/WEB cũ là bằng chứng lịch sử, không phải điểm chạy mặc định.

Nếu Codex Luna Max reviewer không gọi được, Điều phối ghi lý do và dừng ở trạng thái chờ hoặc bị chặn; không đổi sang reviewer khác và không tự chạy lại.

Các AI có thể dùng tiếng Anh trong prompt, phát hiện kỹ thuật và bàn giao cho nhau. Điều phối phải trình bày kết quả, trở ngại và bước tiếp theo cho Owner bằng tiếng Việt dễ hiểu; giữ nguyên tên model, mã lỗi, file, lệnh và khóa dữ liệu.
