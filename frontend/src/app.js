const api = {
  async get(path) {
    const response = await fetch(path);
    return parseResponse(response);
  },
  async post(path, payload) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return parseResponse(response);
  },
};

async function parseResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || '请求失败');
    error.code = data.error?.code || '';
    error.status = response.status;
    throw error;
  }
  return data;
}

const statusBadge = document.querySelector('#statusBadge');
const chartSummary = document.querySelector('#chartSummary');
const collectionMessage = document.querySelector('#collectionMessage');
const readingsMessage = document.querySelector('#readingsMessage');
const readingsBody = document.querySelector('#readingsBody');
const canvas = document.querySelector('#trendCanvas');
const ctx = canvas.getContext('2d');
const roomForm = document.querySelector('#roomForm');
const scheduleForm = document.querySelector('#scheduleForm');
const alertForm = document.querySelector('#alertForm');
const runOnceButton = document.querySelector('#runOnceButton');
const refreshButton = document.querySelector('#refreshButton');
const previousPageButton = document.querySelector('#previousPageButton');
const nextPageButton = document.querySelector('#nextPageButton');
const pageInfo = document.querySelector('#pageInfo');

const readingsState = {
  page: 1,
  pageSize: 20,
  total: 0,
  items: [],
  isLoading: false,
};

function formData(form) {
  const data = new FormData(form);
  const payload = Object.fromEntries(data.entries());
  for (const input of form.querySelectorAll('input[type="checkbox"]')) {
    payload[input.name] = input.checked;
  }
  return payload;
}

function setMessage(id, message, isError = false) {
  const el = document.querySelector(id);
  el.textContent = message;
  el.classList.toggle('error', isError);
}

async function refreshStatus() {
  const status = await api.get('/api/status');
  const parts = [authenticationStatusLabel(status)];
  if (status.roomSelection) parts.push(`${status.roomSelection.building} ${status.roomSelection.room}`);
  statusBadge.textContent = parts.join(' · ');
  applySavedConfig(status);
  updateControlStates(status);
  renderCollectionMessage(status);
}

function authenticationStatusLabel(status) {
  if (status.authenticationStatus === 'session_expired') return '登录已过期';
  return status.authenticated ? '已登录' : '未登录';
}

async function refreshReadings() {
  setReadingsLoading(true);
  try {
    const response = await api.get(readingsPath());
    const pagination = normalizeReadingsResponse(response);
    readingsState.page = pagination.page;
    readingsState.pageSize = pagination.pageSize;
    readingsState.total = pagination.total;
    readingsState.items = pagination.items;
    renderReadingsView();
  } catch (error) {
    renderReadingsError(error);
  } finally {
    setReadingsLoading(false);
  }
}

function readingsPath() {
  const params = new URLSearchParams({
    page: String(readingsState.page),
    pageSize: String(readingsState.pageSize),
  });
  return `/api/readings?${params.toString()}`;
}

function normalizeReadingsResponse(response) {
  if (Array.isArray(response.items)) {
    return {
      items: response.items,
      page: positiveInteger(response.page, readingsState.page),
      pageSize: positiveInteger(response.pageSize, readingsState.pageSize),
      total: nonNegativeInteger(response.total, response.items.length),
    };
  }
  const legacyReadings = Array.isArray(response.readings) ? response.readings : [];
  return {
    items: legacyReadings,
    page: 1,
    pageSize: legacyReadings.length || readingsState.pageSize,
    total: legacyReadings.length,
  };
}

function renderReadingsView() {
  const totalPages = totalReadingsPages();
  const hasReadings = readingsState.items.length > 0;
  renderTable(readingsState.items);
  renderChart(readingsState.items);
  readingsMessage.classList.remove('error');
  readingsMessage.textContent = hasReadings
    ? `共 ${readingsState.total} 条记录，当前显示第 ${readingsState.page} 页。`
    : '暂无历史数据，定时采集成功后会显示记录。';
  pageInfo.textContent = readingsState.total > 0
    ? `第 ${readingsState.page} / ${totalPages} 页`
    : '第 1 / 1 页';
  updatePaginationControls();
}

function renderTable(readings) {
  if (!readings.length) {
    readingsBody.innerHTML = '<tr><td colspan="4">暂无数据，定时采集成功后会显示记录。</td></tr>';
    return;
  }
  readingsBody.innerHTML = readings.map((reading) => `
    <tr>
      <td>${escapeHtml(reading.collectedAt)}</td>
      <td>${escapeHtml(reading.building)}</td>
      <td>${escapeHtml(reading.room)}</td>
      <td>${escapeHtml(formatReadingValue(reading))}</td>
    </tr>
  `).join('');
}

function renderReadingsError(error) {
  const isAuthError = error.status === 401 || error.code === 'SESSION_EXPIRED' || error.code === 'AUTHENTICATION_ERROR';
  const message = isAuthError
    ? '登录状态已失效，请重新登录校园门户后再查看历史数据。'
    : `历史数据加载失败：${error.message || '请稍后重试。'}`;
  readingsState.page = 1;
  readingsState.items = [];
  readingsState.total = 0;
  readingsBody.innerHTML = `<tr><td colspan="4">${escapeHtml(message)}</td></tr>`;
  readingsMessage.textContent = message;
  readingsMessage.classList.add('error');
  pageInfo.textContent = '第 1 / 1 页';
  updatePaginationControls();
  drawChartMessage(isAuthError ? '登录失效，无法加载趋势数据' : '趋势数据加载失败');
  chartSummary.textContent = isAuthError ? '请重新登录后刷新数据。' : '请稍后重试或检查采集状态。';
}

function renderCollectionMessage(status) {
  const lastRun = status.lastCollectionRun;
  if (status.authenticationStatus === 'session_expired') {
    collectionMessage.textContent = '校园门户登录已过期，请重新登录后再采集。';
    collectionMessage.classList.add('error');
    return;
  }
  if (lastRun?.status === 'failed') {
    collectionMessage.textContent = `最近采集失败：${collectionFailureMessage(lastRun)}。`;
    collectionMessage.classList.add('error');
    return;
  }
  if (lastRun?.status === 'duplicate') {
    collectionMessage.textContent = '最近一次采集没有新增记录，可能与上一轮采集处于同一时间窗口。';
    collectionMessage.classList.remove('error');
    return;
  }
  collectionMessage.textContent = '';
  collectionMessage.classList.remove('error');
}

function collectionFailureMessage(run) {
  if (run.errorCode === 'SESSION_EXPIRED') return '登录已失效，请重新登录校园门户';
  if (run.message) return run.message;
  return '请检查登录状态或稍后重试';
}

function setReadingsLoading(isLoading) {
  readingsState.isLoading = isLoading;
  refreshButton.disabled = isLoading;
  updatePaginationControls();
  if (!isLoading) return;
  readingsMessage.textContent = '历史数据加载中。';
  readingsMessage.classList.remove('error');
  readingsBody.innerHTML = '<tr><td colspan="4">历史数据加载中。</td></tr>';
  pageInfo.textContent = readingsState.total > 0 ? `第 ${readingsState.page} / ${totalReadingsPages()} 页` : '第 1 / 1 页';
  drawChartMessage('趋势数据加载中');
  chartSummary.textContent = '历史数据加载中。';
}

function renderChart(readings) {
  const chartReadings = readings
    .filter((reading) => Number.isFinite(Number(reading.numericValue)))
    .sort((left, right) => String(left.collectedAt).localeCompare(String(right.collectedAt)));
  if (!readings.length) {
    drawChartMessage('暂无趋势数据');
    chartSummary.textContent = '定时采集成功后会显示趋势图。';
    return;
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const values = chartReadings.map((reading) => Number(reading.numericValue));
  if (!values.length) {
    drawChartMessage('暂无趋势数据');
    chartSummary.textContent = '当前页没有可绘制的有效数值。';
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = 34;
  const width = canvas.width - pad * 2;
  const height = canvas.height - pad * 2;
  const range = max - min || 1;
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = pad + (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(canvas.width - pad, y);
    ctx.stroke();
  }
  ctx.strokeStyle = '#2563eb';
  ctx.lineWidth = 3;
  ctx.beginPath();
  chartReadings.forEach((reading, index) => {
    const x = pad + (chartReadings.length === 1 ? width : (width / (chartReadings.length - 1)) * index);
    const y = pad + height - ((Number(reading.numericValue) - min) / range) * height;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#1d4ed8';
  chartReadings.forEach((reading, index) => {
    const x = pad + (chartReadings.length === 1 ? width : (width / (chartReadings.length - 1)) * index);
    const y = pad + height - ((Number(reading.numericValue) - min) / range) * height;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
  const latest = chartReadings[chartReadings.length - 1];
  chartSummary.textContent = `当前页最新数值：${formatReadingValue(latest)}，采集时间：${latest.collectedAt}`;
}

function updatePaginationControls() {
  const totalPages = totalReadingsPages();
  previousPageButton.disabled = readingsState.isLoading || readingsState.page <= 1;
  nextPageButton.disabled = readingsState.isLoading || readingsState.page >= totalPages || readingsState.total === 0;
}

function totalReadingsPages() {
  return Math.max(1, Math.ceil(readingsState.total / readingsState.pageSize));
}

function positiveInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

function drawChartMessage(message) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#64748b';
  ctx.font = '18px sans-serif';
  ctx.fillText(message, 32, 60);
}

function formatReadingValue(reading) {
  const value = Number(reading.numericValue);
  if (!Number.isFinite(value)) return '暂无数据';
  return `${value.toFixed(2)} ${reading.unit || ''}`.trim();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function applySavedConfig(status) {
  if (status.roomSelection) {
    roomForm.elements.building.value = status.roomSelection.building;
    roomForm.elements.room.value = status.roomSelection.room;
  }
  if (status.scheduleConfig) {
    scheduleForm.elements.intervalSeconds.value = status.scheduleConfig.intervalSeconds;
    scheduleForm.elements.startTime.value = status.scheduleConfig.startTime;
    scheduleForm.elements.endTime.value = status.scheduleConfig.endTime;
    scheduleForm.elements.enabled.checked = Boolean(status.scheduleConfig.enabled);
  }
  if (status.alertConfig) {
    alertForm.elements.threshold.value = status.alertConfig.threshold;
    alertForm.elements.recipientEmail.value = status.alertConfig.recipientEmail;
    alertForm.elements.cooldownSeconds.value = status.alertConfig.cooldownSeconds;
    alertForm.elements.enabled.checked = Boolean(status.alertConfig.enabled);
  }
}

function updateControlStates(status) {
  const loginRequired = !status.authenticated;
  const roomRequired = !status.roomSelection;
  setFormDisabled(roomForm, loginRequired);
  setFormDisabled(scheduleForm, loginRequired || roomRequired);
  setFormDisabled(alertForm, loginRequired);
  runOnceButton.disabled = loginRequired || roomRequired;
  if (loginRequired) {
    const loginMessage = status.authenticationStatus === 'session_expired'
      ? '校园门户登录已过期，请重新登录。'
      : '请先完成校园门户登录。';
    setMessage('#roomMessage', loginMessage, true);
    setMessage('#scheduleMessage', status.authenticationStatus === 'session_expired' ? loginMessage : '登录并选择宿舍后才能配置定时采集。', true);
    setMessage('#alertMessage', status.authenticationStatus === 'session_expired' ? loginMessage : '登录后才能配置邮件提醒。', true);
    return;
  }
  if (roomRequired) {
    setMessage('#roomMessage', '');
    setMessage('#scheduleMessage', '选择宿舍后才能配置定时采集。', true);
  }
}

function setFormDisabled(form, disabled) {
  for (const control of form.querySelectorAll('input, button')) {
    control.disabled = disabled;
  }
}

function validateSchedulePayload(payload) {
  const intervalSeconds = Number(payload.intervalSeconds);
  if (!Number.isInteger(intervalSeconds) || intervalSeconds <= 0) {
    throw new Error('间隔秒数必须是正整数。');
  }
  if (!payload.startTime || !payload.endTime || payload.startTime >= payload.endTime) {
    throw new Error('开始时间必须早于结束时间。');
  }
}

function validateAlertPayload(payload) {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(payload.recipientEmail || '')) {
    throw new Error('提醒邮箱格式不正确。');
  }
  if (!Number.isFinite(Number(payload.threshold))) {
    throw new Error('阈值必须是数字。');
  }
}

const loginMessage = document.querySelector('#loginMessage');
if (loginMessage && new URLSearchParams(window.location.search).get('login') === 'success') {
  setMessage('#loginMessage', '校园门户登录成功，请继续选择宿舍。');
}

roomForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api.post('/api/room-selection', formData(event.currentTarget));
    setMessage('#roomMessage', '宿舍已保存。');
    await refreshStatus();
    setMessage('#scheduleMessage', '');
  } catch (error) {
    setMessage('#roomMessage', error.message, true);
  }
});

scheduleForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = formData(event.currentTarget);
    validateSchedulePayload(payload);
    await api.post('/api/schedule-config', payload);
    setMessage('#scheduleMessage', '定时设置已保存。');
    await refreshStatus();
  } catch (error) {
    setMessage('#scheduleMessage', error.message, true);
  }
});

alertForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const payload = formData(event.currentTarget);
    validateAlertPayload(payload);
    await api.post('/api/alert-config', payload);
    setMessage('#alertMessage', '提醒设置已保存。');
    await refreshStatus();
  } catch (error) {
    setMessage('#alertMessage', error.message, true);
  }
});

runOnceButton.addEventListener('click', async () => {
  try {
    await api.post('/api/collection/run-once', {});
    setMessage('#scheduleMessage', '已完成一次采集。');
    await refreshStatus();
    readingsState.page = 1;
    await refreshReadings();
  } catch (error) {
    setMessage('#scheduleMessage', error.message, true);
    await refreshStatus().catch(() => {});
  }
});

refreshButton.addEventListener('click', refreshReadings);
previousPageButton.addEventListener('click', () => {
  if (readingsState.page <= 1 || readingsState.isLoading) return;
  readingsState.page -= 1;
  refreshReadings();
});
nextPageButton.addEventListener('click', () => {
  if (readingsState.page >= totalReadingsPages() || readingsState.isLoading) return;
  readingsState.page += 1;
  refreshReadings();
});

refreshStatus().catch(() => { statusBadge.textContent = '状态加载失败'; });
refreshReadings();
