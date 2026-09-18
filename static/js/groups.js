        let groupsCache = [];
        let bcnbZaloGapsCache = [];
        function renderTableMessage(tbody, colSpan, message, isError = false) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = colSpan;
            td.className = `px-6 py-8 text-center ${isError ? 'text-rose-400' : 'text-gray-500'}`;
            td.textContent = message;
            tr.appendChild(td);
            tbody.replaceChildren(tr);
        }

        async function fetchGroups() {
            try {
                const res = await fetch('/api/groups');
                const data = await res.json();
                const tbody = document.getElementById('groups-table-body');
                
                if (data.groups && data.groups.length > 0) {
                    groupsCache = data.groups;
                    renderGroupsTable(groupsCache);
                } else if (data.error) {
                    renderTableMessage(tbody, 3, `Lỗi: ${data.error}`, true);
                } else {
                    renderTableMessage(tbody, 3, 'Không có nhóm nào.');
                }
            } catch (e) { console.error("Lỗi fetch groups:", e); }
        }

        function renderGroupsTable(groups) {
            const tbody = document.getElementById('groups-table-body');
            tbody.replaceChildren();
            groups.forEach((g, idx) => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-white/5 transition-colors';
                const values = [idx + 1, g.TenNhom || '', g.NguoiPhuTrach || 'Trống'];
                values.forEach((value, valueIndex) => {
                    const td = document.createElement('td');
                    td.className = valueIndex === 0
                        ? 'px-6 py-3.5 text-xs font-mono text-gray-500'
                        : 'px-6 py-3.5 font-medium text-gray-200';
                    td.textContent = String(value);
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
        }

        function filterGroups() {
            const query = document.getElementById('groupSearchInput').value.toLowerCase();
            const filtered = groupsCache.filter(g => 
                (g.TenNhom && g.TenNhom.toLowerCase().includes(query)) || 
                (g.NguoiPhuTrach && g.NguoiPhuTrach.toLowerCase().includes(query))
            );
            renderGroupsTable(filtered);
        }

        const bcnbZaloGapLabels = {
            MISSING_ZALO_GROUP: 'Chưa có nhóm Zalo',
            AMBIGUOUS_ZALO_GROUP: 'Có nhiều nhóm có thể trùng',
            TRANSITION_REVIEW: 'Đang chuyển cấp - cần xem lại'
        };

        function formatBcnbZaloGapPeriods(periods) {
            return (Array.isArray(periods) ? periods : []).map(period => {
                const lesson = period.lesson ? ` · buổi ${period.lesson}` : '';
                return `W${period.week}/${period.year}${lesson}`;
            }).join(', ');
        }

        function renderBcnbZaloGaps(items) {
            const tbody = document.getElementById('bcnb-zalo-gaps-table-body');
            if (!tbody) return;
            if (!Array.isArray(items) || items.length === 0) {
                renderTableMessage(tbody, 6, 'Không có lớp BCNB nào cần đối chiếu Zalo.');
                return;
            }
            tbody.replaceChildren();
            items.forEach((item, index) => {
                const periods = Array.isArray(item.periods) ? item.periods : [];
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-amber-500/5 transition-colors';
                const values = [
                    index + 1,
                    item.class_code || '',
                    (Array.isArray(item.teachers) ? item.teachers : []).join(', ') || 'Chưa xác định',
                    formatBcnbZaloGapPeriods(periods),
                    periods.map(period => period.class_date || period.class_date_iso || '').filter(Boolean).join(', '),
                    bcnbZaloGapLabels[item.status] || item.status || 'Cần xem lại'
                ];
                values.forEach((value, valueIndex) => {
                    const td = document.createElement('td');
                    td.className = valueIndex === 0
                        ? 'px-4 py-3 text-xs font-mono text-gray-500'
                        : (valueIndex === 5
                            ? 'px-4 py-3 text-xs font-semibold text-amber-300'
                            : 'px-4 py-3 text-gray-200');
                    td.textContent = String(value);
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
        }

        function filterBcnbZaloGaps() {
            const input = document.getElementById('bcnbZaloGapSearchInput');
            const query = String(input ? input.value : '').trim().toLowerCase();
            const filtered = bcnbZaloGapsCache.filter(item => {
                const searchable = [
                    item.class_code,
                    ...(Array.isArray(item.teachers) ? item.teachers : []),
                    bcnbZaloGapLabels[item.status] || item.status
                ].join(' ').toLowerCase();
                return searchable.includes(query);
            });
            renderBcnbZaloGaps(filtered);
        }

        async function fetchBcnbZaloGaps() {
            const tbody = document.getElementById('bcnb-zalo-gaps-table-body');
            const summaryElement = document.getElementById('bcnb-zalo-gaps-summary');
            const staleElement = document.getElementById('bcnb-zalo-gaps-stale');
            if (!tbody || !summaryElement || !staleElement) return;
            try {
                const response = await fetch('/api/discovery/bcnb-zalo-gaps');
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail?.message || 'Không đọc được dữ liệu đối chiếu.');
                }
                if (!data.available) {
                    const needsRerun = data.reason === 'DISCOVERY_PLAN_UPGRADE_REQUIRED';
                    summaryElement.textContent = needsRerun
                        ? 'Snapshot cũ chưa có dữ liệu này. Hãy bấm Dò tìm lại.'
                        : 'Chưa có snapshot Dò tìm để đối chiếu.';
                    if (bcnbZaloGapsCache.length === 0) {
                        renderTableMessage(tbody, 6, summaryElement.textContent);
                    }
                    staleElement.classList.add('hidden');
                    return;
                }
                bcnbZaloGapsCache = Array.isArray(data.items) ? data.items : [];
                const summary = data.summary || {};
                summaryElement.textContent = `${summary.source_count || 0} lớp BCNB · ${summary.matched_count || 0} đã ghép đúng · ${summary.missing_count || 0} chưa có nhóm · ${summary.ambiguous_count || 0} mơ hồ · ${summary.transition_review_count || 0} đang chuyển cấp`;
                staleElement.textContent = data.stale
                    ? 'Dữ liệu đã cũ hơn thời hạn kế hoạch. Hãy bấm Dò tìm để cập nhật.'
                    : '';
                staleElement.classList.toggle('hidden', !data.stale);
                filterBcnbZaloGaps();
            } catch (error) {
                summaryElement.textContent = `Không thể làm mới: ${error.message}`;
                if (bcnbZaloGapsCache.length === 0) {
                    renderTableMessage(tbody, 6, 'Không thể tải dữ liệu đối chiếu.', true);
                }
            }
        }

        // IGNORED GROUPS APIS
        async function fetchIgnoredGroups() {
            try {
                const res = await fetch('/api/ignored-groups');
                const data = await res.json();
                const tbody = document.getElementById('ignored-groups-table-body');
                const items = Array.isArray(data.items)
                    ? data.items
                    : (data.groups || []).map(groupName => ({
                        group_name: groupName,
                        status: 'PAUSED_OR_TRANSITIONING'
                    }));

                if (items.length > 0) {
                    tbody.replaceChildren();
                    items.forEach((item, idx) => {
                        const groupName = String(item.group_name || '');
                        const isEnded = item.status === 'ENDED_CONFIRMED';
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-rose-500/10 transition-colors';
                        const indexCell = document.createElement('td');
                        indexCell.className = 'px-4 py-3 text-xs font-mono text-gray-500';
                        indexCell.textContent = String(idx + 1);
                        const nameCell = document.createElement('td');
                        nameCell.className = 'px-4 py-3 font-medium text-gray-300';
                        nameCell.textContent = groupName;
                        const statusCell = document.createElement('td');
                        statusCell.className = isEnded
                            ? 'px-4 py-3 text-xs font-semibold text-rose-300'
                            : 'px-4 py-3 text-xs font-semibold text-amber-300';
                        statusCell.textContent = isEnded
                            ? 'Đã xác nhận kết thúc'
                            : 'Tạm nghỉ / chờ chuyển cấp';
                        const actionCell = document.createElement('td');
                        actionCell.className = 'px-4 py-3 text-center space-x-3';
                        if (!isEnded) {
                            const confirmEnded = document.createElement('button');
                            confirmEnded.className = 'text-amber-300 hover:text-rose-300 transition-colors';
                            confirmEnded.title = 'Xác nhận lớp đã thực sự kết thúc';
                            confirmEnded.textContent = 'Kết thúc';
                            confirmEnded.addEventListener(
                                'click',
                                () => confirmIgnoredGroupEnded(groupName)
                            );
                            actionCell.appendChild(confirmEnded);
                        }
                        const restore = document.createElement('button');
                        restore.className = 'text-rose-400 hover:text-emerald-400 transition-colors';
                        restore.title = 'Phục hồi lớp này';
                        restore.textContent = 'Khôi phục';
                        restore.addEventListener('click', () => restoreIgnoredGroup(groupName));
                        actionCell.appendChild(restore);
                        tr.append(indexCell, nameCell, statusCell, actionCell);
                        tbody.appendChild(tr);
                    });
                } else if (data.error) {
                    renderTableMessage(tbody, 4, `Lỗi: ${data.error}`, true);
                } else {
                    renderTableMessage(tbody, 4, 'Không có lớp tạm nghỉ hoặc kết thúc.');
                }
            } catch (e) { console.error("Lỗi fetch ignored groups:", e); }
        }

        async function confirmIgnoredGroupEnded(groupName) {
            if (!confirm(`Xác nhận lớp ${groupName} đã thực sự kết thúc? Lớp sẽ được lưu trữ sau 7 ngày.`)) return;
            try {
                const res = await fetch('/api/ignored-groups/confirm-ended', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ group_name: groupName })
                });
                const data = await res.json();
                if (!res.ok) {
                    alert('Lỗi: ' + (data.detail?.message || data.error || 'Không thể xác nhận'));
                    return;
                }
                fetchIgnoredGroups();
            } catch (err) { alert('Lỗi kết nối'); }
        }

        async function restoreIgnoredGroup(groupName) {
            if (!confirm(`Bạn có chắc muốn phục hồi lớp ${groupName} vào danh sách kiểm tra?`)) return;
            try {
                const res = await fetch('/api/ignored-groups/restore', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ group_name: groupName })
                });
                const data = await res.json();
                if (data.error) {
                    alert('Lỗi: ' + data.error);
                } else {
                    fetchIgnoredGroups();
                    fetchGroups(); // Refresh main groups list
                }
            } catch (err) { alert('Lỗi kết nối'); }
        }
