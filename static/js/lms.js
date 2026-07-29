        function openLmsModal() {
            document.getElementById('lmsModal').classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('lmsModal').firstElementChild.classList.remove('scale-95');
                document.getElementById('lmsModal').firstElementChild.classList.add('scale-100');
            }, 10);
            resetLmsModal();
            // Lắng nghe sự kiện paste trên toàn document
            document.onpaste = handleLmsPaste;
            
            // Xử lý kéo thả bằng JS sau khi modal mở
            const dropzone = document.getElementById('lmsDropzone');
            if (dropzone) {
                dropzone.ondragover = (e) => {
                    e.preventDefault();
                    dropzone.classList.add('border-purple-500', 'bg-purple-500/10');
                };
                dropzone.ondragleave = () => {
                    dropzone.classList.remove('border-purple-500', 'bg-purple-500/10');
                };
                dropzone.ondrop = (e) => {
                    e.preventDefault();
                    dropzone.classList.remove('border-purple-500', 'bg-purple-500/10');
                    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                        handleLmsFile(e.dataTransfer.files[0]);
                    }
                };
            }
        }

        function closeLmsModal() {
            document.getElementById('lmsModal').firstElementChild.classList.remove('scale-100');
            document.getElementById('lmsModal').firstElementChild.classList.add('scale-95');
            setTimeout(() => {
                document.getElementById('lmsModal').classList.add('hidden');
            }, 180);
            document.onpaste = null;
        }

        function resetLmsModal() {
            lmsBase64Image = null;
            lmsUploadId = null;
            lmsAnalysisJobId = null;
            lmsConfirmationToken = null;
            lmsPreviewPlan = null;
            document.getElementById('lmsFileInput').value = '';
            document.getElementById('lmsDropzoneText').classList.remove('hidden');
            document.getElementById('lmsImagePreview').classList.add('hidden');
            document.getElementById('lmsImagePreview').src = '';
            document.getElementById('lmsUploadStep').classList.remove('hidden');
            document.getElementById('lmsReviewStep').classList.add('hidden');
            document.getElementById('btnStartLms').classList.remove('hidden');
            document.getElementById('btnStartLms').disabled = true;
            document.getElementById('btnStartLms').textContent = 'Phân tích ảnh';
            document.getElementById('btnBackLms').classList.add('hidden');
            document.getElementById('btnConfirmLms').classList.add('hidden');
            document.getElementById('btnConfirmLms').disabled = true;
        }

        function setLmsAnalysisBusy(isBusy) {
            const button = document.getElementById('btnStartLms');
            button.disabled = isBusy || !lmsBase64Image;
            button.textContent = isBusy ? 'Đang phân tích...' : 'Phân tích ảnh';
        }



        function handleLmsPaste(e) {
            if (e.clipboardData && e.clipboardData.items) {
                for (let i = 0; i < e.clipboardData.items.length; i++) {
                    if (e.clipboardData.items[i].type.indexOf('image') !== -1) {
                        const file = e.clipboardData.items[i].getAsFile();
                        handleLmsFile(file);
                        break;
                    }
                }
            }
        }

        function handleLmsFile(file) {
            if (!validateClientImage(file)) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                lmsBase64Image = e.target.result;
                lmsUploadId = null;
                const textElem = document.getElementById('lmsDropzoneText');
                if(textElem) textElem.classList.add('hidden');
                
                const preview = document.getElementById('lmsImagePreview');
                if(preview) {
                    preview.src = lmsBase64Image;
                    preview.classList.remove('hidden');
                }
                
                const btn = document.getElementById('btnStartLms');
                if(btn) btn.disabled = false;
            };
            reader.onerror = function() {
                alert("Lỗi đọc file ảnh!");
            };
            reader.readAsDataURL(file);
        }

        async function startLmsAnalysis() {
            if (!lmsBase64Image) return;
            if (!canStartAnotherJob()) return;
            setLmsAnalysisBusy(true);
            document.getElementById('status-text').innerText = "Đang phân tích và lập kế hoạch LMS...";
            document.getElementById('status-dot').className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-purple-500 shadow-[0_0_8px_#a855f7]";

            try {
                if (!lmsUploadId) {
                    const upload = await createRuntimeUpload(lmsBase64Image, 'lms_analysis');
                    lmsUploadId = upload.upload_id;
                }
                const res = await fetch('/api/lms/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upload_id: lmsUploadId })
                });
                const data = await res.json();
                if (!res.ok || data.status !== 'success' || !data.job_id) {
                    throw new Error(data.detail || data.message || 'Không tạo được job LMS.');
                }
                trackJob(data.job_id);
            } catch (err) {
                console.error(err);
                setLmsAnalysisBusy(false);
                alert("Không thể phân tích ảnh: " + err.message);
            }
        }

        async function confirmLmsTick() {
            if (!lmsAnalysisJobId || !lmsConfirmationToken || !lmsPreviewPlan) return;
            if (!canStartAnotherJob()) return;
            const count = lmsPreviewPlan.classes_to_tick.length;
            const action = lmsPreviewPlan.dry_run ? 'mô phỏng' : 'tick thật';
            if (!confirm(`Xác nhận ${action} ${count} lớp cho tuần ${lmsPreviewPlan.week}/${lmsPreviewPlan.year}?`)) return;

            const button = document.getElementById('btnConfirmLms');
            button.disabled = true;
            button.textContent = 'Đang xác nhận...';
            try {
                const res = await fetch('/api/lms/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        analysis_job_id: lmsAnalysisJobId,
                        confirmation_token: lmsConfirmationToken
                    })
                });
                const data = await res.json();
                if (!res.ok || data.status !== 'success' || !data.job_id) {
                    throw new Error(data.detail || data.message || 'Không xác nhận được kế hoạch.');
                }
                trackJob(data.job_id);
                closeLmsModal();
            } catch (err) {
                button.disabled = false;
                button.textContent = 'Xác nhận kế hoạch';
                alert("Xác nhận thất bại: " + err.message + " Hãy phân tích lại ảnh.");
            }
        }
