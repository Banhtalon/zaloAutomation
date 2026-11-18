let todayOverviewData = null;
let todayAllItems = [];
let todayVisibleItems = [];
let todayActiveFilter = 'all';
let todaySelectedDateIso = '';
let todayOverviewRequestSeq = 0;
let todayOverviewLoading = false;

const todaySelectedTargetKeys = new Set();
const todayRowByTargetKey = new Map();

const TODAY_FILTER_CHIP_CONFIG = [
    { key: 'all', id: 'today-chip-all', label: 'Tất cả' },
    { key: 'attention', id: 'today-chip-attention', label: 'Cần xử lý' },
    { key: 'ongoing', id: 'today-chip-ongoing', label: 'Đang diễn ra' },
    { key: 'upcoming', id: 'today-chip-upcoming', label: 'Sắp diễn ra' },
    { key: 'ended', id: 'today-chip-ended', label: 'Đã tan ca' },
    { key: 'missing_zalo', id: 'today-chip-missing-zalo', label: 'Thiếu Zalo' },
    { key: 'missing_lms', id: 'today-chip-missing-lms', label: 'Thiếu LMS / NX' },
    { key: 'spck', id: 'today-chip-spck', label: 'SPCK B12–B14' },
];

const TODAY_FILTER_ACTIVE_CLASS = 'today-filter-chip px-2.5 py-1 rounded-lg border font-bold text-xs bg-blue-600 text-white border-blue-700 transition-colors duration-150';
const TODAY_FILTER_IDLE_CLASS = 'today-filter-chip px-2.5 py-1 rounded-lg border font-bold text-xs bg-white text-slate-700 border-slate-300 transition-colors duration-150';

function getTodayConfirmFn() {
    if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
        return window.confirm.bind(window);
    }
    if (typeof confirm === 'function') {
        return confirm;
    }
    return null;
}

function todayTabIsActive() {
    if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return false;
    const tab = document.getElementById('tab-today');
    return Boolean(tab && tab.classList && tab.classList.contains('active'));
}

function getTodayEl(id) {
    if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return null;
    return document.getElementById(id);
}

function makeTodayEl(tag, className = '', text = null) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== null && text !== undefined) el.textContent = String(text);
    return el;
}

function setTodayText(id, value) {
    const el = getTodayEl(id);
    if (el) el.textContent = String(value ?? '');
}

function resolveTodayEvidenceUrl(rawUrl) {
    const clean = String(rawUrl || '').trim();
    if (!clean) return '';
    if (clean.startsWith('/') || /^https?:\/\//i.test(clean)) return clean;
    if (typeof evidenceUrl === 'function') return evidenceUrl(clean);
    return clean;
}

function isTodayItemMissingZalo(item) {
    if (!item || typeof item !== 'object') return false;
    if (item.missing_zalo_group) return true;
    const cat = String(item.zalo_category || '').toUpperCase();
    const rawCat = String(item.zalo_category || '').toLowerCase();
    return cat === 'MISSING' || cat === 'REVIEW' || rawCat === 'missing_group' || (item.phase === 'ENDED' && rawCat === 'not_scanned');
}

function isTodaySpckComplete(item) {
    if (!item || !item.is_spck_session) return true;
    if (item.spck_exempt_course || !item.requires_spck) return true;
    const spckInfo = item.lms_spck || {};
    const statusUpper = String(spckInfo.status || '').toUpperCase();
    if (statusUpper === 'EXEMPT' || statusUpper === 'NOT_REQUIRED') return true;
    return statusUpper === 'COMPLETE' && Number(spckInfo.missing_comment_count || 0) === 0;
}

function isTodayItemMissingLms(item) {
    if (!item || typeof item !== 'object') return false;
    const attStatus = String(item.lms_attendance?.status || '').toUpperCase();
    const comStatus = String(item.lms_comments?.status || '').toUpperCase();
    if (attStatus === 'UNVERIFIED' || comStatus === 'MISSING' || comStatus === 'REVIEW' || comStatus === 'UNVERIFIED') {
        return true;
    }
    if (item.phase === 'ENDED' && item.is_spck_session && !isTodaySpckComplete(item)) {
        return true;
    }
    if (
        Array.isArray(item.issues)
        && item.issues.some(
            issue =>
                String(issue?.code || '').startsWith('LMS_')
                || /^(?:\[LMS\]|\[SPCK\])\s*/i.test(String(issue?.text || '')),
        )
    ) {
        return true;
    }
    return item.phase === 'ENDED' && (!item.lms_checked || Boolean(item.lms_fetch_unavailable) || !item.lms_ready_for_tick);
}

function hasPendingBcnbTick(item) {
    if (!item || !item.eligible_for_sync) return false;
    const zaloReadyForTick = !item.missing_zalo_group && ['DONE', 'LATE', 'EXEMPT'].includes(String(item.zalo_category || '').toUpperCase());
    const needZalo = zaloReadyForTick && item.nx_zalo_checked !== true;
    const needLms = Boolean(item.lms_ready_for_tick) && item.nx_lms_checked !== true;
    return needZalo || needLms;
}

function isTodayItemNeedsAttention(item) {
    if (!item || typeof item !== 'object') return false;
    if (Number(item.fail_count || 0) > 0 || Number(item.note_count || 0) > 0) return true;
    if (isTodayItemMissingZalo(item) || isTodayItemMissingLms(item) || hasPendingBcnbTick(item)) return true;
    return false;
}

function todayItemMatchesFilter(item, filterKey) {
    switch (filterKey) {
        case 'all':
            return true;
        case 'attention':
            return isTodayItemNeedsAttention(item);
        case 'ongoing':
            return item?.phase === 'ONGOING';
        case 'upcoming':
            return item?.phase === 'UPCOMING';
        case 'ended':
            return item?.phase === 'ENDED';
        case 'missing_zalo':
            return isTodayItemMissingZalo(item);
        case 'missing_lms':
            return isTodayItemMissingLms(item);
        case 'spck':
            return Boolean(item?.is_spck_session);
        default:
            return true;
    }
}

function todayItemMatchesSearch(item, query) {
    if (!query) return true;
    const haystack = [
        item?.class_code,
        item?.group_name,
        item?.person,
        item?.lesson ? `buổi ${item.lesson}` : '',
        item?.lesson ? `b${item.lesson}` : '',
        item?.week_label,
        item?.khoi,
        item?.zalo_status_label,
        item?.lms_status_text,
        item?.qc_issue_note,
    ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
    return haystack.includes(query);
}

function getFilteredTodayItems() {
    const searchEl = getTodayEl('today-search-input');
    const query = String((searchEl && searchEl.value) || '').trim().toLowerCase();
    return todayAllItems.filter(
        item => todayItemMatchesFilter(item, todayActiveFilter) && todayItemMatchesSearch(item, query),
    );
}

function renderTodayHeaderAndBadges(data) {
    const mode = String(data?.execution_mode || 'dry-run').toLowerCase();
    const modeBadge = getTodayEl('today-mode-badge');
    if (modeBadge) {
        if (mode === 'production') {
            modeBadge.className = 'rounded-lg bg-rose-700 text-white border border-rose-950 px-2.5 py-1 text-[11px] font-bold';
            modeBadge.textContent = 'Chế độ: PRODUCTION (có ghi BCNB)';
        } else {
            modeBadge.className = 'rounded-lg bg-slate-800 text-white border border-slate-950 px-2.5 py-1 text-[11px] font-bold';
            modeBadge.textContent = 'Chế độ: DRY-RUN (an toàn)';
        }
    }

    const snap = data?.snapshot || {};
    const snapBadge = getTodayEl('today-snapshot-badge');
    if (snapBadge) {
        if (snap.snapshot_id) {
            const reqLabel = snap.required_week?.week ? `W${snap.required_week.week}` : 'W?';
            const latLabel = snap.latest_week?.week ? `W${snap.latest_week.week}` : 'W?';
            snapBadge.className = snap.is_stale
                ? 'rounded-lg bg-amber-50 text-amber-900 border border-amber-300 px-2.5 py-1 text-[11px] font-bold'
                : 'rounded-lg bg-emerald-50 text-emerald-900 border border-emerald-300 px-2.5 py-1 text-[11px] font-bold';
            snapBadge.textContent = `Kế hoạch #${snap.snapshot_id} · ${reqLabel} + ${latLabel}${snap.is_stale ? ' (cũ)' : ' (mới)'}`;
        } else {
            snapBadge.className = 'rounded-lg bg-amber-50 text-amber-900 border border-amber-300 px-2.5 py-1 text-[11px] font-bold';
            snapBadge.textContent = 'Chưa có kế hoạch BCNB — bấm Kiểm tra Live ngay';
        }
    }

    const banner = getTodayEl('today-sync-banner');
    if (banner) {
        const parts = [];
        const dateTag = data?.is_today ? `Hôm nay (${data?.target_date_iso || ''})` : `Ngày ${data?.target_date_iso || ''}`;
        parts.push(dateTag);
        if (!snap.snapshot_id) {
            parts.push('Chưa có snapshot BCNB trong DB; thao tác Live sẽ tự động dò kế hoạch mới.');
        } else if (snap.is_stale) {
            parts.push('Snapshot BCNB đã quá hạn; thao tác Live sẽ tự động làm mới trước khi kiểm tra.');
        }
        const job = data?.latest_sync_job;
        if (job && job.job_id) {
            const kindText = job.check_only ? 'Chỉ kiểm tra Live' : `Đồng bộ (${job.execution_mode || mode})`;
            const summary = job.bcnb_summary || {};
            const nxText = (summary.updated_count || summary.lms_updated_count)
                ? ` · NX Zalo: ${summary.updated_count || 0}, NX LMS: ${summary.lms_updated_count || 0}`
                : '';
            parts.push(`Lần chạy gần nhất #${job.job_id} [${kindText} · ${job.status || ''}]${nxText}`);
        }
        banner.textContent = parts.join(' | ');
    }
}

function renderTodayKpis(kpis) {
    const total = kpis?.total || {};
    const zalo = kpis?.zalo || {};
    const lms = kpis?.lms || {};
    const spck = kpis?.spck_b12_14 || {};
    const bcnb = kpis?.bcnb_pending || {};

    setTodayText('today-kpi-total', Number(total.count || 0));
    const totalSubParts = [
        `Đang học ${Number(total.ongoing || 0)}`,
        `Sắp học ${Number(total.upcoming || 0)}`,
        `Tan ca ${Number(total.ended || 0)}`,
    ];
    if (Number(total.missing_zalo_group || 0) > 0) {
        totalSubParts.push(`Thiếu nhóm ${Number(total.missing_zalo_group)}`);
    }
    setTodayText('today-kpi-total-sub', totalSubParts.join(' · '));

    setTodayText('today-kpi-zalo', Number(zalo.submitted_or_exempt || 0));
    setTodayText(
        'today-kpi-zalo-sub',
        `Đủ ${Number(zalo.done || 0)} · Trễ ${Number(zalo.late || 0)} · Miễn ${Number(zalo.exempt || 0)} · Thiếu ${Number(zalo.missing || 0)}`,
    );

    setTodayText('today-kpi-lms', Number(lms.ready_for_tick || 0));
    setTodayText(
        'today-kpi-lms-sub',
        `ĐD chưa XM ${Number(lms.attendance_unverified || 0)} · Thiếu NX ${Number(lms.comment_issue_count || 0)}`,
    );

    const codingTotal = Number(spck.coding_total || 0);
    const codingComplete = Number(spck.coding_complete || 0);
    const roboticsTotal = Number(spck.robotics_total || 0);
    const roboticsComplete = Number(spck.robotics_complete || 0);
    const reqTotal = codingTotal + roboticsTotal;
    const reqDone = codingComplete + roboticsComplete;
    const spckHeadline = Number(spck.total || 0) === 0
        ? '0'
        : (reqTotal > 0 ? `${reqDone}/${reqTotal}` : `${Number(spck.exempt_course_total || 0)} miễn`);
    setTodayText('today-kpi-spck', spckHeadline);
    setTodayText(
        'today-kpi-spck-sub',
        `Coding ${codingComplete}/${codingTotal} · Robotics ${roboticsComplete}/${roboticsTotal} · Miễn ${Number(spck.exempt_course_total || 0)}`,
    );

    setTodayText('today-kpi-bcnb', Number(bcnb.total_pending_rows || 0));
    setTodayText(
        'today-kpi-bcnb-sub',
        `Chờ NX Zalo ${Number(bcnb.need_nx_zalo || 0)} · Chờ NX LMS ${Number(bcnb.need_nx_lms || 0)}`,
    );
}

function renderTodayFilterChips(items) {
    for (const chip of TODAY_FILTER_CHIP_CONFIG) {
        const el = getTodayEl(chip.id);
        if (!el) continue;
        const count = items.filter(item => todayItemMatchesFilter(item, chip.key)).length;
        el.textContent = `${chip.label} (${count})`;
        el.className = chip.key === todayActiveFilter ? TODAY_FILTER_ACTIVE_CLASS : TODAY_FILTER_IDLE_CLASS;
    }
}

function renderTodaySpckPanel(items, kpis) {
    const panel = getTodayEl('today-spck-panel');
    const cardsEl = getTodayEl('today-spck-cards');
    const summaryEl = getTodayEl('today-spck-summary');
    if (!panel || !cardsEl) return;

    const spckItems = items.filter(item => Boolean(item?.is_spck_session));
    if (spckItems.length === 0) {
        panel.classList.add('hidden');
        cardsEl.replaceChildren();
        if (summaryEl) summaryEl.textContent = '0 lớp';
        return;
    }

    panel.classList.remove('hidden');
    const spckKpi = kpis?.spck_b12_14 || {};
    if (summaryEl) {
        summaryEl.textContent = `${spckItems.length} lớp B12–14 · Coding ${Number(spckKpi.coding_complete || 0)}/${Number(spckKpi.coding_total || 0)} · Robotics ${Number(spckKpi.robotics_complete || 0)}/${Number(spckKpi.robotics_total || 0)}`;
    }

    const fragment = document.createDocumentFragment();
    for (const item of spckItems) {
        const spckInfo = item.lms_spck || {};
        const statusUpper = String(spckInfo.status || '').toUpperCase();
        const isCourseExempt = Boolean(item.spck_exempt_course || !item.requires_spck);
        const isExempt = isCourseExempt || statusUpper === 'EXEMPT' || statusUpper === 'NOT_REQUIRED';
        const isDone = !isExempt && isTodaySpckComplete(item);
        const cardBorder = isExempt
            ? 'border-slate-300 bg-slate-50'
            : (isDone ? 'border-emerald-300 bg-emerald-50/70' : 'border-amber-400 bg-amber-50');
        const card = makeTodayEl('div', `rounded-xl border-2 p-2.5 space-y-1 text-xs ${cardBorder}`);

        const top = makeTodayEl('div', 'flex items-center justify-between gap-2');
        top.appendChild(makeTodayEl('span', 'font-extrabold text-slate-900', `${item.class_code || ''} · B${item.lesson || ''}`));
        const trackBadgeText = isCourseExempt
            ? 'MIỄN SPCK (PTB/CSB)'
            : (statusUpper === 'EXEMPT'
                ? 'TOÀN LỚP NGHỈ (MIỄN)'
                : (item.is_coding ? 'CODING (bắt buộc SPCK)' : (item.is_robotics ? 'ROBOTICS' : 'SPCK')));
        const trackBadgeClass = isExempt
            ? 'px-1.5 py-0.5 rounded bg-slate-200 text-slate-800 text-[10px] font-bold'
            : (item.is_coding
                ? 'px-1.5 py-0.5 rounded bg-indigo-600 text-white text-[10px] font-extrabold'
                : 'px-1.5 py-0.5 rounded bg-teal-600 text-white text-[10px] font-extrabold');
        top.appendChild(makeTodayEl('span', trackBadgeClass, trackBadgeText));
        card.appendChild(top);

        card.appendChild(
            makeTodayEl('div', 'font-semibold text-slate-700', `${item.time_range_display || 'Chưa rõ giờ'} · ${item.person || 'Chưa xác định'}`),
        );
        const cardSummaryText = spckInfo.summary_text || (isCourseExempt ? '⚪ Miễn SPCK (PTB/CSB)' : '⏳ Chưa kiểm tra SPCK');
        card.appendChild(
            makeTodayEl('div', isDone || isExempt ? 'font-bold text-slate-800' : 'font-bold text-rose-700', cardSummaryText),
        );
        const notes = Array.isArray(spckInfo.detail_notes) ? spckInfo.detail_notes : [];
        if (notes.length > 0) {
            card.appendChild(makeTodayEl('div', 'text-[11px] font-semibold text-amber-900', notes.join(' · ')));
        }
        fragment.appendChild(card);
    }
    cardsEl.replaceChildren(fragment);
}

function syncTodaySelectionUi() {
    const selectableVisible = todayVisibleItems.filter(item => Boolean(item?.eligible_for_sync && item?.target_key));
    const selectAllEl = getTodayEl('today-select-all');
    if (selectAllEl) {
        selectAllEl.disabled = selectableVisible.length === 0;
        selectAllEl.checked = selectableVisible.length > 0
            && selectableVisible.every(item => todaySelectedTargetKeys.has(item.target_key));
    }
    syncTodayActionButtons();
}

function syncTodayActionButtons() {
    const busy = typeof currentJobActive !== 'undefined' && Boolean(currentJobActive);
    const checkBtn = getTodayEl('today-check-live-btn');
    const syncBtn = getTodayEl('today-sync-bcnb-btn');
    const selectedBtn = getTodayEl('today-sync-selected-btn');

    if (checkBtn) checkBtn.disabled = busy;
    if (syncBtn) syncBtn.disabled = busy;
    if (selectedBtn) {
        const count = todaySelectedTargetKeys.size;
        selectedBtn.disabled = busy || count === 0;
        selectedBtn.textContent = count > 0
            ? `Quét các lớp đã chọn (${count})`
            : 'Quét các lớp đã chọn (0)';
    }

    for (const rowMeta of todayRowByTargetKey.values()) {
        const rowBusy = busy || !rowMeta.eligible;
        if (rowMeta.checkBtn) rowMeta.checkBtn.disabled = rowBusy;
        if (rowMeta.syncBtn) rowMeta.syncBtn.disabled = rowBusy;
        if (rowMeta.checkbox) rowMeta.checkbox.disabled = !rowMeta.eligible;
    }
}

function buildTodayPhaseBadge(item) {
    const phase = String(item?.phase || 'TIME_UNKNOWN');
    const clsByPhase = {
        ONGOING: 'inline-block px-2 py-0.5 rounded bg-emerald-100 text-emerald-900 border border-emerald-400 text-[10px] font-extrabold',
        UPCOMING: 'inline-block px-2 py-0.5 rounded bg-blue-50 text-blue-800 border border-blue-300 text-[10px] font-extrabold',
        ENDED: 'inline-block px-2 py-0.5 rounded bg-slate-200 text-slate-800 border border-slate-400 text-[10px] font-extrabold',
        TIME_UNKNOWN: 'inline-block px-2 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-300 text-[10px] font-extrabold',
    };
    return makeTodayEl('span', clsByPhase[phase] || clsByPhase.TIME_UNKNOWN, item?.phase_label || phase);
}

function formatTodayBcnbTickText(flag) {
    if (flag === true) return '✅ Đã tick';
    if (flag === false) return '⏳ Chưa tick';
    return '—';
}

function buildTodayTableRow(item) {
    const tr = makeTodayEl('tr', isTodayItemNeedsAttention(item) ? 'bg-rose-50/40' : 'bg-white');
    const targetKey = String(item?.target_key || '');
    const eligible = Boolean(item?.eligible_for_sync && targetKey);

    // Col 1: Checkbox
    const tdCheck = makeTodayEl('td', 'px-3 py-3 text-center align-top');
    const cb = makeTodayEl('input', 'w-4 h-4 rounded border-slate-400 cursor-pointer');
    cb.type = 'checkbox';
    cb.disabled = !eligible;
    cb.checked = eligible && todaySelectedTargetKeys.has(targetKey);
    cb.setAttribute('aria-label', `Chọn lớp ${item?.class_code || ''}`);
    cb.onchange = () => toggleTodayRowSelection(targetKey, Boolean(cb.checked));
    tdCheck.appendChild(cb);
    tr.appendChild(tdCheck);

    // Col 2: Khung giờ & Trạng thái
    const tdTime = makeTodayEl('td', 'px-4 py-3 align-top space-y-1');
    tdTime.appendChild(makeTodayEl('div', 'font-extrabold font-mono text-slate-900', item?.time_range_display || 'Chưa rõ giờ'));
    tdTime.appendChild(buildTodayPhaseBadge(item));
    if (item?.time_source) {
        tdTime.appendChild(makeTodayEl('div', 'text-[10px] font-semibold text-slate-500', `${item.class_date || ''} · ${item.time_source}`));
    }
    tr.appendChild(tdTime);

    // Col 3: Lớp · Nhóm · Tuần
    const tdClass = makeTodayEl('td', 'px-4 py-3 align-top space-y-1');
    tdClass.appendChild(
        makeTodayEl('div', 'font-extrabold text-slate-900', `${item?.class_code || ''} · Buổi ${item?.lesson || '?'}`),
    );
    const metaLine = makeTodayEl('div', 'flex flex-wrap items-center gap-1');
    metaLine.appendChild(
        makeTodayEl(
            'span',
            item?.is_current_week
                ? 'px-1.5 py-0.5 rounded bg-blue-100 text-blue-900 text-[10px] font-extrabold'
                : 'px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 text-[10px] font-extrabold',
            `${item?.week_label || ''}${item?.is_current_week ? ' (Tuần mới)' : ' (Tuần cần BC)'}`,
        ),
    );
    if (item?.spck_exempt_course) {
        metaLine.appendChild(makeTodayEl('span', 'px-1.5 py-0.5 rounded bg-slate-200 text-slate-800 text-[10px] font-bold', 'Miễn SPCK'));
    } else if (item?.is_coding) {
        metaLine.appendChild(makeTodayEl('span', 'px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-900 text-[10px] font-bold', 'Coding'));
    } else if (item?.is_robotics) {
        metaLine.appendChild(makeTodayEl('span', 'px-1.5 py-0.5 rounded bg-teal-100 text-teal-900 text-[10px] font-bold', 'Robotics'));
    }
    tdClass.appendChild(metaLine);
    if (item?.missing_zalo_group) {
        tdClass.appendChild(
            makeTodayEl('div', 'text-[11px] font-bold text-rose-700', `⚠️ Thiếu nhóm Zalo (${item.match_warning || 'MISSING'})`),
        );
    } else {
        tdClass.appendChild(makeTodayEl('div', 'text-xs font-medium text-slate-600', item?.group_name || '—'));
    }
    tr.appendChild(tdClass);

    // Col 4: Giáo viên
    const tdTeacher = makeTodayEl('td', 'px-4 py-3 align-top space-y-1');
    tdTeacher.appendChild(makeTodayEl('div', 'font-bold text-slate-900', item?.person || 'Chưa xác định'));
    if (item?.zalo_deadline) {
        tdTeacher.appendChild(makeTodayEl('div', 'text-[11px] text-slate-500', `Hạn Zalo: ${item.zalo_deadline}`));
    }
    tr.appendChild(tdTeacher);

    // Col 5: Zalo & Minh chứng
    const tdZalo = makeTodayEl('td', 'px-4 py-3 align-top space-y-1');
    const zaloLabelCls = isTodayItemMissingZalo(item) ? 'font-extrabold text-rose-700' : 'font-bold text-slate-900';
    tdZalo.appendChild(makeTodayEl('div', zaloLabelCls, item?.zalo_status_label || '⏳ Chưa quét Zalo'));
    if (item?.zalo_status_detail) {
        tdZalo.appendChild(makeTodayEl('div', 'text-[11px] text-slate-600', item.zalo_status_detail));
    }
    const evUrl = resolveTodayEvidenceUrl(item?.screenshot_url || (Array.isArray(item?.evidence_urls) ? item.evidence_urls[0] : ''));
    if (evUrl) {
        const imgBtn = makeTodayEl(
            'button',
            'mt-1 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-blue-50 text-blue-800 border border-blue-300 text-[11px] font-bold hover:bg-blue-100',
            'Xem minh chứng',
        );
        imgBtn.type = 'button';
        imgBtn.onclick = () => {
            if (typeof openImageModal === 'function') openImageModal(evUrl);
        };
        tdZalo.appendChild(imgBtn);
    }
    tr.appendChild(tdZalo);

    // Col 6: LMS · ĐD · NX · SPCK
    const tdLms = makeTodayEl('td', 'px-4 py-3 align-top space-y-1');
    tdLms.appendChild(
        makeTodayEl('div', item?.lms_ready_for_tick ? 'font-bold text-emerald-800' : 'font-bold text-slate-900', item?.lms_status_text || '⏳ Chưa kiểm tra LMS'),
    );
    const att = item?.lms_attendance || {};
    if (att.status) {
        tdLms.appendChild(
            makeTodayEl(
                'div',
                'text-[11px] font-semibold text-slate-700',
                `Điểm danh: ${Number(att.attended_count || 0)} đi học · ${Number(att.absent_count || 0)} nghỉ (${att.status})`,
            ),
        );
    }
    const com = item?.lms_comments || {};
    if (com.status) {
        tdLms.appendChild(
            makeTodayEl(
                'div',
                'text-[11px] font-semibold text-slate-700',
                `Nhận xét LMS: ${Number(com.submitted_count || 0)} đã NX · ${Number(com.exempt_count || 0)} miễn (${com.status})`,
            ),
        );
    }
    const spckUpper = String(item?.lms_spck?.status || '').toUpperCase();
    const isCourseExemptSpck = Boolean(item?.spck_exempt_course || !item?.requires_spck);
    const isExemptSpck = isCourseExemptSpck || spckUpper === 'EXEMPT' || spckUpper === 'NOT_REQUIRED';
    const spckSummary = item?.lms_spck?.summary_text || (item?.is_spck_session && isCourseExemptSpck ? '⚪ Miễn SPCK (PTB/CSB)' : '');
    if (item?.is_spck_session && spckSummary) {
        const spckCls = isExemptSpck
            ? 'text-[11px] font-bold text-slate-600'
            : (item.phase === 'ENDED' && !isTodaySpckComplete(item)
                ? 'text-[11px] font-extrabold text-rose-700'
                : 'text-[11px] font-extrabold text-emerald-800');
        tdLms.appendChild(makeTodayEl('div', spckCls, spckSummary));
    }
    const issues = Array.isArray(item?.issues) ? item.issues : [];
    for (const issue of issues) {
        const sev = String(issue?.severity || 'NOTE').toUpperCase();
        tdLms.appendChild(
            makeTodayEl(
                'div',
                sev === 'FAIL' ? 'text-[11px] font-bold text-rose-700' : 'text-[11px] font-semibold text-amber-800',
                issue?.text || '',
            ),
        );
    }
    tr.appendChild(tdLms);

    // Col 7: BCNB & Thao tác
    const tdOps = makeTodayEl('td', 'px-4 py-3 align-top space-y-1.5 text-center');
    tdOps.appendChild(makeTodayEl('div', 'text-[11px] font-bold text-slate-800', `NX Zalo: ${formatTodayBcnbTickText(item?.nx_zalo_checked)}`));
    tdOps.appendChild(makeTodayEl('div', 'text-[11px] font-bold text-slate-800', `NX LMS: ${formatTodayBcnbTickText(item?.nx_lms_checked)}`));
    if (item?.phase === 'ENDED' && !item?.lms_ready_for_tick && item?.lms_ready_reason) {
        tdOps.appendChild(makeTodayEl('div', 'text-[10px] font-semibold text-amber-800', item.lms_ready_reason));
    }

    const btnGroup = makeTodayEl('div', 'flex flex-wrap items-center justify-center gap-1 pt-1');
    const rowCheckBtn = makeTodayEl(
        'button',
        'px-2 py-1 rounded-md border border-blue-400 bg-blue-50 text-blue-900 text-[11px] font-bold hover:bg-blue-100 disabled:opacity-50',
        'Kiểm tra Live',
    );
    rowCheckBtn.type = 'button';
    rowCheckBtn.disabled = !eligible;
    rowCheckBtn.onclick = () => triggerSingleTodayRowSync(targetKey, true);

    const rowSyncBtn = makeTodayEl(
        'button',
        'px-2 py-1 rounded-md border border-emerald-500 bg-emerald-50 text-emerald-900 text-[11px] font-bold hover:bg-emerald-100 disabled:opacity-50',
        'Quét & Tick',
    );
    rowSyncBtn.type = 'button';
    rowSyncBtn.disabled = !eligible;
    rowSyncBtn.onclick = () => triggerSingleTodayRowSync(targetKey, false);

    btnGroup.appendChild(rowCheckBtn);
    btnGroup.appendChild(rowSyncBtn);
    tdOps.appendChild(btnGroup);
    tr.appendChild(tdOps);

    if (targetKey) {
        todayRowByTargetKey.set(targetKey, {
            eligible,
            checkbox: cb,
            checkBtn: rowCheckBtn,
            syncBtn: rowSyncBtn,
        });
    }
    return tr;
}

function renderTodayTable(items) {
    const tbody = getTodayEl('today-results-tbody');
    if (!tbody) return;
    todayRowByTargetKey.clear();
    todayVisibleItems = items;

    if (!items || items.length === 0) {
        const emptyTr = makeTodayEl('tr');
        const emptyTd = makeTodayEl('td', 'border-none px-6 py-10 text-center');
        emptyTd.colSpan = 7;
        emptyTd.appendChild(makeTodayEl('p', 'font-extrabold text-slate-800', 'Không có lớp học nào khớp bộ lọc'));
        emptyTd.appendChild(
            makeTodayEl('p', 'mt-1 text-sm font-medium text-slate-600', 'Thử chọn ngày khác hoặc bấm Kiểm tra Live ngay để quét kế hoạch BCNB.'),
        );
        emptyTr.appendChild(emptyTd);
        tbody.replaceChildren(emptyTr);
        syncTodaySelectionUi();
        return;
    }

    const fragment = document.createDocumentFragment();
    for (const item of items) {
        fragment.appendChild(buildTodayTableRow(item));
    }
    tbody.replaceChildren(fragment);
    syncTodaySelectionUi();
}

function renderTodayView() {
    renderTodayFilterChips(todayAllItems);
    renderTodayTable(getFilteredTodayItems());
}

async function loadTodayOverview(options = {}) {
    const reqSeq = ++todayOverviewRequestSeq;
    todayOverviewLoading = true;
    const requestedDate = options.dateIso !== undefined ? String(options.dateIso || '') : todaySelectedDateIso;
    const url = requestedDate
        ? `/api/today/overview?date=${encodeURIComponent(requestedDate)}`
        : '/api/today/overview';

    try {
        const res = await fetch(url);
        if (reqSeq !== todayOverviewRequestSeq) return null;
        if (!res.ok) {
            const errInfo = await parseTodayErrorResponse(res);
            setTodayText('today-sync-banner', `Không thể tải Lớp Hôm Nay: ${errInfo.message}`);
            return null;
        }
        const data = await res.json();
        if (reqSeq !== todayOverviewRequestSeq) return null;

        todayOverviewData = data;
        todayAllItems = Array.isArray(data.items) ? data.items : [];

        const eligibleKeys = new Set(
            todayAllItems.filter(it => it?.eligible_for_sync && it?.target_key).map(it => it.target_key),
        );
        for (const existingKey of Array.from(todaySelectedTargetKeys)) {
            if (!eligibleKeys.has(existingKey)) todaySelectedTargetKeys.delete(existingKey);
        }

        const dateInput = getTodayEl('today-date-input');
        if (dateInput && (!todaySelectedDateIso || options.forceSyncDateInput)) {
            dateInput.value = data.target_date_iso || '';
        }

        renderTodayHeaderAndBadges(data);
        renderTodayKpis(data.kpis || {});
        renderTodaySpckPanel(todayAllItems, data.kpis || {});
        renderTodayView();
        return data;
    } catch (_err) {
        if (reqSeq === todayOverviewRequestSeq) {
            setTodayText('today-sync-banner', 'Lỗi kết nối máy chủ khi tải dữ liệu Lớp Hôm Nay.');
        }
        return null;
    } finally {
        if (reqSeq === todayOverviewRequestSeq) {
            todayOverviewLoading = false;
        }
    }
}

function onTodayDateChange() {
    const dateInput = getTodayEl('today-date-input');
    todaySelectedDateIso = String((dateInput && dateInput.value) || '').trim();
    todaySelectedTargetKeys.clear();
    return loadTodayOverview({ dateIso: todaySelectedDateIso });
}

function resetTodayDate() {
    todaySelectedDateIso = '';
    todaySelectedTargetKeys.clear();
    return loadTodayOverview({ dateIso: '', forceSyncDateInput: true });
}

function setTodayFilter(filterKey) {
    todayActiveFilter = String(filterKey || 'all');
    renderTodayView();
}

function filterTodayRows() {
    renderTodayView();
}

function toggleTodayRowSelection(targetKey, checked) {
    if (!targetKey) return;
    if (checked) todaySelectedTargetKeys.add(targetKey);
    else todaySelectedTargetKeys.delete(targetKey);
    syncTodaySelectionUi();
}

function toggleSelectAllTodayRows(checked) {
    const wantChecked = Boolean(checked);
    for (const item of todayVisibleItems) {
        if (!item?.eligible_for_sync || !item?.target_key) continue;
        if (wantChecked) todaySelectedTargetKeys.add(item.target_key);
        else todaySelectedTargetKeys.delete(item.target_key);
        const meta = todayRowByTargetKey.get(item.target_key);
        if (meta && meta.checkbox) meta.checkbox.checked = wantChecked;
    }
    syncTodaySelectionUi();
}

async function parseTodayErrorResponse(res) {
    let errData = {};
    try {
        errData = await res.json();
    } catch (_err) {
        errData = {};
    }
    const detailObj = (errData && typeof errData.detail === 'object' && errData.detail !== null)
        ? errData.detail
        : ((errData && typeof errData.error === 'object' && errData.error !== null) ? errData.error : null);
    const code = String((detailObj && detailObj.code) || (errData && errData.code) || '');
    const message = String(
        (detailObj && detailObj.message)
        || (typeof (errData && errData.detail) === 'string' ? errData.detail : '')
        || (errData && errData.message)
        || `Yêu cầu thất bại (HTTP ${res.status}).`,
    );
    return { code, message };
}

async function executeTodaySyncRequest(options = {}) {
    if (typeof canStartAnotherJob === 'function' && !canStartAnotherJob()) {
        return null;
    }

    const checkOnly = Boolean(options.checkOnly);
    const confirmYellow = Boolean(options.confirmYellow);
    const skipProdConfirm = Boolean(options.skipProdConfirm);
    const selectedKeys = Array.isArray(options.selectedTargetKeys)
        ? options.selectedTargetKeys.filter(Boolean)
        : null;
    const targetDateIso = String(todaySelectedDateIso || todayOverviewData?.target_date_iso || '').trim() || null;

    if (!checkOnly && !skipProdConfirm && todayOverviewData?.execution_mode === 'production') {
        const confirmFn = getTodayConfirmFn();
        const prodWarning = selectedKeys && selectedKeys.length > 0
            ? `Bạn sắp chạy QUÉT LIVE & TICK BCNB (PRODUCTION) cho ${selectedKeys.length} lớp đã chọn. Xác nhận ghi lên BCNB?`
            : 'Bạn sắp chạy QUÉT LIVE & TICK BCNB ĐÚNG TUẦN ở chế độ PRODUCTION (có ghi dữ liệu lên BCNB). Xác nhận tiếp tục?';
        if (confirmFn && !confirmFn(prodWarning)) {
            return null;
        }
    }

    const body = {
        target_date_iso: targetDateIso,
        check_only: checkOnly,
        tick_lms: !checkOnly,
        refresh_discovery_if_stale: true,
        confirm_yellow: confirmYellow,
    };
    if (selectedKeys && selectedKeys.length > 0) {
        body.selected_target_keys = selectedKeys;
    }

    try {
        const res = await fetch('/api/today/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!res.ok) {
            const errInfo = await parseTodayErrorResponse(res);
            if (res.status === 409 && errInfo.code === 'SCAN_PREFLIGHT_CONFIRMATION_REQUIRED' && !confirmYellow) {
                const confirmFn = getTodayConfirmFn();
                if (confirmFn && confirmFn(`${errInfo.message}\n\nBạn có muốn xác nhận tiếp tục chạy không?`)) {
                    return executeTodaySyncRequest({
                        ...options,
                        confirmYellow: true,
                        skipProdConfirm: true,
                    });
                }
                return null;
            }
            alert(errInfo.message);
            return null;
        }

        const data = await res.json();
        setTodayText('today-sync-banner', data.message || 'Đang chạy đồng bộ Lớp Hôm Nay...');
        if (data.job_id && typeof trackJob === 'function') {
            await trackJob(data.job_id);
        }
        return data;
    } catch (_err) {
        alert('Lỗi kết nối máy chủ khi bắt đầu kiểm tra Lớp Hôm Nay.');
        return null;
    }
}

function triggerTodayLiveSync(checkOnly) {
    return executeTodaySyncRequest({ checkOnly: Boolean(checkOnly) });
}

function triggerTodaySelectedSync() {
    const selectedKeys = Array.from(todaySelectedTargetKeys);
    if (selectedKeys.length === 0) {
        alert('Vui lòng tích chọn ít nhất 1 lớp đã tan ca để quét.');
        return null;
    }
    return executeTodaySyncRequest({ checkOnly: false, selectedTargetKeys: selectedKeys });
}

function triggerSingleTodayRowSync(targetKey, checkOnly = false) {
    if (!targetKey) return null;
    return executeTodaySyncRequest({ checkOnly: Boolean(checkOnly), selectedTargetKeys: [targetKey] });
}

async function handleTodayJobTerminal(job, _payload) {
    if (job && job.message) {
        setTodayText('today-sync-banner', job.message);
    }
    if (job && job.status && job.status !== 'SUCCESS') {
        alert(
            job.status === 'CANCELLED'
                ? 'Đã hủy đồng bộ Lớp Hôm Nay.'
                : (job.message || 'Đồng bộ Lớp Hôm Nay hoàn tất với cảnh báo hoặc lỗi.'),
        );
    }
    await loadTodayOverview({ silent: true });
}

function collectTodayMissingReasons(item) {
    if (!item || item.phase !== 'ENDED') return [];
    const reasons = [];
    if (isTodayItemMissingZalo(item)) {
        reasons.push(item?.missing_zalo_group ? 'Thiếu nhóm Zalo' : (item?.zalo_status_label || 'Thiếu báo cáo Zalo'));
    }
    const attStatus = String(item?.lms_attendance?.status || '').toUpperCase();
    const comStatus = String(item?.lms_comments?.status || '').toUpperCase();
    if (attStatus === 'UNVERIFIED') reasons.push('Chưa điểm danh LMS');
    if (comStatus === 'MISSING' || comStatus === 'REVIEW') reasons.push('Thiếu nhận xét LMS');
    if (item?.is_spck_session && !isTodaySpckComplete(item)) {
        const spckNote = Array.isArray(item?.lms_spck?.detail_notes) ? item.lms_spck.detail_notes[0] : '';
        reasons.push(spckNote || item?.lms_spck?.summary_text || `Chưa đủ SPCK B${item.lesson || ''}`);
    }
    return reasons;
}

async function copyTodayReminders() {
    const byTeacher = new Map();
    for (const item of todayAllItems) {
        const reasons = collectTodayMissingReasons(item);
        if (reasons.length === 0) continue;
        const teacher = String(item?.person || 'Chưa xác định').trim();
        if (!byTeacher.has(teacher)) byTeacher.set(teacher, []);
        byTeacher.get(teacher).push(
            `- ${item.time_range_display || ''} | ${item.class_code || ''} B${item.lesson || '?'} (${item.week_label || ''}): ${reasons.join(', ')}`,
        );
    }

    if (byTeacher.size === 0) {
        alert('Không có lớp nào thiếu Zalo, LMS hoặc SPCK để nhắc giáo viên.');
        return;
    }

    const dateLabel = todayOverviewData?.target_date_iso || todaySelectedDateIso || '';
    const sections = [`Nhắc bổ sung Lớp Hôm Nay (${dateLabel}):`];
    for (const [teacher, lines] of byTeacher.entries()) {
        sections.push(`\n${teacher}:`);
        sections.push(...lines);
    }
    const text = sections.join('\n');

    try {
        if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(text);
            alert(`Đã copy nhắc nhở cho ${byTeacher.size} giáo viên.`);
        } else {
            alert(text);
        }
    } catch (_err) {
        alert('Không thể truy cập clipboard tự động.');
    }
}
