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
            let appendedCount = 0;
            lines.forEach(log => {
                const isObject = Boolean(log && typeof log === 'object');
                const rawMessage = isObject ? String(log.message || '') : String(log || '');
                const statusCode = isObject && log.status_code !== undefined ? Number(log.status_code) : NaN;
                const level = isObject ? String(log.level || 'INFO').toUpperCase() : '';
                const isHttpCompletion = (isObject && log.event === 'http.completed')
                    || rawMessage.includes('HTTP request hoàn tất.');
                const isHttpError = (Number.isFinite(statusCode) && statusCode >= 400)
                    || level === 'ERROR'
                    || /\b[45]\d{2}\b|Lỗi|LỖI|ERROR/i.test(rawMessage);
                if (isHttpCompletion && !isHttpError) return;
                const timePrefix = isObject
                    ? `[${String(log.timestamp || '').slice(11, 19)}] `
                    : '';
                const httpDetail = isObject && isHttpCompletion && isHttpError
                    ? `HTTP ${Number.isFinite(statusCode) ? statusCode : 'ERR'}${log.method ? ` ${log.method}` : ''}${log.path ? ` ${log.path}` : ''} — `
                    : '';
                const text = isObject
                    ? `${timePrefix}${httpDetail}${rawMessage}`
                    : rawMessage;
                let colorClass = "log-info";
                if (isHttpError || text.includes("Lỗi") || text.includes("LỖI") || text.includes("thất bại") || text.includes("❌")) colorClass = "log-error";
                else if (text.includes("Tick xanh") || text.includes("THÀNH CÔNG") || text.includes("thành công") || text.includes("✅")) colorClass = "log-success";
                else if (text.includes("MIỄN BÁO CÁO") || text.includes("Miễn báo cáo") || text.includes("⚠️")) colorClass = "log-warning";
                const line = document.createElement('div');
                line.className = `${colorClass} mb-1`;
                line.textContent = text;
                fragment.appendChild(line);
                appendedCount += 1;
            });
            if (appendedCount === 0 && !replace) return;
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
            const emptyHint = document.getElementById('specs-target-progress-empty');
            const review = document.getElementById('zalo-review-queue');
            const reviewItems = document.getElementById('zalo-review-queue-items');
            if (!container) return;
            const summary = payload && payload.summary;
            if (!summary || !summary.total) {
                container.classList.add('hidden');
                container.textContent = '';
                if (emptyHint) emptyHint.classList.remove('hidden');
                if (review) review.classList.add('hidden');
                if (reviewItems) reviewItems.replaceChildren();
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
            const report = payload.report && typeof payload.report === 'object' ? payload.report : {};
            const reportLines = [
                `${Number(payload.worker_count || 1)} worker · ${summary.total} target`,
                `đã kiểm tra ${Number(report.checked || 0)}`,
                `đã nộp hợp lệ ${Number(report.submitted_verified || 0)}`,
                `không tìm thấy sau khi kiểm tra đủ ${Number(report.not_found_verified || 0)}`,
                `thiếu bằng chứng ${Number(report.insufficient_evidence || 0)}`,
                `lỗi kỹ thuật ${Number(report.scan_failed || 0)}`,
                `batch halt ${Number(report.blocked_batch_halt || 0)}`,
                `đang xử lý ${processing}`,
                `verified ${verified}`,
                `đã có sẵn ${alreadyDone}`,
                `dry-run ${dryRun}`,
                `lỗi trước ghi ${failedBeforeWrite}`,
                `cần kiểm tra ${needsReview}`,
                `bị chặn ${blocked}`
            ];
            container.textContent = reportLines.join(' · ');
            container.classList.remove('hidden');
            if (emptyHint) emptyHint.classList.add('hidden');

            if (!review || !reviewItems) return;
            const queue = Array.isArray(payload.review_queue) ? payload.review_queue : [];
            reviewItems.replaceChildren();
            if (!queue.length) {
                review.classList.add('hidden');
                return;
            }
            queue.slice(0, 100).forEach(item => {
                const line = document.createElement('div');
                line.className = 'flex flex-wrap items-center gap-2';
                const label = document.createElement('span');
                label.textContent = `${String(item.class_code || 'Không rõ mã lớp')} · Buổi ${String(item.lesson || '?')} · ${String(item.reason || 'Cần xem lại')}`;
                line.appendChild(label);
                const paths = Array.isArray(item.evidence_paths) ? item.evidence_paths : [];
                paths.slice(0, 4).forEach(path => {
                    if (typeof openImageModal !== 'function') return;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'table-link';
                    button.textContent = 'Xem evidence';
                    button.addEventListener('click', () => openImageModal(evidenceUrl(path)));
                    line.appendChild(button);
                });
                reviewItems.appendChild(line);
            });
            review.classList.remove('hidden');
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
            const specsJobType = document.getElementById('specs-job-type');
            const specsJobReason = document.getElementById('specs-job-reason');
            const cancelButton = document.getElementById('job-cancel-button');
            const retryButton = document.getElementById('job-retry-button');
            currentJobExecutionMode = job.execution_mode === 'production'
                || (job.execution_mode !== 'dry-run' && Boolean(job.mutates_external))
                ? 'production'
                : 'dry-run';
            currentJobType = job.job_type || null;
            if (typeof renderRuntimeMode === 'function') {
                renderRuntimeMode(currentJobExecutionMode, currentJobType);
            }
            if (specsJobType) specsJobType.textContent = currentJobType || 'Chưa có';
            if (specsJobReason) {
                const reasonText = job.job_type === 'ZALO_SCAN'
                    ? `${String(job.reason_code || 'UNKNOWN_FAIL_CLOSED')} · đã thử ${Number(job.attempted_target_count || 0)} target`
                    : (job.reason_code || job.status || '—');
                specsJobReason.textContent = reasonText;
            }
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
                        tone: item.new_status === 'FAILED' ? 'text-rose-600 font-semibold' : 'text-slate-700'
                    })),
                    ...actions.map(item => ({
                        time: item.created_at || '',
                        text: `AUDIT ${item.action_type} ${item.target_id}: ${item.outcome}`,
                        tone: item.outcome === 'FAILED' ? 'text-rose-600 font-semibold' : 'text-emerald-700 font-medium'
                    }))
                ].sort((a, b) => String(b.time).localeCompare(String(a.time))).slice(0, 100);
                container.replaceChildren();
                if (!items.length) {
                    const empty = document.createElement('div');
                    empty.className = 'text-slate-400';
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
                line.className = 'text-slate-400';
                line.textContent = 'Không có';
                container.appendChild(line);
            }
        }

        function resolveLmsMissingBcnbStatus(plan, code) {
            const checkedList = plan && Array.isArray(plan.missing_but_checked_classes)
                ? plan.missing_but_checked_classes
                : [];
            if (checkedList.includes(code)) {
                return { label: 'BCNB: Đã tích ⚠️', tone: 'text-amber-900 border-amber-300 bg-amber-100 font-semibold' };
            }
            const unmatchedList = plan && Array.isArray(plan.unmatched_classes)
                ? plan.unmatched_classes
                : [];
            if (unmatchedList.includes(code)) {
                return { label: 'Ngoài BCNB tuần này', tone: 'text-slate-600 border-slate-300 bg-slate-100 font-medium' };
            }
            return { label: 'BCNB: Chưa tích', tone: 'text-rose-800 border-rose-300 bg-rose-100 font-semibold' };
        }

        function renderLmsMissingClasses(plan) {
            const container = document.getElementById('lmsMissingClasses');
            if (!container) return;
            container.replaceChildren();
            const codes = plan && Array.isArray(plan.missing_classes) ? plan.missing_classes : [];
            const overdueDetails = plan && plan.overdue_details && typeof plan.overdue_details === 'object'
                ? plan.overdue_details
                : {};
            if (codes.length === 0) {
                const line = document.createElement('div');
                line.className = 'text-slate-400';
                line.textContent = 'Không có';
                container.appendChild(line);
                return;
            }
            codes.forEach(rawCode => {
                const code = String(rawCode || '').trim();
                if (!code) return;
                const detail = String(overdueDetails[code] || '').trim();
                const bcnbStatus = resolveLmsMissingBcnbStatus(plan, code);
                const row = document.createElement('div');
                row.className = 'flex flex-wrap items-center justify-between gap-1.5 rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 shadow-sm';

                const codeSpan = document.createElement('span');
                codeSpan.className = 'font-bold text-rose-900';
                codeSpan.textContent = detail ? `${code} (${detail})` : code;
                row.appendChild(codeSpan);

                const statusSpan = document.createElement('span');
                statusSpan.className = `px-1.5 py-0.5 text-[10px] rounded border ${bcnbStatus.tone}`;
                statusSpan.textContent = bcnbStatus.label;
                row.appendChild(statusSpan);

                row.textContent = `${codeSpan.textContent} · ${statusSpan.textContent}`;
                row.replaceChildren(codeSpan, statusSpan);
                container.appendChild(row);
            });
        }

        function getSelectedLmsTickClasses() {
            const allowed = lmsPreviewPlan && Array.isArray(lmsPreviewPlan.classes_to_tick)
                ? lmsPreviewPlan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
            const selectedSet = new Set(
                Array.isArray(lmsSelectedClassesToTick)
                    ? lmsSelectedClassesToTick.map(c => String(c || '').trim()).filter(Boolean)
                    : []
            );
            return allowed.filter(code => selectedSet.has(code));
        }

        function syncLmsConfirmButtonState() {
            const allowed = lmsPreviewPlan && Array.isArray(lmsPreviewPlan.classes_to_tick)
                ? lmsPreviewPlan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
            const selected = getSelectedLmsTickClasses();
            const isBusy = Boolean(typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight);
            const countEl = document.getElementById('lmsSelectedTickCount');
            if (countEl) {
                countEl.textContent = `${selected.length}/${allowed.length}`;
            }
            const toggleBtn = document.getElementById('btnToggleAllLmsTick');
            if (toggleBtn) {
                toggleBtn.disabled = isBusy || allowed.length === 0;
                const allSelected = allowed.length > 0 && selected.length === allowed.length;
                toggleBtn.textContent = allSelected ? 'Bỏ chọn hết' : 'Chọn tất cả';
            }
            const backBtn = document.getElementById('btnBackLms');
            if (backBtn) {
                backBtn.disabled = isBusy;
            }
            const confirmBtn = document.getElementById('btnConfirmLms');
            if (confirmBtn) {
                confirmBtn.disabled = isBusy || !(
                    lmsPreviewPlan
                    && lmsPreviewPlan.can_confirm
                    && lmsConfirmationToken
                    && selected.length > 0
                );
                confirmBtn.textContent = isBusy ? 'Đang xác nhận...' : 'Xác nhận kế hoạch';
            }
        }

        function setLmsConfirmBusy(isBusy) {
            lmsConfirmInFlight = Boolean(isBusy);
            renderLmsTickClasses(lmsPreviewPlan);
        }

        function toggleLmsTickClass(classCode, checked) {
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) return;
            const normalized = String(classCode || '').trim();
            if (!normalized) return;
            const allowed = lmsPreviewPlan && Array.isArray(lmsPreviewPlan.classes_to_tick)
                ? lmsPreviewPlan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
            if (!allowed.includes(normalized)) return;
            const current = new Set(getSelectedLmsTickClasses());
            const shouldCheck = checked === undefined ? !current.has(normalized) : Boolean(checked);
            if (shouldCheck) current.add(normalized);
            else current.delete(normalized);
            lmsSelectedClassesToTick = allowed.filter(code => current.has(code));
            syncLmsConfirmButtonState();
        }

        function renderLmsTickClasses(plan) {
            const container = document.getElementById('lmsTickClasses');
            if (!container) return;
            container.replaceChildren();
            const codes = plan && Array.isArray(plan.classes_to_tick)
                ? plan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
            if (codes.length === 0) {
                const line = document.createElement('div');
                line.className = 'text-slate-400';
                line.textContent = 'Không có';
                container.appendChild(line);
                syncLmsConfirmButtonState();
                return;
            }
            const isBusy = Boolean(typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight);
            const selectedSet = new Set(getSelectedLmsTickClasses());
            codes.forEach(code => {
                const label = document.createElement('label');
                label.className = 'flex items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-white px-2.5 py-1.5 cursor-pointer hover:bg-emerald-50 transition-colors shadow-sm';

                const leftWrap = document.createElement('div');
                leftWrap.className = 'flex items-center gap-2';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.value = code;
                checkbox.checked = selectedSet.has(code);
                checkbox.disabled = isBusy;
                checkbox.className = 'rounded border-emerald-400 text-emerald-600 focus:ring-emerald-500';
                checkbox.addEventListener('change', () => {
                    if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) {
                        checkbox.checked = selectedSet.has(code);
                        return;
                    }
                    toggleLmsTickClass(code, checkbox.checked);
                });

                const codeSpan = document.createElement('span');
                codeSpan.className = 'font-bold text-emerald-900';
                codeSpan.textContent = code;

                leftWrap.appendChild(checkbox);
                leftWrap.appendChild(codeSpan);

                const statusSpan = document.createElement('span');
                statusSpan.className = 'px-1.5 py-0.5 text-[10px] rounded border border-emerald-300 bg-emerald-100 text-emerald-800 font-semibold';
                statusSpan.textContent = 'BCNB: Chưa tích';

                label.textContent = `${code} · ${statusSpan.textContent}`;
                label.replaceChildren(leftWrap, statusSpan);
                container.appendChild(label);
            });
            syncLmsConfirmButtonState();
        }

        function toggleAllLmsTickSelection() {
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) return;
            const allowed = lmsPreviewPlan && Array.isArray(lmsPreviewPlan.classes_to_tick)
                ? lmsPreviewPlan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
            if (allowed.length === 0) return;
            const selected = getSelectedLmsTickClasses();
            if (selected.length === allowed.length) {
                lmsSelectedClassesToTick = [];
            } else {
                lmsSelectedClassesToTick = [...allowed];
            }
            renderLmsTickClasses(lmsPreviewPlan);
        }

        function showLmsReview(job, plan) {
            lmsConfirmInFlight = false;
            lmsAnalysisJobId = job.id;
            lmsConfirmationToken = plan.confirmation_token || null;
            lmsPreviewPlan = plan;
            lmsSelectedClassesToTick = Array.isArray(plan.classes_to_tick)
                ? plan.classes_to_tick.map(c => String(c || '').trim()).filter(Boolean)
                : [];
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
            renderLmsMissingClasses(plan);
            renderLmsTickClasses(plan);

            const warning = document.getElementById('lmsReviewWarning');
            const issues = Array.isArray(plan.blocking_issues) ? plan.blocking_issues : [];
            warning.textContent = issues.join(' • ');
            warning.classList.toggle('hidden', issues.length === 0);
            syncLmsConfirmButtonState();
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

            if (job.job_type === 'GOOGLEFORM_OCR' || job.job_type === 'GOOGLEFORM_LMS_STUDENTS') {
                if (job.job_type === 'GOOGLEFORM_OCR') {
                    setOcrBusy(false);
                } else if (typeof setLmsStudentsFetchBusy === 'function') {
                    setLmsStudentsFetchBusy(false);
                }
                const defaultPrefix = job.job_type === 'GOOGLEFORM_LMS_STUDENTS'
                    ? 'Lấy danh sách học viên từ LMS'
                    : 'OCR';
                if (
                    job.status === 'SUCCESS'
                    && payload
                    && Array.isArray(payload.students)
                    && payload.students.length > 0
                    && payload.students.length <= 200
                ) {
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
                    ocrStudents = [];
                    const submitBtn = document.getElementById('btnSubmitGoogleForm');
                    if (submitBtn) submitBtn.disabled = true;
                    const limitExceeded = Boolean(
                        job.status === 'SUCCESS'
                        && payload
                        && Array.isArray(payload.students)
                        && payload.students.length > 200
                    );
                    setOcrMessage(
                        limitExceeded
                            ? 'Số lượng học viên vượt quá giới hạn an toàn (tối đa 200 học viên).'
                            : (job.status === 'CANCELLED'
                                ? `${defaultPrefix} đã được hủy.`
                                : (job.message || `${defaultPrefix} thất bại.`)),
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
            } else if (job.job_type === 'ZALO_MANUAL_APPROVE') {
                if (job.status !== 'SUCCESS') {
                    alert(
                        job.status === 'CANCELLED'
                            ? 'Duyệt tay BCNB đã được hủy.'
                            : (job.message || 'Duyệt tay BCNB thất bại và đã dừng an toàn.')
                    );
                }
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
                    if (typeof renderZaloWorkflow === 'function') renderZaloWorkflow();
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
                    statusText.innerText = `${job.message || job.status}${queueMessage}`;
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
                        const incomingItems = zaloResultItems(payload).map(item => (
                            item && typeof item === 'object'
                                && (!Number.isInteger(item.source_job_id) || item.source_job_id <= 0)
                                ? { ...item, source_job_id: job.id }
                                : item
                        ));
                        const payloadIsSelective = Boolean(payload && payload.selective_rescan);
                        if (incomingItems.length > 0 && !payloadIsSelective) {
                            clearSelectiveScanBaseline();
                        }
                        const isSelective = payloadIsSelective || hasActiveSelectiveScanBaseline();
                        const mergedItems = mergeSelectiveZaloResults(incomingItems, isSelective, payload);
                        const verifiedSnapshotId = (payload && payload.snapshot_id != null)
                            ? payload.snapshot_id
                            : (isSelective && hasActiveSelectiveScanBaseline() ? selectiveBaselineSnapshotId : null);
                        renderZaloResults(mergedItems, false, {
                            snapshotId: verifiedSnapshotId,
                            payload,
                            sourceJobId: job.id,
                        });
                    } else if (
                        job.job_type === 'ZALO_MANUAL_APPROVE'
                        && job.status === 'SUCCESS'
                        && payload
                        && payload.kind === 'zalo_manual_approve'
                    ) {
                        applyManualApprovePayload(payload);
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

        function isHistoricalWeekViewActive() {
            return Boolean(
                typeof weeklyHistoryView === 'object'
                && weeklyHistoryView
                && Number.isInteger(Number(weeklyHistoryView.week))
                && Number.isInteger(Number(weeklyHistoryView.year))
                && Number(weeklyHistoryView.week) >= 1
                && Number(weeklyHistoryView.week) <= 53
            );
        }

        function effectiveDashboardPeriod() {
            if (isHistoricalWeekViewActive()) {
                return {
                    week: Number(weeklyHistoryView.week),
                    year: Number(weeklyHistoryView.year),
                };
            }
            return requiredDashboardPeriod();
        }

        function dashboardResultsForRequiredWeek(resultsArray) {
            const period = effectiveDashboardPeriod();
            if (!period) return [];
            return resultsArray.filter(result => {
                const target = result && result.scan_target;
                return target
                    && Number(target.week) === period.week
                    && Number(target.year) === period.year;
            });
        }

        function renderSpecsEvidenceTable(resultsArray) {
            const tbody = document.getElementById('specs-evidence-table-body');
            if (!tbody) return;
            tbody.replaceChildren();
            if (!Array.isArray(resultsArray) || resultsArray.length === 0) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 6;
                td.className = 'px-6 py-10 text-center text-slate-400 border-none';
                td.textContent = 'Chưa có thông số kiểm chứng tin nhắn. Dữ liệu sẽ hiển thị khi có kết quả quét Zalo.';
                tr.appendChild(td);
                tbody.appendChild(tr);
                return;
            }
            resultsArray.forEach(r => {
                const tr = document.createElement('tr');
                tr.className = 'result-row transition-colors';
                const evidence = r.evidence && typeof r.evidence === 'object' ? r.evidence : null;
                const target = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : null;

                const groupTd = document.createElement('td');
                groupTd.className = 'px-5 py-3 font-semibold text-slate-700';
                groupTd.textContent = String(r.group || '');
                if (evidence && evidence.actual_group_name && evidence.actual_group_name !== r.group) {
                    const actualDiv = document.createElement('div');
                    actualDiv.className = 'text-[11px] font-normal text-slate-500 mt-0.5';
                    actualDiv.textContent = `Thực tế: ${evidence.actual_group_name}`;
                    groupTd.appendChild(actualDiv);
                }

                const targetTd = document.createElement('td');
                targetTd.className = 'px-5 py-3 text-slate-600 font-mono';
                if (target && target.week != null && target.year != null) {
                    const classDate = target.class_date || target.class_date_iso || '?';
                    const lesson = target.lesson != null ? target.lesson : '?';
                    targetTd.textContent = `W${target.week}/${target.year} · Buổi ${lesson} · ${classDate}`;
                } else {
                    targetTd.textContent = '—';
                }

                const countTd = document.createElement('td');
                countTd.className = 'px-5 py-3 font-mono text-slate-600';
                countTd.textContent = evidence ? `${evidence.messages_checked || 0} tin` : '—';

                const rangeTd = document.createElement('td');
                rangeTd.className = 'px-5 py-3 font-mono text-slate-600';
                rangeTd.textContent = evidence
                    ? `${evidence.oldest_message_date || '?'} → ${evidence.newest_message_date || '?'}`
                    : '—';

                const windowTd = document.createElement('td');
                windowTd.className = 'px-5 py-3 font-mono text-slate-600';
                windowTd.textContent = evidence
                    ? `${evidence.required_window_start || '?'} → ${evidence.required_window_end || '?'}`
                    : '—';

                const stateTd = document.createElement('td');
                stateTd.className = 'px-5 py-3 font-mono text-slate-600';
                if (evidence) {
                    const states = Array.isArray(evidence.evidence_statuses)
                        ? evidence.evidence_statuses.join(', ')
                        : 'UNKNOWN';
                    stateTd.textContent = states;
                    if (evidence.scanned_at) {
                        const timeDiv = document.createElement('div');
                        timeDiv.className = 'text-[10px] text-slate-400 mt-0.5';
                        timeDiv.textContent = `Quét: ${evidence.scanned_at}`;
                        stateTd.appendChild(timeDiv);
                    }
                } else {
                    stateTd.textContent = '—';
                }

                tr.append(groupTd, targetTd, countTd, rangeTd, windowTd, stateTd);
                tbody.appendChild(tr);
            });
        }

        function normalizeAutoIssueTextInJs(rawText) {
            const cleaned = String(rawText || '').replace(/\s+/g, ' ').replace(/^[;| ]+|[;| ]+$/g, '').trim();
            if (!cleaned) return '';
            if (
                /^\[LMS\]\s*(?:(?:Nhận|Nhan)\s+x[eé]t\s+(?:HS\s+)?s[oơ]\s+s[aà]i\s*,?\s*thi[eế]u\s*(?:đủ\s*)?3\s*[yý]|(?:Nhận|Nhan)\s+x[eé]t\s+thi[eế]u\s+[yý])(?:\s*\([^)]*\))?\.?$/i.test(cleaned)
            ) {
                return '[LMS] Nhận xét thiếu ý';
            }
            if (
                /^\[LMS\]\s*(?:Nhận|Nhan)\s+x[eé]t\s+Checkpoint\s+ch[uư]a\s+[đd][uủ]\s+c[aấ]u\s+tr[uú]c\s+n[aă]ng\s+l[uự]c(?:\s*\([^)]*\))?\.?$/i.test(cleaned)
            ) {
                return '[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực';
            }
            const mTemplate = cleaned.match(
                /^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai ngày\/buổi(?:\s+trong\s+mẫu)?\s*(?:[;—,\-]+|\()\s*['"]?(Bài\s+số\s+\d+)['"]?(?:\s+khớp\s+với\b.*|\s*\))\.?$/i
            );
            if (mTemplate) {
                const rawLabel = mTemplate[1].replace(/\s+/g, ' ').trim();
                const lessonLabel = rawLabel ? rawLabel.charAt(0).toUpperCase() + rawLabel.slice(1) : rawLabel;
                return `[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( ${lessonLabel} )`;
            }
            const mWrongDate = cleaned.match(
                /^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai ngày\s+([^\s;—,\)]+)\s*[;—,\-]+\s*đã ghép theo BCNB\s+W\d+\/\d+\s*,\s*ngày\s+([^,.\)]+)\s*,\s*buổi\s+([^\s.\)]+)\.?$/i
            );
            if (mWrongDate) {
                return `[Zalo] Đã báo cáo nhưng ghi sai ngày ( ghi ngày ${mWrongDate[1].trim()} nhưng lịch là ngày ${mWrongDate[2].trim()}, buổi ${mWrongDate[3].trim()})`;
            }
            const mWrongLesson = cleaned.match(
                /^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai buổi\s+([^\s;—,\)]+)\s*[;—,\-]+\s*đã ghép theo BCNB\s+W\d+\/\d+\s*,\s*ngày\s+([^,.\)]+)\s*,\s*buổi\s+([^\s.\)]+)\.?$/i
            );
            if (mWrongLesson) {
                return `[Zalo] Đã báo cáo nhưng ghi sai buổi ( ghi buổi ${mWrongLesson[1].trim()} nhưng lịch là ngày ${mWrongLesson[2].trim()}, buổi ${mWrongLesson[3].trim()})`;
            }
            return cleaned;
        }

        function isLesson14ExemptRow(r) {
            if (!r || typeof r !== 'object') return false;
            if (String(r.evidence_state || '').trim() === 'LESSON_14_EXEMPTION') return true;
            const st = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
            const ev = r.evidence && typeof r.evidence === 'object' ? r.evidence : {};
            const ctx = ev.date_context && typeof ev.date_context === 'object' ? ev.date_context : {};
            const lesson = String(
                st.lesson != null ? st.lesson : (ctx.target_lesson != null ? ctx.target_lesson : (r.lesson != null ? r.lesson : ''))
            ).trim();
            return lesson === '14';
        }

        function normalizeZaloStatusDisplay(status, row = null) {
            if (isLesson14ExemptRow(row)) {
                return '✅ Miễn báo cáo (Buổi 14)';
            }
            const text = String(status || '').replace(/\s+/g, ' ').trim();
            if (
                text === '❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng' ||
                text === '❌ Chưa tìm thấy trong khoảng đã kiểm chứng'
            ) {
                return 'Chưa nhận xét';
            }
            return text;
        }

        function normalizeZaloStatusDetailDisplay(detail, row = null) {
            const text = String(detail || '').trim();
            if (isLesson14ExemptRow(row)) {
                if (
                    !text
                    || text.includes('Buổi 14 được miễn báo cáo Zalo')
                    || text.includes('Không xác minh được tên nhóm Zalo thực tế')
                ) {
                    return 'Buổi 14 – miễn báo cáo theo quy tắc nghiệp vụ.';
                }
            }
            return text;
        }

        function isUnreportedOrWarningZaloStatus(status, row = null) {
            if (isLesson14ExemptRow(row)) {
                return false;
            }
            const text = normalizeZaloStatusDisplay(status, row);
            return (
                text.includes('❌') ||
                text.includes('⚠️') ||
                /^chưa\s+nhận\s+xét\b/i.test(text)
            );
        }

        function splitCheckerIssueNoteInJs(rawNote) {
            const text = String(rawNote || '').trim();
            if (!text) return { manual: '', autoIssues: [] };
            const autoIssues = [];
            let remaining = text;
            const pattern = /\[Auto\s+QC:\s*/i;
            while (true) {
                const match = pattern.exec(remaining);
                if (!match) break;
                const startIdx = match.index;
                const pos = startIdx + match[0].length;
                let depth = 1;
                let endIdx = remaining.length;
                let inside = '';
                for (let i = pos; i < remaining.length; i += 1) {
                    const ch = remaining[i];
                    if (ch === '[') depth += 1;
                    else if (ch === ']') {
                        depth -= 1;
                        if (depth === 0) {
                            inside = remaining.slice(pos, i).trim();
                            endIdx = i + 1;
                            break;
                        }
                    }
                }
                if (depth > 0) {
                    inside = remaining.slice(pos).replace(/[\s\]]+$/g, '').trim();
                    endIdx = remaining.length;
                }
                inside.split(';').forEach(seg => {
                    const cleaned = normalizeAutoIssueTextInJs(seg);
                    if (cleaned) autoIssues.push(cleaned);
                });
                const before = remaining.slice(0, startIdx).replace(/\s*\|\s*$/, '').trim();
                const after = remaining.slice(endIdx).replace(/^\s*\|\s*/, '').trim();
                remaining = before && after ? `${before} | ${after}` : (before || after);
            }
            const manualChunks = [];
            const autoTagRe = /^\[(?:LMS|Zalo)\]\s*/i;
            remaining.split('|').forEach(pipeChunk => {
                const chunkManual = [];
                pipeChunk.split(';').forEach(seg => {
                    const cleaned = seg.replace(/\s+/g, ' ').replace(/^[;| ]+|[;| ]+$/g, '').trim();
                    if (!cleaned) return;
                    if (autoTagRe.test(cleaned)) {
                        autoIssues.push(normalizeAutoIssueTextInJs(cleaned));
                    } else {
                        chunkManual.push(cleaned);
                    }
                });
                if (chunkManual.length > 0) {
                    manualChunks.push(chunkManual.join('; '));
                }
            });
            const manual = manualChunks.join(' | ').trim();
            return { manual, autoIssues };
        }

        function resolveDashboardRowLmsStatus(r) {
            if (!r || typeof r !== 'object') return 'Chưa quét LMS';
            const st = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
            const rawLms = String(r.lms_status || '').trim();
            if (rawLms && rawLms !== 'Chưa quét LMS') return rawLms;
            const nxLms = typeof r.nx_lms_checked === 'boolean'
                ? r.nx_lms_checked
                : (typeof st.nx_lms_checked === 'boolean' ? st.nx_lms_checked : null);
            if (nxLms === true) return '✅ Đã nộp LMS';
            if (nxLms === false) return '❌ Chưa nộp LMS';
            return rawLms || 'Chưa quét LMS';
        }

        function resolveDashboardRowIssueDescription(r) {
            if (!r || typeof r !== 'object') return '';
            const st = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
            const parts = [];
            const seen = new Set();
            const normKey = text => String(text || '')
                .replace(/^\[[^\]]+\]\s*/, '')
                .replace(/\s+—\s+/g, '; ')
                .replace(/^[.;\s]+|[.;\s]+$/g, '')
                .toLowerCase()
                .replace(/\s+/g, ' ');
            const legacyDupRe = /^\[LMS\]\s*(?:Nhận|Nhan)\s+x[eé]t\s+HS\s+copy-paste\s+tr[uù]ng\s+l[aặ]p\b/i;
            const addOne = (seg, isAuto = false) => {
                let cleaned = String(seg || '').replace(/\s+/g, ' ').replace(/^[;\s]+|[;\s]+$/g, '').trim();
                if (!cleaned) return;
                if (
                    legacyDupRe.test(cleaned) ||
                    (isAuto && /^\[LMS\]/i.test(cleaned) && cleaned.toLowerCase().includes('copy-paste trùng lặp'))
                ) {
                    return;
                }
                if (isAuto || /^\[(?:LMS|Zalo)\]\s*/i.test(cleaned)) {
                    cleaned = normalizeAutoIssueTextInJs(cleaned);
                }
                const key = normKey(cleaned);
                if (key && !seen.has(key)) {
                    seen.add(key);
                    parts.push(cleaned);
                }
            };
            const addVal = (val, splitSemi = false) => {
                if (!val) return;
                if (Array.isArray(val)) {
                    val.forEach(entry => addVal(entry, false));
                    return;
                }
                const text = String(val).trim();
                if (!text) return;
                const parsed = splitCheckerIssueNoteInJs(text);
                if (parsed.autoIssues.length > 0 || /\[Auto\s+QC:\s*/i.test(text)) {
                    if (parsed.manual) addOne(parsed.manual, false);
                    parsed.autoIssues.forEach(s => addOne(s, true));
                    return;
                }
                if (splitSemi) {
                    text.split(';').forEach(s => addOne(s, false));
                } else {
                    addOne(text, false);
                }
            };
            if (r.issue_description) {
                addVal(r.issue_description, false);
            }
            const qcNote = r.qc_issue_note !== undefined && r.qc_issue_note !== null
                ? r.qc_issue_note
                : st.qc_issue_note;
            addVal(qcNote, false);
            addVal(r.quality_issues || st.quality_issues, false);
            addVal(r.lms_quality_issues || st.lms_quality_issues, false);
            addVal(r.zalo_content_issues || st.zalo_content_issues, false);
            return parts.join('; ');
        }

        function resolveDashboardRowEvaluationStatus(r) {
            if (!r || typeof r !== 'object') return '';
            const st = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
            let explicitEval = String(r.evaluation_status || st.evaluation_status || '').trim();
            const zaloStatus = normalizeZaloStatusDisplay(r.status || r.zalo_status || '', r);
            const lmsStatus = resolveDashboardRowLmsStatus(r);
            if (
                zaloStatus.includes('Miễn')
                && (/zalo\s+cần\s+kiểm\s+tra/i.test(explicitEval) || /chưa\s+nhận\s+xét\s+zalo/i.test(explicitEval))
            ) {
                explicitEval = '';
            }
            const zaloWarn = zaloStatus.startsWith('⚠️');
            const lmsWarn = lmsStatus.startsWith('⚠️');
            const zaloDone = !isUnreportedOrWarningZaloStatus(zaloStatus, r) && (
                zaloStatus.includes('✅') || zaloStatus.includes('Miễn') || zaloStatus.includes('🟡')
            );
            const lmsDone = !lmsStatus.startsWith('❌') && !lmsWarn && (
                lmsStatus.startsWith('✅') || lmsStatus.includes('Dry-run LMS') || lmsStatus.includes('Chờ tích BCNB')
            );
            if (lmsWarn || zaloWarn) {
                if (zaloDone && lmsWarn) return '⚠️ Đã nhận xét Zalo, LMS cần kiểm tra';
                if (lmsDone && zaloWarn) return '⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra';
                return '⚠️ Cần kiểm tra nhận xét';
            }
            if (explicitEval) {
                if (zaloDone && lmsDone && /^⏳/.test(explicitEval)) {
                    return '✅ Đã hoàn thành nhận xét';
                }
                return explicitEval;
            }
            if (lmsStatus === 'Chưa quét LMS') return '';
            if (zaloDone && lmsDone) return '✅ Đã hoàn thành nhận xét';
            if (lmsDone && !zaloDone) return '⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo';
            if (zaloDone && !lmsDone) return '⏳ Đã hoàn tất nhận xét Zalo, chưa nhận xét LMS';
            return '❌ Chưa hoàn thành nhận xét';
        }

        function renderZaloResults(resultsArray, force = false, options = {}) {
            const isHistoricalOption = Boolean(options && options.isHistoricalView);
            const preserveOrigin = Boolean(
                (options && options.preserveOrigin) || resultsArray === latestZaloResults
            );
            if (!isHistoricalOption && !preserveOrigin) {
                if (typeof weeklyHistoryRequestSeq === 'number') {
                    weeklyHistoryRequestSeq += 1;
                }
                if (typeof weeklyHistoryView !== 'undefined' && weeklyHistoryView !== null) {
                    weeklyHistoryView = null;
                    if (typeof updateWeeklyHistoryStatusBadge === 'function') {
                        updateWeeklyHistoryStatusBadge();
                    }
                    if (typeof applyZaloRunState === 'function') {
                        applyZaloRunState();
                    }
                }
            }
            if (!Array.isArray(resultsArray) || resultsArray.length === 0) {
                lastResultsSignature = '[]';
                latestZaloResults = [];
                latestZaloResultsPeriodKey = '';
                latestZaloResultsSnapshotId = null;
                latestZaloResultsJobId = null;
                ['count-total', 'count-success', 'count-exempt', 'count-failed']
                    .forEach(id => { document.getElementById(id).innerText = '0'; });
                const tbody = document.getElementById('results-table');
                tbody.replaceChildren();
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 5;
                cell.className = 'px-6 py-10 text-center text-slate-500';
                const histPeriod = isHistoricalWeekViewActive() ? effectiveDashboardPeriod() : null;
                cell.textContent = histPeriod
                    ? `Không có kết quả quét Zalo cho tuần lịch sử W${histPeriod.week}/${histPeriod.year}.`
                    : 'Job quét hiện tại chưa có kết quả target.';
                row.appendChild(cell);
                tbody.appendChild(row);
                renderSpecsEvidenceTable([]);
                return;
            }
            if (options && options.sourceJobId !== undefined) {
                latestZaloResultsJobId = (Number.isInteger(options.sourceJobId) && options.sourceJobId > 0)
                    ? options.sourceJobId
                    : null;
            }
            const resolvedSnapshotId = (options && options.snapshotId !== undefined)
                ? options.snapshotId
                : (
                    !isHistoricalOption && zaloScanPlan && zaloScanPlan.snapshot_id
                        ? zaloScanPlan.snapshot_id
                        : null
                );
            const resolvedPeriodKey = (options && options.periodKey !== undefined)
                ? normalizePeriodKey(options.periodKey)
                : normalizePeriodKey(
                    resolveResultsPeriodKey(
                        resultsArray,
                        options && options.payload ? options.payload : null,
                        resolvedSnapshotId,
                    )
                );
            const signature = JSON.stringify(resultsArray);
            if (!force && signature === lastResultsSignature) {
                if (!preserveOrigin) {
                    latestZaloResultsSnapshotId = resolvedSnapshotId;
                    latestZaloResultsPeriodKey = resolvedPeriodKey;
                } else if (
                    !latestZaloResultsPeriodKey
                    && latestZaloResultsSnapshotId != null
                    && zaloScanPlan
                    && zaloScanPlan.snapshot_id === latestZaloResultsSnapshotId
                ) {
                    latestZaloResultsPeriodKey = currentRequiredPeriodKey();
                }
                return;
            }
            lastResultsSignature = signature;
            latestZaloResults = resultsArray;
            if (!preserveOrigin) {
                latestZaloResultsSnapshotId = resolvedSnapshotId;
                latestZaloResultsPeriodKey = resolvedPeriodKey;
            } else if (
                !latestZaloResultsPeriodKey
                && latestZaloResultsSnapshotId != null
                && zaloScanPlan
                && zaloScanPlan.snapshot_id === latestZaloResultsSnapshotId
            ) {
                latestZaloResultsPeriodKey = currentRequiredPeriodKey();
            }
            const dashboardResults = dashboardResultsForRequiredWeek(resultsArray);
            renderSpecsEvidenceTable(dashboardResults);
            let total = dashboardResults.length, success = 0, exempt = 0, failed = 0;
            dashboardResults.forEach(r => {
                const status = normalizeZaloStatusDisplay(r.status, r);
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
                const status = normalizeZaloStatusDisplay(r.status, r);
                const lmsStatusText = resolveDashboardRowLmsStatus(r);
                const issueText = resolveDashboardRowIssueDescription(r);
                const isFailed = isUnreportedOrWarningZaloStatus(status, r)
                    || lmsStatusText.startsWith('❌')
                    || lmsStatusText.startsWith('⚠️')
                    || Boolean(issueText);
                if (filterUnreported && !isFailed) return false;
                return !searchValue
                    || String(r.group || '').toLowerCase().includes(searchValue)
                    || String(r.person || '').toLowerCase().includes(searchValue)
                    || issueText.toLowerCase().includes(searchValue);
            });
            tbody.replaceChildren();
            rows.forEach(r => {
                const tr = document.createElement('tr');
                tr.className = 'result-row transition-colors';
                const values = [r.group, r.person, r.timestamp || 'Hôm nay', r.status];
                values.forEach((value, valueIndex) => {
                    const td = document.createElement('td');
                    td.className = 'px-6 py-3.5';
                    if (valueIndex === 3) {
                        const status = normalizeZaloStatusDisplay(value, r);
                        td.classList.add('status-cell');
                        if (status.includes('Miễn')) {
                            td.classList.add('status-cell--exempt');
                        } else if (status.includes('✅')) {
                            td.classList.add('status-cell--success');
                        } else {
                            td.classList.add('status-cell--error');
                        }
                        td.textContent = status;
                        const detailText = normalizeZaloStatusDetailDisplay(r.status_detail, r);
                        if (detailText) {
                            const detail = document.createElement('div');
                            detail.className = 'status-detail';
                            detail.textContent = detailText;
                            td.appendChild(detail);
                        }
                        const resolvedLms = resolveDashboardRowLmsStatus(r);
                        if (resolvedLms && resolvedLms !== 'Chưa quét LMS') {
                            const lmsDetail = document.createElement('div');
                            lmsDetail.className = 'status-detail';
                            lmsDetail.textContent = `LMS: ${resolvedLms}`;
                            td.appendChild(lmsDetail);
                        }
                        const resolvedEval = resolveDashboardRowEvaluationStatus(r);
                        if (resolvedEval) {
                            const evalDetail = document.createElement('div');
                            evalDetail.className = 'status-detail font-medium text-slate-700';
                            evalDetail.textContent = `Đánh giá: ${resolvedEval}`;
                            td.appendChild(evalDetail);
                        }
                        const resolvedIssue = resolveDashboardRowIssueDescription(r);
                        if (resolvedIssue) {
                            const issueDetail = document.createElement('div');
                            issueDetail.className = 'status-detail font-semibold text-rose-700';
                            issueDetail.textContent = `Vấn đề: ${resolvedIssue}`;
                            td.appendChild(issueDetail);
                        }
                        const st = r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
                        const instructorNote = String(r.instructor_note || st.instructor_note || '').trim();
                        if (instructorNote) {
                            const noteDetail = document.createElement('div');
                            noteDetail.className = 'status-detail text-slate-600';
                            noteDetail.textContent = `Giải trình GV: ${instructorNote}`;
                            td.appendChild(noteDetail);
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
                            targetMeta.textContent = `W${target.week}/${target.year} · Buổi ${lesson} · ${classDate}`;
                            td.appendChild(targetMeta);
                        }
                    } else {
                        td.textContent = String(value || '');
                    }
                    tr.appendChild(td);
                });
                const action = document.createElement('td');
                action.className = 'px-6 py-3.5 text-center';
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
                const rowStatus = normalizeZaloStatusDisplay(r.status, r);
                const rowTargetKey = canonicalTargetKey(r.scan_target);
                if (rowTargetKey && isUnreportedOrWarningZaloStatus(rowStatus, r)) {
                    const availableEvidencePath = getAvailableRowEvidencePath(r);
                    const rowSourceJobId = resolveResultRowSourceJobId(r);
                    if (availableEvidencePath && rowSourceJobId) {
                        const approveBtn = document.createElement('button');
                        approveBtn.type = 'button';
                        approveBtn.className = 'table-link';
                        approveBtn.textContent = '✅ Duyệt & Tích';
                        approveBtn.title = `Duyệt tay và tích BCNB cho lớp ${r.group || ''}`;
                        approveBtn.addEventListener('click', () => manualApproveZaloTarget(r, availableEvidencePath, rowSourceJobId));
                        actions.appendChild(approveBtn);
                    }
                    if (!isHistoricalWeekViewActive()) {
                        const rescanBtn = document.createElement('button');
                        rescanBtn.type = 'button';
                        rescanBtn.className = 'table-link';
                        rescanBtn.textContent = 'Quét lại';
                        rescanBtn.title = `Quét lại riêng lớp ${r.group || ''}`;
                        rescanBtn.addEventListener('click', () => startScan([rowTargetKey]));
                        actions.appendChild(rescanBtn);
                    }
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
                const period = effectiveDashboardPeriod();
                td.textContent = period
                    ? `Không có kết quả cho tuần cần báo cáo W${period.week}/${period.year}`
                    : 'Chưa có tuần cần báo cáo để hiển thị';
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
                btn.innerHTML = '<i class="fa-solid fa-filter mr-2"></i> Đang lọc: Cần xem lại';
            } else {
                btn.classList.remove('bg-rose-500/20', 'text-rose-300', 'border-rose-500/40');
                btn.innerHTML = '<i class="fa-solid fa-filter mr-2"></i> Cần xem lại';
            }
            renderZaloResults(latestZaloResults, true, {
                preserveOrigin: true,
                isHistoricalView: isHistoricalWeekViewActive(),
            });
        }

        function buildZaloTeacherReminderText(resultsArray) {
            const period = effectiveDashboardPeriod();
            if (!period) {
                return { text: '', itemCount: 0, teacherCount: 0, totalCount: 0, missingPeriod: true };
            }
            const sourceRows = dashboardResultsForRequiredWeek(Array.isArray(resultsArray) ? resultsArray : []);
            const unreported = sourceRows.filter(r => {
                const status = normalizeZaloStatusDisplay(r && r.status, r);
                return isUnreportedOrWarningZaloStatus(status, r);
            });
            if (unreported.length === 0) {
                return { text: '', itemCount: 0, teacherCount: 0, totalCount: sourceRows.length, missingPeriod: false };
            }
            const byTeacher = new Map();
            unreported.forEach(r => {
                const teacher = String((r && r.person) || '').trim() || 'Chưa phân công';
                const groupName = String((r && r.group) || '').trim() || 'Không rõ nhóm';
                const target = r && r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : null;
                const lesson = target && target.lesson != null && String(target.lesson).trim() !== ''
                    ? `Buổi ${String(target.lesson).trim()}`
                    : '';
                const classDate = target && (target.class_date || target.class_date_iso)
                    ? String(target.class_date || target.class_date_iso).trim()
                    : '';
                const metaParts = [lesson, classDate].filter(Boolean);
                const itemLabel = metaParts.length ? `${groupName} (${metaParts.join(' - ')})` : groupName;
                if (!byTeacher.has(teacher)) byTeacher.set(teacher, []);
                byTeacher.get(teacher).push(itemLabel);
            });
            const periodLabel = `Tuần ${period.week}/${period.year}`;
            const lines = [
                `📢 Nhắc báo cáo Zalo sau buổi học (${periodLabel} - ${unreported.length} lớp):`
            ];
            byTeacher.forEach((items, teacher) => {
                lines.push(`- GV ${teacher}: ${items.join(', ')}`);
            });
            return {
                text: lines.join('\n'),
                itemCount: unreported.length,
                teacherCount: byTeacher.size,
                totalCount: sourceRows.length,
                missingPeriod: false,
            };
        }

        async function copyZaloTeacherReminders() {
            const summary = buildZaloTeacherReminderText(latestZaloResults);
            if (summary.missingPeriod) {
                alert('Chưa có tuần cần báo cáo hợp lệ để tổng hợp.');
                return;
            }
            if (summary.totalCount === 0) {
                alert('Chưa có dữ liệu kết quả quét Zalo để tổng hợp.');
                return;
            }
            if (summary.itemCount === 0 || !summary.text) {
                alert('Tất cả các lớp trong tuần kiểm tra đã hoàn thành hoặc được miễn báo cáo!');
                return;
            }
            try {
                await navigator.clipboard.writeText(summary.text);
                alert(`Đã copy danh sách nhắc giáo viên (${summary.itemCount} lớp / ${summary.teacherCount} giáo viên)!`);
            } catch (err) {
                alert('Không thể copy vào clipboard: ' + (err && err.message ? err.message : String(err)));
            }
        }

        let latestZaloResultsPeriodKey = '';
        let latestZaloResultsSnapshotId = null;
        let latestZaloResultsJobId = null;
        let selectiveBaselineResults = [];
        let selectiveBaselinePeriodKey = '';
        let selectiveBaselineSnapshotId = null;

        function getAvailableRowEvidencePaths(r) {
            if (!r || typeof r !== 'object') return [];
            const evidence = r.evidence && typeof r.evidence === 'object' ? r.evidence : null;
            const evidenceImages = evidence && evidence.images && typeof evidence.images === 'object'
                ? evidence.images
                : {};
            const orderedKeys = ['report', 'latest', 'oldest', 'oldest_message'];
            const paths = [];
            orderedKeys.forEach(key => {
                const item = evidenceImages[key];
                if (!item || typeof item !== 'object') return;
                const path = String(item.path || '').trim();
                const lifecycle = String(item.evidence_status || item.status || '');
                if (path && lifecycle === 'AVAILABLE' && !paths.includes(path)) {
                    paths.push(path);
                }
            });
            return paths;
        }

        function getAvailableRowEvidencePath(r) {
            const paths = getAvailableRowEvidencePaths(r);
            return paths.length > 0 ? paths[0] : '';
        }

        function resolveResultRowSourceJobId(r) {
            if (r && typeof r === 'object' && Number.isInteger(r.source_job_id) && r.source_job_id > 0) {
                return r.source_job_id;
            }
            const candidatePaths = [...getAvailableRowEvidencePaths(r)];
            if (r && typeof r === 'object' && r.screenshot_path) {
                candidatePaths.push(String(r.screenshot_path).trim());
            }
            for (const path of candidatePaths) {
                const apiMatch = path.match(/^\/api\/evidence\/(\d+)\//);
                if (apiMatch) {
                    const parsed = Number(apiMatch[1]);
                    if (Number.isInteger(parsed) && parsed > 0) return parsed;
                }
                const legacyMatch = path.match(/^\/static\/screenshots\/job-(\d+)\//);
                if (legacyMatch) {
                    const parsed = Number(legacyMatch[1]);
                    if (Number.isInteger(parsed) && parsed > 0) return parsed;
                }
            }
            if (Number.isInteger(latestZaloResultsJobId) && latestZaloResultsJobId > 0) {
                return latestZaloResultsJobId;
            }
            if (currentJobType === 'ZALO_SCAN' && Number.isInteger(currentJobId) && currentJobId > 0) {
                return currentJobId;
            }
            return null;
        }

        async function manualApproveZaloTarget(rowOrKey, evidencePath = '', explicitJobId = null) {
            if (!canStartAnotherJob()) return;
            const row = typeof rowOrKey === 'string'
                ? (Array.isArray(latestZaloResults) ? latestZaloResults : []).find(
                    item => canonicalTargetKey(item && item.scan_target) === rowOrKey
                )
                : rowOrKey;
            if (!row || typeof row !== 'object') {
                alert('Không tìm thấy dòng kết quả hợp lệ để duyệt.');
                return;
            }
            const status = normalizeZaloStatusDisplay(row.status, row);
            if (!isUnreportedOrWarningZaloStatus(status, row)) {
                alert('Chỉ có thể duyệt tay các lớp đang ở trạng thái cần xem lại hoặc chưa báo cáo.');
                return;
            }
            const target = row.scan_target && typeof row.scan_target === 'object' ? row.scan_target : null;
            const targetKey = canonicalTargetKey(target);
            if (!target || !targetKey) {
                alert('Dòng kết quả thiếu thông tin định danh buổi học BCNB hợp lệ.');
                return;
            }
            const availablePaths = getAvailableRowEvidencePaths(row);
            const candidatePath = String(evidencePath || (availablePaths[0] || '')).trim();
            if (!candidatePath || !availablePaths.includes(candidatePath)) {
                alert('Lớp này chưa có ảnh bằng chứng AVAILABLE hợp lệ để duyệt tay.');
                return;
            }
            const resolvedJobId = Number.isInteger(explicitJobId) && explicitJobId > 0
                ? explicitJobId
                : resolveResultRowSourceJobId(row);
            if (!Number.isInteger(resolvedJobId) || resolvedJobId <= 0) {
                alert('Không xác định được mã job gốc của bằng chứng; hãy chạy quét lại.');
                return;
            }
            if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
                const confirmed = window.confirm(
                    `Xác nhận duyệt tay báo cáo Zalo và tích BCNB cho lớp ${row.group || target.class_code} `
                    + `(W${target.week}/${target.year} · Buổi ${target.lesson} · ${target.class_date || target.class_date_iso})?`
                );
                if (!confirmed) return;
            }
            const requestBody = {
                job_id: resolvedJobId,
                group_name: String(row.group || target.group_name || '').trim(),
                class_code: String(target.class_code || '').trim().toUpperCase(),
                week: Number(target.week),
                year: Number(target.year),
                lesson: String(target.lesson || '').trim(),
                class_date_iso: String(target.class_date_iso || '').trim(),
                evidence_path: candidatePath,
            };
            try {
                const res = await fetch('/api/zalo-manual-approve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody),
                });
                const data = await res.json();
                if (!res.ok) {
                    const detail = data.detail;
                    const message = detail && typeof detail === 'object'
                        ? (detail.message || detail.code)
                        : (detail || data.message || 'Không thể duyệt tay BCNB.');
                    alert('Lỗi: ' + message);
                } else if (data.job_id) {
                    trackJob(data.job_id);
                }
            } catch (err) {
                alert('Lỗi kết nối đến server!');
            }
        }

        function applyManualApprovePayload(payload) {
            if (!payload || payload.kind !== 'zalo_manual_approve' || !payload.target_key) return;
            const targetKey = String(payload.target_key);
            const verifiedProduction = payload.dry_run === false && payload.outcome === 'VERIFIED';
            const approvedStatus = String(payload.status || '✅ Đã duyệt tay & Tích BCNB');
            const approvedDetail = payload.status_detail != null
                ? String(payload.status_detail)
                : (
                    verifiedProduction
                        ? `Duyệt tay bởi ${payload.approved_by || 'operator'}`
                        : `[DRY-RUN] Đã mô phỏng duyệt tay bởi ${payload.approved_by || 'operator'} (chưa tích thật)`
                );
            const sourceJobId = Number.isInteger(payload.source_job_id) && payload.source_job_id > 0
                ? payload.source_job_id
                : null;
            const mergeRow = existing => {
                const key = canonicalTargetKey(existing && existing.scan_target);
                if (key !== targetKey) return existing;
                const base = payload.updated_item && typeof payload.updated_item === 'object'
                    ? { ...existing, ...payload.updated_item }
                    : { ...existing };
                if (verifiedProduction) {
                    base.status = approvedStatus;
                    base.submitted_for_bcnb = true;
                    base.manual_approved = true;
                    base.evidence_state = 'MANUAL_APPROVED_VERIFIED';
                } else {
                    base.status = String(existing.status || payload.status || '⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công');
                    base.submitted_for_bcnb = false;
                    base.manual_approved = false;
                }
                base.status_detail = approvedDetail;
                base.bcnb_update_state = payload.outcome || (verifiedProduction ? 'VERIFIED' : 'DRY_RUN');
                if (sourceJobId && (!Number.isInteger(base.source_job_id) || base.source_job_id <= 0)) {
                    base.source_job_id = sourceJobId;
                }
                return base;
            };
            if (Array.isArray(latestZaloResults) && latestZaloResults.length > 0) {
                const nextResults = latestZaloResults.map(mergeRow);
                if (Array.isArray(selectiveBaselineResults) && selectiveBaselineResults.length > 0) {
                    selectiveBaselineResults = selectiveBaselineResults.map(mergeRow);
                }
                renderZaloResults(nextResults, true, {
                    preserveOrigin: true,
                    isHistoricalView: isHistoricalWeekViewActive(),
                });
                return;
            }
            if (Array.isArray(payload.items) && payload.items.length > 0) {
                const items = payload.items.map(item => (
                    item && typeof item === 'object'
                        && sourceJobId
                        && (!Number.isInteger(item.source_job_id) || item.source_job_id <= 0)
                        ? { ...item, source_job_id: sourceJobId }
                        : item
                ));
                renderZaloResults(items, true, {
                    snapshotId: payload.snapshot_id != null ? payload.snapshot_id : null,
                    payload,
                    sourceJobId: sourceJobId || undefined,
                });
            }
        }

        function canonicalTargetKey(target) {
            if (!target || typeof target !== 'object') return '';
            const week = Number(target.week);
            const year = Number(target.year);
            const classCode = String(target.class_code || '').trim().toUpperCase();
            const lesson = String(target.lesson || '').trim();
            const classDateIso = String(target.class_date_iso || '').trim();
            if (!Number.isInteger(week) || !Number.isInteger(year) || week < 1 || week > 53 || year <= 2000 || year > 2100) {
                return '';
            }
            if (!classCode || !lesson || !classDateIso) return '';
            return `${String(year).padStart(4, '0')}-W${String(week).padStart(2, '0')}|${classCode}|${lesson}|${classDateIso}`;
        }

        function currentRequiredPeriodKey() {
            const period = requiredDashboardPeriod();
            return period ? `${period.year}-W${period.week}` : '';
        }

        function normalizePeriodKey(rawKey) {
            const text = String(rawKey || '').trim();
            if (!text) return '';
            const isoMatch = text.match(/^(\d{4})-W(\d{1,2})$/i);
            if (isoMatch) {
                const year = Number(isoMatch[1]);
                const week = Number(isoMatch[2]);
                if (Number.isInteger(week) && Number.isInteger(year) && week >= 1 && week <= 53) {
                    return `${year}-W${week}`;
                }
            }
            const slashMatch = text.match(/^W?(\d{1,2})\/(\d{4})$/i);
            if (slashMatch) {
                const week = Number(slashMatch[1]);
                const year = Number(slashMatch[2]);
                if (Number.isInteger(week) && Number.isInteger(year) && week >= 1 && week <= 53) {
                    return `${year}-W${week}`;
                }
            }
            return text;
        }

        function resolveResultsPeriodKey(resultsArray, payload = null, snapshotId = null) {
            if (payload && payload.required_week && typeof payload.required_week === 'object') {
                const pWeek = Number(payload.required_week.week);
                const pYear = Number(payload.required_week.year);
                if (Number.isInteger(pWeek) && Number.isInteger(pYear) && pWeek >= 1 && pWeek <= 53) {
                    return `${pYear}-W${pWeek}`;
                }
            }
            const activePeriodKey = currentRequiredPeriodKey();
            if (
                activePeriodKey
                && snapshotId != null
                && zaloScanPlan
                && zaloScanPlan.snapshot_id === snapshotId
                && dashboardResultsForRequiredWeek(Array.isArray(resultsArray) ? resultsArray : []).length > 0
            ) {
                return activePeriodKey;
            }
            if (Array.isArray(resultsArray) && resultsArray.length > 0) {
                const periods = new Set();
                resultsArray.forEach(item => {
                    const target = item && item.scan_target;
                    const week = Number(target && target.week);
                    const year = Number(target && target.year);
                    if (Number.isInteger(week) && Number.isInteger(year) && week >= 1 && week <= 53) {
                        periods.add(`${year}-W${week}`);
                    }
                });
                if (periods.size === 1) {
                    return [...periods][0];
                }
            }
            return '';
        }

        function hasActiveSelectiveScanBaseline() {
            const periodKey = normalizePeriodKey(currentRequiredPeriodKey());
            const snapshotId = zaloScanPlan && zaloScanPlan.snapshot_id ? zaloScanPlan.snapshot_id : null;
            return Boolean(
                periodKey
                && snapshotId
                && selectiveBaselineResults.length > 0
                && normalizePeriodKey(selectiveBaselinePeriodKey) === periodKey
                && selectiveBaselineSnapshotId === snapshotId
            );
        }

        function rememberSelectiveScanBaseline() {
            if (isHistoricalWeekViewActive()) {
                clearSelectiveScanBaseline();
                return false;
            }
            const periodKey = normalizePeriodKey(currentRequiredPeriodKey());
            const snapshotId = zaloScanPlan && zaloScanPlan.snapshot_id ? zaloScanPlan.snapshot_id : null;
            if (
                !latestZaloResultsPeriodKey
                && periodKey
                && snapshotId != null
                && latestZaloResultsSnapshotId === snapshotId
                && dashboardResultsForRequiredWeek(Array.isArray(latestZaloResults) ? latestZaloResults : []).length > 0
            ) {
                latestZaloResultsPeriodKey = periodKey;
            }
            const normalizedResultsPeriodKey = normalizePeriodKey(latestZaloResultsPeriodKey);
            const hasMismatchedRowSnapshot = Array.isArray(latestZaloResults) && latestZaloResults.some(item => {
                if (!item || typeof item !== 'object') return false;
                if (Object.prototype.hasOwnProperty.call(item, 'snapshot_id')) {
                    return item.snapshot_id !== snapshotId;
                }
                if (Object.prototype.hasOwnProperty.call(item, 'originSnapshot')) {
                    return item.originSnapshot !== snapshotId;
                }
                return false;
            });
            if (
                !periodKey
                || !snapshotId
                || !Array.isArray(latestZaloResults)
                || latestZaloResults.length === 0
                || normalizedResultsPeriodKey !== periodKey
                || latestZaloResultsSnapshotId !== snapshotId
                || hasMismatchedRowSnapshot
            ) {
                clearSelectiveScanBaseline();
                return false;
            }
            selectiveBaselinePeriodKey = periodKey;
            selectiveBaselineSnapshotId = snapshotId;
            selectiveBaselineResults = latestZaloResults.map(item => ({ ...item }));
            return true;
        }

        function clearSelectiveScanBaseline() {
            selectiveBaselineResults = [];
            selectiveBaselinePeriodKey = '';
            selectiveBaselineSnapshotId = null;
        }

        function mergeSelectiveZaloResults(incomingItems, isSelectivePayload = false, payload = null) {
            const incoming = Array.isArray(incomingItems) ? incomingItems : [];
            const periodKey = currentRequiredPeriodKey();
            const snapshotId = zaloScanPlan && zaloScanPlan.snapshot_id ? zaloScanPlan.snapshot_id : null;
            const payloadSnapshotId = payload && payload.snapshot_id != null ? payload.snapshot_id : null;
            if (
                !isSelectivePayload
                || !periodKey
                || !snapshotId
                || selectiveBaselinePeriodKey !== periodKey
                || selectiveBaselineSnapshotId !== snapshotId
                || (payloadSnapshotId != null && payloadSnapshotId !== selectiveBaselineSnapshotId)
                || selectiveBaselineResults.length === 0
            ) {
                return incoming;
            }
            if (incoming.length === 0) {
                return selectiveBaselineResults.map(item => ({ ...item }));
            }
            const incomingByKey = new Map();
            incoming.forEach(item => {
                const key = canonicalTargetKey(item && item.scan_target);
                if (key) incomingByKey.set(key, item);
            });
            if (incomingByKey.size === 0) return incoming;
            const usedKeys = new Set();
            const merged = selectiveBaselineResults.map(existing => {
                const key = canonicalTargetKey(existing && existing.scan_target);
                if (key && incomingByKey.has(key)) {
                    usedKeys.add(key);
                    return incomingByKey.get(key);
                }
                return existing;
            });
            incoming.forEach(item => {
                const key = canonicalTargetKey(item && item.scan_target);
                if (!key || !usedKeys.has(key)) {
                    merged.push(item);
                }
            });
            selectiveBaselineResults = merged.map(item => ({ ...item }));
            return merged;
        }

        function collectFailedRequiredWeekTargetKeys(resultsArray) {
            const period = requiredDashboardPeriod();
            if (!period) {
                return { keys: [], missingPeriod: true, totalCount: 0, failedCount: 0, skippedWithoutIdentity: 0 };
            }
            const sourceRows = dashboardResultsForRequiredWeek(Array.isArray(resultsArray) ? resultsArray : []);
            const failedRows = sourceRows.filter(r => {
                const status = normalizeZaloStatusDisplay(r && r.status, r);
                return isUnreportedOrWarningZaloStatus(status, r);
            });
            const keys = [];
            const seen = new Set();
            let skippedWithoutIdentity = 0;
            failedRows.forEach(r => {
                const key = canonicalTargetKey(r && r.scan_target);
                if (!key) {
                    skippedWithoutIdentity += 1;
                    return;
                }
                if (!seen.has(key)) {
                    seen.add(key);
                    keys.push(key);
                }
            });
            return {
                keys,
                missingPeriod: false,
                totalCount: sourceRows.length,
                failedCount: failedRows.length,
                skippedWithoutIdentity,
            };
        }

        async function startSelectiveRescanFailed() {
            if (isHistoricalWeekViewActive()) {
                alert('Đang ở chế độ xem lịch sử theo tuần; chỉ có thể quét lại lớp lỗi cho tuần cần báo cáo hiện tại.');
                return;
            }
            const summary = collectFailedRequiredWeekTargetKeys(latestZaloResults);
            if (summary.missingPeriod) {
                alert('Chưa có tuần cần báo cáo hợp lệ để quét lại.');
                return;
            }
            if (summary.totalCount === 0) {
                alert('Chưa có dữ liệu kết quả quét Zalo để quét lại.');
                return;
            }
            if (summary.failedCount === 0) {
                alert('Tất cả các lớp trong tuần kiểm tra đã hoàn thành hoặc được miễn báo cáo!');
                return;
            }
            if (summary.keys.length === 0) {
                alert('Các dòng lỗi hiện tại thiếu định danh target chuẩn; hãy chạy quét đầy đủ.');
                return;
            }
            await startScan(summary.keys);
        }

        const WEEKLY_CSV_HEADERS = [
            'Mã lớp',
            'Giáo viên',
            'Buổi học',
            'Ngày học',
            'Trạng thái Zalo',
            'Trạng thái LMS',
            'Tình trạng đánh giá',
            'Mô tả vấn đề (TE/TF/QC)',
            'Thời gian kiểm chứng',
        ];

        function sanitizeCsvCell(value) {
            if (value == null) return '';
            const raw = String(value);
            const hadLeadingTabOrCr = raw.startsWith('\t') || raw.startsWith('\r');
            const cleaned = raw.replace(/[\r\n\t]+/g, ' ').trim();
            if (!cleaned) return '';
            if (hadLeadingTabOrCr || ['=', '+', '-', '@'].includes(cleaned[0])) {
                return `'${cleaned}`;
            }
            return cleaned;
        }

        function escapeCsvField(value) {
            const sanitized = sanitizeCsvCell(value);
            if (
                sanitized.includes(',')
                || sanitized.includes('"')
                || sanitized.includes('\n')
                || sanitized.includes('\r')
            ) {
                return `"${sanitized.replace(/"/g, '""')}"`;
            }
            return sanitized;
        }

        function extractRowClassCodeForCsv(item) {
            if (!item || typeof item !== 'object') return 'KHONG_RO_LOP';
            const st = item.scan_target && typeof item.scan_target === 'object' ? item.scan_target : null;
            if (st && st.class_code) return String(st.class_code).trim().toUpperCase();
            if (item.class_code) return String(item.class_code).trim().toUpperCase();
            const ev = item.evidence && typeof item.evidence === 'object' ? item.evidence : null;
            const ctx = ev && ev.date_context && typeof ev.date_context === 'object' ? ev.date_context : null;
            if (ctx && ctx.target_class_code) return String(ctx.target_class_code).trim().toUpperCase();
            const grp = String(item.group || '').trim();
            if (grp) {
                return grp.replace(/^MindX\s*[-–—]\s*/i, '').replace(/\s*\([^)]*\)\s*$/, '').trim().toUpperCase() || grp;
            }
            return 'KHONG_RO_LOP';
        }

        function buildWeeklySummaryCsvRowsFromDashboard(resultsArray, targetPeriod = null, targetJobId = null) {
            const period = targetPeriod || effectiveDashboardPeriod();
            if (!period) return [];
            const rawList = Array.isArray(resultsArray) ? resultsArray : [];
            const sourceRows = rawList.filter(r => {
                const target = r && r.scan_target;
                if (
                    !target
                    || Number(target.week) !== Number(period.week)
                    || Number(target.year) !== Number(period.year)
                ) {
                    return false;
                }
                if (targetJobId != null) {
                    const rowJobId = resolveResultRowSourceJobId(r);
                    return Number(rowJobId) === Number(targetJobId);
                }
                return true;
            });
            return sourceRows.map(r => {
                const st = r && r.scan_target && typeof r.scan_target === 'object' ? r.scan_target : {};
                const ev = r && r.evidence && typeof r.evidence === 'object' ? r.evidence : {};
                const verifiedAt = String(ev.scanned_at || r.timestamp || '—').trim() || '—';
                const zaloStatus = normalizeZaloStatusDisplay((r && r.status) || 'Chưa xác định', r);
                const lmsStatus = resolveDashboardRowLmsStatus(r);
                const evaluationStatus = resolveDashboardRowEvaluationStatus(r, zaloStatus, lmsStatus);
                const issueDescription = resolveDashboardRowIssueDescription(r);
                return {
                    class_code: extractRowClassCodeForCsv(r),
                    teacher: String((r && r.person) || 'Chưa phân công').trim() || 'Chưa phân công',
                    lesson: String(st.lesson != null ? st.lesson : '—').trim() || '—',
                    class_date: String(st.class_date || st.class_date_iso || '—').trim() || '—',
                    zalo_status: zaloStatus,
                    lms_status: lmsStatus,
                    evaluation_status: evaluationStatus,
                    issue_description: issueDescription,
                    verified_at: verifiedAt,
                };
            });
        }

        function isFailedWeeklySummaryRow(row) {
            if (!row || typeof row !== 'object') return false;
            const zaloStatus = normalizeZaloStatusDisplay(row.zalo_status || row.status || '', row);
            const lmsStatus = String(row.lms_status || resolveDashboardRowLmsStatus(row)).trim();
            const normalizedEval = String(resolveDashboardRowEvaluationStatus(row) || '').trim();
            const rawEval = String(row.evaluation_status || '').trim();
            const evalStatus = normalizedEval || (
                zaloStatus.includes('Miễn')
                && (/zalo\s+cần\s+kiểm\s+tra/i.test(rawEval) || /chưa\s+nhận\s+xét\s+zalo/i.test(rawEval))
                    ? ''
                    : rawEval
            );
            const issueDesc = String(
                row.issue_description != null ? row.issue_description : resolveDashboardRowIssueDescription(row)
            ).trim();
            const zaloOkOrExempt = zaloStatus.includes('✅') || zaloStatus.includes('Miễn');
            const lmsFailed = lmsStatus.startsWith('❌') || lmsStatus.startsWith('⚠️');
            const evalFailed = evalStatus.startsWith('❌') || evalStatus.startsWith('⚠️');
            return !zaloOkOrExempt || lmsFailed || evalFailed || Boolean(issueDesc);
        }

        function filterFailedWeeklySummaryRows(rows) {
            return (Array.isArray(rows) ? rows : []).filter(isFailedWeeklySummaryRow);
        }

        function buildWeeklySummaryCsv(rows) {
            const safeRows = Array.isArray(rows) ? rows : [];
            const lines = [WEEKLY_CSV_HEADERS.map(escapeCsvField).join(',')];
            safeRows.forEach(row => {
                if (!row || typeof row !== 'object') return;
                const zaloStatus = normalizeZaloStatusDisplay(row.zalo_status || row.status || '', row);
                const lmsStatus = String(row.lms_status || resolveDashboardRowLmsStatus(row)).trim();
                const normalizedEval = String(resolveDashboardRowEvaluationStatus(row) || '').trim();
                const rawEval = String(row.evaluation_status || '').trim();
                const evaluationStatus = normalizedEval || (
                    zaloStatus.includes('Miễn')
                    && (/zalo\s+cần\s+kiểm\s+tra/i.test(rawEval) || /chưa\s+nhận\s+xét\s+zalo/i.test(rawEval))
                        ? '⏳ Đã hoàn tất nhận xét Zalo, chưa nhận xét LMS'
                        : rawEval
                ) || '—';
                const issueDescription = String(
                    row.issue_description != null ? row.issue_description : resolveDashboardRowIssueDescription(row)
                ).trim();
                const cells = [
                    row.class_code,
                    row.teacher,
                    row.lesson,
                    row.class_date,
                    zaloStatus,
                    lmsStatus,
                    evaluationStatus,
                    issueDescription,
                    row.verified_at,
                ];
                lines.push(cells.map(escapeCsvField).join(','));
            });
            return '\uFEFF' + lines.join('\r\n') + '\r\n';
        }

        async function copyFailedEmailReportToClipboard(emailData) {
            if (!emailData || typeof emailData !== 'object') return false;
            const htmlPayload = String(emailData.html_body || emailData.html_table || '').trim();
            const textPayload = String(emailData.text_body || emailData.text_table || '').trim();
            if (!htmlPayload && !textPayload) return false;
            try {
                if (
                    typeof navigator !== 'undefined'
                    && navigator.clipboard
                    && typeof navigator.clipboard.write === 'function'
                    && typeof ClipboardItem === 'function'
                    && typeof Blob === 'function'
                    && htmlPayload
                ) {
                    const itemData = {
                        'text/html': new Blob([htmlPayload], { type: 'text/html' }),
                    };
                    if (textPayload) {
                        itemData['text/plain'] = new Blob([textPayload], { type: 'text/plain' });
                    }
                    await navigator.clipboard.write([new ClipboardItem(itemData)]);
                    return true;
                }
                if (
                    typeof navigator !== 'undefined'
                    && navigator.clipboard
                    && typeof navigator.clipboard.writeText === 'function'
                ) {
                    await navigator.clipboard.writeText(textPayload || htmlPayload);
                    return true;
                }
            } catch (_) {
                // Bỏ qua lỗi quyền truy cập clipboard trong trình duyệt
            }
            return false;
        }

        function triggerCsvFileDownload(csvText, filename) {
            lastExportedWeeklyCsv = String(csvText || '');
            if (
                typeof Blob === 'function'
                && typeof URL !== 'undefined'
                && typeof URL.createObjectURL === 'function'
                && typeof document !== 'undefined'
                && typeof document.createElement === 'function'
            ) {
                try {
                    const blob = new Blob([lastExportedWeeklyCsv], { type: 'text/csv;charset=utf-8;' });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename || 'Bao_Cao_Tuan.csv';
                    link.style = 'display:none';
                    if (document.body && typeof document.body.appendChild === 'function') {
                        document.body.appendChild(link);
                    }
                    if (typeof link.click === 'function') {
                        link.click();
                    }
                    if (typeof URL.revokeObjectURL === 'function') {
                        setTimeout(() => URL.revokeObjectURL(url), 1000);
                    }
                } catch (_) {
                    // Bỏ qua lỗi tạo link tải trong môi trường headless/test
                }
            }
            return lastExportedWeeklyCsv;
        }

        function parseWeekSelectValue(rawValue) {
            const text = String(rawValue || '').trim();
            if (!text) return null;
            const isoMatch = text.match(/^(\d{4})-W(\d{1,2})$/i);
            if (isoMatch) {
                const year = Number(isoMatch[1]);
                const week = Number(isoMatch[2]);
                if (Number.isInteger(week) && Number.isInteger(year) && week >= 1 && week <= 53) {
                    return { week, year };
                }
            }
            const slashMatch = text.match(/^W?(\d{1,2})\/(\d{4})$/i);
            if (slashMatch) {
                const week = Number(slashMatch[1]);
                const year = Number(slashMatch[2]);
                if (Number.isInteger(week) && Number.isInteger(year) && week >= 1 && week <= 53) {
                    return { week, year };
                }
            }
            return null;
        }

        function updateWeeklyHistoryStatusBadge() {
            const badge = document.getElementById('weekly-history-status-badge');
            if (!badge) return;
            if (!isHistoricalWeekViewActive()) {
                const req = requiredDashboardPeriod();
                badge.textContent = req
                    ? `Chế độ: Tuần hiện tại W${req.week}/${req.year}`
                    : 'Chế độ: Theo dõi phiên hiện tại';
                return;
            }
            const w = Number(weeklyHistoryView.week);
            const y = Number(weeklyHistoryView.year);
            const jobId = weeklyHistoryView.jobId;
            const lms = weeklyHistoryView.lmsSummary || {};
            let lmsText = ' · LMS: chưa quét';
            if (lms.has_lms_data) {
                const lmsParts = [`${lms.ticked_count || 0} đã tích`];
                if (Number(lms.dry_run_count || 0) > 0) {
                    lmsParts.push(`${lms.dry_run_count} chạy thử (dry-run)`);
                }
                if (Number(lms.already_checked_count || 0) > 0) {
                    lmsParts.push(`${lms.already_checked_count} đã tích sẵn`);
                }
                if (Number(lms.pending_tick_count || 0) > 0) {
                    lmsParts.push(`${lms.pending_tick_count} chờ tích`);
                }
                lmsParts.push(`${lms.missing_count || 0} trễ`);
                lmsText = ` · LMS: ${lmsParts.join(', ')}`;
            }
            const jobText = jobId ? `Job #${jobId}` : 'Tổng hợp tuần';
            badge.textContent = `Đang xem lịch sử W${w}/${y} (${jobText})${lmsText}`;
        }

        function syncWeeklyHistoryControlsWithPlan() {
            const weekSelect = document.getElementById('history-week-select');
            if (!weekSelect) return;
            const knownMap = new Map();
            const addPeriod = (w, y) => {
                const week = Number(w);
                const year = Number(y);
                if (!Number.isInteger(week) || !Number.isInteger(year) || week < 1 || week > 53) return;
                const key = `${String(year).padStart(4, '0')}-W${String(week).padStart(2, '0')}`;
                if (!knownMap.has(key)) {
                    knownMap.set(key, { week, year, period_key: key, label: `W${String(week).padStart(2, '0')}/${year}` });
                }
            };
            if (Array.isArray(weeklyHistoryAvailableWeeks)) {
                weeklyHistoryAvailableWeeks.forEach(item => {
                    if (item && typeof item === 'object') addPeriod(item.week, item.year);
                });
            }
            if (zaloScanPlan && typeof zaloScanPlan === 'object') {
                if (zaloScanPlan.required_week) {
                    addPeriod(zaloScanPlan.required_week.week, zaloScanPlan.required_week.year);
                }
                if (Array.isArray(zaloScanPlan.selected_weeks)) {
                    zaloScanPlan.selected_weeks.forEach(item => {
                        if (item && typeof item === 'object') addPeriod(item.week, item.year);
                    });
                }
            }
            const sorted = [...knownMap.values()].sort((a, b) => (b.year - a.year) || (b.week - a.week));
            const currentVal = weekSelect.value;
            weekSelect.replaceChildren();
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            const req = requiredDashboardPeriod();
            defaultOpt.textContent = req
                ? `Tuần hiện tại (W${req.week}/${req.year})`
                : 'Tuần hiện tại (theo kế hoạch)';
            weekSelect.appendChild(defaultOpt);
            sorted.forEach(item => {
                const opt = document.createElement('option');
                opt.value = item.period_key;
                opt.textContent = `Tuần ${item.label}`;
                weekSelect.appendChild(opt);
            });
            if (isHistoricalWeekViewActive()) {
                const activeKey = `${String(weeklyHistoryView.year).padStart(4, '0')}-W${String(weeklyHistoryView.week).padStart(2, '0')}`;
                weekSelect.value = activeKey;
            } else if (currentVal && knownMap.has(currentVal)) {
                weekSelect.value = currentVal;
            } else {
                weekSelect.value = '';
            }
            updateWeeklyHistoryStatusBadge();
        }

        function renderWeeklyHistoryControls(summaryPayload) {
            if (summaryPayload && Array.isArray(summaryPayload.available_weeks)) {
                weeklyHistoryAvailableWeeks = summaryPayload.available_weeks;
            }
            if (summaryPayload && Array.isArray(summaryPayload.jobs)) {
                weeklyHistoryJobs = summaryPayload.jobs;
            }
            syncWeeklyHistoryControlsWithPlan();
            const jobSelect = document.getElementById('history-job-select');
            if (jobSelect) {
                jobSelect.replaceChildren();
                const allOpt = document.createElement('option');
                allOpt.value = '';
                allOpt.textContent = 'Mới nhất / Tổng hợp tuần';
                jobSelect.appendChild(allOpt);
                (Array.isArray(weeklyHistoryJobs) ? weeklyHistoryJobs : []).forEach(job => {
                    if (!job || !job.job_id) return;
                    const opt = document.createElement('option');
                    opt.value = String(job.job_id);
                    const modeLabel = job.execution_mode === 'production' ? 'Prod' : 'Dry-run';
                    const selLabel = job.selective_rescan ? ' · Quét chọn lọc' : '';
                    opt.textContent = `Job #${job.job_id} (${job.status || 'UNKNOWN'} · ${modeLabel}${selLabel} · ${job.target_count || 0} lớp)`;
                    jobSelect.appendChild(opt);
                });
                if (weeklyHistoryView && weeklyHistoryView.jobId) {
                    jobSelect.value = String(weeklyHistoryView.jobId);
                } else {
                    jobSelect.value = '';
                }
            }
            updateWeeklyHistoryStatusBadge();
            if (typeof applyZaloRunState === 'function') {
                applyZaloRunState();
            }
        }

        async function loadWeeklyHistorySummary(options = {}) {
            const weekSelect = document.getElementById('history-week-select');
            const jobSelect = document.getElementById('history-job-select');
            const keepLiveMode = Boolean(options && options.keepLiveMode);
            let targetPeriod = null;
            if (options && Number.isInteger(Number(options.week)) && Number.isInteger(Number(options.year))) {
                targetPeriod = { week: Number(options.week), year: Number(options.year) };
            } else if (weekSelect && weekSelect.value) {
                targetPeriod = parseWeekSelectValue(weekSelect.value);
            } else {
                targetPeriod = effectiveDashboardPeriod();
            }
            let explicitJobId = null;
            if (options && options.jobId !== undefined) {
                explicitJobId = options.jobId ? Number(options.jobId) : null;
            } else if (jobSelect && jobSelect.value) {
                const parsedJob = Number(jobSelect.value);
                if (Number.isInteger(parsedJob) && parsedJob > 0) explicitJobId = parsedJob;
            }
            const params = [];
            if (targetPeriod) {
                params.push(`week=${encodeURIComponent(targetPeriod.week)}`);
                params.push(`year=${encodeURIComponent(targetPeriod.year)}`);
            }
            if (explicitJobId) {
                params.push(`job_id=${encodeURIComponent(explicitJobId)}`);
            }
            const query = params.length > 0 ? `?${params.join('&')}` : '';
            const requestSeq = (typeof weeklyHistoryRequestSeq === 'number')
                ? (++weeklyHistoryRequestSeq)
                : 1;
            try {
                const res = await fetch(`/api/reports/weekly-summary${query}`);
                const data = await res.json();
                if (typeof weeklyHistoryRequestSeq === 'number' && requestSeq !== weeklyHistoryRequestSeq) {
                    return null;
                }
                if (!res.ok) {
                    if (keepLiveMode) {
                        renderZaloResults([], true, {
                            snapshotId: null,
                            periodKey: '',
                            sourceJobId: null,
                        });
                    }
                    const detail = data && data.detail;
                    const message = detail && typeof detail === 'object'
                        ? (detail.message || detail.code)
                        : (detail || 'Không thể tải lịch sử báo cáo tuần.');
                    alert('Lỗi: ' + message);
                    return null;
                }
                if (!keepLiveMode && data && data.week != null && data.year != null) {
                    weeklyHistoryView = {
                        week: Number(data.week),
                        year: Number(data.year),
                        jobId: explicitJobId || null,
                        selectedJobId: data.selected_job_id || null,
                        rows: Array.isArray(data.rows) ? data.rows : [],
                        summary: data.summary || null,
                        lmsSummary: data.lms_summary || null,
                    };
                } else {
                    weeklyHistoryView = null;
                }
                renderWeeklyHistoryControls(data);
                if (keepLiveMode && weekSelect) {
                    weekSelect.value = '';
                }
                const items = Array.isArray(data && data.items) ? data.items : [];
                const summarySnapshotId = (
                    data
                    && data.snapshot_id != null
                    && !data.is_selective_only
                ) ? data.snapshot_id : null;
                const summaryPeriodKey = (
                    data
                    && Number.isInteger(Number(data.week))
                    && Number.isInteger(Number(data.year))
                )
                    ? `${Number(data.year)}-W${Number(data.week)}`
                    : normalizePeriodKey((data && data.period_key) || '');
                const summaryJobId = (
                    data
                    && Number.isInteger(Number(data.selected_job_id))
                    && Number(data.selected_job_id) > 0
                )
                    ? Number(data.selected_job_id)
                    : null;
                if (
                    !keepLiveMode
                    || summarySnapshotId == null
                    || !zaloScanPlan
                    || zaloScanPlan.snapshot_id !== summarySnapshotId
                    || summaryPeriodKey !== normalizePeriodKey(currentRequiredPeriodKey())
                ) {
                    clearSelectiveScanBaseline();
                }
                renderZaloResults(items, true, {
                    isHistoricalView: !keepLiveMode,
                    preserveOrigin: false,
                    snapshotId: summarySnapshotId,
                    periodKey: summaryPeriodKey,
                    sourceJobId: summaryJobId,
                });
                return data;
            } catch (err) {
                if (typeof weeklyHistoryRequestSeq === 'number' && requestSeq !== weeklyHistoryRequestSeq) {
                    return null;
                }
                if (keepLiveMode) {
                    renderZaloResults([], true, {
                        snapshotId: null,
                        periodKey: '',
                        sourceJobId: null,
                    });
                }
                alert('Lỗi kết nối khi tải lịch sử tuần!');
                return null;
            }
        }

        async function onHistoryWeekChange() {
            const weekSelect = document.getElementById('history-week-select');
            const jobSelect = document.getElementById('history-job-select');
            if (jobSelect) jobSelect.value = '';
            if (!weekSelect || !weekSelect.value) {
                if (typeof weeklyHistoryRequestSeq === 'number') {
                    weeklyHistoryRequestSeq += 1;
                }
                weeklyHistoryView = null;
                clearSelectiveScanBaseline();
                updateWeeklyHistoryStatusBadge();
                if (typeof applyZaloRunState === 'function') {
                    applyZaloRunState();
                }
                const req = requiredDashboardPeriod();
                if (req) {
                    await loadWeeklyHistorySummary({
                        week: req.week,
                        year: req.year,
                        jobId: null,
                        keepLiveMode: true,
                    });
                } else {
                    renderZaloResults([], true, {
                        snapshotId: null,
                        periodKey: '',
                        sourceJobId: null,
                    });
                }
                return;
            }
            const period = parseWeekSelectValue(weekSelect.value);
            if (!period) return;
            await loadWeeklyHistorySummary({ week: period.week, year: period.year, jobId: null });
        }

        async function onHistoryJobChange() {
            const weekSelect = document.getElementById('history-week-select');
            const jobSelect = document.getElementById('history-job-select');
            const selectedJobId = jobSelect && jobSelect.value ? Number(jobSelect.value) : null;
            const validJobId = Number.isInteger(selectedJobId) && selectedJobId > 0 ? selectedJobId : null;
            const dropdownPeriod = weekSelect && weekSelect.value
                ? parseWeekSelectValue(weekSelect.value)
                : null;
            const period = dropdownPeriod || effectiveDashboardPeriod();
            await loadWeeklyHistorySummary({
                week: period ? period.week : undefined,
                year: period ? period.year : undefined,
                jobId: validJobId,
                keepLiveMode: !dropdownPeriod && !validJobId,
            });
        }

        async function exportWeeklySummaryCsv(options = {}) {
            const failedOnly = Boolean(options && options.failedOnly);
            const sendEmail = Boolean(options && options.sendEmail);
            const downloadCsv = (options && options.downloadCsv !== undefined)
                ? Boolean(options.downloadCsv)
                : !sendEmail;
            const requestedRecipients = (options && Array.isArray(options.recipients))
                ? options.recipients
                : null;
            const weekSelect = document.getElementById('history-week-select');
            const jobSelect = document.getElementById('history-job-select');
            const selectedFromDropdown = weekSelect && weekSelect.value
                ? parseWeekSelectValue(weekSelect.value)
                : null;
            const period = selectedFromDropdown || effectiveDashboardPeriod();
            if (!period) {
                alert('Chưa có tuần báo cáo hợp lệ để xuất CSV.');
                return '';
            }
            const matchesActiveHistoryPeriod = Boolean(
                isHistoricalWeekViewActive()
                && Number(weeklyHistoryView.week) === Number(period.week)
                && Number(weeklyHistoryView.year) === Number(period.year)
            );
            const selectedJobId = (jobSelect && jobSelect.value && Number.isInteger(Number(jobSelect.value)) && Number(jobSelect.value) > 0)
                ? Number(jobSelect.value)
                : (matchesActiveHistoryPeriod && weeklyHistoryView.jobId ? Number(weeklyHistoryView.jobId) : null);

            let snapshotFallbackRows = [];
            if (
                matchesActiveHistoryPeriod
                && (weeklyHistoryView.jobId || null) === (selectedJobId || null)
                && Array.isArray(weeklyHistoryView.rows)
            ) {
                snapshotFallbackRows = weeklyHistoryView.rows
                    .filter(r => r && typeof r === 'object')
                    .map(r => ({ ...r }));
            } else if (!isHistoricalWeekViewActive() && !selectedJobId) {
                snapshotFallbackRows = buildWeeklySummaryCsvRowsFromDashboard(latestZaloResults, period, null);
            }

            if (sendEmail) {
                const defaultFailedFilename = `Bao_Cao_Lop_Loi_Tuan_W${String(period.week).padStart(2, '0')}_${period.year}.csv`;
                try {
                    const emailPayload = {
                        week: Number(period.week),
                        year: Number(period.year),
                        job_id: selectedJobId || null,
                    };
                    if (requestedRecipients && requestedRecipients.length > 0) {
                        emailPayload.recipients = requestedRecipients;
                    }
                    const emailRes = await fetch('/api/reports/send-failed-csv-email', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(emailPayload),
                    });
                    const emailData = emailRes ? await emailRes.json() : null;
                    lastFailedCsvEmailResult = emailData;
                    if (emailRes && emailRes.ok && emailData) {
                        const filename = emailData.filename || defaultFailedFilename;
                        const serverCsvText = (typeof emailData.csv_text === 'string' && emailData.csv_text)
                            ? emailData.csv_text
                            : '';
                        if (serverCsvText) {
                            if (downloadCsv) {
                                triggerCsvFileDownload(serverCsvText, filename);
                            } else {
                                lastExportedWeeklyCsv = String(serverCsvText || '');
                            }
                        }
                        const recList = (Array.isArray(emailData.recipients) && emailData.recipients.length > 0)
                            ? emailData.recipients.join(', ')
                            : 'quangda@mindx.net.vn';
                        let clipboardFallbackWarning = '';
                        if (emailData.delivery_status === 'SMTP_NOT_CONFIGURED') {
                            const copiedOk = await copyFailedEmailReportToClipboard(emailData);
                            if (!copiedOk) {
                                clipboardFallbackWarning =
                                    '\n⚠️ Không thể tự động sao chép bảng báo cáo vào Clipboard; '
                                    + 'vui lòng sao chép thủ công danh sách lớp lỗi trước khi gửi để tránh thiếu lớp.';
                                const fullFallbackText = String(emailData.text_body || emailData.text_table || '').trim();
                                const promptFn = (typeof window !== 'undefined' && typeof window.prompt === 'function')
                                    ? window.prompt.bind(window)
                                    : (typeof prompt === 'function' ? prompt : null);
                                if (fullFallbackText && promptFn) {
                                    try {
                                        promptFn(
                                            'Trình duyệt chặn tự động copy vào Clipboard. Hãy nhấn Ctrl+C để sao chép đầy đủ danh sách lớp lỗi dưới đây:',
                                            fullFallbackText
                                        );
                                    } catch (_) {
                                        // ignore prompt error in headless mode
                                    }
                                }
                            }
                            if (
                                emailData.gmail_compose_url
                                && typeof window !== 'undefined'
                                && typeof window.open === 'function'
                            ) {
                                try {
                                    window.open(emailData.gmail_compose_url, '_blank', 'noopener,noreferrer');
                                } catch (_) {
                                    // ignore popup block in headless mode
                                }
                            }
                        }
                        const baseAlertMsg = emailData.message
                            || `Đã chuẩn bị báo cáo lớp lỗi (${emailData.failed_count || 0} lớp) gửi tới: ${recList}`;
                        alert(baseAlertMsg + clipboardFallbackWarning);
                        return serverCsvText;
                    }
                    const detail = emailData && emailData.detail;
                    const errCode = detail && typeof detail === 'object' ? detail.code : '';
                    const errMsg = detail && typeof detail === 'object'
                        ? (detail.message || detail.code || 'Không thể gửi email báo cáo.')
                        : (detail || 'Không thể gửi email báo cáo.');
                    if (
                        detail
                        && typeof detail === 'object'
                        && typeof detail.csv_text === 'string'
                        && detail.csv_text
                    ) {
                        const errFilename = detail.filename || defaultFailedFilename;
                        if (downloadCsv) {
                            triggerCsvFileDownload(detail.csv_text, errFilename);
                            alert(`Đã xuất file ${errFilename}, nhưng gửi email thất bại: ${errMsg}`);
                        } else {
                            lastExportedWeeklyCsv = String(detail.csv_text || '');
                            alert(`Gửi email báo cáo thất bại: ${errMsg}`);
                        }
                        return detail.csv_text;
                    }
                    if (errCode === 'NO_FAILED_CLASSES' || errCode === 'EMPTY_WEEKLY_SUMMARY') {
                        alert(errMsg);
                        return '';
                    }
                    alert('Lỗi: ' + errMsg);
                    return '';
                } catch (_) {
                    lastFailedCsvEmailResult = null;
                    alert(
                        'Chưa xác định trạng thái gửi email do mất kết nối phản hồi từ máy chủ. '
                        + 'Hệ thống không tự gửi lại và không tải CSV cục bộ để tránh lệch nội dung với email.'
                    );
                    return '';
                }
            }

            let rowsToExport = [];
            try {
                const params = [
                    `week=${encodeURIComponent(period.week)}`,
                    `year=${encodeURIComponent(period.year)}`,
                ];
                if (selectedJobId) {
                    params.push(`job_id=${encodeURIComponent(selectedJobId)}`);
                }
                const res = await fetch(`/api/reports/weekly-summary?${params.join('&')}`);
                if (res && res.ok) {
                    const payload = await res.json();
                    const payloadMatchesPeriod = !payload
                        || payload.week == null
                        || payload.year == null
                        || (Number(payload.week) === Number(period.week) && Number(payload.year) === Number(period.year));
                    if (payload && payloadMatchesPeriod && Array.isArray(payload.rows)) {
                        rowsToExport = payload.rows;
                    }
                } else if (res && !res.ok) {
                    let message = 'Không thể xuất báo cáo tuần.';
                    try {
                        const errData = await res.json();
                        const detail = errData && errData.detail;
                        message = detail && typeof detail === 'object'
                            ? (detail.message || detail.code || message)
                            : (detail || message);
                    } catch (_) {
                        // ignore json parse error
                    }
                    alert('Lỗi: ' + message);
                    return '';
                }
            } catch (_) {
                // Chỉ fallback sang snapshot bất biến đã chụp trước khi gửi request
                rowsToExport = snapshotFallbackRows;
            }

            if (rowsToExport.length === 0) {
                alert('Chưa có dữ liệu báo cáo trong tuần đã chọn để xuất CSV.');
                return '';
            }

            if (failedOnly) {
                rowsToExport = filterFailedWeeklySummaryRows(rowsToExport);
                if (rowsToExport.length === 0) {
                    alert('Không có lớp lỗi nào trong tuần đã chọn cần xuất CSV và gửi mail.');
                    return '';
                }
            }

            const csvText = buildWeeklySummaryCsv(rowsToExport);
            const filePrefix = failedOnly ? 'Bao_Cao_Lop_Loi_Tuan' : 'Bao_Cao_Tuan';
            const filename = `${filePrefix}_W${String(period.week).padStart(2, '0')}_${period.year}.csv`;
            if (downloadCsv) {
                triggerCsvFileDownload(csvText, filename);
            } else {
                lastExportedWeeklyCsv = String(csvText || '');
            }
            return csvText;
        }

        async function exportAndEmailFailedClassesCsv(options = {}) {
            return exportWeeklySummaryCsv({
                ...(options && typeof options === 'object' ? options : {}),
                failedOnly: true,
                sendEmail: true,
            });
        }

        document.getElementById('searchInput').addEventListener('input', () => {
            renderZaloResults(latestZaloResults, true, {
                preserveOrigin: true,
                isHistoricalView: isHistoricalWeekViewActive(),
            });
        });

        // Một lần tải ban đầu để khôi phục job đang chạy sau khi reload.
        // Các lần tiếp theo chỉ được lên lịch khi job thực sự còn hoạt động.
        updateDashboardData();
        if (typeof refreshZaloScanPlan === 'function') {
            refreshZaloScanPlan();
        }
