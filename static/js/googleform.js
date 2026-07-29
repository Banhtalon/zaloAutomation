        const pasteArea = document.getElementById('paste-area');
        const imgPreview = document.getElementById('image-preview');
        const placeholder = document.getElementById('upload-placeholder');
        const overlay = document.getElementById('preview-overlay');

        function setPreviewImage(base64) {
            imgPreview.src = base64;
            imgPreview.classList.remove('hidden');
            overlay.classList.remove('hidden');
            overlay.classList.add('flex');
            placeholder.classList.add('hidden');
            setTimeout(() => { imgPreview.classList.remove('opacity-0'); }, 10);
        }

        pasteArea.addEventListener('paste', async (e) => {
            e.preventDefault();
            for (let item of e.clipboardData.items) {
                if (item.type.indexOf('image') !== -1) {
                    const file = item.getAsFile();
                    if (!validateClientImage(file)) return;
                    const reader = new FileReader();
                    reader.onload = async (event) => {
                        setPreviewImage(event.target.result);
                        await uploadImage(event.target.result);
                    };
                    reader.readAsDataURL(file);
                }
            }
        });

        async function handleFileUpload(e) {
            if (e.target.files[0] && validateClientImage(e.target.files[0])) {
                const reader = new FileReader();
                reader.onload = async (event) => {
                    setPreviewImage(event.target.result);
                    await uploadImage(event.target.result);
                };
                reader.readAsDataURL(e.target.files[0]);
            }
        }

        function validateClientImage(file) {
            const allowed = new Set(['image/png', 'image/jpeg', 'image/webp']);
            if (!file || !allowed.has(file.type)) {
                alert('Chỉ hỗ trợ PNG, JPEG hoặc WEBP.');
                return false;
            }
            if (file.size > 8 * 1024 * 1024) {
                alert('Ảnh vượt quá giới hạn 8 MB.');
                return false;
            }
            return true;
        }

        async function createRuntimeUpload(base64Data, purpose) {
            const response = await fetch('/api/uploads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_data: base64Data, purpose })
            });
            const data = await response.json();
            if (!response.ok || !data.upload_id) {
                throw new Error(data.detail || data.error || 'Không thể tải ảnh.');
            }
            return data;
        }

        async function uploadImage(base64Data) {
            googleformUploadInFlight += 1;
            setOcrBusy(ocrBusy);
            try {
                const data = await createRuntimeUpload(base64Data, 'googleform_ocr');
                googleformUploadIds.push(data.upload_id);
                googleformBatchId = null;
            } catch (err) {
                alert("Lỗi tải ảnh: " + err.message);
            } finally {
                googleformUploadInFlight -= 1;
                setOcrBusy(ocrBusy);
            }
        }

        let ocrStudents = [];

        function setOcrBusy(isBusy) {
            ocrBusy = isBusy;
            const button = document.getElementById('btnRunOcr');
            button.disabled = isBusy || googleformUploadInFlight > 0 || googleformUploadIds.length === 0;
            button.textContent = isBusy ? 'Đang quét AI...' : 'Quét AI (Gemini Vision)';
        }

        function setOcrMessage(message, isError = false) {
            const tbody = document.getElementById('ocr-results-table');
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 3;
            td.className = `px-5 py-8 text-center ${isError ? 'text-rose-400' : 'text-gray-500'}`;
            td.textContent = message;
            tr.appendChild(td);
            tbody.replaceChildren(tr);
        }

        async function runOCR() {
            if (!canStartAnotherJob() || googleformUploadIds.length === 0) return;
            setOcrBusy(true);
            setOcrMessage('Đang xử lý phân tích hình ảnh...');
            try {
                const pendingUploadIds = [...googleformUploadIds];
                const res = await fetch('/api/googleform/run-ocr', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upload_ids: pendingUploadIds })
                });
                const data = await res.json();
                if (!res.ok || !data.job_id) throw new Error(data.detail || data.error || 'Không tạo được job OCR.');
                googleformUploadIds = [];
                trackJob(data.job_id);
            } catch (err) {
                setOcrBusy(false);
                setOcrMessage(err.message || 'Lỗi kết nối máy chủ!', true);
            }
        }

        function renderOCRTable() {
            const tbody = document.getElementById('ocr-results-table');
            tbody.replaceChildren();
            if (ocrStudents.length === 0) {
                setOcrMessage('Không trích xuất được học viên nào.');
                document.getElementById('btnSubmitGoogleForm').disabled = true;
                return;
            }
            ocrStudents.forEach((student, index) => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-white/5 transition-colors';
                ['name', 'class_code'].forEach(field => {
                    const td = document.createElement('td');
                    td.className = 'px-5 py-3 text-gray-200';
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.value = String(student[field] || '');
                    input.className = 'bg-black/30 border border-transparent focus:border-brand-blue rounded px-2 py-1 outline-none w-full text-sm';
                    input.addEventListener('change', event => {
                        ocrStudents[index][field] = event.target.value;
                    });
                    td.appendChild(input);
                    tr.appendChild(td);
                });
                const action = document.createElement('td');
                action.className = 'px-5 py-3 text-center';
                const remove = document.createElement('button');
                remove.className = 'text-rose-500 hover:text-white hover:bg-rose-500 w-8 h-8 rounded-lg transition-colors';
                remove.textContent = '×';
                remove.addEventListener('click', () => {
                    ocrStudents.splice(index, 1);
                    renderOCRTable();
                });
                action.appendChild(remove);
                tr.appendChild(action);
                tbody.appendChild(tr);
            });
            document.getElementById('btnSubmitGoogleForm').disabled = false;
        }

        async function fetchMappings() {
            try {
                const res = await fetch('/api/googleform/mappings');
                const data = await res.json();
                const tbody = document.getElementById('mapping-table');
                if (data.mappings && data.mappings.length > 0) {
                    tbody.replaceChildren();
                    data.mappings.forEach(mapping => {
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-white/5 transition-colors group';
                        const question = document.createElement('td');
                        question.className = 'px-5 py-3.5 text-gray-300 font-medium';
                        question.textContent = String(mapping.question || '');
                        const type = document.createElement('td');
                        type.className = 'px-5 py-3.5 text-xs text-gray-300';
                        type.textContent = mapping.field_type === 'radio' ? 'Trắc nghiệm' : 'Văn bản';
                        const value = document.createElement('td');
                        value.className = 'px-5 py-3.5 text-emerald-400 font-mono text-[13px]';
                        value.textContent = String(mapping.value || '');
                        const action = document.createElement('td');
                        action.className = 'px-5 py-3.5 text-center';
                        const remove = document.createElement('button');
                        remove.className = 'text-gray-500 hover:text-rose-400 transition-colors';
                        remove.textContent = 'Xóa';
                        remove.addEventListener('click', () => deleteMapping(Number(mapping.id)));
                        action.appendChild(remove);
                        tr.append(question, type, value, action);
                        tbody.appendChild(tr);
                    });
                } else {
                    renderTableMessage(tbody, 4, 'Chưa có cấu hình mapping nào.');
                }
            } catch (err) {}
        }

        async function addMapping() {
            const q = document.getElementById('map-question').value;
            const t = document.getElementById('map-type').value;
            const v = document.getElementById('map-value').value;
            if (!q || !v) return alert("Vui lòng điền đủ câu hỏi và giá trị!");
            try {
                await fetch('/api/googleform/mappings', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question: q, field_type: t, value: v })
                });
                document.getElementById('map-question').value = '';
                document.getElementById('map-value').value = '';
                fetchMappings();
            } catch (err) { alert("Lỗi lưu cấu hình"); }
        }

        async function deleteMapping(id) {
            try { await fetch('/api/googleform/mappings/' + id, { method: 'DELETE' }); fetchMappings(); } catch (err) {}
        }

        async function fetchGoogleFormHistory() {
            try {
                const res = await fetch('/api/googleform/history');
                const data = await res.json();
                const tbody = document.getElementById('form-history-table');
                if (data.history && data.history.length > 0) {
                    tbody.replaceChildren();
                    data.history.forEach(history => {
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-white/5 transition-colors';
                        const time = document.createElement('td');
                        time.className = 'px-5 py-3 text-[11px] text-gray-500 font-mono';
                        time.textContent = String(history.submitted_at || '');
                        const student = document.createElement('td');
                        student.className = 'px-5 py-3 text-sm text-gray-200';
                        student.textContent = `${history.student_name || ''} (${history.class_code || ''})`;
                        const status = document.createElement('td');
                        const isSuccess = String(history.status || '').includes('Thành công');
                        status.className = `px-5 py-3 text-xs font-medium ${isSuccess ? 'text-emerald-400' : 'text-rose-400'}`;
                        const statusLabel = document.createElement('div');
                        statusLabel.textContent = String(history.status || '');
                        status.appendChild(statusLabel);
                        if (history.error_message) {
                            const errorMessage = document.createElement('div');
                            errorMessage.className = 'mt-1 whitespace-pre-wrap break-words text-[11px] font-normal leading-relaxed text-rose-500';
                            errorMessage.textContent = String(history.error_message);
                            status.appendChild(errorMessage);
                        }
                        tr.append(time, student, status);
                        tbody.appendChild(tr);
                    });
                } else {
                    renderTableMessage(tbody, 3, 'Chưa có lịch sử nộp.');
                }
            } catch (err) {}
        }

        async function submitGoogleForm() {
            if (ocrStudents.length === 0) return alert("Danh sách học viên trống! Hãy quét AI trước.");
            if (!canStartAnotherJob()) return;
            if (!confirm(`Sẽ nộp form cho ${ocrStudents.length} học viên. Bắt đầu ngay?`)) return;
            try {
                const res = await fetch('/api/googleform/submit', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        students: ocrStudents,
                        idempotency_key: googleformBatchId
                    })
                });
                let data = {};
                try {
                    data = await res.json();
                } catch (parseError) {
                    data = {};
                }
                if (!res.ok) {
                    throw new Error(
                        data.detail || data.error || data.message ||
                        `Máy chủ từ chối yêu cầu (HTTP ${res.status}).`
                    );
                }
                if (!data.job_id) {
                    throw new Error(data.detail || data.error || 'Máy chủ không trả về mã job.');
                }
                trackJob(data.job_id);
                alert(data.message || 'Đã bắt đầu xử lý Google Form.');
            } catch (err) {
                alert(`Không thể bắt đầu nộp Google Form: ${err.message || 'Lỗi kết nối máy chủ.'}`);
            }
        }
