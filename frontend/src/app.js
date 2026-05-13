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
    throw new Error(data.error?.message || '请求失败');
  }
  return data;
}

const statusBadge = document.querySelector('#statusBadge');
const chartSummary = document.querySelector('#chartSummary');
const readingsBody = document.querySelector('#readingsBody');
const canvas = document.querySelector('#trendCanvas');
const ctx = canvas.getContext('2d');
const roomForm = document.querySelector('#roomForm');
const scheduleForm = document.querySelector('#scheduleForm');
const alertForm = document.querySelector('#alertForm');
const runOnceButton = document.querySelector('#runOnceButton');
const refreshButton = document.querySelector('#refreshButton');

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
}

function authenticationStatusLabel(status) {
  if (status.authenticationStatus === 'session_expired') return '登录已过期';
  return status.authenticated ? '已登录' : '未登录';
}

async function refreshReadings() {
  const { readings } = await api.get('/api/readings');
  renderTable(readings);
  renderChart(readings);
}

function renderTable(readings) {
  if (!readings.length) {
    readingsBody.innerHTML = '<tr><td colspan="4">暂无数据，定时采集成功后会显示记录。</td></tr>';
    return;
  }
  readingsBody.innerHTML = readings.map((r) => `
    <tr>
      <td>${escapeHtml(r.collectedAt)}</td>
      <td>${escapeHtml(r.building)}</td>
      <td>${escapeHtml(r.room)}</td>
      <td>${Number(r.numericValue).toFixed(2)} ${escapeHtml(r.unit || '')}</td>
    </tr>
  `).join('');
}

function renderChart(readings) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#64748b';
  ctx.font = '18px sans-serif';
  if (!readings.length) {
    ctx.fillText('暂无趋势数据', 32, 60);
    chartSummary.textContent = '定时采集成功后会显示趋势图。';
    return;
  }
  const values = readings.map((r) => Number(r.numericValue));
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
  readings.forEach((reading, index) => {
    const x = pad + (readings.length === 1 ? width : (width / (readings.length - 1)) * index);
    const y = pad + height - ((Number(reading.numericValue) - min) / range) * height;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#1d4ed8';
  readings.forEach((reading, index) => {
    const x = pad + (readings.length === 1 ? width : (width / (readings.length - 1)) * index);
    const y = pad + height - ((Number(reading.numericValue) - min) / range) * height;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
  const latest = readings[readings.length - 1];
  chartSummary.textContent = `最新数值：${Number(latest.numericValue).toFixed(2)} ${latest.unit || ''}，采集时间：${latest.collectedAt}`;
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
    await refreshReadings();
  } catch (error) {
    setMessage('#scheduleMessage', error.message, true);
  }
});

refreshButton.addEventListener('click', refreshReadings);

refreshStatus().catch(() => { statusBadge.textContent = '状态加载失败'; });
refreshReadings().catch(() => { chartSummary.textContent = '趋势数据加载失败。'; });
