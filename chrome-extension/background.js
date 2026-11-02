/* Capture only the Voyo tab explicitly selected by the user. No cookies are exported. */
const LICENSE = 'https://drm-widevine-licensing.axprod.net/AcquireLicense';
const FILTER = {urls: ['https://*.markiza.sk/*', 'https://*.cdn.cra.cz/*', LICENSE]};
const HEADERS = new Set(['x-axdrm-message', 'origin', 'referer', 'user-agent', 'content-type']);
let session = null;

function isEpisode(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'voyo.markiza.sk' &&
      !url.username && !url.password && (!url.port || url.port === '443') && url.pathname.includes('/epizoda/');
  } catch { return false; }
}

function isManifest(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port &&
      (url.hostname.endsWith('.cdn.cra.cz') || url.hostname.endsWith('.markiza.sk')) &&
      url.pathname.toLowerCase().endsWith('.mpd');
  } catch { return false; }
}

function status(text) {
  chrome.storage.session.set({status: text});
  chrome.action.setTitle({title: text});
}

function finish(text) {
  const current = session;
  session = null;
  chrome.alarms.clear('capture-timeout');
  if (current) {
    current.requests.clear();
    current.port.disconnect();
  }
  status(text);
}

function post(current, message) {
  if (session !== current) return;
  if (new TextEncoder().encode(JSON.stringify(message)).length > 6000000) {
    finish('This episode is too large to inspect. Try the desktop app.');
    return;
  }
  try { current.port.postMessage(message); }
  catch { finish('Connection lost. Run Setup-Chrome.cmd, then try again.'); }
}

async function start(tab) {
  if (session) return;
  if (!tab || !isEpisode(tab.url)) {
    status('Open a Voyo Markíza episode in this tab first.');
    return;
  }
  const port = chrome.runtime.connectNative('com.deijvoo.voyo');
  const current = {tabId: tab.id, episode: tab.url, port, requests: new Map(), capturing: true};
  session = current;
  status('Connecting to the downloader…');
  chrome.alarms.create('capture-timeout', {delayInMinutes: 10});
  port.onMessage.addListener(message => {
    if (session !== current) return;
    if (message.type === 'listening') {
      status('Play the episode in this tab. The download window will open.');
      chrome.tabs.reload(tab.id).catch(() => finish('Could not reload the Voyo tab. Try again.'));
    } else if (message.type === 'ready') {
      current.capturing = false;
      current.requests.clear();
      chrome.alarms.clear('capture-timeout');
      status('Ready. Click Download in the Voyo Downloader window. Keep Chrome open.');
    } else if (message.type === 'closed') {
      finish('Download window closed. You can choose another episode.');
    } else if (message.type === 'error') {
      finish('Could not read this episode. Reload Voyo and try again.');
    }
  });
  port.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError;
    if (session === current) finish(error ?
      'Connection lost or not installed. Run Setup-Chrome.cmd and keep Chrome open during downloads.' :
      'Download window closed. You can choose another episode.');
  });
  post(current, {type: 'start', url: tab.url});
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return;
  if (message.type === 'start') {
    chrome.tabs.query({active: true, currentWindow: true})
      .then(tabs => start(tabs[0])).then(() => reply({ok: true}))
      .catch(() => { finish('Could not connect. Run Setup-Chrome.cmd and try again.'); reply({ok: false}); });
    return true;
  }
  if (message.type === 'stop' && session?.capturing) finish('Cancelled.');
});

chrome.webRequest.onBeforeRequest.addListener(details => {
  const current = session;
  if (!current?.capturing || details.tabId !== current.tabId) return;
  if (details.url !== LICENSE && !isManifest(details.url)) return;
  if (current.requests.size >= 100) { finish('Too many playback requests. Try again.'); return; }
  const record = {url: details.url, headers: {}};
  if (details.url === LICENSE) {
    if (details.method !== 'POST') return;
    const parts = details.requestBody?.raw;
    if (!parts?.length || parts.some(part => !part.bytes)) return;
    if (parts.reduce((size, part) => size + part.bytes.byteLength, 0) > 1000000) return;
    let binary = '';
    for (const part of parts) {
      for (const byte of new Uint8Array(part.bytes)) binary += String.fromCharCode(byte);
    }
    record.body = btoa(binary);
  }
  current.requests.set(details.requestId, record);
}, FILTER, ['requestBody']);

chrome.webRequest.onBeforeSendHeaders.addListener(details => {
  const current = session;
  if (!current?.capturing || details.tabId !== current.tabId) return;
  const record = current.requests.get(details.requestId);
  if (!record || record.url !== LICENSE) return;
  for (const header of details.requestHeaders || []) {
    const name = header.name.toLowerCase();
    if (HEADERS.has(name) && typeof header.value === 'string') record.headers[name] = header.value;
  }
}, FILTER, ['requestHeaders', 'extraHeaders']);

async function completed(details) {
  const current = session;
  if (!current?.capturing || details.tabId !== current.tabId) return;
  const record = current.requests.get(details.requestId);
  current.requests.delete(details.requestId);
  if (!record || details.statusCode !== 200 || record.url !== details.url) return;
  if (record.url === LICENSE) {
    if (record.headers['x-axdrm-message']) post(current, {type: 'capture', kind: 'license', ...record});
    return;
  }
  // Signed manifest URL only; do not forward browser cookies or follow redirects.
  try {
    const response = await fetch(record.url, {credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(15000)});
    if (!response.ok || !response.body) throw new Error('Manifest unavailable');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = '', length = 0;
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        length += value.length;
        if (length > 5000000) throw new Error('Manifest too large');
        text += decoder.decode(value, {stream: true});
      }
      text += decoder.decode();
    } finally { await reader.cancel(); }
    if (current.capturing) post(current, {type: 'capture', kind: 'manifest', url: record.url, text});
  } catch {
    if (session === current && current.capturing) finish('Could not read the episode manifest. Try the desktop app.');
  }
}
chrome.webRequest.onCompleted.addListener(details => { void completed(details); }, FILTER);
chrome.webRequest.onErrorOccurred.addListener(details => {
  if (session?.tabId === details.tabId) session.requests.delete(details.requestId);
}, FILTER);
chrome.tabs.onRemoved.addListener(tabId => {
  if (session?.tabId === tabId && session.capturing) finish('Voyo tab closed.');
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (session?.tabId === tabId && session.capturing && change.url && change.url !== session.episode)
    finish('Episode changed. Click Use this Voyo tab again.');
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'capture-timeout' && session?.capturing) finish('Timed out. Start again and play the episode.');
});
