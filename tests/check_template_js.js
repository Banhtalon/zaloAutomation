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
  'zalo-review-queue', 'zalo-review-queue-items',
  'paste-area', 'ocr-results-table', 'mapping-table', 'form-history-table',
  'groups-table-body', 'ignored-groups-table-body', 'full-log-terminal',
  'bcnb-zalo-gaps-summary', 'bcnb-zalo-gaps-stale', 'bcnb-zalo-gaps-table-body',
  'job-timeline', 'lmsModal', 'lmsFileInput', 'image-modal', 'modal-image',
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
  let nextTimerId = 1;
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
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    crypto: { randomUUID: () => 'frontend-test-uuid' },
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
  return { context, elements, timers, alerts };
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
  assertFrontend(table.children.length === 2, 'Dashboard must render only required-week rows');
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

async function runFrontendBehaviorChecks() {
  await checkJobPollingAndTerminalDetails();
  await checkGoogleFormHistoryAndHttpError();
  await checkLazyIncrementalLogs();
  await checkRequiredWeekDashboardView();
  await checkZaloPreflightUiSafety();
  await checkBcnbZaloGapView();
  console.log(`template-js-contract-ok scripts=${scriptSources.length} handlers=${handlerNames.size} behavior=6`);
}

runFrontendBehaviorChecks().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
