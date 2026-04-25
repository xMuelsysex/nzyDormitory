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
  const parts = [status.authenticated ? '已登录' : '未登录'];
  if (status.roomSelection) parts.push(`${status.roomSelection.building} ${status.roomSelection.room}`);
  statusBadge.textContent = parts.join(' · ');
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

document.querySelector('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api.post('/api/login', formData(event.currentTarget));
    setMessage('#loginMessage', '登录成功，请继续选择宿舍。');
    await refreshStatus();
  } catch (error) {
    setMessage('#loginMessage', error.message, true);
  }
});

document.querySelector('#roomForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api.post('/api/room-selection', formData(event.currentTarget));
    setMessage('#roomMessage', '宿舍已保存。');
    await refreshStatus();
  } catch (error) {
    setMessage('#roomMessage', error.message, true);
  }
});

document.querySelector('#scheduleForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api.post('/api/schedule-config', formData(event.currentTarget));
    setMessage('#scheduleMessage', '定时设置已保存。');
  } catch (error) {
    setMessage('#scheduleMessage', error.message, true);
  }
});

document.querySelector('#alertForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api.post('/api/alert-config', formData(event.currentTarget));
    setMessage('#alertMessage', '提醒设置已保存。');
    await refreshStatus();
  } catch (error) {
    setMessage('#alertMessage', error.message, true);
  }
});

document.querySelector('#runOnceButton').addEventListener('click', async () => {
  try {
    await api.post('/api/collection/run-once', {});
    setMessage('#scheduleMessage', '已完成一次采集。');
    await refreshReadings();
  } catch (error) {
    setMessage('#scheduleMessage', error.message, true);
  }
});

document.querySelector('#refreshButton').addEventListener('click', refreshReadings);

refreshStatus().catch(() => { statusBadge.textContent = '状态加载失败'; });
refreshReadings().catch(() => { chartSummary.textContent = '趋势数据加载失败。'; });
