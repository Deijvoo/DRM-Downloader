const label = document.getElementById('status');
chrome.storage.session.get('status').then(value => { if (value.status) label.textContent = value.status; });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.status) label.textContent = changes.status.newValue;
});
document.getElementById('start').onclick = () => chrome.runtime.sendMessage({type: 'start'});
document.getElementById('stop').onclick = () => chrome.runtime.sendMessage({type: 'stop'});
