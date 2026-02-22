const ADMIN_PWD = 'grok2api';

function $(id) { return document.getElementById(id); }

function showStatus(type, text) {
  const el = $('status');
  el.className = 'status ' + type;
  el.textContent = text;
  el.style.display = 'block';
}

// Restore saved server URL
chrome.storage.local.get(['serverUrl'], (data) => {
  if (data.serverUrl) $('serverUrl').value = data.serverUrl;
  loadTokenCount();
});

// Save server URL on change
$('serverUrl').addEventListener('change', () => {
  chrome.storage.local.set({ serverUrl: $('serverUrl').value.replace(/\/+$/, '') });
});

async function doImport() {
  const btn = $('importBtn');
  btn.disabled = true;
  btn.textContent = '提取中...';
  showStatus('loading', '正在从 grok.com 提取 SSO Cookie...');

  try {
    // 1. Read SSO cookie from grok.com
    const cookie = await chrome.cookies.get({ url: 'https://grok.com', name: 'sso' });

    if (!cookie || !cookie.value) {
      showStatus('error', '未找到 SSO Cookie!\n\n请先在浏览器中打开 grok.com 并登录，然后再点击此按钮。');
      btn.disabled = false;
      btn.textContent = '一键导入 Token';
      return;
    }

    const token = cookie.value;
    const pool = document.querySelector('input[name="pool"]:checked').value;
    const server = $('serverUrl').value.replace(/\/+$/, '');

    showStatus('loading', '正在导入到 ' + pool + '...');

    // 2. POST to grok2api
    const resp = await fetch(`${server}/v1/admin/tokens`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${ADMIN_PWD}`
      },
      body: JSON.stringify({
        [pool]: [{
          token: token,
          status: 'active',
          quota: pool === 'ssoSuper' ? 140 : 80,
          tags: [],
          note: `ext-${new Date().toISOString().slice(0, 10)}`
        }]
      })
    });

    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(`服务器返回 ${resp.status}: ${err}`);
    }

    const short = token.slice(0, 12) + '...' + token.slice(-6);
    showStatus('success', `导入成功!\nToken: ${short}\n池: ${pool}`);
    loadTokenCount();

  } catch (e) {
    if (e.message.includes('Failed to fetch') || e.message.includes('NetworkError')) {
      showStatus('error', '无法连接服务器!\n请检查服务器地址是否正确，以及服务是否在运行。');
    } else {
      showStatus('error', '导入失败: ' + e.message);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '一键导入 Token';
  }
}

async function loadTokenCount() {
  const el = $('tokenCount');
  try {
    const server = $('serverUrl').value.replace(/\/+$/, '');
    const resp = await fetch(`${server}/v1/admin/tokens`, {
      headers: { 'Authorization': `Bearer ${ADMIN_PWD}` }
    });
    const data = await resp.json();
    let total = 0;
    for (const [, tokens] of Object.entries(data)) {
      if (Array.isArray(tokens)) total += tokens.length;
    }
    el.textContent = `服务器当前共 ${total} 个 Token`;
    el.style.display = 'block';
  } catch {
    el.textContent = '';
  }
}

function openManager() {
  const server = $('serverUrl').value.replace(/\/+$/, '');
  chrome.tabs.create({ url: `${server}/static/public/pages/token-manager.html` });
}
