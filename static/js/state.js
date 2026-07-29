        let currentJobId = null;
        let currentJobActive = false;
        let jobPollingTimer = null;
        const JOB_POLL_INTERVAL_MS = 2000;
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
            } else if (tabId === 'logs') {
                headerTitle.innerText = "Nhật ký Hệ thống";
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

        async function startScan() {
            if (!canStartAnotherJob()) return;
            try {
                const res = await fetch('/run-scan', { method: 'POST' });
                const data = await res.json();
                if (!res.ok) {
                    alert("Lỗi: " + (data.detail || data.message || "Hệ thống đang chạy"));
                } else {
                    if(data.job_id) { trackJob(data.job_id); }
                }
            } catch (err) { alert("Lỗi kết nối đến server!"); }
        }
