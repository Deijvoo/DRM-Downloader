const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const event = () => ({listeners: [], addListener(fn) { this.listeners.push(fn); }});
const ports = [], updates = [], reloads = [];
let activeTab = {id: 7, url: 'https://voyo.markiza.sk/relacie/show/epizoda/1'};
const chrome = {
  runtime: {id: 'test', getURL: path => 'chrome-extension://test/' + path, onMessage: event(), connectNative() {
    const port = {onMessage: event(), onDisconnect: event(), sent: [],
      postMessage(value) { this.sent.push(value); }, disconnect() { this.disconnected = true; }};
    ports.push(port); return port;
  }},
  tabs: {query: async () => [activeTab], reload: async id => reloads.push(id), onRemoved: event(), onUpdated: event()},
  webRequest: Object.fromEntries(['onBeforeRequest', 'onBeforeSendHeaders', 'onCompleted', 'onErrorOccurred'].map(key => [key, event()])),
  storage: {session: {set: value => updates.push(value.status)}},
  action: {setTitle() {}}, alarms: {create() {}, clear() {}, onAlarm: event()},
};
let fetches = 0;
const context = vm.createContext({chrome, URL, TextEncoder, TextDecoder, AbortSignal,
  btoa: value => Buffer.from(value, 'binary').toString('base64'),
  fetch: async (url, options) => {
    fetches++;
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    return new Response('<MPD/>');
  }});
vm.runInContext(fs.readFileSync('chrome-extension/background.js', 'utf8'), context);
const fire = (event, ...args) => event.listeners.forEach(fn => fn(...args));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function start() {
  await new Promise(resolve => fire(chrome.runtime.onMessage, {type: 'start'},
    {id: 'test', url: 'chrome-extension://test/popup.html'}, resolve));
}
(async () => {
  activeTab.url = 'https://voyo.markiza.sk.evil.test/epizoda/1';
  await start(); assert.equal(ports.length, 0);
  activeTab.url = 'https://voyo.markiza.sk/relacie/show/epizoda/1';
  await start();
  const port = ports[0];
  assert.equal(port.sent[0].type, 'start');
  fire(port.onMessage, {type: 'listening'});
  assert.deepEqual(reloads, [7]);
  const license = {tabId: 7, requestId: 'l', method: 'POST',
    url: 'https://drm-widevine-licensing.axprod.net/AcquireLicense',
    requestBody: {raw: [{bytes: Uint8Array.from([0, 255, 128, 1]).buffer}]}};
  fire(chrome.webRequest.onBeforeRequest, {...license, tabId: 8});
  fire(chrome.webRequest.onCompleted, {...license, tabId: 8, statusCode: 200});
  assert.equal(port.sent.length, 1);
  fire(chrome.webRequest.onBeforeRequest, license);
  fire(chrome.webRequest.onBeforeSendHeaders, {...license, requestHeaders: [
    {name: 'X-AxDRM-Message', value: 'test-token'}, {name: 'Cookie', value: 'must-not-export'},
  ]});
  fire(chrome.webRequest.onCompleted, {...license, statusCode: 200});
  assert.equal(port.sent[1].body, 'AP+AAQ==');
  assert.equal(port.sent[1].headers.cookie, undefined);
  const manifest = {tabId: 7, requestId: 'm', method: 'GET', url: 'https://test.cdn.cra.cz/title.mpd'};
  fire(chrome.webRequest.onBeforeRequest, manifest);
  fire(chrome.webRequest.onCompleted, {...manifest, statusCode: 200});
  await tick(); await tick();
  assert.equal(fetches, 1);
  assert.equal(port.sent[2].kind, 'manifest');
  assert.equal(port.sent[2].text, '<MPD/>');
  fire(port.onMessage, {type: 'ready'});
  fire(chrome.webRequest.onBeforeRequest, license);
  fire(chrome.webRequest.onCompleted, {...license, statusCode: 200});
  assert.equal(port.sent.length, 3);
  assert.ok(!port.disconnected, 'Keep native window alive after capture');
  fire(port.onMessage, {type: 'closed'});
  assert.ok(port.disconnected);
  await start();
  fire(chrome.alarms.onAlarm, {name: 'capture-timeout'});
  assert.ok(ports[1].disconnected);
  assert.match(updates.at(-1), /Timed out/);
  await start();
  fire(chrome.tabs.onUpdated, 7, {url: 'https://example.com/'});
  assert.ok(ports[2].disconnected);
  console.log('Extension checks passed: tab scope, binary request, header filtering, manifest, lifecycle.');
})().catch(error => { console.error(error); process.exitCode = 1; });
