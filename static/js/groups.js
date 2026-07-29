        let groupsCache = [];
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
            if (!confirm(`Xác nhận lớp ${groupName} đã thực sự kết thúc? Lớp sẽ được lưu trữ sau 14 ngày.`)) return;
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
