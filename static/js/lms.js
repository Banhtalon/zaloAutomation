        function openLmsModal() {
            document.getElementById('lmsModal').classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('lmsModal').firstElementChild.classList.remove('scale-95');
                document.getElementById('lmsModal').firstElementChild.classList.add('scale-100');
            }, 10);
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) {
                return;
            }
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

        function closeLmsModal(options = {}) {
            const force = Boolean(options && options.force);
            if (!force && typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) {
                return;
            }
            document.getElementById('lmsModal').firstElementChild.classList.remove('scale-100');
            document.getElementById('lmsModal').firstElementChild.classList.add('scale-95');
            setTimeout(() => {
                document.getElementById('lmsModal').classList.add('hidden');
            }, 180);
            document.onpaste = null;
        }

        function resetLmsModal() {
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) {
                return;
            }
            lmsBase64Image = null;
            lmsUploadId = null;
            lmsAnalysisJobId = null;
            lmsConfirmationToken = null;
            lmsPreviewPlan = null;
            lmsSelectedClassesToTick = [];
            lmsConfirmInFlight = false;
            document.getElementById('lmsFileInput').value = '';
            document.getElementById('lmsDropzoneText').classList.remove('hidden');
            document.getElementById('lmsImagePreview').classList.add('hidden');
            document.getElementById('lmsImagePreview').src = '';
            document.getElementById('lmsUploadStep').classList.remove('hidden');
            document.getElementById('lmsReviewStep').classList.add('hidden');
            document.getElementById('btnStartLms').classList.remove('hidden');
            document.getElementById('btnStartLms').disabled = false;
            document.getElementById('btnStartLms').textContent = 'Quét LMS & Lập kế hoạch';
            document.getElementById('btnBackLms').classList.add('hidden');
            document.getElementById('btnBackLms').disabled = false;
            document.getElementById('btnConfirmLms').classList.add('hidden');
            document.getElementById('btnConfirmLms').disabled = true;
            document.getElementById('btnConfirmLms').textContent = 'Xác nhận kế hoạch';
        }

        function setLmsAnalysisBusy(isBusy) {
            const button = document.getElementById('btnStartLms');
            button.disabled = isBusy;
            button.textContent = isBusy ? 'Đang quét LMS & BCNB...' : 'Quét LMS & Lập kế hoạch';
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
            if (!canStartAnotherJob()) return;
            setLmsAnalysisBusy(true);
            document.getElementById('status-text').innerText = "Đang quét trang LMS và lập kế hoạch BCNB...";
            document.getElementById('status-dot').className = "relative inline-flex rounded-full h-2.5 w-2.5 bg-purple-500 shadow-[0_0_8px_#a855f7]";

            try {
                let requestPayload = {};
                if (lmsBase64Image) {
                    if (!lmsUploadId) {
                        const upload = await createRuntimeUpload(lmsBase64Image, 'lms_analysis');
                        lmsUploadId = upload.upload_id;
                    }
                    requestPayload = { upload_id: lmsUploadId };
                }
                const res = await fetch('/api/lms/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestPayload)
                });
                const data = await res.json();
                if (!res.ok || data.status !== 'success' || !data.job_id) {
                    throw new Error(data.detail || data.message || 'Không tạo được job LMS.');
                }
                trackJob(data.job_id);
            } catch (err) {
                console.error(err);
                setLmsAnalysisBusy(false);
                alert("Không thể quét LMS: " + err.message);
            }
        }

        async function confirmLmsTick() {
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) return;
            if (!lmsAnalysisJobId || !lmsConfirmationToken || !lmsPreviewPlan) return;
            if (!canStartAnotherJob()) return;
            const selected = typeof getSelectedLmsTickClasses === 'function'
                ? getSelectedLmsTickClasses()
                : (Array.isArray(lmsPreviewPlan.classes_to_tick) ? lmsPreviewPlan.classes_to_tick : []);
            if (!Array.isArray(selected) || selected.length === 0) {
                alert('Hãy chọn ít nhất 1 lớp để thực hiện tick BCNB.');
                return;
            }
            const count = selected.length;
            const action = lmsPreviewPlan.dry_run ? 'mô phỏng' : 'tick thật';
            if (!confirm(`Xác nhận ${action} ${count} lớp cho tuần ${lmsPreviewPlan.week}/${lmsPreviewPlan.year}?`)) return;

            if (typeof setLmsConfirmBusy === 'function') {
                setLmsConfirmBusy(true);
            } else {
                lmsConfirmInFlight = true;
                const button = document.getElementById('btnConfirmLms');
                button.disabled = true;
                button.textContent = 'Đang xác nhận...';
            }
            try {
                const res = await fetch('/api/lms/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        analysis_job_id: lmsAnalysisJobId,
                        confirmation_token: lmsConfirmationToken,
                        selected_classes_to_tick: selected,
                    })
                });
                const data = await res.json();
                if (!res.ok || data.status !== 'success' || !data.job_id) {
                    const detail = data && data.detail;
                    const errMessage = detail && typeof detail === 'object'
                        ? (detail.message || detail.code)
                        : (detail || (data && data.message) || 'Không xác nhận được kế hoạch.');
                    throw new Error(errMessage);
                }
                if (typeof setLmsConfirmBusy === 'function') {
                    setLmsConfirmBusy(false);
                } else {
                    lmsConfirmInFlight = false;
                }
                trackJob(data.job_id);
                closeLmsModal();
            } catch (err) {
                if (typeof setLmsConfirmBusy === 'function') {
                    setLmsConfirmBusy(false);
                } else {
                    lmsConfirmInFlight = false;
                    const button = document.getElementById('btnConfirmLms');
                    button.disabled = false;
                    button.textContent = 'Xác nhận kế hoạch';
                }
                alert("Xác nhận thất bại: " + err.message + " Hãy phân tích lại ảnh.");
            }
        }

        function buildLmsOverdueReminderText(plan) {
            const missing = plan && Array.isArray(plan.missing_classes)
                ? plan.missing_classes.map(code => String(code || '').trim()).filter(Boolean)
                : [];
            if (missing.length === 0) return '';
            const overdueDetails = plan && plan.overdue_details && typeof plan.overdue_details === 'object'
                ? plan.overdue_details
                : {};
            const periodLabel = plan && plan.week && plan.year
                ? `Tuần ${plan.week}/${plan.year}`
                : 'Tuần hiện tại';
            return [
                `📢 Danh sách lớp quá hạn nhận xét LMS (${periodLabel} - ${missing.length} lớp):`,
                ...missing.map(code => {
                    const detail = String(overdueDetails[code] || '').trim();
                    return detail ? `- ${code} (${detail})` : `- ${code}`;
                }),
            ].join('\n');
        }

        async function copyLmsMissingClasses() {
            const text = buildLmsOverdueReminderText(lmsPreviewPlan);
            if (!text) {
                alert('Không có lớp nào quá hạn nhận xét LMS để copy.');
                return;
            }
            try {
                await navigator.clipboard.writeText(text);
                const count = lmsPreviewPlan && Array.isArray(lmsPreviewPlan.missing_classes)
                    ? lmsPreviewPlan.missing_classes.map(c => String(c || '').trim()).filter(Boolean).length
                    : 0;
                alert(`Đã copy danh sách ${count} lớp quá hạn LMS!`);
            } catch (err) {
                alert('Không thể copy vào clipboard: ' + (err && err.message ? err.message : String(err)));
            }
        }
