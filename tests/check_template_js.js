const fs = require('fs');
const path = require('path');
const vm = require('vm');

const indexPath = 'templates/index.html';
const indexHtml = fs.readFileSync(indexPath, 'utf8');
const versionedScriptSources = [...indexHtml.matchAll(/<script\s+src="([^"]+)"\s*><\/script>/g)]
  .map(match => match[1])
  .filter(src => src.startsWith('/static/js/'));
const scriptSources = versionedScriptSources.map(src => src.split('?')[0]);

const expectedOrder = [
  '/static/js/tailwind-config.js',
  '/static/js/state.js',
  '/static/js/groups.js',
  '/static/js/jobs.js',
  '/static/js/googleform.js',
  '/static/js/lms.js',
  '/static/js/modals.js',
];
if (JSON.stringify(scriptSources) !== JSON.stringify(expectedOrder)) {
  throw new Error(`Unexpected local script order: ${JSON.stringify(scriptSources)}`);
}
if (/<script>([\s\S]*?)<\/script>/.test(indexHtml)) {
  throw new Error('Inline JavaScript is not allowed after Phase 3.3 modularization');
}

let combinedSource = '';
const scriptSourceByPath = new Map();
for (const src of versionedScriptSources) {
  if (!src.includes('?v={{ asset_version }}')) {
    throw new Error(`Local script is missing cache-busting version: ${src}`);
  }
  const filePath = src.split('?')[0].replace(/^\//, '').replaceAll('/', path.sep);
  if (!fs.existsSync(filePath)) throw new Error(`Missing script: ${filePath}`);
  const source = fs.readFileSync(filePath, 'utf8');
  new Function(source);
  scriptSourceByPath.set(src.split('?')[0], source);
  combinedSource += `\n${source}`;
}

const templateFiles = [indexPath, ...fs.readdirSync('templates/partials')
  .filter(name => name.endsWith('.html'))
  .map(name => path.join('templates/partials', name))];
const templates = templateFiles.map(file => fs.readFileSync(file, 'utf8')).join('\n');
const requiredIds = [
  'header-title', 'status-text', 'results-table', 'searchInput',
  'zalo-preflight', 'zalo-preflight-status', 'zalo-preflight-checks',
  'zalo-dashboard-plan-brief', 'tab-specs', 'btn-specs', 'specs-evidence-table-body',
  'zalo-review-queue', 'zalo-review-queue-items',
  'paste-area', 'ocr-results-table', 'mapping-table', 'form-history-table',
  'groups-table-body', 'ignored-groups-table-body', 'full-log-terminal',
  'bcnb-zalo-gaps-summary', 'bcnb-zalo-gaps-stale', 'bcnb-zalo-gaps-table-body',
  'job-timeline', 'lmsModal', 'lmsFileInput', 'image-modal', 'modal-image',
  'btn-copy-reminders', 'btnCopyLmsMissing', 'btn-rescan-failed',
  'lmsSelectedTickCount', 'btnToggleAllLmsTick',
  'lms-student-class-codes', 'btnFetchLmsStudents',
  'btn-export-weekly-csv', 'history-week-select', 'history-job-select',
  'btn-load-weekly-history', 'weekly-history-status-badge',
];
for (const id of requiredIds) {
  if (!new RegExp(`id=["']${id}["']`).test(templates)) {
    throw new Error(`Missing DOM contract id: ${id}`);
  }
}

const handlerNames = new Set();
const ignoredCallKeywords = new Set(['if', 'for', 'while', 'switch', 'catch']);
for (const attribute of templates.matchAll(/on(?:click|keyup|change)="([^"]+)"/g)) {
  for (const call of attribute[1].matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
    if (!ignoredCallKeywords.has(call[1])) handlerNames.add(call[1]);
  }
}
for (const name of handlerNames) {
  if (!new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`).test(combinedSource)) {
    throw new Error(`Missing global handler function: ${name}`);
  }
}

if (indexHtml.split(/\r?\n/).length > 120) {
  throw new Error('templates/index.html exceeded the Phase 3.3 facade budget');
}

const jobsSource = scriptSourceByPath.get('/static/js/jobs.js') || '';
const googleformSource = scriptSourceByPath.get('/static/js/googleform.js') || '';
if (/setInterval\s*\(\s*updateDashboardData/.test(jobsSource)) {
  throw new Error('Job polling must not use an always-on setInterval');
}
for (const contract of [
  ['stopJobPolling', jobsSource],
  ['scheduleJobPolling', jobsSource],
  ['formatGoogleFormTerminalMessage', jobsSource],
  ['errorMessage.textContent', googleformSource],
  ['if (!res.ok', googleformSource],
  ['data.detail', googleformSource],
]) {
  if (!contract[1].includes(contract[0])) {
    throw new Error(`Missing Google Form frontend regression contract: ${contract[0]}`);
  }
}

function assertFrontend(condition, message) {
  if (!condition) throw new Error(message);
}

function createFakeElement(id = '') {
  const classes = new Set();
  return {
    id,
    children: [],
    className: '',
    disabled: false,
    hidden: false,
    innerHTML: '',
    innerText: '',
    textContent: '',
    value: '',
    scrollHeight: 0,
    clientHeight: 0,
    scrollTop: 0,
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name);
        else classes.delete(name);
        return enabled;
      },
    },
    addEventListener: () => {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = [...children];
    },
    setAttribute: () => {},
  };
}

function createFrontendHarness(fetchImpl) {
  const elements = new Map();
  const timers = new Map();
  const alerts = [];
  const clipboardWrites = [];
  let nextTimerId = 1;
  let nextUuidId = 0;
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, createFakeElement(id));
      return elements.get(id);
    },
    querySelector(selector) {
      return selector === '.action-button--scan'
        ? this.getElementById('scan-button')
        : null;
    },
    querySelectorAll: () => [],
    createElement: tag => createFakeElement(tag),
    createDocumentFragment: () => createFakeElement('fragment'),
  };
  const context = vm.createContext({
    document,
    fetch: fetchImpl,
    alert: message => alerts.push(String(message)),
    confirm: () => true,
    navigator: {
      clipboard: {
        writeText: text => {
          clipboardWrites.push(String(text));
          return Promise.resolve();
        },
      },
    },
    crypto: {
      randomUUID: () => {
        nextUuidId += 1;
        return nextUuidId === 1 ? 'frontend-test-uuid' : `frontend-test-uuid-${nextUuidId}`;
      },
    },
    FileReader: function FileReader() {},
    console,
    setTimeout(callback, delay) {
      const id = nextTimerId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  });
  context.fetchGoogleFormHistory = () => Promise.resolve();
  return { context, elements, timers, alerts, clipboardWrites };
}

async function flushAsyncWork() {
  for (let index = 0; index < 4; index += 1) {
    await new Promise(resolve => setImmediate(resolve));
  }
}

async function checkJobPollingAndTerminalDetails() {
  const jobResponses = [
    {
      id: 901,
      job_type: 'GOOGLEFORM_SUBMIT',
      status: 'RUNNING',
      message: 'Đang xử lý',
      retryable: true,
      cancel_allowed: true,
      attempt: 1,
    },
    {
      id: 901,
      job_type: 'GOOGLEFORM_SUBMIT',
      status: 'PARTIAL_SUCCESS',
      message: 'Hoàn tất: 1 thành công, 2 thất bại.',
      retryable: true,
      cancel_allowed: false,
      attempt: 1,
      results: {
        kind: 'googleform_submit',
        outcomes: [
          { name: 'Học viên A', class_code: 'PTI10', success: true },
          { name: 'Học viên B', class_code: 'PTI10', success: false, error: 'Radio chưa được chọn.' },
          { name: 'Học viên C', class_code: 'PTI10', success: false, error: 'Không tìm thấy lớp.' },
        ],
      },
    },
  ];
  const requests = [];
  const harness = createFrontendHarness(async url => {
    requests.push(url);
    if (url === '/api/logs') return { ok: true, json: async () => ({ logs: [] }) };
    if (url.startsWith('/api/jobs/')) {
      return { ok: true, status: 200, json: async () => jobResponses.shift() };
    }
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => jobResponses.shift() };
    }
    throw new Error(`Unexpected request in polling harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();

  assertFrontend(requests[0] === '/api/jobs/latest', 'Reload must inspect the latest job once');
  assertFrontend(
    vm.runInContext('currentJobActive', harness.context) === true,
    'Reload must restore an active job',
  );
  assertFrontend(harness.timers.size === 1, 'Active job must schedule exactly one poll');
  const scheduledPoll = [...harness.timers.entries()][0];
  assertFrontend(scheduledPoll[1].delay === 3000, 'Active job poll interval must be 3 seconds');
  harness.timers.delete(scheduledPoll[0]);
  await scheduledPoll[1].callback();
  await flushAsyncWork();

  assertFrontend(
    vm.runInContext('currentJobActive', harness.context) === false,
    'Terminal job must clear active state',
  );
  assertFrontend(harness.timers.size === 0, 'Terminal job must stop polling immediately');
  const terminalAlert = harness.alerts.at(-1) || '';
  assertFrontend(terminalAlert.includes('Học viên B (PTI10): Radio chưa được chọn.'), 'Terminal alert must show the first student error');
  assertFrontend(terminalAlert.includes('Học viên C (PTI10): Không tìm thấy lớp.'), 'Terminal alert must show every student error');
  assertFrontend(
    harness.elements.get('status-text').textContent.includes('Học viên B'),
    'Terminal status must persist student error details',
  );
  assertFrontend(
    !harness.elements.get('job-retry-button').classList.contains('hidden'),
    'Partial Google Form job must offer retry',
  );

  vm.runInContext("renderJobControls({id: 902, job_type: 'GOOGLEFORM_SUBMIT', status: 'SUCCESS', retryable: true, cancel_allowed: false})", harness.context);
  assertFrontend(
    harness.elements.get('job-retry-button').classList.contains('hidden'),
    'Successful Google Form job must not offer retry',
  );
  vm.runInContext("renderJobControls({id: 903, job_type: 'GOOGLEFORM_SUBMIT', status: 'CANCELLED', retryable: true, cancel_allowed: false})", harness.context);
  assertFrontend(
    harness.elements.get('job-retry-button').classList.contains('hidden'),
    'Cancelled Google Form job must not offer retry',
  );
  vm.runInContext("renderJobControls({id: 904, job_type: 'LMS_AUTOMATION', status: 'SUCCESS', retryable: true, cancel_allowed: false})", harness.context);
  assertFrontend(
    !harness.elements.get('job-retry-button').classList.contains('hidden'),
    'Retry restriction must remain scoped to Google Form jobs',
  );
  vm.runInContext("renderJobControls({id: 905, job_type: 'ZALO_DISCOVERY', status: 'FAILED', execution_mode: 'dry-run', retryable: true, cancel_allowed: false})", harness.context);
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Dò tìm · chỉ đọc',
    'Discovery job must show its read-only mode',
  );
  vm.runInContext("renderJobControls({id: 906, job_type: 'ZALO_SCAN', status: 'SUCCESS', mutates_external: true, retryable: false, cancel_allowed: false})", harness.context);
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Production',
    'A mutating job without an exposed mode must still show production',
  );
}

async function checkGoogleFormHistoryAndHttpError() {
  const requests = [];
  const harness = createFrontendHarness(async (url) => {
    requests.push(url);
    if (url === '/api/googleform/history') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          history: [{
            submitted_at: '2026-07-17 10:00:00',
            student_name: 'Học viên lỗi',
            class_code: 'PTI10',
            status: 'Thất bại',
            error_message: 'Radio chưa chuyển sang trạng thái đã chọn.',
          }],
        }),
      };
    }
    if (url === '/api/googleform/submit') {
      return {
        ok: false,
        status: 422,
        json: async () => ({ detail: 'Danh sách học viên không hợp lệ.' }),
      };
    }
    throw new Error(`Unexpected request in Google Form harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(googleformSource, harness.context);
  await vm.runInContext('fetchGoogleFormHistory()', harness.context);

  const historyTable = harness.elements.get('form-history-table');
  const statusCell = historyTable.children[0].children[2];
  assertFrontend(statusCell.children.length === 2, 'History error must render as visible content');
  assertFrontend(
    statusCell.children[1].textContent === 'Radio chưa chuyển sang trạng thái đã chọn.',
    'History must display the complete error_message',
  );

  vm.runInContext("ocrStudents = [{name: 'Học viên lỗi', class_code: 'PTI10'}]", harness.context);
  await vm.runInContext('submitGoogleForm()', harness.context);
  assertFrontend(requests.includes('/api/googleform/submit'), 'Submit harness must call the Google Form endpoint');
  assertFrontend(
    harness.alerts.at(-1).includes('Danh sách học viên không hợp lệ.'),
    'Submit HTTP error must display API detail',
  );
  assertFrontend(
    vm.runInContext('currentJobId', harness.context) === null,
    'Rejected submit response must not start job polling',
  );
}

async function checkLazyIncrementalLogs() {
  const requests = [];
  const harness = createFrontendHarness(async (url) => {
    requests.push(url);
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (url === '/api/logs?after=0&limit=100') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          records: [{ timestamp: '2026-08-14T10:00:00Z', message: 'first' }],
          cursor: 1,
        }),
      };
    }
    if (url === '/api/logs?after=1&limit=100') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          records: [{ timestamp: '2026-08-14T10:00:01Z', message: 'second' }],
          cursor: 2,
        }),
      };
    }
    throw new Error(`Unexpected request in incremental log harness: ${url}`);
  });
  harness.context.document.getElementById('tab-logs').classList.add('active');
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();
  await vm.runInContext('refreshLogs()', harness.context);
  await flushAsyncWork();

  assertFrontend(
    requests.includes('/api/logs?after=0&limit=100')
      && requests.includes('/api/logs?after=1&limit=100'),
    'Visible log tab must request only incremental records using a cursor',
  );
  const terminal = harness.elements.get('full-log-terminal');
  assertFrontend(terminal.children.length === 2, 'Incremental logs must append instead of rebuilding duplicates');

  harness.context.document.getElementById('tab-logs').classList.remove('active');
  const requestCount = requests.length;
  await vm.runInContext('refreshLogs()', harness.context);
  assertFrontend(requests.length === requestCount, 'Hidden log tab must not poll the log endpoint');
}

async function checkRequiredWeekDashboardView() {
  const harness = createFrontendHarness(async (url) => {
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    throw new Error(`Unexpected request in required-week harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();

  const results = [
    {
      group: 'GA37 W32',
      person: 'Teacher',
      status: '✅ Đã nộp – Có minh chứng',
      scan_target: { week: 32, year: 2026, lesson: '3', class_date: '07/08/2026' },
    },
    {
      group: 'GA37 W33',
      person: 'Teacher',
      status: '❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng',
      scan_target: { week: 33, year: 2026, lesson: '4', class_date: '14/08/2026' },
    },
    {
      group: 'PTA12 W32',
      person: 'Teacher',
      status: '⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công',
      scan_target: { week: 32, year: 2026, lesson: '3', class_date: '07/08/2026' },
    },
  ];
  vm.runInContext('zaloScanPlan = {required_week: {week: 32, year: 2026}}', harness.context);
  vm.runInContext(`renderZaloResults(${JSON.stringify(results)})`, harness.context);
  const table = harness.elements.get('results-table');
  const specsTable = harness.elements.get('specs-evidence-table-body');
  assertFrontend(table.children.length === 2, 'Dashboard must render only required-week rows');
  assertFrontend(specsTable.children.length === 2, 'Technical specs table must render required-week evidence rows');
  assertFrontend(table.children.every(row => row.children[0].textContent.includes('W32')), 'Dashboard must hide latest-week rows');
  assertFrontend(harness.elements.get('count-total').innerText === 2, 'Dashboard metrics must count required-week rows only');

  vm.runInContext('zaloScanPlan = {required_week: {week: 33, year: 2026}}; renderZaloResults(latestZaloResults, true)', harness.context);
  assertFrontend(harness.elements.get('results-table').children.length === 1, 'Dashboard must follow the refreshed required week');
  assertFrontend(harness.elements.get('results-table').children[0].children[0].textContent.includes('W33'), 'Dashboard must render the refreshed required-week target');

  vm.runInContext('renderZaloResults([])', harness.context);
  assertFrontend(harness.elements.get('count-total').innerText === '0', 'Empty Zalo job must clear old metrics');
  assertFrontend(harness.elements.get('results-table').children.length === 1, 'Empty Zalo job must render one empty-state row');
  assertFrontend(vm.runInContext('latestZaloResults.length', harness.context) === 0, 'Empty Zalo job must clear old target rows');
}

async function checkZaloPreflightUiSafety() {
  const harness = createFrontendHarness(async () => {
    throw new Error('Preflight UI check must not call the network');
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);

  vm.runInContext('renderZaloPreflight(null)', harness.context);
  assertFrontend(
    harness.elements.get('scan-button').disabled,
    'Scan must stay disabled until a fresh preflight report exists',
  );
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Chưa xác định',
    'Missing preflight must not claim a runtime mode',
  );

  vm.runInContext(
    "zaloScanPlan = {snapshot_id: 7, target_count: 12, required_week: {week: 38, year: 2026}}; renderZaloPreflight({status: 'GREEN', mode: 'production', target_count: 12, worker_count: 1, checked_at: '2026-09-26T10:00:00+00:00', checks: []})",
    harness.context,
  );
  assertFrontend(
    !harness.elements.get('scan-button').disabled,
    'GREEN preflight with a plan must enable scanning',
  );
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Production',
    'Runtime mode badge must use the server preflight mode',
  );
  assertFrontend(
    harness.elements.get('zalo-step-scan-detail').textContent === 'Sẵn sàng bắt đầu quét',
    'Workflow must explain that a GREEN scan is ready',
  );
  vm.runInContext("currentJobExecutionMode = 'dry-run'; currentJobType = 'ZALO_DISCOVERY'; renderRuntimeMode(currentJobExecutionMode, currentJobType)", harness.context);
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Dò tìm · chỉ đọc',
    'Discovery job must show its read-only mode',
  );
  vm.runInContext(
    "renderZaloPreflight({status: 'RED', mode: 'production', target_count: 12, worker_count: 1, checks: []})",
    harness.context,
  );
  assertFrontend(
    harness.elements.get('runtime-mode-badge').textContent === 'Dò tìm · chỉ đọc',
    'Preflight refresh must not overwrite the current job mode',
  );

  vm.runInContext(
    "renderZaloPreflight({status: 'RED', mode: 'production', target_count: 12, worker_count: 1, checks: [{status: 'RED', message: 'blocked'}]})",
    harness.context,
  );
  assertFrontend(
    harness.elements.get('scan-button').disabled,
    'RED preflight must disable scanning again',
  );
  vm.runInContext(
    "renderZaloPreflight({status: 'UNKNOWN', mode: 'production', target_count: 12, checks: []})",
    harness.context,
  );
  assertFrontend(
    harness.elements.get('scan-button').disabled,
    'Unknown preflight status must fail closed like RED',
  );
}

async function checkBcnbZaloGapView() {
  let shouldFail = false;
  const requests = [];
  const harness = createFrontendHarness(async (url) => {
    requests.push(url);
    if (url !== '/api/discovery/bcnb-zalo-gaps') {
      throw new Error(`Unexpected request in BCNB/Zalo gap harness: ${url}`);
    }
    if (shouldFail) throw new Error('temporary read failure');
    return {
      ok: true,
      status: 200,
      json: async () => ({
        available: true,
        stale: false,
        summary: {
          source_count: 4,
          matched_count: 2,
          missing_count: 1,
          ambiguous_count: 1,
          transition_review_count: 0,
        },
        items: [
          {
            class_code: 'VT-C4K-GA01',
            status: 'MISSING_ZALO_GROUP',
            teachers: ['Teacher A'],
            periods: [{ week: 32, year: 2026, lesson: '7', class_date: '07/08/2026' }],
          },
          {
            class_code: '<img src=x onerror=alert(1)>',
            status: 'AMBIGUOUS_ZALO_GROUP',
            teachers: ['Teacher B'],
            periods: [{ week: 33, year: 2026, lesson: '8', class_date: '14/08/2026' }],
          },
        ],
      }),
    };
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/groups.js'), harness.context);
  await vm.runInContext('fetchBcnbZaloGaps()', harness.context);

  const table = harness.elements.get('bcnb-zalo-gaps-table-body');
  assertFrontend(requests[0] === '/api/discovery/bcnb-zalo-gaps', 'Gap view must use the read-only snapshot endpoint');
  assertFrontend(table.children.length === 2, 'Gap view must render every review item');
  assertFrontend(table.children[1].children[1].textContent === '<img src=x onerror=alert(1)>', 'Gap values must render through textContent');
  assertFrontend(harness.elements.get('bcnb-zalo-gaps-summary').textContent.includes('1 chưa có nhóm'), 'Gap summary must expose missing class count');

  shouldFail = true;
  await vm.runInContext('fetchBcnbZaloGaps()', harness.context);
  assertFrontend(table.children.length === 2, 'A refresh failure must preserve the last good gap list');
}

async function checkTeacherReminderCopyHelpers() {
  const harness = createFrontendHarness(async (url) => {
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    throw new Error(`Unexpected request in reminder copy harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  vm.runInContext(scriptSourceByPath.get('/static/js/lms.js'), harness.context);
  await flushAsyncWork();

  // 1. Missing required week must fail closed even if latestZaloResults has multi-week rows
  vm.runInContext(
    "zaloScanPlan = null; latestZaloResults = [{group: 'W42', person: 'GV A', status: '❌ Chưa báo cáo', scan_target: {week: 42, year: 2026}}, {group: 'W43', person: 'GV A', status: '❌ Chưa báo cáo', scan_target: {week: 43, year: 2026}}]",
    harness.context,
  );
  await vm.runInContext('copyZaloTeacherReminders()', harness.context);
  assertFrontend(
    harness.alerts.at(-1).includes('Chưa có tuần cần báo cáo hợp lệ'),
    'Missing required week must block copying multi-week rows',
  );
  assertFrontend(harness.clipboardWrites.length === 0, 'Missing required week must not write to clipboard');

  // 1b. Empty Zalo results when required week exists
  vm.runInContext('zaloScanPlan = {required_week: {week: 42, year: 2026}}; latestZaloResults = []', harness.context);
  await vm.runInContext('copyZaloTeacherReminders()', harness.context);
  assertFrontend(
    harness.alerts.at(-1).includes('Chưa có dữ liệu kết quả quét Zalo'),
    'Empty Zalo results must alert clearly without copying',
  );
  assertFrontend(harness.clipboardWrites.length === 0, 'Empty Zalo results must not write to clipboard');

  // 2. Mixed Zalo results grouped by teacher for required week
  const results = [
    {
      group: 'VT-C4K-GI35',
      person: 'Nguyễn Văn A',
      status: '❌ Chưa tìm thấy trong khoảng đã kiểm chứng',
      scan_target: { week: 42, year: 2026, lesson: '2', class_date: '17/10/2026' },
    },
    {
      group: 'VT-C4K-GA41',
      person: 'Nguyễn Văn A',
      status: '⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công',
      scan_target: { week: 42, year: 2026, lesson: '3', class_date: '18/10/2026' },
    },
    {
      group: 'VT-JSB16',
      person: 'Trần Thị B',
      status: '✅ Đã nộp – Có minh chứng',
      scan_target: { week: 42, year: 2026, lesson: '5', class_date: '18/10/2026' },
    },
    {
      group: 'VT-PTB09',
      person: 'Lê Văn C',
      status: '❌ Chưa báo cáo',
      scan_target: { week: 42, year: 2026, lesson: '1', class_date: '19/10/2026' },
    },
  ];
  vm.runInContext('zaloScanPlan = {required_week: {week: 42, year: 2026}}', harness.context);
  vm.runInContext(`renderZaloResults(${JSON.stringify(results)})`, harness.context);
  await vm.runInContext('copyZaloTeacherReminders()', harness.context);

  assertFrontend(harness.clipboardWrites.length === 1, 'Zalo reminder copy must write once to clipboard');
  const zaloCopied = harness.clipboardWrites[0];
  assertFrontend(zaloCopied.includes('Tuần 42/2026 - 3 lớp'), 'Zalo reminder must include week and unreported count');
  assertFrontend(
    zaloCopied.includes('- GV Nguyễn Văn A: VT-C4K-GI35 (Buổi 2 - 17/10/2026), VT-C4K-GA41 (Buổi 3 - 18/10/2026)'),
    'Zalo reminder must group multiple classes under the same teacher',
  );
  assertFrontend(
    zaloCopied.includes('- GV Lê Văn C: VT-PTB09 (Buổi 1 - 19/10/2026)'),
    'Zalo reminder must include second teacher',
  );
  assertFrontend(!zaloCopied.includes('VT-JSB16'), 'Completed classes must be excluded from teacher reminders');

  // 3. LMS overdue copy helper
  await vm.runInContext('copyLmsMissingClasses()', harness.context);
  assertFrontend(
    harness.alerts.at(-1).includes('Không có lớp nào quá hạn nhận xét LMS'),
    'Empty LMS plan must alert clearly',
  );

  vm.runInContext(
    "lmsPreviewPlan = {week: 42, year: 2026, missing_classes: ['VT-C4K-GI35', 'VT-C4K-GA41']}",
    harness.context,
  );
  await vm.runInContext('copyLmsMissingClasses()', harness.context);
  assertFrontend(harness.clipboardWrites.length === 2, 'LMS overdue copy must write to clipboard');
  const lmsCopied = harness.clipboardWrites[1];
  assertFrontend(lmsCopied.includes('Tuần 42/2026 - 2 lớp'), 'LMS overdue copy must include week and class count');
  assertFrontend(lmsCopied.includes('- VT-C4K-GI35\n- VT-C4K-GA41'), 'LMS overdue copy must list all overdue classes');
}

async function checkSelectiveRescanWorkflow() {
  const postBodies = [];
  let job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    message: 'Hoàn tất quét chọn lọc',
    results: {
      snapshot_id: 88,
      selective_rescan: true,
      selected_target_keys: ['2026-W42|VT-C4K-GI35|2|2026-10-17'],
      items: [
        {
          group: 'VT-C4K-GI35',
          person: 'Nguyễn Văn A',
          status: '✅ Đã nộp – Có minh chứng',
          originSnapshot: 88,
          scan_target: {
            week: 42,
            year: 2026,
            class_code: 'VT-C4K-GI35',
            lesson: '2',
            class_date: '17/10/2026',
            class_date_iso: '2026-10-17',
          },
        },
      ],
    },
  };
  const harness = createFrontendHarness(async (url, options = {}) => {
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (url.startsWith('/api/zalo-scan-preflight')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'GREEN',
          mode: 'dry-run',
          target_count: 3,
          worker_count: 1,
          checks: [],
        }),
      };
    }
    if (url === '/run-scan') {
      postBodies.push(JSON.parse(options.body || '{}'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          job_id: 950,
          selective_rescan: true,
        }),
      };
    }
    if (url === '/api/jobs/950') {
      return {
        ok: true,
        status: 200,
        json: async () => job950Response,
      };
    }
    if (url === '/api/jobs/950/targets') {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    throw new Error(`Unexpected request in selective rescan harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();

  // 1. Missing required period blocks selective rescan
  vm.runInContext('zaloScanPlan = null; latestZaloResults = []', harness.context);
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  assertFrontend(
    harness.alerts.at(-1).includes('Chưa có tuần cần báo cáo hợp lệ để quét lại'),
    'Selective rescan must block when required period is missing',
  );

  // 2. Setup 2 rows (1 passed, 1 failed) in W42 (snapshot 88)
  const initialRows = [
    {
      group: 'VT-JSB16',
      person: 'Trần Thị B',
      status: '✅ Đã nộp – Có minh chứng',
      originSnapshot: 88,
      scan_target: {
        week: 42,
        year: 2026,
        class_code: 'VT-JSB16',
        lesson: '5',
        class_date: '18/10/2026',
        class_date_iso: '2026-10-18',
      },
    },
    {
      group: 'VT-C4K-GI35',
      person: 'Nguyễn Văn A',
      status: '❌ Chưa tìm thấy trong khoảng đã kiểm chứng',
      originSnapshot: 88,
      scan_target: {
        week: 42,
        year: 2026,
        class_code: 'VT-C4K-GI35',
        lesson: '2',
        class_date: '17/10/2026',
        class_date_iso: '2026-10-17',
      },
    },
  ];
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 88, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  vm.runInContext(`renderZaloResults(${JSON.stringify(initialRows)})`, harness.context);
  assertFrontend(harness.elements.get('count-total').innerText === 2, 'Initial total must be 2');
  assertFrontend(harness.elements.get('count-success').innerText === 1, 'Initial success must be 1');
  assertFrontend(harness.elements.get('count-failed').innerText === 1, 'Initial failed must be 1');

  // 3. Selective rescan RUNNING (results=null) and CANCELLED must preserve baseline rows
  vm.runInContext('rememberSelectiveScanBaseline(); currentJobId = 950', harness.context);
  job950Response = { id: 950, job_type: 'ZALO_SCAN', status: 'RUNNING', results: null };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2,
    'Selective rescan RUNNING state with null results must preserve baseline table',
  );
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'CANCELLED',
    results: { kind: 'zalo_scan_cancelled', verified_actions: 0, cancelled: true },
  };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2,
    'Selective rescan CANCELLED state must preserve baseline table',
  );

  // 4. Trigger selective rescan of failed targets on same snapshot 88 and verify merge with baseline
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    message: 'Hoàn tất quét chọn lọc',
    results: {
      snapshot_id: 88,
      selective_rescan: true,
      selected_target_keys: ['2026-W42|VT-C4K-GI35|2|2026-10-17'],
      items: [
        {
          group: 'VT-C4K-GI35',
          person: 'Nguyễn Văn A',
          status: '✅ Đã nộp – Có minh chứng',
          originSnapshot: 88,
          scan_target: {
            week: 42,
            year: 2026,
            class_code: 'VT-C4K-GI35',
            lesson: '2',
            class_date: '17/10/2026',
            class_date_iso: '2026-10-17',
          },
        },
      ],
    },
  };
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  await flushAsyncWork();
  assertFrontend(postBodies.length === 1, 'Selective rescan must POST to /run-scan once');
  assertFrontend(
    JSON.stringify(postBodies[0].selected_target_keys) === JSON.stringify(['2026-W42|VT-C4K-GI35|2|2026-10-17']),
    'Selective rescan must send only failed canonical target keys',
  );
  assertFrontend(harness.timers.size === 1, 'trackJob must schedule immediate poll for selective rescan');
  const scheduledPoll = [...harness.timers.entries()][0];
  harness.timers.delete(scheduledPoll[0]);
  await scheduledPoll[1].callback();
  await flushAsyncWork();
  assertFrontend(harness.elements.get('count-total').innerText === 2, 'Merged total after selective rescan must stay 2');
  assertFrontend(harness.elements.get('count-success').innerText === 2, 'Merged success after selective rescan must become 2');
  assertFrontend(harness.elements.get('count-failed').innerText === 0, 'Merged failed after selective rescan must become 0');

  // 5. Cross-snapshot guard (88 -> 89): must not merge snapshot 88 baseline into snapshot 89 selective rescan
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 88, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  vm.runInContext(`renderZaloResults(${JSON.stringify(initialRows)})`, harness.context);
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 89, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    results: {
      snapshot_id: 89,
      selective_rescan: true,
      items: [
        {
          group: 'VT-C4K-GI35',
          person: 'Nguyễn Văn A',
          status: '✅ Đã nộp – Có minh chứng',
          originSnapshot: 89,
          scan_target: {
            week: 42,
            year: 2026,
            class_code: 'VT-C4K-GI35',
            lesson: '2',
            class_date: '17/10/2026',
            class_date_iso: '2026-10-17',
          },
        },
      ],
    },
  };
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  const snapshot89Origins = vm.runInContext('latestZaloResults.map(r => r.originSnapshot)', harness.context);
  assertFrontend(
    JSON.stringify(snapshot89Origins) === JSON.stringify([89]),
    'Cross-snapshot selective rescan must not retain rows from an older discovery snapshot',
  );

  // 6. Legacy persisted job payload without snapshot_id must remain unknown-origin and never merge into snapshot 89
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 89, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    results: {
      items: initialRows,
    },
  };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    vm.runInContext('latestZaloResultsSnapshotId', harness.context) === null,
    'Legacy payload without snapshot_id must keep null snapshot origin',
  );
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    results: {
      snapshot_id: 89,
      selective_rescan: true,
      items: [
        {
          group: 'VT-C4K-GI35',
          person: 'Nguyễn Văn A',
          status: '✅ Đã nộp – Có minh chứng',
          originSnapshot: 89,
          scan_target: {
            week: 42,
            year: 2026,
            class_code: 'VT-C4K-GI35',
            lesson: '2',
            class_date: '17/10/2026',
            class_date_iso: '2026-10-17',
          },
        },
      ],
    },
  };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    JSON.stringify(vm.runInContext('latestZaloResults.map(r => r.originSnapshot)', harness.context)) === JSON.stringify([89]),
    'Legacy payload without snapshot_id must not be merged into snapshot 89 selective rescan',
  );

  // 7. Initial load order: job results arriving before renderZaloScanPlan must still preserve baseline on RUNNING/CANCELLED
  vm.runInContext('zaloScanPlan = null; clearSelectiveScanBaseline()', harness.context);
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'SUCCESS',
    results: {
      snapshot_id: 88,
      items: initialRows,
    },
  };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 88, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  job950Response = { id: 950, job_type: 'ZALO_SCAN', status: 'RUNNING', results: null };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2,
    'Initial-load order selective RUNNING must keep 2 rows',
  );
  job950Response = {
    id: 950,
    job_type: 'ZALO_SCAN',
    status: 'CANCELLED',
    results: { kind: 'zalo_scan_cancelled', snapshot_id: 88, selective_rescan: true, cancelled: true },
  };
  await vm.runInContext('updateDashboardData()', harness.context);
  harness.timers.clear();
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2,
    'Initial-load order selective CANCELLED must keep 2 rows',
  );
}

async function checkManualApproveWorkflow() {
  const postBodies = [];
  let job960Response = {
    id: 960,
    job_type: 'ZALO_MANUAL_APPROVE',
    status: 'SUCCESS',
    message: '[DRY-RUN] Đã duyệt tay & mô phỏng tích BCNB cho VT-C4K-GI35',
    results: {
      kind: 'zalo_manual_approve',
      source_job_id: 900,
      target_key: '2026-W42|VT-C4K-GI35|2|2026-10-17',
      status: '❌ Chưa tìm thấy trong khoảng đã kiểm chứng',
      status_detail: '[DRY-RUN] Đã mô phỏng duyệt tay bởi operator (chưa tích thật)',
      outcome: 'DRY_RUN',
      dry_run: true,
      evidence_path: '/api/evidence/900/gi35.png',
    },
  };
  const harness = createFrontendHarness(async (url, options = {}) => {
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (url === '/api/zalo-manual-approve') {
      postBodies.push(JSON.parse(options.body || '{}'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          job_id: 960,
          source_job_id: 900,
          target_key: '2026-W42|VT-C4K-GI35|2|2026-10-17',
        }),
      };
    }
    if (url === '/api/jobs/960') {
      return { ok: true, status: 200, json: async () => job960Response };
    }
    throw new Error(`Unexpected request in manual approve harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();

  const rows = [
    {
      group: 'VT-JSB16',
      person: 'Trần Thị B',
      status: '✅ Đã nộp – Có minh chứng',
      evidence_path: '/api/evidence/900/jsb16.png',
      originSnapshot: 88,
      source_job_id: 900,
      scan_target: {
        week: 42,
        year: 2026,
        class_code: 'VT-JSB16',
        lesson: '5',
        class_date: '18/10/2026',
        class_date_iso: '2026-10-18',
      },
    },
    {
      group: 'VT-C4K-GI35',
      person: 'Nguyễn Văn A',
      status: '❌ Chưa tìm thấy trong khoảng đã kiểm chứng',
      evidence: {
        images: {
          latest: {
            path: '/api/evidence/900/gi35.png',
            evidence_status: 'AVAILABLE',
          },
        },
      },
      originSnapshot: 88,
      source_job_id: 900,
      scan_target: {
        week: 42,
        year: 2026,
        class_code: 'VT-C4K-GI35',
        lesson: '2',
        class_date: '17/10/2026',
        class_date_iso: '2026-10-17',
      },
    },
    {
      group: 'VT-C4K-GA41',
      person: 'Lê Văn C',
      status: '⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công',
      evidence: {
        images: {
          latest: {
            path: '/api/evidence/900/ga41.png',
            evidence_status: 'MISSING',
          },
        },
      },
      originSnapshot: 88,
      source_job_id: 900,
      scan_target: {
        week: 42,
        year: 2026,
        class_code: 'VT-C4K-GA41',
        lesson: '3',
        class_date: '18/10/2026',
        class_date_iso: '2026-10-18',
      },
    },
  ];
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 88, available: true, target_count: 3, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  vm.runInContext(
    `latestZaloResultsJobId = 900; renderZaloResults(${JSON.stringify(rows)}); rememberSelectiveScanBaseline()`,
    harness.context,
  );

  const table = harness.elements.get('results-table');
  assertFrontend(table.children.length === 3, 'Manual approve harness must render 3 rows');
  const row0Texts = (table.children[0].children[4].children[0]?.children || []).map(el => el.textContent || '');
  const row1Texts = (table.children[1].children[4].children[0]?.children || []).map(el => el.textContent || '');
  const row2Texts = (table.children[2].children[4].children[0]?.children || []).map(el => el.textContent || '');
  assertFrontend(
    !row0Texts.some(text => text.includes('Duyệt & Tích')),
    'Already submitted row must not render manual approve button',
  );
  assertFrontend(
    row1Texts.some(text => text.includes('Duyệt & Tích')),
    'Failed row with AVAILABLE evidence must render manual approve button',
  );
  assertFrontend(
    !row2Texts.some(text => text.includes('Duyệt & Tích')),
    'Warning row with MISSING evidence must not render manual approve button',
  );

  // 1. DRY_RUN manual approve must NOT mark row as ✅ submitted
  await vm.runInContext(
    `manualApproveZaloTarget(${JSON.stringify(rows[1])}, '/api/evidence/900/gi35.png', 900)`,
    harness.context,
  );
  await flushAsyncWork();
  assertFrontend(postBodies.length === 1, 'Manual approve must POST to /api/zalo-manual-approve once');
  assertFrontend(postBodies[0].job_id === 900, 'Manual approve must pass originating source job_id');
  assertFrontend(postBodies[0].class_code === 'VT-C4K-GI35', 'Manual approve must pass class_code');
  assertFrontend(
    postBodies[0].evidence_path === '/api/evidence/900/gi35.png',
    'Manual approve must pass available evidence_path',
  );

  const dryRunPoll = [...harness.timers.entries()][0];
  harness.timers.delete(dryRunPoll[0]);
  await dryRunPoll[1].callback();
  await flushAsyncWork();

  assertFrontend(
    harness.elements.get('count-success').innerText === 1,
    'DRY_RUN manual approve must keep dashboard success count at 1',
  );
  assertFrontend(
    harness.elements.get('count-failed').innerText === 2,
    'DRY_RUN manual approve must keep dashboard failed count at 2',
  );
  assertFrontend(
    vm.runInContext("latestZaloResults[1].submitted_for_bcnb === false && latestZaloResults[1].status.includes('❌')", harness.context),
    'DRY_RUN manual approve must not mark row as submitted',
  );

  // 2. Production VERIFIED manual approve transitions row to ✅ Đã duyệt tay & Tích BCNB
  job960Response = {
    id: 960,
    job_type: 'ZALO_MANUAL_APPROVE',
    status: 'SUCCESS',
    message: 'Đã duyệt tay & tích BCNB thành công cho VT-C4K-GI35',
    results: {
      kind: 'zalo_manual_approve',
      source_job_id: 900,
      target_key: '2026-W42|VT-C4K-GI35|2|2026-10-17',
      status: '✅ Đã duyệt tay & Tích BCNB',
      outcome: 'VERIFIED',
      dry_run: false,
      evidence_path: '/api/evidence/900/gi35.png',
      updated_item: {
        ...rows[1],
        status: '✅ Đã duyệt tay & Tích BCNB',
        submitted_for_bcnb: true,
        manual_approved: true,
        bcnb_update_state: 'VERIFIED',
      },
    },
  };
  await vm.runInContext(
    `manualApproveZaloTarget(latestZaloResults[1], '/api/evidence/900/gi35.png', 900)`,
    harness.context,
  );
  await flushAsyncWork();
  const prodPoll = [...harness.timers.entries()][0];
  harness.timers.delete(prodPoll[0]);
  await prodPoll[1].callback();
  await flushAsyncWork();

  assertFrontend(
    harness.elements.get('count-success').innerText === 2,
    'VERIFIED manual approve completion must increment dashboard success count to 2',
  );
  assertFrontend(
    harness.elements.get('count-failed').innerText === 1,
    'VERIFIED manual approve completion must decrement dashboard failed count to 1',
  );
  assertFrontend(
    vm.runInContext("latestZaloResults[1].status === '✅ Đã duyệt tay & Tích BCNB'", harness.context),
    'VERIFIED manual approve completion must update latestZaloResults row in-place',
  );
  assertFrontend(
    vm.runInContext("selectiveBaselineResults[1].status === '✅ Đã duyệt tay & Tích BCNB'", harness.context),
    'VERIFIED manual approve completion must also update selectiveBaselineResults in-place',
  );
}

async function checkLmsPreviewDetailAndClassSelection() {
  const postBodies = [];
  let resolveConfirmFetch = null;
  const harness = createFrontendHarness(async (url, options = {}) => {
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (url === '/api/lms/confirm') {
      postBodies.push(JSON.parse(options.body || '{}'));
      if (resolveConfirmFetch === 'DEFER') {
        await new Promise(resolve => {
          resolveConfirmFetch = resolve;
        });
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'success',
          job_id: 970,
          dry_run: true,
        }),
      };
    }
    if (url === '/api/jobs/970') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: 970,
          job_type: 'LMS_AUTOMATION',
          status: 'SUCCESS',
          message: 'Dry-run hoàn tất: 1 lớp dự kiến tick.',
        }),
      };
    }
    throw new Error(`Unexpected request in LMS selection harness: ${url}`);
  });
  harness.context.document.getElementById('lmsModal').firstElementChild = createFakeElement('lmsModalCard');
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  vm.runInContext(scriptSourceByPath.get('/static/js/lms.js'), harness.context);
  await flushAsyncWork();

  const plan = {
    kind: 'lms_preview',
    week: 42,
    year: 2026,
    dry_run: true,
    can_confirm: true,
    confirmation_token: 'tok-lms-42',
    missing_classes: ['VT-C4K-SB24', 'VT-OTHER-99'],
    overdue_details: {
      'VT-C4K-SB24': 'Buổi 3, Buổi 4',
    },
    unmatched_classes: ['VT-OTHER-99'],
    missing_but_checked_classes: [],
    classes_to_tick: ['VT-JSB15', 'VT-PTB09'],
    blocking_issues: [],
  };

  vm.runInContext(
    `showLmsReview({id: 969}, ${JSON.stringify(plan)})`,
    harness.context,
  );

  const missingContainer = harness.elements.get('lmsMissingClasses');
  assertFrontend(missingContainer.children.length === 2, 'LMS preview must render 2 overdue rows');
  assertFrontend(
    missingContainer.children[0].textContent.includes('VT-C4K-SB24 (Buổi 3, Buổi 4)') &&
      missingContainer.children[0].textContent.includes('BCNB: Chưa tích'),
    'First overdue row must display overdue session numbers and BCNB unchecked badge',
  );
  assertFrontend(
    missingContainer.children[1].textContent.includes('VT-OTHER-99') &&
      missingContainer.children[1].textContent.includes('Ngoài BCNB tuần này'),
    'Unmatched overdue row must display out-of-BCNB status badge',
  );

  // Copy helper includes overdue session detail when present
  await vm.runInContext('copyLmsMissingClasses()', harness.context);
  assertFrontend(
    harness.clipboardWrites.at(-1).includes('- VT-C4K-SB24 (Buổi 3, Buổi 4)\n- VT-OTHER-99'),
    'LMS copy reminder must include overdue session details when present',
  );

  // Default: all classes_to_tick selected (2/2)
  assertFrontend(
    harness.elements.get('lmsSelectedTickCount').textContent === '2/2',
    'Default LMS preview must select all eligible classes (2/2)',
  );
  assertFrontend(
    harness.elements.get('btnConfirmLms').disabled === false,
    'Confirm button must be enabled when at least 1 class is selected',
  );

  // Toggle all -> deselects all (0/2) and disables confirm button
  vm.runInContext('toggleAllLmsTickSelection()', harness.context);
  assertFrontend(
    harness.elements.get('lmsSelectedTickCount').textContent === '0/2',
    'Toggle all must clear selection to 0/2',
  );
  assertFrontend(
    harness.elements.get('btnConfirmLms').disabled === true,
    'Confirm button must be disabled when 0 classes are selected',
  );

  // Select only VT-PTB09
  vm.runInContext("toggleLmsTickClass('VT-PTB09', true)", harness.context);
  assertFrontend(
    harness.elements.get('lmsSelectedTickCount').textContent === '1/2',
    'Selecting one class must update counter to 1/2',
  );
  assertFrontend(
    harness.elements.get('btnConfirmLms').disabled === false,
    'Confirm button must re-enable when 1 class is selected',
  );

  // While confirmLmsTick is in-flight, checkboxes and toggle buttons must be locked
  resolveConfirmFetch = 'DEFER';
  const confirmPromise = vm.runInContext('confirmLmsTick()', harness.context);
  await flushAsyncWork();
  assertFrontend(
    vm.runInContext('lmsConfirmInFlight', harness.context) === true,
    'Confirm in-flight state must be active while awaiting /api/lms/confirm',
  );
  vm.runInContext("toggleLmsTickClass('VT-PTB09', false); toggleAllLmsTickSelection()", harness.context);
  assertFrontend(
    harness.elements.get('lmsSelectedTickCount').textContent === '1/2',
    'In-flight confirmation must ignore attempts to mutate selected classes',
  );
  assertFrontend(
    harness.elements.get('btnConfirmLms').disabled === true &&
      harness.elements.get('btnToggleAllLmsTick').disabled === true,
    'Confirm and toggle-all buttons must stay disabled while confirmation is in-flight',
  );
  // Closing/reopening/resetting modal or starting a new analysis while confirmation is in-flight must be blocked
  await vm.runInContext('closeLmsModal(); openLmsModal(); resetLmsModal(); startLmsAnalysis()', harness.context);
  assertFrontend(
    vm.runInContext('lmsConfirmInFlight', harness.context) === true &&
      vm.runInContext('lmsAnalysisJobId', harness.context) === 969,
    'Close/open/reset/startLmsAnalysis must not clear in-flight LMS confirmation lock or state',
  );
  assertFrontend(
    harness.alerts.at(-1).includes('Đang gửi xác nhận kế hoạch LMS'),
    'Starting another job while LMS confirmation is in-flight must alert and block',
  );
  resolveConfirmFetch();
  await confirmPromise;
  await flushAsyncWork();
  harness.timers.clear();

  assertFrontend(postBodies.length === 1, 'Confirm LMS must POST once to /api/lms/confirm');
  assertFrontend(
    JSON.stringify(postBodies[0].selected_classes_to_tick) === JSON.stringify(['VT-PTB09']),
    'Confirm LMS must pass only the user-selected subset of classes_to_tick',
  );
}

async function checkGoogleFormFetchLmsStudentsAndIdempotency() {
  const postBodies = [];
  const harness = createFrontendHarness(async (url, options = {}) => {
    if (url === '/api/googleform/fetch-lms-students') {
      postBodies.push(JSON.parse(options.body));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'success',
          job_id: 985,
          class_codes: ['VT-C4K-GA41', 'VT-C4K-GI35'],
        }),
      };
    }
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (
      url.startsWith('/api/zalo-scan-plan') ||
      url.startsWith('/api/zalo-scan-preflight') ||
      url.startsWith('/api/bcnb-zalo-gaps') ||
      url.startsWith('/api/logs')
    ) {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    throw new Error(`Unexpected URL in LMS students harness: ${url}`);
  });
  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  vm.runInContext(googleformSource, harness.context);
  await flushAsyncWork();
  harness.timers.clear();

  const codeInput = harness.context.document.getElementById('lms-student-class-codes');
  // Empty input must alert and not call API
  codeInput.value = '   ';
  await vm.runInContext('fetchLmsStudentsForGoogleForm()', harness.context);
  assertFrontend(
    postBodies.length === 0 && harness.alerts.at(-1).includes('ít nhất 1 mã lớp'),
    'Empty LMS class codes input must alert and not dispatch fetch',
  );

  // Invalid format must alert and not call API
  codeInput.value = 'bad_code';
  await vm.runInContext('fetchLmsStudentsForGoogleForm()', harness.context);
  assertFrontend(
    postBodies.length === 0 && harness.alerts.at(-1).includes('không hợp lệ'),
    'Invalid LMS class code input must alert and not dispatch fetch',
  );

  // Valid comma/space input deduplicates and dispatches POST /api/googleform/fetch-lms-students
  codeInput.value = 'vt-c4k-ga41, VT-C4K-GI35 vt-c4k-ga41';
  await vm.runInContext('fetchLmsStudentsForGoogleForm()', harness.context);
  harness.timers.clear();
  assertFrontend(
    postBodies.length === 1 &&
      JSON.stringify(postBodies[0].class_codes) === JSON.stringify(['VT-C4K-GA41', 'VT-C4K-GI35']),
    'Valid LMS class codes input must deduplicate and POST to /api/googleform/fetch-lms-students',
  );
  assertFrontend(
    vm.runInContext('currentJobId', harness.context) === 985 &&
      vm.runInContext('lmsStudentsFetchBusy', harness.context) === true,
    'Fetch LMS students must track job #985 and set busy lock',
  );

  // Simulate terminal GOOGLEFORM_LMS_STUDENTS job completion
  const lmsPayload = {
    kind: 'googleform_lms_students',
    students: [
      { name: 'Nguyễn Văn A', class_code: 'VT-C4K-GA41' },
      { name: 'Trần Thị B', class_code: 'VT-C4K-GI35' },
    ],
    class_codes: ['VT-C4K-GA41', 'VT-C4K-GI35'],
  };
  vm.runInContext(
    `handleTerminalJob({id: 985, job_type: 'GOOGLEFORM_LMS_STUDENTS', status: 'SUCCESS', message: 'Hoàn tất'}, ${JSON.stringify(lmsPayload)})`,
    harness.context,
  );
  assertFrontend(
    vm.runInContext('lmsStudentsFetchBusy', harness.context) === false,
    'Terminal GOOGLEFORM_LMS_STUDENTS job must release busy lock',
  );
  assertFrontend(
    vm.runInContext('ocrStudents.length', harness.context) === 2 &&
      Boolean(vm.runInContext('googleformBatchId', harness.context)),
    'Terminal GOOGLEFORM_LMS_STUDENTS job must populate ocrStudents and generate googleformBatchId',
  );
  const initialBatchId = vm.runInContext('googleformBatchId', harness.context);
  // Re-fetching the exact same student roster must preserve the same googleformBatchId
  vm.runInContext('currentJobActive = false;', harness.context);
  await vm.runInContext('fetchLmsStudentsForGoogleForm()', harness.context);
  harness.timers.clear();
  vm.runInContext(
    `handleTerminalJob({id: 986, job_type: 'GOOGLEFORM_LMS_STUDENTS', status: 'SUCCESS', message: 'Hoàn tất'}, ${JSON.stringify(lmsPayload)})`,
    harness.context,
  );
  const refetchedBatchId = vm.runInContext('googleformBatchId', harness.context);
  assertFrontend(
    Boolean(initialBatchId) && refetchedBatchId === initialBatchId,
    'Re-fetching identical students must keep the same googleformBatchId even without localStorage',
  );
  const changedBatchId = vm.runInContext(
    "ensureGoogleFormIdempotencyKey([{name: 'Khác', class_code: 'VT-C4K-GA41'}])",
    harness.context,
  );
  assertFrontend(
    Boolean(changedBatchId) && changedBatchId !== initialBatchId,
    'ensureGoogleFormIdempotencyKey must rotate batch_id when student roster changes',
  );
  assertFrontend(
    harness.context.document.getElementById('ocr-results-table').children.length === 2 &&
      harness.context.document.getElementById('btnSubmitGoogleForm').disabled === false,
    'Terminal GOOGLEFORM_LMS_STUDENTS job must render student rows and enable submit button',
  );
}

async function checkWeeklyHistoryAndCsvExport() {
  const requestedUrls = [];
  const sampleSummary = {
    week: 41,
    year: 2026,
    period_key: '2026-W41',
    selected_job_id: 702,
    available_weeks: [
      { week: 42, year: 2026, period_key: '2026-W42', label: 'W42/2026' },
      { week: 41, year: 2026, period_key: '2026-W41', label: 'W41/2026' },
    ],
    jobs: [
      {
        job_id: 702,
        status: 'SUCCESS',
        execution_mode: 'production',
        selective_rescan: true,
        target_count: 2,
      },
      {
        job_id: 701,
        status: 'PARTIAL_SUCCESS',
        execution_mode: 'dry-run',
        selective_rescan: false,
        target_count: 2,
      },
    ],
    summary: {
      total: 2,
      zalo_completed: 1,
      zalo_exempt: 0,
      zalo_pending: 1,
      zalo_unscanned: 0,
      lms_completed: 1,
      lms_overdue: 1,
      lms_unscanned: 0,
    },
    lms_summary: {
      has_lms_data: true,
      ticked_count: 1,
      dry_run_count: 1,
      missing_count: 1,
    },
    rows: [
      {
        class_code: 'VT-JSB16',
        teacher: '=Trần Thị B',
        lesson: '4',
        class_date: '11/10/2026',
        zalo_status: '✅ Đã nộp – Có minh chứng',
        lms_status: '✅ Đã nhận xét & Tích BCNB',
        verified_at: '2026-10-11T14:00:00Z',
        raw_chat: 'TIN_NHAN_RIENG_TU_KHONG_XUAT',
      },
      {
        class_code: 'VT-C4K-GI35',
        teacher: 'Nguyễn Văn A',
        lesson: '1',
        class_date: '10/10/2026',
        zalo_status: '❌ Chưa tìm thấy báo cáo',
        lms_status: '❌ Chưa nhận xét (Trễ 3 ngày)',
        verified_at: '2026-10-11T14:02:00Z',
      },
    ],
    items: [
      {
        group: 'VT-JSB16',
        person: 'Trần Thị B',
        timestamp: '2026-10-11T14:00:00Z',
        status: '✅ Đã nộp – Có minh chứng',
        lms_status: '✅ Đã nhận xét & Tích BCNB',
        source_job_id: 702,
        scan_target: {
          week: 41,
          year: 2026,
          class_code: 'VT-JSB16',
          lesson: '4',
          class_date: '11/10/2026',
          class_date_iso: '2026-10-11',
        },
        evidence: {
          scanned_at: '2026-10-11T14:00:00Z',
          messages_checked: 10,
        },
      },
      {
        group: 'VT-C4K-GI35',
        person: 'Nguyễn Văn A',
        timestamp: '2026-10-11T14:02:00Z',
        status: '❌ Chưa tìm thấy báo cáo',
        lms_status: '❌ Chưa nhận xét (Trễ 3 ngày)',
        source_job_id: 701,
        scan_target: {
          week: 41,
          year: 2026,
          class_code: 'VT-C4K-GI35',
          lesson: '1',
          class_date: '10/10/2026',
          class_date_iso: '2026-10-10',
        },
        evidence: {
          scanned_at: '2026-10-11T14:02:00Z',
          messages_checked: 8,
        },
      },
    ],
  };

  let pendingW40Resolver = null;
  let pendingW41ExportRejecter = null;
  let pendingW42LiveResolver = null;
  const harness = createFrontendHarness(async url => {
    requestedUrls.push(url);
    if (url === '/api/jobs/latest') {
      return { ok: true, status: 200, json: async () => null };
    }
    if (url.startsWith('/api/reports/weekly-summary?week=41') && pendingW41ExportRejecter === 'DEFER_REJECT') {
      return new Promise((_, reject) => {
        pendingW41ExportRejecter = reject;
      });
    }
    if (url.startsWith('/api/reports/weekly-summary?week=42')) {
      if (pendingW42LiveResolver === 'DEFER') {
        return new Promise(resolve => {
          pendingW42LiveResolver = resolve;
        });
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          week: 42,
          year: 2026,
          period_key: '2026-W42',
          selected_job_id: 800,
          available_weeks: sampleSummary.available_weeks,
          jobs: [],
          summary: { total: 1 },
          lms_summary: { has_lms_data: false },
          rows: [
            {
              class_code: 'VT-ONLY-W42',
              teacher: 'GV Tuần 42',
              lesson: '8',
              class_date: '18/10/2026',
              zalo_status: '✅ Đã nộp – Có minh chứng',
              lms_status: 'Chưa quét LMS',
              verified_at: '2026-10-18T14:00:00Z',
            },
          ],
          items: [],
        }),
      };
    }
    if (url === '/api/reports/weekly-summary?week=40&year=2026') {
      if (pendingW40Resolver === 'DEFER') {
        return new Promise(resolve => {
          pendingW40Resolver = resolve;
        });
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          week: 40,
          year: 2026,
          period_key: '2026-W40',
          selected_job_id: null,
          available_weeks: sampleSummary.available_weeks,
          jobs: [],
          summary: { total: 0 },
          lms_summary: { has_lms_data: false },
          rows: [],
          items: [],
        }),
      };
    }
    if (url.startsWith('/api/reports/weekly-summary')) {
      return { ok: true, status: 200, json: async () => sampleSummary };
    }
    if (
      url.startsWith('/api/zalo-scan-plan') ||
      url.startsWith('/api/zalo-scan-preflight') ||
      url.startsWith('/api/logs')
    ) {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    throw new Error(`Unexpected URL in weekly history harness: ${url}`);
  });

  vm.runInContext(scriptSourceByPath.get('/static/js/state.js'), harness.context);
  vm.runInContext(jobsSource, harness.context);
  await flushAsyncWork();
  harness.timers.clear();

  // 1. Load historical summary for W41/2026
  await vm.runInContext('loadWeeklyHistorySummary({ week: 41, year: 2026 })', harness.context);
  assertFrontend(
    requestedUrls.includes('/api/reports/weekly-summary?week=41&year=2026'),
    'loadWeeklyHistorySummary must request /api/reports/weekly-summary with week and year',
  );
  assertFrontend(
    vm.runInContext('isHistoricalWeekViewActive()', harness.context) === true,
    'Historical week view must become active after loading weekly history',
  );
  assertFrontend(
    harness.elements.get('count-total').innerText === 2 &&
      harness.elements.get('count-success').innerText === 1 &&
      harness.elements.get('count-failed').innerText === 1,
    'Historical week view must update Dashboard metrics for the selected week',
  );
  assertFrontend(
    harness.elements.get('history-week-select').children.length === 3 &&
      harness.elements.get('history-job-select').children.length === 3,
    'Weekly history controls must populate week and scan job selectors',
  );
  assertFrontend(
    harness.elements.get('weekly-history-status-badge').textContent.includes('W41/2026') &&
      harness.elements.get('weekly-history-status-badge').textContent.includes('LMS: 1 đã tích, 1 chạy thử (dry-run), 1 trễ'),
    'Weekly history status badge must display selected week and separate dry-run from production LMS ticks',
  );

  // 2. Selective rescan is blocked while viewing historical week
  await vm.runInContext('startSelectiveRescanFailed()', harness.context);
  assertFrontend(
    harness.alerts.at(-1).includes('Đang ở chế độ xem lịch sử theo tuần'),
    'Selective rescan must block when historical week view is active',
  );

  // 3. Export CSV produces UTF-8 with BOM, 7 management columns, formula sanitization, and no raw chat leak
  const csvOutput = await vm.runInContext('exportWeeklySummaryCsv()', harness.context);
  assertFrontend(
    csvOutput.startsWith('\uFEFFMã lớp,Giáo viên,Buổi học,Ngày học,Trạng thái Zalo,Trạng thái LMS,Thời gian kiểm chứng\r\n'),
    'Exported CSV must start with UTF-8 BOM and exact 7 management headers',
  );
  assertFrontend(
    csvOutput.includes("VT-JSB16,'=Trần Thị B,4,11/10/2026") &&
      !csvOutput.includes('TIN_NHAN_RIENG_TU_KHONG_XUAT'),
    'Exported CSV must sanitize formula prefixes and exclude raw chat fields',
  );

  // 4. Exporting an empty week (W40) while viewing W41 must NOT leak W41 rows into W40 CSV
  harness.elements.get('history-week-select').value = '2026-W40';
  const emptyWeekCsv = await vm.runInContext('exportWeeklySummaryCsv()', harness.context);
  assertFrontend(
    emptyWeekCsv === '' && harness.alerts.at(-1).includes('Chưa có dữ liệu báo cáo trong tuần đã chọn để xuất CSV'),
    'Exporting an empty week must alert and never fall back to another week rows',
  );
  harness.elements.get('history-week-select').value = '2026-W41';

  // 5. Out-of-order response guard: slow W40 response resolving after W41 must be ignored
  pendingW40Resolver = 'DEFER';
  const slowW40Promise = vm.runInContext('loadWeeklyHistorySummary({ week: 40, year: 2026 })', harness.context);
  await flushAsyncWork();
  const resolveSlowW40 = pendingW40Resolver;
  pendingW40Resolver = null;
  await vm.runInContext('loadWeeklyHistorySummary({ week: 41, year: 2026 })', harness.context);
  resolveSlowW40({
    ok: true,
    status: 200,
    json: async () => ({
      week: 40,
      year: 2026,
      period_key: '2026-W40',
      selected_job_id: null,
      available_weeks: sampleSummary.available_weeks,
      jobs: [],
      summary: { total: 0 },
      lms_summary: { has_lms_data: false },
      rows: [],
      items: [],
    }),
  });
  await slowW40Promise;
  await flushAsyncWork();
  assertFrontend(
    vm.runInContext('weeklyHistoryView && weeklyHistoryView.week', harness.context) === 41 &&
      harness.elements.get('weekly-history-status-badge').textContent.includes('W41/2026'),
    'Stale out-of-order weekly history response must not overwrite newer week selection',
  );

  // 6. Export in-flight week switch + offline fallback must use pre-captured W41 snapshot, never W42 rows
  harness.elements.get('history-week-select').value = '2026-W41';
  pendingW41ExportRejecter = 'DEFER_REJECT';
  const inflightExportPromise = vm.runInContext('exportWeeklySummaryCsv()', harness.context);
  await flushAsyncWork();
  const rejectW41Export = pendingW41ExportRejecter;
  pendingW41ExportRejecter = null;
  harness.elements.get('history-week-select').value = '2026-W42';
  await vm.runInContext('onHistoryWeekChange()', harness.context);
  rejectW41Export(new Error('network offline during W41 export'));
  const fallbackW41Csv = await inflightExportPromise;
  assertFrontend(
    fallbackW41Csv.includes('VT-JSB16') && !fallbackW41Csv.includes('VT-ONLY-W42'),
    'Offline fallback during in-flight export must use immutable pre-captured W41 rows and never leak W42 rows',
  );

  // 7. Viewing history then returning to live week must NOT stamp current plan snapshot_id
  // onto weekly-summary rows that lack a matching snapshot_id, preventing cross-plan selective merge.
  vm.runInContext(
    'renderZaloScanPlan({snapshot_id: 99, available: true, target_count: 2, required_week: {week: 42, year: 2026}, latest_week: {week: 43, year: 2026}, selected_weeks: [{week: 42, year: 2026}, {week: 43, year: 2026}]})',
    harness.context,
  );
  await vm.runInContext('loadWeeklyHistorySummary({ week: 41, year: 2026 })', harness.context);
  assertFrontend(
    vm.runInContext('latestZaloResultsSnapshotId', harness.context) === null,
    'Historical week without matching snapshot_id must keep null snapshot origin',
  );
  harness.elements.get('history-week-select').value = '';
  await vm.runInContext('onHistoryWeekChange()', harness.context);
  assertFrontend(
    vm.runInContext('latestZaloResultsSnapshotId', harness.context) === null,
    'Returning to live mode when weekly-summary lacks current snapshot_id must keep null snapshot origin',
  );
  assertFrontend(
    vm.runInContext('rememberSelectiveScanBaseline()', harness.context) === false,
    'Weekly-summary rows without matching snapshot_id must be blocked from selective rescan baseline',
  );

  // 8. Row-level null/mismatched snapshot_id must block rememberSelectiveScanBaseline even if top-level snapshot matches
  vm.runInContext(
    `renderZaloResults([
      {
        group: 'VT-LIVE-99',
        person: 'GV 99',
        status: '❌ Chưa tìm thấy báo cáo',
        snapshot_id: null,
        scan_target: { week: 42, year: 2026, class_code: 'VT-LIVE-99', lesson: '1', class_date: '15/10/2026', class_date_iso: '2026-10-15' }
      }
    ], true, { snapshotId: 99, periodKey: '2026-W42' })`,
    harness.context,
  );
  assertFrontend(
    vm.runInContext('rememberSelectiveScanBaseline()', harness.context) === false,
    'Row with explicit null snapshot_id must be blocked from selective rescan baseline',
  );

  // 9. Superseded live-mode request in onHistoryWeekChange must NOT wipe a newly loaded historical week
  pendingW42LiveResolver = 'DEFER';
  harness.elements.get('history-week-select').value = '';
  const supersededLivePromise = vm.runInContext('onHistoryWeekChange()', harness.context);
  await flushAsyncWork();
  const resolveSupersededLive = pendingW42LiveResolver;
  pendingW42LiveResolver = null;
  harness.elements.get('history-week-select').value = '2026-W41';
  await vm.runInContext('onHistoryWeekChange()', harness.context);
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2,
    'Switching to W41 while W42 live reload is in-flight must render W41 rows',
  );
  resolveSupersededLive({
    ok: true,
    status: 200,
    json: async () => ({
      week: 42,
      year: 2026,
      period_key: '2026-W42',
      selected_job_id: 800,
      available_weeks: sampleSummary.available_weeks,
      jobs: [],
      summary: { total: 0 },
      lms_summary: { has_lms_data: false },
      rows: [],
      items: [],
    }),
  });
  await supersededLivePromise;
  await flushAsyncWork();
  assertFrontend(
    vm.runInContext('latestZaloResults.length', harness.context) === 2 &&
      vm.runInContext('weeklyHistoryView && weeklyHistoryView.week', harness.context) === 41,
    'Superseded live-mode reload in onHistoryWeekChange must not clear newly loaded W41 rows',
  );
}

async function runFrontendBehaviorChecks() {
  await checkJobPollingAndTerminalDetails();
  await checkGoogleFormHistoryAndHttpError();
  await checkLazyIncrementalLogs();
  await checkRequiredWeekDashboardView();
  await checkZaloPreflightUiSafety();
  await checkBcnbZaloGapView();
  await checkTeacherReminderCopyHelpers();
  await checkSelectiveRescanWorkflow();
  await checkManualApproveWorkflow();
  await checkLmsPreviewDetailAndClassSelection();
  await checkGoogleFormFetchLmsStudentsAndIdempotency();
  await checkWeeklyHistoryAndCsvExport();
  console.log(`template-js-contract-ok scripts=${scriptSources.length} handlers=${handlerNames.size} behavior=12`);
}

runFrontendBehaviorChecks().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
