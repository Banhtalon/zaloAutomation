        let currentJobId = null;
        let currentJobActive = false;
        let jobPollingTimer = null;
        const JOB_POLL_INTERVAL_MS = 3000;
        const handledTerminalJobIds = new Set();
        const terminalStatuses = new Set(['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'CANCELLED']);
        let googleformUploadIds = [];
        let googleformUploadInFlight = 0;
        let googleformBatchId = null;
        let ocrBusy = false;
        let lmsBase64Image = null;
        let lmsUploadId = null;
        let lmsAnalysisJobId = null;
        let lmsConfirmationToken = null;
        let lmsPreviewPlan = null;
        let latestZaloResults = [];
        let zaloScanPlan = null;
        let zaloPreflight = null;

        function trackJob(jobId) {
            currentJobId = jobId;
            currentJobActive = true;
            if (typeof restartJobPolling === 'function') {
                restartJobPolling();
            }
        }

        function canStartAnotherJob() {
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
            if (Array.isArray(latestZaloResults)
                && latestZaloResults.length > 0
                && typeof renderZaloResults === 'function') {
                renderZaloResults(latestZaloResults, true);
            }
        }

        function renderZaloScanPlan(plan) {
            const element = document.getElementById('zalo-scan-plan');
            if (!element) return;
            if (typeof document.querySelector !== 'function') return;
            const scanButton = document.querySelector('.action-button--scan');
            if (!plan || !plan.snapshot_id
                || !Array.isArray(plan.selected_weeks)
                || plan.selected_weeks.length !== 2) {
                zaloScanPlan = null;
                element.textContent = 'Chưa có kế hoạch tuần BCNB. Hãy bấm “Dò tìm” trước khi quét.';
                element.className = 'rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900';
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
                element.className = 'rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900';
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
            element.className = 'rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-900';
            if (scanButton) scanButton.disabled = false;
            refreshDashboardRequiredWeekView();
            applyZaloRunState();
        }

        function applyZaloRunState() {
            if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return;
            const scanButton = document.querySelector('.action-button--scan');
            if (!scanButton) return;
            const hasPlan = Boolean(zaloScanPlan && zaloScanPlan.snapshot_id);
            const blocked = Boolean(zaloPreflight && zaloPreflight.status === 'RED');
            scanButton.disabled = !hasPlan || blocked;
            scanButton.title = blocked
                ? 'Cần xử lý kiểm tra đỏ trước khi chạy.'
                : (!hasPlan ? 'Hãy bấm Dò tìm trước.' : '');
        }

        function renderZaloPreflight(report) {
            const panel = document.getElementById('zalo-preflight');
            const summary = document.getElementById('zalo-preflight-summary');
            const status = document.getElementById('zalo-preflight-status');
            const checks = document.getElementById('zalo-preflight-checks');
            if (!panel || !summary || !status || !checks) return;
            zaloPreflight = report || null;
            checks.replaceChildren();
            if (!report) {
                status.textContent = 'Chưa kiểm tra';
                status.className = 'rounded-full bg-amber-100 px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-amber-800';
                summary.textContent = 'Chưa đọc được kiểm tra an toàn. Hãy thử làm mới.';
                panel.className = 'rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900';
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
            status.className = `rounded-full ${style[3]} px-3 py-1 text-[11px] font-bold uppercase tracking-wide ${style[4]}`;
            panel.className = `rounded-xl border ${style[0]} ${style[1]} ${style[2]} px-4 py-3 text-sm`;
            const mode = report.mode === 'production' ? 'production' : 'dry-run';
            const targets = Number(report.target_count || 0);
            summary.textContent = `${mode} · ${targets} target có thể quét · worker Zalo ${Number(report.worker_count || 1)}`;
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
            checks.classList.toggle('hidden', visibleChecks.length === 0);
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

        async function startScan() {
            if (!canStartAnotherJob()) return;
            if (!zaloScanPlan || !zaloScanPlan.snapshot_id) {
                await refreshZaloScanPlan();
            }
            if (!zaloScanPlan || !zaloScanPlan.snapshot_id) {
                alert('Hãy bấm Dò tìm để tạo kế hoạch hai tuần trước khi quét.');
                return;
            }
            await refreshZaloPreflight(zaloScanPlan.snapshot_id);
            if (!zaloPreflight || zaloPreflight.status === 'RED') {
                const blocker = (zaloPreflight && Array.isArray(zaloPreflight.checks))
                    ? zaloPreflight.checks.find(item => item.status === 'RED')
                    : null;
                alert(blocker ? `${blocker.message}${blocker.action ? `\n${blocker.action}` : ''}` : 'Chưa thể chạy; hãy xem phần kiểm tra an toàn.');
                return;
            }
            let confirmYellow = false;
            if (zaloPreflight.status === 'YELLOW' && zaloPreflight.mode === 'production') {
                confirmYellow = window.confirm(
                    'Có cảnh báo an toàn trước khi chạy production. Bạn đã đọc và muốn tiếp tục chưa?'
                );
                if (!confirmYellow) return;
            }
            try {
                const res = await fetch('/run-scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        snapshot_id: zaloScanPlan.snapshot_id,
                        confirm_yellow: confirmYellow,
                    }),
                });
                const data = await res.json();
                if (!res.ok) {
                    const detail = data.detail;
                    const message = detail && typeof detail === 'object'
                        ? (detail.message || detail.code)
                        : (detail || data.message || 'Hệ thống đang chạy');
                    alert("Lỗi: " + message);
                } else {
                    if(data.job_id) { trackJob(data.job_id); }
                }
            } catch (err) { alert("Lỗi kết nối đến server!"); }
        }
