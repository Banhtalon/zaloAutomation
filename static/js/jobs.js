        function copyLogs() {
            const el = document.getElementById('full-log-terminal');
            navigator.clipboard.writeText(el.innerText);
            alert("Đã copy toàn bộ nhật ký hệ thống!");
        }

        function parseJobResults(raw) {
            if (!raw) return null;
            if (typeof raw === 'object') return raw;
            try { return JSON.parse(raw); } catch (e) { return null; }
        }

        function renderJobControls(job) {
            const meta = document.getElementById('job-meta');
            const cancelButton = document.getElementById('job-cancel-button');
            const retryButton = document.getElementById('job-retry-button');
            const heartbeat = Number.isInteger(job.heartbeat_age_seconds)
                ? ` · heartbeat ${job.heartbeat_age_seconds}s`
                : '';
            meta.textContent = `#${job.id} · lần ${job.attempt || 1}${heartbeat}`;
            meta.classList.remove('hidden');
            meta.classList.toggle('text-rose-400', Boolean(job.heartbeat_stale));
            cancelButton.classList.toggle('hidden', !job.cancel_allowed);
            cancelButton.disabled = Boolean(job.cancel_requested);
            const googleFormRetryAllowed = (
                job.job_type !== 'GOOGLEFORM_SUBMIT' ||
                job.status === 'FAILED' ||
                job.status === 'PARTIAL_SUCCESS'
            );
            retryButton.classList.toggle(
                'hidden',
                !(terminalStatuses.has(job.status) && job.retryable && googleFormRetryAllowed)
            );
            retryButton.disabled = false;
        }

        async function cancelCurrentJob() {
            if (!currentJobId) return;
            const button = document.getElementById('job-cancel-button');
            button.disabled = true;
            try {
                const response = await fetch(`/api/jobs/${currentJobId}/cancel`, { method: 'POST' });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Không thể hủy job.');
                document.getElementById('status-text').textContent =
                    data.status === 'CANCELLED'
                        ? 'Job đã được hủy.'
                        : 'Đã yêu cầu hủy; đang chờ điểm dừng an toàn.';
            } catch (error) {
                button.disabled = false;
                alert(error.message);
            }
        }

        async function retryCurrentJob() {
            if (!currentJobId || currentJobActive) return;
            const button = document.getElementById('job-retry-button');
            button.disabled = true;
            try {
                const response = await fetch(`/api/jobs/${currentJobId}/retry`, { method: 'POST' });
                const data = await response.json();
                if (!response.ok || !data.job_id) throw new Error(data.detail || 'Không thể retry job.');
                trackJob(data.job_id);
            } catch (error) {
                button.disabled = false;
                alert(error.message);
            }
        }

        async function renderJobTimeline(jobId) {
            const container = document.getElementById('job-timeline');
            if (!container || !jobId) return;
            try {
                const [eventsResponse, auditResponse] = await Promise.all([
                    fetch(`/api/jobs/${jobId}/events?limit=60`),
                    fetch(`/api/jobs/${jobId}/audit?limit=60`)
                ]);
                if (!eventsResponse.ok || !auditResponse.ok) return;
                const events = (await eventsResponse.json()).events || [];
                const actions = (await auditResponse.json()).actions || [];
                const items = [
                    ...events.map(item => ({
                        time: item.created_at || '',
                        text: `${item.event_type}: ${item.message || item.new_status || ''}`,
                        tone: item.new_status === 'FAILED' ? 'text-rose-400' : 'text-gray-300'
                    })),
                    ...actions.map(item => ({
                        time: item.created_at || '',
                        text: `AUDIT ${item.action_type} ${item.target_id}: ${item.outcome}`,
                        tone: item.outcome === 'FAILED' ? 'text-rose-400' : 'text-emerald-300'
                    }))
                ].sort((a, b) => String(b.time).localeCompare(String(a.time))).slice(0, 100);
                container.replaceChildren();
                if (!items.length) {
                    const empty = document.createElement('div');
                    empty.className = 'text-gray-500';
                    empty.textContent = 'Chưa có event/audit.';
                    container.appendChild(empty);
                    return;
                }
                items.forEach(item => {
                    const line = document.createElement('div');
                    line.className = item.tone;
                    line.textContent = `${item.time} · ${item.text}`;
                    container.appendChild(line);
                });
            } catch (error) {
                console.error('Lỗi tải timeline job:', error);
            }
        }

        function renderCodeList(elementId, codes) {
            const container = document.getElementById(elementId);
            container.replaceChildren();
            (codes || []).forEach(code => {
                const line = document.createElement('div');
                line.textContent = code;
                container.appendChild(line);
            });
            if (!codes || codes.length === 0) {
                const line = document.createElement('div');
                line.className = 'text-gray-500';
                line.textContent = 'Không có';
                container.appendChild(line);
            }
        }

        function showLmsReview(job, plan) {
            lmsAnalysisJobId = job.id;
            lmsConfirmationToken = plan.confirmation_token || null;
            lmsPreviewPlan = plan;
            const modal = document.getElementById('lmsModal');
            modal.classList.remove('hidden');
            modal.firstElementChild.classList.remove('scale-95');
            modal.firstElementChild.classList.add('scale-100');
            document.getElementById('lmsUploadStep').classList.add('hidden');
            document.getElementById('lmsReviewStep').classList.remove('hidden');
            document.getElementById('btnStartLms').classList.add('hidden');
            document.getElementById('btnBackLms').classList.remove('hidden');
            document.getElementById('btnConfirmLms').classList.remove('hidden');
            document.getElementById('lmsReviewWeek').textContent = `${plan.week}/${plan.year}`;
            document.getElementById('lmsDryRunNotice').classList.toggle('hidden', !plan.dry_run);
            renderCodeList('lmsMissingClasses', plan.missing_classes);
            renderCodeList('lmsTickClasses', plan.classes_to_tick);

            const warning = document.getElementById('lmsReviewWarning');
            const issues = Array.isArray(plan.blocking_issues) ? plan.blocking_issues : [];
            warning.textContent = issues.join(' • ');
            warning.classList.toggle('hidden', issues.length === 0);
            document.getElementById('btnConfirmLms').disabled = !(
                plan.can_confirm && lmsConfirmationToken &&
                Array.isArray(plan.classes_to_tick) && plan.classes_to_tick.length > 0
            );
        }

        function formatGoogleFormTerminalMessage(job, payload) {
            const summary = String(job.message || job.status || 'Job Google Form đã kết thúc.');
            if (!payload || payload.kind !== 'googleform_submit' || !Array.isArray(payload.outcomes)) {
                return summary;
            }
            const failures = payload.outcomes.filter(item => item && item.success !== true);
            if (failures.length === 0) return summary;

            const detailLines = failures.map(item => {
                const name = String(item.name || 'Học viên không xác định');
                const classCode = String(item.class_code || '').trim();
                const target = classCode ? `${name} (${classCode})` : name;
                const reason = String(item.error || 'Không có thông tin lỗi từ máy chủ.');
                return `• ${target}: ${reason}`;
            });
            return `${summary}\n\nChi tiết học viên thất bại:\n${detailLines.join('\n')}`;
        }

        function handleTerminalJob(job, payload) {
            if (handledTerminalJobIds.has(job.id)) return;
            handledTerminalJobIds.add(job.id);
            if (handledTerminalJobIds.size > 200) {
                handledTerminalJobIds.delete(handledTerminalJobIds.values().next().value);
            }

            if (job.job_type === 'GOOGLEFORM_OCR') {
                setOcrBusy(false);
                if (job.status === 'SUCCESS' && payload && Array.isArray(payload.students)) {
                    ocrStudents = payload.students.map(item => ({
                        name: String(item.name || ''),
                        class_code: String(item.class_code || '')
                    }));
                    googleformBatchId = crypto.randomUUID
                        ? crypto.randomUUID()
                        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
                    renderOCRTable();
                } else {
                    setOcrMessage(
                        job.status === 'CANCELLED' ? 'OCR đã được hủy.' : (job.message || 'OCR thất bại.'),
                        job.status !== 'CANCELLED'
                    );
                }
            } else if (job.job_type === 'LMS_ANALYSIS') {
                setLmsAnalysisBusy(false);
                if (payload && payload.kind === 'lms_preview') {
                    showLmsReview(job, payload);
                } else {
                    alert(job.status === 'CANCELLED' ? 'Phân tích LMS đã được hủy.' : (job.message || 'Phân tích LMS thất bại và đã dừng an toàn.'));
                }
            } else if (job.job_type === 'LMS_AUTOMATION') {
                alert(job.message || job.status);
            } else if (job.job_type === 'GOOGLEFORM_SUBMIT') {
                fetchGoogleFormHistory();
                const terminalMessage = formatGoogleFormTerminalMessage(job, payload);
                document.getElementById('status-text').textContent = terminalMessage;
                alert(terminalMessage);
            }
        }

        function stopJobPolling() {
            if (jobPollingTimer === null) return;
            clearTimeout(jobPollingTimer);
            jobPollingTimer = null;
        }

        function scheduleJobPolling(delay = JOB_POLL_INTERVAL_MS) {
            if (!currentJobActive || jobPollingTimer !== null) return;
            jobPollingTimer = setTimeout(async () => {
                jobPollingTimer = null;
                if (!currentJobActive) return;
                await updateDashboardData();
            }, delay);
        }

        function restartJobPolling() {
            stopJobPolling();
            if (currentJobActive) scheduleJobPolling(0);
        }

        // DASHBOARD POLLING (2s)
        async function updateDashboardData() {
            try {
                const url = currentJobId ? `/api/jobs/${currentJobId}` : '/api/jobs/latest';
                const resResults = await fetch(url);
                if (!resResults.ok) throw new Error(`Không thể tải trạng thái job (HTTP ${resResults.status}).`);
                const job = await resResults.json();
                if (job) {
                    if (!currentJobId) currentJobId = job.id;
                    const payload = parseJobResults(job.results);
                    const isTerminal = terminalStatuses.has(job.status);
                    currentJobActive = !isTerminal;
                    if (isTerminal) stopJobPolling();
                    renderJobControls(job);
                    if (document.getElementById('tab-logs').classList.contains('active')) {
                        renderJobTimeline(job.id);
                    }

                    const statusText = document.getElementById('status-text');
                    const pulseDot = document.getElementById('pulse-dot');
                    const statusDot = document.getElementById('status-dot');
                    statusText.innerText = job.message || job.status;
                    if (isTerminal) {
                        pulseDot.classList.add('hidden');
                        const isError = job.status === 'FAILED' || job.status === 'CANCELLED';
                        statusDot.className = 'relative inline-flex rounded-full h-2.5 w-2.5 ' + (isError ? 'bg-rose-500 shadow-[0_0_8px_#f43f5e]' : 'bg-gray-500');
                        handleTerminalJob(job, payload);
                    } else {
                        pulseDot.classList.remove('hidden');
                        statusDot.className = 'relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500 shadow-[0_0_8px_#3b82f6]';
                    }

                    if (job.job_type === 'ZALO_SCAN' && Array.isArray(payload)) {
                        renderZaloResults(payload);
                    }
                }
            } catch (e) { console.error('Lỗi cập nhật job:', e); }

            // Logs Fetch & Highlight
            try {
                const resLogs = await fetch('/api/logs');
                const dataLogs = await resLogs.json();
                
                if (dataLogs.logs && dataLogs.logs.length > 0) {
                    const fullTerminal = document.getElementById('full-log-terminal');
                    const isScrolledToBottom = fullTerminal.scrollHeight - fullTerminal.clientHeight <= fullTerminal.scrollTop + 10;
                    const fragment = document.createDocumentFragment();
                    dataLogs.logs.forEach(log => {
                        let colorClass = "log-info";
                        if (log.includes("Tick xanh") || log.includes("THÀNH CÔNG") || log.includes("thành công") || log.includes("✅")) colorClass = "log-success";
                        else if (log.includes("Lỗi") || log.includes("LỖI") || log.includes("thất bại") || log.includes("❌")) colorClass = "log-error";
                        else if (log.includes("MIỄN BÁO CÁO") || log.includes("Miễn báo cáo") || log.includes("⚠️")) colorClass = "log-warning";
                        const line = document.createElement('div');
                        line.className = `${colorClass} mb-1`;
                        line.textContent = log;
                        fragment.appendChild(line);
                    });
                    fullTerminal.replaceChildren(fragment);
                    if (isScrolledToBottom) {
                        fullTerminal.scrollTop = fullTerminal.scrollHeight;
                    }
                }
            } catch (e) {}

            if (currentJobActive) {
                scheduleJobPolling();
            } else {
                stopJobPolling();
            }
        }

        function renderZaloResults(resultsArray) {
            if (!resultsArray || resultsArray.length === 0) return;
            let total = resultsArray.length, success = 0, exempt = 0, failed = 0;
            resultsArray.forEach(r => {
                const status = String(r.status || '');
                if (status.includes('Miễn')) exempt++;
                else if (status.includes('✅')) success++;
                else failed++;
            });
            document.getElementById('count-total').innerText = total;
            document.getElementById('count-success').innerText = success;
            document.getElementById('count-exempt').innerText = exempt;
            document.getElementById('count-failed').innerText = failed;

            const tbody = document.getElementById('results-table');
            const searchValue = document.getElementById('searchInput').value.toLowerCase();
            const rows = resultsArray.filter(r => {
                const status = String(r.status || '');
                const isFailed = status.includes('❌') || status.includes('⚠️');
                if (filterUnreported && !isFailed) return false;
                return !searchValue || String(r.group || '').toLowerCase().includes(searchValue) || String(r.person || '').toLowerCase().includes(searchValue);
            });
            tbody.replaceChildren();
            rows.forEach(r => {
                const tr = document.createElement('tr');
                tr.className = 'result-row transition-colors';
                const values = [r.group, r.person, r.timestamp || 'Hôm nay', r.status];
                values.forEach((value, valueIndex) => {
                    const td = document.createElement('td');
                    td.className = 'px-6 py-4';
                    if (valueIndex === 3) {
                        const status = String(value || '');
                        td.classList.add('status-cell');
                        if (status.includes('Miễn')) {
                            td.classList.add('status-cell--exempt');
                        } else if (status.includes('✅')) {
                            td.classList.add('status-cell--success');
                        } else {
                            td.classList.add('status-cell--error');
                        }
                        td.textContent = status;
                        if (r.status_detail) {
                            const detail = document.createElement('div');
                            detail.className = 'status-detail';
                            detail.textContent = String(r.status_detail);
                            td.appendChild(detail);
                        }
                    } else {
                        td.textContent = String(value || '');
                    }
                    tr.appendChild(td);
                });
                const action = document.createElement('td');
                action.className = 'px-6 py-4 text-center';
                const actions = document.createElement('div');
                actions.className = 'evidence-actions';
                const evidence = r.evidence && typeof r.evidence === 'object' ? r.evidence : null;
                const evidenceImages = evidence && evidence.images ? evidence.images : {};
                const imageOptions = [
                    ['report', 'Ảnh báo cáo'],
                    ['latest', 'Tin mới nhất'],
                    ['oldest', 'Mốc cũ nhất'],
                ];
                let imageCount = 0;
                imageOptions.forEach(([key, label]) => {
                    const item = evidenceImages[key];
                    if (!item || !item.path) return;
                    imageCount++;
                    const button = document.createElement('button');
                    button.className = 'table-link';
                    button.textContent = label;
                    button.title = item.sha256 ? `SHA-256: ${item.sha256}` : label;
                    button.addEventListener('click', () => openImageModal(item.path));
                    actions.appendChild(button);
                });
                if (imageCount === 0 && r.screenshot_path) {
                    const legacyButton = document.createElement('button');
                    legacyButton.className = 'table-link';
                    legacyButton.textContent = 'Xem ảnh';
                    legacyButton.addEventListener('click', () => openImageModal(r.screenshot_path));
                    actions.appendChild(legacyButton);
                    imageCount++;
                }
                if (imageCount === 0) {
                    const unavailable = document.createElement('button');
                    unavailable.className = 'table-link is-disabled';
                    unavailable.textContent = 'Chưa có ảnh';
                    unavailable.disabled = true;
                    actions.appendChild(unavailable);
                }
                if (evidence) {
                    const meta = document.createElement('div');
                    meta.className = 'evidence-meta';
                    const oldest = evidence.oldest_message_date || '?';
                    const newest = evidence.newest_message_date || '?';
                    meta.textContent = `${evidence.messages_checked || 0} tin · ${oldest} → ${newest}`;
                    const actualGroup = evidence.actual_group_name || 'Chưa xác minh';
                    meta.title = `Nhóm Zalo: ${actualGroup}\nQuét lúc: ${evidence.scanned_at || '?'}`;
                    actions.appendChild(meta);
                }
                action.appendChild(actions);
                tr.appendChild(action);
                tbody.appendChild(tr);
            });
            if (rows.length === 0) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 5;
                td.className = 'px-6 py-8 text-center text-gray-500';
                td.textContent = 'Không tìm thấy kết quả';
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
        }

        let filterUnreported = false;
        function toggleUnreportedFilter() {
            filterUnreported = !filterUnreported;
            const btn = document.getElementById('btn-filter-unreported');
            if (filterUnreported) {
                btn.classList.add('bg-rose-500/20', 'text-rose-300', 'border-rose-500/40');
                btn.innerHTML = '<i class="fa-solid fa-filter mr-2"></i> Đang lọc: Trễ / Chưa nộp';
            } else {
                btn.classList.remove('bg-rose-500/20', 'text-rose-300', 'border-rose-500/40');
                btn.innerHTML = '<i class="fa-solid fa-filter mr-2"></i> Lọc: Chưa báo cáo';
            }
            updateDashboardData();
        }

        document.getElementById('searchInput').addEventListener('keyup', updateDashboardData);

        // Một lần tải ban đầu để khôi phục job đang chạy sau khi reload.
        // Các lần tiếp theo chỉ được lên lịch khi job thực sự còn hoạt động.
        updateDashboardData();
