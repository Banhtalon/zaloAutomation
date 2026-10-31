        let currentJobId = null;
        let currentJobActive = false;
        let currentJobExecutionMode = null;
        let currentJobType = null;
        let jobPollingTimer = null;
        const JOB_POLL_INTERVAL_MS = 3000;
        const pageLoadAtMs = Date.now();
        const handledTerminalJobIds = new Set();
        const sessionTrackedJobIds = new Set();
        const terminalStatuses = new Set(['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'CANCELLED']);
        const HANDLED_TERMINAL_JOBS_STORAGE_KEY = 'zalo_handled_terminal_jobs';

        function loadSessionHandledTerminalJobs() {
            try {
                if (typeof sessionStorage !== 'undefined' && sessionStorage && typeof sessionStorage.getItem === 'function') {
                    const raw = sessionStorage.getItem(HANDLED_TERMINAL_JOBS_STORAGE_KEY);
                    const parsed = raw ? JSON.parse(raw) : [];
                    if (Array.isArray(parsed)) {
                        parsed.forEach(id => {
                            const num = Number(id);
                            if (Number.isInteger(num) && num > 0) handledTerminalJobIds.add(num);
                        });
                    }
                }
            } catch (_) {
                // Ignore storage errors in sandboxed/headless environments
            }
        }

        function markTerminalJobHandled(jobId) {
            const num = Number(jobId);
            if (!Number.isInteger(num) || num <= 0) return;
            handledTerminalJobIds.add(num);
            if (handledTerminalJobIds.size > 200) {
                handledTerminalJobIds.delete(handledTerminalJobIds.values().next().value);
            }
            try {
                if (typeof sessionStorage !== 'undefined' && sessionStorage && typeof sessionStorage.setItem === 'function') {
                    sessionStorage.setItem(
                        HANDLED_TERMINAL_JOBS_STORAGE_KEY,
                        JSON.stringify([...handledTerminalJobIds])
                    );
                }
            } catch (_) {
                // Ignore storage errors
            }
        }

        function isJobFinishedBeforePageLoad(job) {
            if (!job || typeof job !== 'object') return false;
            const jobId = Number(job.id);
            if (Number.isInteger(jobId) && sessionTrackedJobIds.has(jobId)) return false;
            if (!job.finished_at) return false;
            const finishedMs = Date.parse(String(job.finished_at));
            return Number.isFinite(finishedMs) && finishedMs < pageLoadAtMs;
        }

        loadSessionHandledTerminalJobs();
        let googleformUploadIds = [];
        let googleformUploadInFlight = 0;
        let googleformBatchId = null;
        let googleformBatchMarker = null;
        let ocrBusy = false;
        let lmsStudentsFetchBusy = false;
        let lmsBase64Image = null;
        let lmsUploadId = null;
        let lmsAnalysisJobId = null;
        let lmsConfirmationToken = null;
        let lmsPreviewPlan = null;
        let lmsSelectedClassesToTick = [];
        let lmsConfirmInFlight = false;
        let latestZaloResults = [];
        let latestLmsFetchUnavailable = false;
        let zaloScanPlan = null;
        let zaloPreflight = null;
        let weeklyHistoryView = null;
        let weeklyHistoryAvailableWeeks = [];
        let weeklyHistoryJobs = [];
        let weeklyHistoryRequestSeq = 0;
        let lastExportedWeeklyCsv = '';
        let lastFailedCsvEmailResult = null;

        function trackJob(jobId) {
            const num = Number(jobId);
            if (Number.isInteger(num) && num > 0) {
                sessionTrackedJobIds.add(num);
            }
            currentJobId = jobId;
            currentJobActive = true;
            applyZaloRunState();
            if (typeof restartJobPolling === 'function') {
                restartJobPolling();
            }
        }

        function canStartAnotherJob() {
            if (typeof lmsConfirmInFlight !== 'undefined' && lmsConfirmInFlight) {
                alert('Đang gửi xác nhận kế hoạch LMS. Hãy chờ phản hồi từ máy chủ.');
                return false;
            }
            if (typeof lmsStudentsFetchBusy !== 'undefined' && lmsStudentsFetchBusy) {
                alert('Đang gửi yêu cầu lấy danh sách học viên từ LMS. Hãy chờ hoàn tất.');
                return false;
            }
            if (!currentJobActive) return true;
            alert('Đang có một job hoạt động. Hãy chờ hoàn tất hoặc bấm Hủy job.');
            return false;
        }

        // TAB SWITCHING
        function switchTab(tabId) {
            const isDiagnostics = tabId === 'specs' || tabId === 'logs';
            const navTarget = isDiagnostics ? 'specs' : tabId;
            document.querySelectorAll('.tab-content').forEach(el => {
                const isSelected = isDiagnostics
                    ? (el.id === 'tab-specs' || el.id === 'tab-logs')
                    : (el.id === `tab-${tabId}`);
                el.classList.toggle('active', isSelected);
                el.hidden = !isSelected;
            });

            document.querySelectorAll('.tab-btn').forEach(el => {
                const isSelected = el.id === `btn-${navTarget}`;
                el.classList.toggle('active', isSelected);
                el.setAttribute('aria-selected', String(isSelected));
                el.tabIndex = isSelected ? 0 : -1;
            });

            const headerTitle = document.getElementById('header-title');
            if (tabId === 'dashboard') {
                headerTitle.innerText = "Zalo Auto";
            } else if (tabId === 'groups') {
                headerTitle.innerText = "Quản lý Cấu hình Nhóm Zalo";
                fetchGroups();
                fetchIgnoredGroups();
                if (typeof fetchBcnbZaloGaps === 'function') fetchBcnbZaloGaps();
            } else if (isDiagnostics) {
                headerTitle.innerText = "Chẩn đoán & Nhật ký";
                if (typeof refreshCurrentZaloPreflight === 'function') {
                    refreshCurrentZaloPreflight();
                }
                if (typeof refreshLogs === 'function') refreshLogs();
                if (currentJobId && typeof renderJobTimeline === 'function') {
                    renderJobTimeline(currentJobId);
                }
                if (tabId === 'logs') {
                    const logsEl = document.getElementById('tab-logs');
                    if (logsEl && typeof logsEl.scrollIntoView === 'function') {
                        logsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                }
            } else if (tabId === 'googleform') {
                headerTitle.innerText = "Hệ thống Nộp Form Tự động";
                fetchMappings();
                fetchGoogleFormHistory();
            }
        }

        // ZALO AUTOMATION APIS
        async function startAutoDiscover() {
            if (!canStartAnotherJob()) return;
            try {
                const res = await fetch('/auto-discover', { method: 'POST' });
                const data = await res.json();
                if(data.job_id) { trackJob(data.job_id); }
                alert(data.message);
            } catch (err) { alert("Lỗi kết nối đến server!"); }
        }

        function isZaloScanPlanRunnable(plan = zaloScanPlan) {
            if (!plan || !plan.snapshot_id) return false;
            if (plan.available === false || plan.stale === true || plan.reason_code === 'SCAN_PLAN_STALE') {
                return false;
            }
            const hasCounts = plan.target_count !== undefined || plan.exempt_target_count !== undefined;
            if (hasCounts) {
                const totalAdmitted = Number(plan.target_count || 0) + Number(plan.exempt_target_count || 0);
                if (totalAdmitted < 1) return false;
            }
            return true;
        }

        function canRunZaloScanNow() {
            const hasPlan = isZaloScanPlanRunnable(zaloScanPlan);
            const hasPreflight = Boolean(zaloPreflight);
            const blocked = getZaloPreflightStatus() === 'RED';
            return Boolean(!currentJobActive && hasPlan && hasPreflight && !blocked);
        }

        function refreshDashboardRequiredWeekView() {
            if (typeof syncWeeklyHistoryControlsWithPlan === 'function') {
                syncWeeklyHistoryControlsWithPlan();
            }
            if (Array.isArray(latestZaloResults)
                && latestZaloResults.length > 0
                && typeof renderZaloResults === 'function') {
                const keepHistory = typeof isHistoricalWeekViewActive === 'function' && isHistoricalWeekViewActive();
                renderZaloResults(latestZaloResults, true, {
                    preserveOrigin: true,
                    isHistoricalView: keepHistory,
                });
            }
        }

        function renderZaloScanPlan(plan) {
            const element = document.getElementById('zalo-scan-plan');
            const brief = document.getElementById('zalo-dashboard-plan-brief');
            const stripPlanText = typeof document.getElementById === 'function'
                ? document.getElementById('dashboard-plan-strip-text')
                : null;
            if (!element) return;
            if (typeof document.querySelector !== 'function') return;
            const scanButton = document.querySelector('.action-button--scan');
            const isStale = Boolean(plan && (plan.stale === true || plan.reason_code === 'SCAN_PLAN_STALE'));
            if (!plan || (!plan.snapshot_id && !isStale)
                || !Array.isArray(plan.selected_weeks)
                || plan.selected_weeks.length !== 2) {
                zaloScanPlan = null;
                element.textContent = 'Chưa có kế hoạch tuần BCNB. Hãy bấm “Dò kế hoạch” trước khi quét.';
                element.className = 'rounded-xl border-2 border-amber-400 border-l-8 border-l-amber-600 bg-amber-50 px-4 py-2.5 text-xs font-bold text-amber-950 shadow-sm';
                if (brief) brief.textContent = 'Chưa có kế hoạch tuần BCNB · Hãy bấm “Dò kế hoạch”';
                if (stripPlanText) stripPlanText.textContent = 'Chưa có kế hoạch tuần BCNB · Hãy bấm “Dò kế hoạch”';
                if (scanButton) scanButton.disabled = true;
                refreshDashboardRequiredWeekView();
                applyZaloRunState();
                return;
            }
            const latest = plan.latest_week || {};
            const required = plan.required_week || {};
            const deferred = Number(plan.deferred_target_count || 0);
            const exempt = Number(plan.exempt_target_count || 0);
            const droppedCounts = (plan.dropped_rows && plan.dropped_rows.counts) ? plan.dropped_rows.counts : {};
            const droppedTotal = Number(droppedCounts.total || 0);
            const totalAdmitted = Number(plan.target_count || 0) + exempt;
            zaloScanPlan = plan;
            if (isStale) {
                const observedSuffix = plan.observed_at ? ` (${plan.observed_at})` : '';
                element.textContent = `Kế hoạch đã cũ — Dò lại${observedSuffix} · mới nhất W${latest.week}/${latest.year} · phải xử lý W${required.week}/${required.year} · Dashboard chỉ hiển thị W${required.week}/${required.year}.`;
                element.className = 'rounded-xl border-2 border-amber-400 border-l-8 border-l-amber-600 bg-amber-50 px-4 py-2.5 text-xs font-bold text-amber-950 shadow-sm';
                const staleBrief = `Tuần W${required.week}/${required.year} · Kế hoạch đã cũ — Dò lại${observedSuffix}`;
                if (brief) brief.textContent = staleBrief;
                if (stripPlanText) stripPlanText.textContent = staleBrief;
                if (scanButton) scanButton.disabled = true;
                refreshDashboardRequiredWeekView();
                applyZaloRunState();
                return;
            }
            if (!plan.available || totalAdmitted < 1) {
                element.textContent = `Kế hoạch BCNB: mới nhất W${latest.week}/${latest.year} · phải xử lý W${required.week}/${required.year} · Dashboard chỉ hiển thị W${required.week}/${required.year} · ${deferred} lớp chưa đến giờ học, chưa thể quét.`;
                element.className = 'rounded-xl border-2 border-amber-400 border-l-8 border-l-amber-600 bg-amber-50 px-4 py-2.5 text-xs font-bold text-amber-950 shadow-sm';
                const deferredBrief = `Tuần W${required.week}/${required.year} · Chưa có lớp đủ điều kiện (${deferred} lớp chờ giờ học)`;
                if (brief) brief.textContent = deferredBrief;
                if (stripPlanText) stripPlanText.textContent = deferredBrief;
                if (scanButton) scanButton.disabled = true;
                refreshDashboardRequiredWeekView();
                applyZaloRunState();
                return;
            }
            const deferredText = deferred > 0
                ? ` · ${deferred} lớp chưa đến giờ học (chưa quét)`
                : '';
            const exemptText = exempt > 0
                ? ` · ${exempt} lớp Buổi 14 miễn báo cáo`
                : '';
            const droppedText = droppedTotal > 0
                ? ` · ${droppedTotal} dòng bị loại cần rà soát`
                : '';
            element.textContent = `Kế hoạch BCNB: mới nhất W${latest.week}/${latest.year} · phải xử lý W${required.week}/${required.year} · Dashboard chỉ hiển thị W${required.week}/${required.year} · ${plan.target_count || 0} target${exemptText}${deferredText}${droppedText}`;
            element.className = 'rounded-xl border-2 border-indigo-400 border-l-8 border-l-indigo-600 bg-indigo-50 px-4 py-2.5 text-xs font-bold text-indigo-950 shadow-sm';
            const activeBrief = `Tuần báo cáo W${required.week}/${required.year} · ${totalAdmitted} lớp cần xử lý${deferred > 0 ? ` (${deferred} lớp chờ giờ học)` : ''}${droppedTotal > 0 ? ` · ${droppedTotal} dòng bị loại` : ''}`;
            if (brief) {
                brief.textContent = activeBrief;
            }
            if (stripPlanText) {
                stripPlanText.textContent = activeBrief;
            }
            if (scanButton) scanButton.disabled = false;
            refreshDashboardRequiredWeekView();
            applyZaloRunState();
        }

        function getZaloPreflightStatus() {
            const status = zaloPreflight && String(zaloPreflight.status || '');
            return ['GREEN', 'YELLOW', 'RED'].includes(status)
                ? status
                : (zaloPreflight ? 'RED' : '');
        }

        function renderRuntimeMode(mode, jobType = '') {
            const modeBadge = document.getElementById('runtime-mode-badge');
            const specsMode = document.getElementById('specs-job-mode');
            if (!modeBadge) return;
            const normalized = mode === 'production'
                ? 'production'
                : (mode === 'dry-run' ? 'dry-run' : 'unknown');
            const label = normalized === 'production'
                ? 'Production'
                : (normalized === 'dry-run'
                    ? (jobType === 'ZALO_DISCOVERY' ? 'Dò tìm · chỉ đọc' : 'Dry-run')
                    : 'Chưa xác định');
            modeBadge.textContent = label;
            modeBadge.className = `mode-badge mode-badge--${normalized}`;
            if (specsMode) specsMode.textContent = label;
        }

        function applyZaloRunState() {
            if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return;
            const scanButton = document.querySelector('.action-button--scan');
            const btnScanById = typeof document.getElementById === 'function'
                ? document.getElementById('btn-scan')
                : null;
            const discoverButton = typeof document.getElementById === 'function'
                ? document.getElementById('btn-discover')
                : null;
            const lmsButton = typeof document.getElementById === 'function'
                ? document.getElementById('btn-lms-run')
                : null;
            const rescanButton = typeof document.getElementById === 'function'
                ? document.getElementById('btn-rescan-failed')
                : null;
            const hasPlan = isZaloScanPlanRunnable(zaloScanPlan);
            const hasPreflight = Boolean(zaloPreflight);
            const blocked = getZaloPreflightStatus() === 'RED';
            const scanDisabled = Boolean(currentJobActive || !hasPlan || !hasPreflight || blocked);
            const scanTitle = currentJobActive
                ? 'Đang có một job hoạt động.'
                : (!hasPlan
                    ? 'Hãy bấm Dò kế hoạch trước.'
                    : (!hasPreflight
                        ? 'Bấm “Kiểm tra lại” trước khi chạy.'
                        : (blocked ? 'Cần xử lý kiểm tra đỏ trước khi chạy.' : '')));
            if (scanButton) {
                scanButton.disabled = scanDisabled;
                scanButton.title = scanTitle;
            }
            if (btnScanById && btnScanById !== scanButton) {
                btnScanById.disabled = scanDisabled;
                btnScanById.title = scanTitle;
            }
            if (discoverButton) {
                discoverButton.disabled = Boolean(currentJobActive);
            }
            if (lmsButton) {
                lmsButton.disabled = Boolean(currentJobActive);
            }
            if (rescanButton) {
                const inHistoricalView = typeof isHistoricalWeekViewActive === 'function'
                    && isHistoricalWeekViewActive();
                rescanButton.disabled = Boolean(currentJobActive || !hasPlan || !hasPreflight || blocked || inHistoricalView);
            }
            const canRescanNow = canRunZaloScanNow()
                && !(typeof isHistoricalWeekViewActive === 'function' && isHistoricalWeekViewActive());
            const syncRowButton = btn => {
                if (!btn) return;
                const cls = String(btn.className || '');
                const isRescan = (btn.classList && btn.classList.contains('btn-row-rescan'))
                    || /\bbtn-row-rescan\b/.test(cls);
                const isApprove = (btn.classList && btn.classList.contains('btn-row-manual-approve'))
                    || /\bbtn-row-manual-approve\b/.test(cls);
                if (isRescan) {
                    btn.disabled = !canRescanNow;
                    if (btn.classList) btn.classList.toggle('is-disabled', !canRescanNow);
                } else if (isApprove) {
                    btn.disabled = Boolean(currentJobActive);
                    if (btn.classList) btn.classList.toggle('is-disabled', Boolean(currentJobActive));
                }
            };
            if (typeof document.querySelectorAll === 'function') {
                Array.from(document.querySelectorAll('.btn-row-rescan') || []).forEach(syncRowButton);
                Array.from(document.querySelectorAll('.btn-row-manual-approve') || []).forEach(syncRowButton);
            }
            if (typeof document.getElementById === 'function') {
                const tbody = document.getElementById('results-table');
                const rows = tbody && tbody.children ? Array.from(tbody.children) : [];
                rows.forEach(tr => {
                    const trChildren = tr && tr.children ? Array.from(tr.children) : [];
                    const actionTd = trChildren[4] || null;
                    const tdChildren = actionTd && actionTd.children ? Array.from(actionTd.children) : [];
                    const actionsWrap = tdChildren[0] || null;
                    const buttons = actionsWrap && actionsWrap.children ? Array.from(actionsWrap.children) : [];
                    buttons.forEach(syncRowButton);
                });
            }
            renderZaloWorkflow();
        }

        function renderZaloWorkflow() {
            if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return;
            const hasPlan = isZaloScanPlanRunnable(zaloScanPlan);
            const preflightStatus = getZaloPreflightStatus();
            const updateStep = (id, state, detail) => {
                const step = document.getElementById(id);
                if (!step) return;
                step.setAttribute('data-state', state);
                step.classList.remove('is-current', 'is-complete', 'is-blocked');
                step.classList.add(`is-${state}`);
                const detailElement = document.getElementById(`${id}-detail`);
                if (detailElement) detailElement.textContent = detail;
            };

            updateStep(
                'zalo-step-discover',
                hasPlan ? 'complete' : 'current',
                hasPlan
                    ? `Kế hoạch W${zaloScanPlan.required_week?.week || '?'} đã sẵn sàng`
                    : 'Bấm “Dò kế hoạch” để lấy dữ liệu',
            );

            const preflightState = !hasPlan
                ? 'blocked'
                : (!zaloPreflight ? 'current' : (preflightStatus === 'RED' ? 'blocked' : 'complete'));
            const preflightDetail = !hasPlan
                ? 'Chờ kế hoạch hợp lệ'
                : (!zaloPreflight
                    ? 'Bấm “Kiểm tra lại” để kiểm tra'
                    : (preflightStatus === 'RED'
                        ? 'Cần xử lý cảnh báo đỏ'
                        : (preflightStatus === 'YELLOW' ? 'Đã kiểm tra · cần xác nhận khi chạy' : 'Đã kiểm tra · sẵn sàng')));
            updateStep('zalo-step-preflight', preflightState, preflightDetail);

            const scanState = currentJobActive
                ? 'current'
                : (!hasPlan || !zaloPreflight || preflightStatus === 'RED' ? 'blocked' : 'current');
            const scanDetail = currentJobActive
                ? 'Đang theo dõi job đang chạy'
                : (!hasPlan
                    ? 'Chưa có kế hoạch để quét'
                    : (!zaloPreflight
                        ? 'Chờ kiểm tra an toàn'
                        : (preflightStatus === 'RED'
                            ? 'Chưa thể chạy · xem cảnh báo đỏ'
                            : (preflightStatus === 'YELLOW' ? 'Sẵn sàng · cần xác nhận cảnh báo' : 'Sẵn sàng bắt đầu quét'))));
            updateStep('zalo-step-scan', scanState, scanDetail);
        }

        function renderZaloPreflight(report) {
            const panel = document.getElementById('zalo-preflight');
            const summary = document.getElementById('zalo-preflight-summary');
            const status = document.getElementById('zalo-preflight-status');
            const checks = document.getElementById('zalo-preflight-checks');
            const details = document.getElementById('zalo-preflight-details');
            const detailSummary = document.getElementById('zalo-preflight-detail-summary');
            const checkedAt = document.getElementById('zalo-preflight-checked-at');
            const stripPill = document.getElementById('dashboard-preflight-pill');
            const stripDrawer = document.getElementById('dashboard-preflight-drawer');
            const stripDrawerSummary = document.getElementById('dashboard-preflight-drawer-summary');
            const stripDrawerChecks = document.getElementById('dashboard-preflight-drawer-checks');
            if (!panel || !summary || !status || !checks) return;
            zaloPreflight = report || null;
            checks.replaceChildren();
            if (stripDrawerChecks) stripDrawerChecks.replaceChildren();
            if (!report) {
                status.textContent = 'Chưa kiểm tra';
                status.className = 'rounded-md bg-amber-800 border border-amber-950 px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider text-white shadow-sm';
                if (stripPill) {
                    stripPill.textContent = 'Chưa kiểm tra';
                    stripPill.className = 'rounded-md bg-amber-800 border border-amber-950 px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wider text-white shrink-0';
                }
                summary.textContent = 'Chưa đọc được kiểm tra an toàn. Hãy thử làm mới.';
                if (checkedAt) checkedAt.textContent = 'Chưa có lần kiểm tra gần nhất';
                if (!currentJobExecutionMode) renderRuntimeMode(null);
                if (details) details.classList.add('hidden');
                if (stripDrawer) stripDrawer.classList.add('hidden');
                panel.className = 'preflight-compact flex flex-wrap items-center justify-between gap-2.5 rounded-xl border-2 border-amber-400 border-l-8 border-l-amber-600 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-950 shadow-sm';
                applyZaloRunState();
                return;
            }
            const labels = {
                GREEN: 'Sẵn sàng chạy',
                YELLOW: 'Có cảnh báo — cần xác nhận',
                RED: 'Chưa thể chạy'
            };
            const styles = {
                GREEN: ['border-emerald-400 border-l-emerald-600', 'bg-emerald-50/90', 'text-emerald-950', 'bg-emerald-800 border border-emerald-950', 'text-white'],
                YELLOW: ['border-amber-400 border-l-amber-600', 'bg-amber-50/90', 'text-amber-950', 'bg-amber-800 border border-amber-950', 'text-white'],
                RED: ['border-rose-400 border-l-rose-600', 'bg-rose-50/90', 'text-rose-950', 'bg-rose-800 border border-rose-950', 'text-white']
            };
            const level = labels[report.status] ? report.status : 'RED';
            const style = styles[level];
            status.textContent = labels[level];
            status.className = `rounded-md ${style[3]} px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider ${style[4]} shadow-sm shrink-0`;
            if (stripPill) {
                stripPill.textContent = labels[level];
                stripPill.className = `rounded-md ${style[3]} px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wider ${style[4]} shrink-0`;
            }
            panel.className = `preflight-compact flex flex-wrap items-center justify-between gap-2.5 rounded-xl border-2 border-l-8 ${style[0]} ${style[1]} ${style[2]} px-3.5 py-2.5 text-xs shadow-sm`;
            const mode = report.mode === 'production' ? 'production' : 'dry-run';
            const targets = Number(report.target_count || 0);
            if (currentJobExecutionMode) {
                renderRuntimeMode(currentJobExecutionMode, currentJobType);
            } else {
                renderRuntimeMode(mode);
            }
            if (checkedAt) {
                const parsed = report.checked_at ? new Date(report.checked_at) : null;
                const time = parsed && !Number.isNaN(parsed.getTime())
                    ? parsed.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                    : null;
                checkedAt.textContent = time ? `Kiểm tra lúc ${time}` : 'Đã kiểm tra, chưa rõ thời điểm';
            }
            summary.textContent = `${targets} target có thể quét · worker Zalo ${Number(report.worker_count || 1)}`;
            const visibleChecks = Array.isArray(report.checks) ? report.checks : [];
            visibleChecks.forEach(item => {
                const line = document.createElement('div');
                const icon = item.status === 'GREEN' ? '✓' : (item.status === 'YELLOW' ? '!' : '×');
                line.className = item.status === 'RED'
                    ? 'text-rose-800'
                    : (item.status === 'YELLOW' ? 'text-amber-800' : 'text-emerald-800');
                line.textContent = `${icon} ${item.message || item.code || 'Kiểm tra'}`;
                if (item.action) line.textContent += ` — ${item.action}`;
                checks.appendChild(line);
                if (stripDrawerChecks) {
                    const stripLine = document.createElement('div');
                    stripLine.className = line.className;
                    stripLine.textContent = line.textContent;
                    stripDrawerChecks.appendChild(stripLine);
                }
            });
            checks.classList.remove('hidden');
            if (detailSummary) detailSummary.textContent = `Xem ${visibleChecks.length} kiểm tra chi tiết`;
            if (details) details.classList.toggle('hidden', visibleChecks.length === 0);
            if (stripDrawerSummary) stripDrawerSummary.textContent = `Chi tiết kiểm tra (${visibleChecks.length})`;
            if (stripDrawer) stripDrawer.classList.toggle('hidden', visibleChecks.length === 0);
            applyZaloRunState();
        }

        async function refreshZaloPreflight(snapshotId = null) {
            try {
                const suffix = snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : '';
                const response = await fetch(`/api/zalo-scan-preflight${suffix}`);
                if (!response.ok) throw new Error('Không đọc được kiểm tra an toàn.');
                renderZaloPreflight(await response.json());
            } catch (error) {
                renderZaloPreflight(null);
            }
        }

        function refreshCurrentZaloPreflight() {
            const snapshotId = zaloScanPlan && zaloScanPlan.snapshot_id
                ? zaloScanPlan.snapshot_id
                : null;
            return refreshZaloPreflight(snapshotId);
        }

        async function refreshZaloScanPlan() {
            try {
                const response = await fetch('/api/zalo-scan-plan');
                if (!response.ok) throw new Error('Không đọc được kế hoạch BCNB.');
                const plan = await response.json();
                renderZaloScanPlan(plan);
                await refreshZaloPreflight(plan && plan.snapshot_id ? plan.snapshot_id : null);
                if (
                    plan
                    && plan.required_week
                    && typeof refreshZaloRequiredWeekSummary === 'function'
                ) {
                    await refreshZaloRequiredWeekSummary(plan, { keepLiveMode: true, silent: true });
                }
            } catch (error) {
                renderZaloScanPlan(null);
                await refreshZaloPreflight();
            }
        }

        async function startScan(selectedTargetKeys = null) {
            if (!canStartAnotherJob()) return;
            const isSelective = Array.isArray(selectedTargetKeys);
            if (isSelective && selectedTargetKeys.length === 0) {
                alert('Không có lớp hợp lệ nào được chọn để quét lại.');
                return;
            }
            if (!isZaloScanPlanRunnable(zaloScanPlan)) {
                await refreshZaloScanPlan();
            }
            if (!isZaloScanPlanRunnable(zaloScanPlan)) {
                alert('Hãy bấm Dò kế hoạch để tạo kế hoạch hai tuần trước khi quét.');
                return;
            }
            await refreshZaloPreflight(zaloScanPlan.snapshot_id);
            const preflightStatus = getZaloPreflightStatus();
            if (!zaloPreflight || preflightStatus === 'RED') {
                const blocker = (zaloPreflight && Array.isArray(zaloPreflight.checks))
                    ? zaloPreflight.checks.find(item => item.status === 'RED')
                    : null;
                alert(blocker ? `${blocker.message}${blocker.action ? `\n${blocker.action}` : ''}` : 'Chưa thể chạy; hãy xem phần kiểm tra an toàn.');
                return;
            }
            const targetCountLabel = isSelective
                ? selectedTargetKeys.length
                : (Number(zaloScanPlan.target_count || 0) + Number(zaloScanPlan.exempt_target_count || 0));
            let confirmYellow = false;
            if (preflightStatus === 'YELLOW' && (zaloPreflight.mode === 'production' || zaloPreflight.requires_confirmation)) {
                const yellowChecks = (zaloPreflight && Array.isArray(zaloPreflight.checks))
                    ? zaloPreflight.checks.filter(item => item && item.status === 'YELLOW')
                    : [];
                const warningLines = yellowChecks
                    .map(item => `• ${item.message || item.code || 'Cảnh báo'}${item.action ? ` — ${item.action}` : ''}`)
                    .join('\n');
                const confirmPrompt = `Có cảnh báo an toàn trước khi chạy production (${targetCountLabel} target):\n`
                    + (warningLines ? `${warningLines}\n\n` : '')
                    + 'Hãy đọc phần cảnh báo và chỉ tiếp tục khi bạn chắc chắn muốn chạy.';
                const confirmFn = (typeof window !== 'undefined' && typeof window.confirm === 'function')
                    ? window.confirm.bind(window)
                    : (typeof confirm === 'function' ? confirm : null);
                confirmYellow = confirmFn ? Boolean(confirmFn(confirmPrompt)) : false;
                if (!confirmYellow) return;
            }
            const requestBody = {
                snapshot_id: zaloScanPlan.snapshot_id,
                confirm_yellow: confirmYellow,
            };
            if (isSelective) {
                requestBody.selected_target_keys = selectedTargetKeys;
                if (typeof rememberSelectiveScanBaseline === 'function') {
                    rememberSelectiveScanBaseline();
                }
            } else if (typeof clearSelectiveScanBaseline === 'function') {
                clearSelectiveScanBaseline();
            }
            try {
                const res = await fetch('/run-scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody),
                });
                const data = await res.json();
                if (!res.ok) {
                    if (isSelective && typeof clearSelectiveScanBaseline === 'function') {
                        clearSelectiveScanBaseline();
                    }
                    const detail = data.detail;
                    const message = detail && typeof detail === 'object'
                        ? (detail.message || detail.code)
                        : (detail || data.message || 'Hệ thống đang chạy');
                    alert("Lỗi: " + message);
                } else {
                    if(data.job_id) { trackJob(data.job_id); }
                }
            } catch (err) {
                if (isSelective && typeof clearSelectiveScanBaseline === 'function') {
                    clearSelectiveScanBaseline();
                }
                alert("Lỗi kết nối đến server!");
            }
        }
