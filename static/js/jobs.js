        const MAX_LOG_DOM_LINES = 500;
        const LOG_FETCH_LIMIT = 100;
        const TIMELINE_REFRESH_INTERVAL_MS = 6000;
        let logCursor = 0;
        let lastLegacyLogSignature = '';
        let timelineRequestInFlight = false;
        let nextTimelineRefreshAt = 0;
        let lastResultsSignature = '';

        function copyLogs() {
            const el = document.getElementById('full-log-terminal');
            navigator.clipboard.writeText(el.innerText);
            alert("Đã copy toàn bộ nhật ký hệ thống!");
        }

        function logsTabIsActive() {
            const tab = document.getElementById('tab-logs');
            return Boolean(tab && tab.classList.contains('active'));
        }

        function appendLogLines(lines, { replace = false } = {}) {
            const fullTerminal = document.getElementById('full-log-terminal');
            if (!fullTerminal || !Array.isArray(lines) || lines.length === 0) return;
            const isScrolledToBottom = fullTerminal.scrollHeight - fullTerminal.clientHeight
                <= fullTerminal.scrollTop + 10;
            const fragment = document.createDocumentFragment();
            lines.forEach(log => {
                const text = typeof log === 'string'
                    ? log
                    : `[${String(log.timestamp || '').slice(11, 19)}] ${String(log.message || '')}`;
                let colorClass = "log-info";
                if (text.includes("Tick xanh") || text.includes("THÀNH CÔNG") || text.includes("thành công") || text.includes("✅")) colorClass = "log-success";
                else if (text.includes("Lỗi") || text.includes("LỖI") || text.includes("thất bại") || text.includes("❌")) colorClass = "log-error";
                else if (text.includes("MIỄN BÁO CÁO") || text.includes("Miễn báo cáo") || text.includes("⚠️")) colorClass = "log-warning";
                const line = document.createElement('div');
                line.className = `${colorClass} mb-1`;
                line.textContent = text;
                fragment.appendChild(line);
            });
            if (replace) fullTerminal.replaceChildren(fragment);
            else fullTerminal.appendChild(fragment);
            while (fullTerminal.children.length > MAX_LOG_DOM_LINES) {
                fullTerminal.removeChild(fullTerminal.firstChild);
            }
            if (isScrolledToBottom) fullTerminal.scrollTop = fullTerminal.scrollHeight;
        }

        async function refreshLogs() {
            if (!logsTabIsActive()) return;
            try {
                const response = await fetch(`/api/logs?after=${encodeURIComponent(logCursor)}&limit=${LOG_FETCH_LIMIT}`);
                if (!response.ok) return;
                const data = await response.json();
                const records = Array.isArray(data.records) ? data.records : [];
                const lines = records.length
                    ? records
                    : (Array.isArray(data.logs) ? data.logs : []);
                if (records.length) {
                    const nextCursor = Number(data.cursor);
                    if (Number.isFinite(nextCursor) && nextCursor >= logCursor) logCursor = nextCursor;
                    appendLogLines(lines);
                    return;
                }
                // Compatibility fallback for older servers that return only `logs`.
                const signature = lines.join('\n');
                if (signature && signature !== lastLegacyLogSignature) {
                    lastLegacyLogSignature = signature;
                    appendLogLines(lines, { replace: true });
                }
            } catch (error) {
                // A temporary log refresh failure must not interrupt job polling.
            }
        }

        function parseJobResults(raw) {
            if (!raw) return null;
            if (typeof raw === 'object') return raw;
            try { return JSON.parse(raw); } catch (e) { return null; }
        }

        function zaloResultItems(payload) {
            if (Array.isArray(payload)) return payload;
            return payload && Array.isArray(payload.items) ? payload.items : [];
        }

        function renderZaloTargetProgress(payload) {
            const container = document.getElementById('zalo-target-progress');
            if (!container) return;
            const summary = payload && payload.summary;
            if (!summary || !summary.total) {
                container.classList.add('hidden');
                container.textContent = '';
                return;
            }
            const scan = summary.scan || {};
            const write = summary.write || {};
            const processing = Number(scan.RUNNING || 0) + Number(write.RUNNING || 0);
            const verified = Number(write.VERIFIED || 0);
            const alreadyDone = Number(write.ALREADY_DONE || 0);
            const dryRun = Number(write.DRY_RUN || 0);
            const failedBeforeWrite = Number(write.FAILED_BEFORE_WRITE || 0);
            const needsReview = Number(write.OUTCOME_UNKNOWN || 0);
            const blocked = Number(write.BLOCKED_BATCH_HALT || 0);
            container.textContent = [
                `${Number(payload.worker_count || 1)} worker · ${summary.total} target`,
                `đang xử lý ${processing}`,
                `verified ${verified}`,
                `đã có sẵn ${alreadyDone}`,
                `dry-run ${dryRun}`,
                `lỗi trước ghi ${failedBeforeWrite}`,
                `cần kiểm tra ${needsReview}`,
                `bị chặn ${blocked}`
            ].join(' · ');
            container.classList.remove('hidden');
        }

        async function refreshZaloTargetProgress(job) {
            if (!job || job.job_type !== 'ZALO_SCAN') return;
            try {
                const response = await fetch(`/api/jobs/${job.id}/targets`);
                if (!response.ok) return;
                renderZaloTargetProgress(await response.json());
            } catch (error) {
                // Target progress is supplementary; normal job polling continues.
            }
        }

        function evidenceUrl(path) {
            const value = String(path || '');
            const legacy = value.match(/^\/static\/screenshots\/job-(\d+)\/([^/]+)$/);
            return legacy ? `/api/evidence/${legacy[1]}/${legacy[2]}` : value;
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
            if (job.status === 'PENDING' && Number.isInteger(job.queue_position)) {
                meta.textContent += ` · hàng đợi #${job.queue_position + 1}`;
            }
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
            if (!container || !jobId || timelineRequestInFlight) return;
            const now = Date.now();
            if (now < nextTimelineRefreshAt) return;
            timelineRequestInFlight = true;
            nextTimelineRefreshAt = now + TIMELINE_REFRESH_INTERVAL_MS;
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
            } finally {
                timelineRequestInFlight = false;
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
                    if (typeof ensureGoogleFormIdempotencyKey === 'function') {
                        ensureGoogleFormIdempotencyKey(ocrStudents);
                    } else {
                        googleformBatchId = crypto.randomUUID
                            ? crypto.randomUUID()
                            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
                    }
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
            } else if (job.job_type === 'ZALO_DISCOVERY') {
                if (typeof refreshZaloScanPlan === 'function') {
                    refreshZaloScanPlan();
                }
                if (job.status === 'SUCCESS'
                    && typeof fetchBcnbZaloGaps === 'function') {
                    fetchBcnbZaloGaps();
                }
            } else if (job.job_type === 'LMS_AUTOMATION') {
                alert(job.message || job.status);
            } else if (job.job_type === 'GOOGLEFORM_SUBMIT') {
                if (typeof clearGoogleFormIdempotencyState === 'function') {
                    clearGoogleFormIdempotencyState();
                }
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

        // DASHBOARD POLLING (3s, only while a job is active)
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
                    if (logsTabIsActive()) {
                        renderJobTimeline(job.id);
                    }

                    const statusText = document.getElementById('status-text');
                    const queueMessage = job.status === 'PENDING' && Number.isInteger(job.queue_position)
                        ? ` · vị trí hàng đợi ${job.queue_position + 1}`
                        : '';
                    const pulseDot = document.getElementById('pulse-dot');
                    const statusDot = document.getElementById('status-dot');
                    const zaloProgressMessage = job.job_type === 'ZALO_SCAN'
                        ? ` · ${String(job.reason_code || 'UNKNOWN_FAIL_CLOSED')} · đã thử ${Number(job.attempted_target_count || 0)} target`
                        : '';
                    statusText.innerText = `${job.message || job.status}${queueMessage}${zaloProgressMessage}`;
                    if (isTerminal) {
                        pulseDot.classList.add('hidden');
                        const isError = job.status === 'FAILED' || job.status === 'CANCELLED';
                        statusDot.className = 'relative inline-flex rounded-full h-2.5 w-2.5 ' + (isError ? 'bg-rose-500 shadow-[0_0_8px_#f43f5e]' : 'bg-gray-500');
                        handleTerminalJob(job, payload);
                    } else {
                        pulseDot.classList.remove('hidden');
                        statusDot.className = 'relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500 shadow-[0_0_8px_#3b82f6]';
                    }

                    if (job.job_type === 'ZALO_SCAN') {
                        renderZaloResults(zaloResultItems(payload));
                    }
                    if (job.job_type === 'ZALO_SCAN') {
                        await refreshZaloTargetProgress(job);
                    }
                }
            } catch (e) { console.error('Lỗi cập nhật job:', e); }

            // Fetch only new log records while the log tab is visible.
            await refreshLogs();

            if (currentJobActive) {
                scheduleJobPolling();
            } else {
                stopJobPolling();
            }
        }

        function requiredDashboardPeriod() {
            const required = typeof zaloScanPlan === 'object' && zaloScanPlan
                ? zaloScanPlan.required_week
                : null;
            const week = Number(required && required.week);
            const year = Number(required && required.year);
            if (!Number.isInteger(week) || !Number.isInteger(year) || week < 1 || week > 53) {
                return null;
            }
            return { week, year };
        }

        function dashboardResultsForRequiredWeek(resultsArray) {
            const period = requiredDashboardPeriod();
            if (!period) return [];
            return resultsArray.filter(result => {
                const target = result && result.scan_target;
                return target
                    && Number(target.week) === period.week
                    && Number(target.year) === period.year;
            });
        }

        function renderZaloResults(resultsArray, force = false) {
            if (!Array.isArray(resultsArray) || resultsArray.length === 0) {
                lastResultsSignature = '[]';
                latestZaloResults = [];
                ['count-total', 'count-success', 'count-exempt', 'count-failed']
                    .forEach(id => { document.getElementById(id).innerText = '0'; });
                const tbody = document.getElementById('results-table');
                tbody.replaceChildren();
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 5;
                cell.className = 'px-6 py-10 text-center text-slate-500';
                cell.textContent = 'Job quét hiện tại chưa có kết quả target.';
                row.appendChild(cell);
                tbody.appendChild(row);
                return;
            }
            const signature = JSON.stringify(resultsArray);
            if (!force && signature === lastResultsSignature) return;
            lastResultsSignature = signature;
            latestZaloResults = resultsArray;
            const dashboardResults = dashboardResultsForRequiredWeek(resultsArray);
            let total = dashboardResults.length, success = 0, exempt = 0, failed = 0;
            dashboardResults.forEach(r => {
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
            const rows = dashboardResults.filter(r => {
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
                    } else if (valueIndex === 0) {
                        td.textContent = String(value || '');
                        const target = r.scan_target && typeof r.scan_target === 'object'
                            ? r.scan_target
                            : null;
                        if (target && target.week != null && target.year != null) {
                            const targetMeta = document.createElement('div');
                            targetMeta.className = 'result-target-meta';
                            const classDate = target.class_date || target.class_date_iso || '?';
                            const lesson = target.lesson != null ? target.lesson : '?';
                            targetMeta.textContent = `Target BCNB · W${target.week}/${target.year} · ${classDate} · Buổi ${lesson}`;
                            td.appendChild(targetMeta);
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
                    ['oldest', 'Mốc ngày cũ nhất'],
                    ['oldest_message', 'Tin ở mốc cũ nhất'],
                ];
                imageOptions[2][1] = 'Mốc ngày cũ nhất (separator)';
                let imageCount = 0;
                imageOptions.forEach(([key, label]) => {
                    const item = evidenceImages[key];
                    if (!item || !item.path) return;
                    imageCount++;
                    const button = document.createElement('button');
                    button.className = 'table-link';
                    button.textContent = label;
                    const lifecycle = String(item.evidence_status || item.status || 'UNKNOWN');
                    button.title = [
                        `Trạng thái: ${lifecycle}`,
                        item.sha256 ? `SHA-256: ${item.sha256}` : 'Thiếu SHA-256',
                        item.size_bytes != null ? `${item.size_bytes} bytes` : 'Thiếu size',
                        item.capture_type ? `Capture: ${item.capture_type}` : '',
                    ].filter(Boolean).join('\\n');
                    if (lifecycle !== 'AVAILABLE') {
                        button.classList.add('is-disabled');
                        button.disabled = true;
                        button.textContent = `${label} · ${lifecycle}`;
                    } else {
                        button.addEventListener('click', () => openImageModal(evidenceUrl(item.path)));
                    }
                    actions.appendChild(button);
                });
                if (imageCount === 0 && r.screenshot_path) {
                    const legacyButton = document.createElement('button');
                    legacyButton.className = 'table-link';
                    legacyButton.textContent = 'Xem ảnh';
                    legacyButton.addEventListener('click', () => openImageModal(evidenceUrl(r.screenshot_path)));
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
                    const requiredStart = evidence.required_window_start || '?';
                    const requiredEnd = evidence.required_window_end || '?';
                    const states = Array.isArray(evidence.evidence_statuses)
                        ? evidence.evidence_statuses.join(', ')
                        : 'UNKNOWN';
                    meta.textContent = `${evidence.messages_checked || 0} tin · ${oldest} → ${newest} · Cửa sổ ${requiredStart} → ${requiredEnd} · Evidence: ${states}`;
                    actions.appendChild(meta);
                }
                const manual = r.manual_verification && typeof r.manual_verification === 'object'
                    ? r.manual_verification
                    : null;
                if (manual && manual.active) {
                    const manualMeta = document.createElement('div');
                    manualMeta.className = 'manual-verification-meta';
                    manualMeta.textContent = `Đã xác minh thủ công: ${manual.decision === 'REPORTED' ? 'Đã báo cáo' : 'Chưa báo cáo'} · ${manual.verifier_username || '?'} · ${manual.verified_at || '?'}`;
                    manualMeta.title = String(manual.note || '');
                    actions.appendChild(manualMeta);
                } else if (String(r.evidence_state || '') === 'INSUFFICIENT_EVIDENCE') {
                    const target = r.manual_target && typeof r.manual_target === 'object'
                        ? r.manual_target
                        : null;
                    const manualActions = document.createElement('div');
                    manualActions.className = 'manual-verify-actions';
                    [['REPORTED', 'Đã báo cáo'], ['NOT_REPORTED', 'Chưa báo cáo']].forEach(([decision, label]) => {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = decision === 'REPORTED'
                            ? 'manual-verify-button manual-verify-button--reported'
                            : 'manual-verify-button manual-verify-button--not-reported';
                        button.textContent = label;
                        button.disabled = !target;
                        button.title = target
                            ? 'Ghi nhận xác minh thủ công; không tự tích BCNB.'
                            : 'Thiếu phạm vi lớp/tuần/buổi để ghi nhận an toàn.';
                        button.addEventListener('click', () => manualVerifyResult(r, decision, button));
                        manualActions.appendChild(button);
                    });
                    actions.appendChild(manualActions);
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
                const period = requiredDashboardPeriod();
                td.textContent = period
                    ? `Không có kết quả cho tuần cần báo cáo W${period.week}/${period.year}`
                    : 'Chưa có tuần cần báo cáo để hiển thị';
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
        }

        async function manualVerifyResult(result, decision, button) {
            const target = result && result.manual_target;
            if (!target || !currentJobId) {
                alert('Không đủ phạm vi lớp/tuần/buổi hoặc chưa có job nguồn.');
                return;
            }
            const label = decision === 'REPORTED' ? 'ĐÃ BÁO CÁO' : 'CHƯA BÁO CÁO';
            const note = window.prompt(`Ghi chú xác minh thủ công (${label}):`, 'Đã kiểm tra trực tiếp trên Zalo.');
            if (note === null) return;
            if (String(note).trim().length < 3) {
                alert('Ghi chú phải có ít nhất 3 ký tự.');
                return;
            }
            if (button) button.disabled = true;
            try {
                const response = await fetch('/api/manual-verifications', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        class_code: target.class_code,
                        week: target.week,
                        year: target.year,
                        lesson: target.lesson,
                        decision,
                        note: String(note).trim(),
                        group_name: result.group,
                        source_job_id: currentJobId,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    const detail = data && data.detail;
                    throw new Error(typeof detail === 'object' ? (detail.message || detail.code) : (detail || 'Không thể ghi nhận xác minh.'));
                }
                await updateDashboardData();
            } catch (error) {
                if (button) button.disabled = false;
                alert(error.message || 'Không thể ghi nhận xác minh.');
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
            renderZaloResults(latestZaloResults, true);
        }

        document.getElementById('searchInput').addEventListener('input', () => {
            renderZaloResults(latestZaloResults, true);
        });

        // Một lần tải ban đầu để khôi phục job đang chạy sau khi reload.
        // Các lần tiếp theo chỉ được lên lịch khi job thực sự còn hoạt động.
        updateDashboardData();
        if (typeof refreshZaloScanPlan === 'function') {
            refreshZaloScanPlan();
        }
