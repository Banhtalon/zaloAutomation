        let currentJobId = null;
        let currentJobActive = false;
        let currentJobExecutionMode = null;
        let currentJobType = null;
        let jobPollingTimer = null;
        const JOB_POLL_INTERVAL_MS = 3000;
        const handledTerminalJobIds = new Set();
        const terminalStatuses = new Set(['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'CANCELLED']);
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
        let zaloScanPlan = null;
        let zaloPreflight = null;
        let weeklyHistoryView = null;
        let weeklyHistoryAvailableWeeks = [];
        let weeklyHistoryJobs = [];
        let weeklyHistoryRequestSeq = 0;
        let lastExportedWeeklyCsv = '';

        function trackJob(jobId) {
            currentJobId = jobId;
            currentJobActive = true;
            renderZaloWorkflow();
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
            document.querySelectorAll('.tab-content').forEach(el => {
                const isSelected = el.id === `tab-${tabId}`;
                el.classList.toggle('active', isSelected);
                el.hidden = !isSelected;
            });

            document.querySelectorAll('.tab-btn').forEach(el => {
                const isSelected = el.id === `btn-${tabId}`;
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
            } else if (tabId === 'specs') {
                headerTitle.innerText = "Thông số kỹ thuật";
                if (typeof refreshCurrentZaloPreflight === 'function') {
                    refreshCurrentZaloPreflight();
                }
            } else if (tabId === 'logs') {
                headerTitle.innerText = "Nhật ký Hệ thống";
                if (typeof refreshLogs === 'function') refreshLogs();
                if (currentJobId && typeof renderJobTimeline === 'function') {
                    renderJobTimeline(currentJobId);
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

        function refreshDashboardRequiredWeekView() {
            if (typeof syncWeeklyHistoryControlsWithPlan === 'function') {
                syncWeeklyHistoryControlsWithPlan();
            }
            if (Array.isArray(latestZaloResults)
                && latestZaloResults.length > 0
                && typeof renderZaloResults === 'function') {
                renderZaloResults(latestZaloResults, true);
            }
        }

        function renderZaloScanPlan(plan) {
            const element = document.getElementById('zalo-scan-plan');
            const brief = document.getElementById('zalo-dashboard-plan-brief');
            if (!element) return;
            if (typeof document.querySelector !== 'function') return;
            const scanButton = document.querySelector('.action-button--scan');
            if (!plan || !plan.snapshot_id
                || !Array.isArray(plan.selected_weeks)
                || plan.selected_weeks.length !== 2) {
                zaloScanPlan = null;
                element.textContent = 'Chưa có kế hoạch tuần BCNB. Hãy bấm “Dò tìm” trước khi quét.';
                element.className = 'rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900';
                if (brief) brief.textContent = 'Chưa có kế hoạch tuần BCNB · Hãy bấm “Dò tìm lại”';
                if (scanButton) scanButton.disabled = true;
                refreshDashboardRequiredWeekView();
                applyZaloRunState();
                return;
            }
            const latest = plan.latest_week || {};
            const required = plan.required_week || {};
            const deferred = Number(plan.deferred_target_count || 0);
            if (!plan.available || Number(plan.target_count || 0) < 1) {
                zaloScanPlan = null;
                element.textContent = `Kế hoạch BCNB: mới nhất W${latest.week}/${latest.year} · phải xử lý W${required.week}/${required.year} · Dashboard chỉ hiển thị W${required.week}/${required.year} · ${deferred} lớp chưa đến giờ học, chưa thể quét.`;
                element.className = 'rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900';
                if (brief) brief.textContent = `Tuần W${required.week}/${required.year} · Chưa có lớp đủ điều kiện (${deferred} lớp chờ giờ học)`;
                if (scanButton) scanButton.disabled = true;
                refreshDashboardRequiredWeekView();
                applyZaloRunState();
                return;
            }
            zaloScanPlan = plan;
            const deferredText = deferred > 0
                ? ` · ${deferred} lớp chưa đến giờ học (chưa quét)`
                : '';
            element.textContent = `Kế hoạch BCNB: mới nhất W${latest.week}/${latest.year} · phải xử lý W${required.week}/${required.year} · Dashboard chỉ hiển thị W${required.week}/${required.year} · ${plan.target_count || 0} target${deferredText}`;
            element.className = 'rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs text-indigo-900';
            if (brief) {
                brief.textContent = `Tuần báo cáo W${required.week}/${required.year} · ${plan.target_count || 0} lớp cần xử lý${deferred > 0 ? ` (${deferred} lớp chờ giờ học)` : ''}`;
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
            const rescanButton = typeof document.getElementById === 'function'
                ? document.getElementById('btn-rescan-failed')
                : null;
            const hasPlan = Boolean(zaloScanPlan && zaloScanPlan.snapshot_id);
            const hasPreflight = Boolean(zaloPreflight);
            const blocked = getZaloPreflightStatus() === 'RED';
            if (scanButton) {
                scanButton.disabled = !hasPlan || !hasPreflight || blocked;
                scanButton.title = !hasPlan
                    ? 'Hãy bấm Dò tìm lại trước.'
                    : (!hasPreflight
                        ? 'Bấm “Kiểm tra lại” trước khi chạy.'
                        : (blocked ? 'Cần xử lý kiểm tra đỏ trước khi chạy.' : ''));
            }
            if (rescanButton) {
                const inHistoricalView = typeof isHistoricalWeekViewActive === 'function'
                    && isHistoricalWeekViewActive();
                rescanButton.disabled = !hasPlan || !hasPreflight || blocked || inHistoricalView;
            }
            renderZaloWorkflow();
        }

        function renderZaloWorkflow() {
            if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return;
            const hasPlan = Boolean(zaloScanPlan && zaloScanPlan.snapshot_id);
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
                    : 'Bấm “Dò tìm lại” để lấy dữ liệu',
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
            if (!panel || !summary || !status || !checks) return;
            zaloPreflight = report || null;
            checks.replaceChildren();
            if (!report) {
                status.textContent = 'Chưa kiểm tra';
                status.className = 'rounded-full bg-amber-100 px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-amber-800';
                summary.textContent = 'Chưa đọc được kiểm tra an toàn. Hãy thử làm mới.';
                if (checkedAt) checkedAt.textContent = 'Chưa có lần kiểm tra gần nhất';
                if (!currentJobExecutionMode) renderRuntimeMode(null);
                if (details) details.classList.add('hidden');
                panel.className = 'preflight-compact flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2 text-xs text-amber-900';
                applyZaloRunState();
                return;
            }
            const labels = {
                GREEN: 'Sẵn sàng chạy',
                YELLOW: 'Có cảnh báo — cần xác nhận',
                RED: 'Chưa thể chạy'
            };
            const styles = {
                GREEN: ['border-emerald-200', 'bg-emerald-50', 'text-emerald-900', 'bg-emerald-100', 'text-emerald-800'],
                YELLOW: ['border-amber-200', 'bg-amber-50', 'text-amber-900', 'bg-amber-100', 'text-amber-800'],
                RED: ['border-rose-200', 'bg-rose-50', 'text-rose-900', 'bg-rose-100', 'text-rose-800']
            };
            const level = labels[report.status] ? report.status : 'RED';
            const style = styles[level];
            status.textContent = labels[level];
            status.className = `rounded-full ${style[3]} px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${style[4]} shrink-0`;
            panel.className = `preflight-compact flex flex-wrap items-center justify-between gap-2 rounded-xl border ${style[0]} ${style[1]} ${style[2]} px-3.5 py-2 text-xs`;
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
            });
            checks.classList.remove('hidden');
            if (detailSummary) detailSummary.textContent = `Xem ${visibleChecks.length} kiểm tra chi tiết`;
            if (details) details.classList.toggle('hidden', visibleChecks.length === 0);
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
                await refreshZaloPreflight(plan.snapshot_id || null);
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
            if (!zaloScanPlan || !zaloScanPlan.snapshot_id) {
                await refreshZaloScanPlan();
            }
            if (!zaloScanPlan || !zaloScanPlan.snapshot_id) {
                alert('Hãy bấm Dò tìm để tạo kế hoạch hai tuần trước khi quét.');
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
                : Number(zaloScanPlan.target_count || 0);
            let confirmYellow = false;
            if (preflightStatus === 'YELLOW' && zaloPreflight.mode === 'production') {
                confirmYellow = window.confirm(
                    `Có cảnh báo an toàn trước khi chạy production (${targetCountLabel} target). `
                    + 'Hãy đọc phần cảnh báo và chỉ tiếp tục khi bạn chắc chắn muốn chạy.'
                );
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
