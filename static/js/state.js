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
        }

        async function refreshZaloScanPlan() {
            try {
                const response = await fetch('/api/zalo-scan-plan');
                if (!response.ok) throw new Error('Không đọc được kế hoạch BCNB.');
                renderZaloScanPlan(await response.json());
            } catch (error) {
                renderZaloScanPlan(null);
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
            try {
                const res = await fetch('/run-scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ snapshot_id: zaloScanPlan.snapshot_id }),
                });
                const data = await res.json();
                if (!res.ok) {
                    alert("Lỗi: " + (data.detail || data.message || "Hệ thống đang chạy"));
                } else {
                    if(data.job_id) { trackJob(data.job_id); }
                }
            } catch (err) { alert("Lỗi kết nối đến server!"); }
        }
