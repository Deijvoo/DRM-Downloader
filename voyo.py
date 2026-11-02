"""Windows front end for one Voyo episode at a time."""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from pywidevine.device import Device
from pywidevine.pssh import PSSH
from pywidevine.license_protocol_pb2 import SignedMessage, LicenseRequest, WidevinePsshData
import requests

import drmdl

BASE = Path(__file__).resolve().parent
STATE = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'VoyoDownloader'
LICENSE = 'https://drm-widevine-licensing.axprod.net/AcquireLicense'
TOOLS = ('yt-dlp', 'aria2c', 'mp4decrypt', 'ffmpeg', 'ffprobe')


class Cancelled(Exception):
    pass


def episode_url(value):
    value = value.strip()
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'voyo.markiza.sk'
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or '/epizoda/' not in parsed.path):
        raise ValueError('Paste an HTTPS episode link from voyo.markiza.sk.')
    return value


def next_output(link, directory):
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^\w.-]+', '-', urlsplit(link).path.rstrip('/').split('/')[-1])[:100]
    stem = 'Voyo-' + (slug.strip('.-') or 'episode')
    output = directory / (stem + '.mp4')
    number = 2
    while output.exists():
        output = directory / f'{stem} ({number}).mp4'
        number += 1
    return output


def manifest_details(text):
    if len(text) > 5_000_000 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Unsupported manifest.')
    root = ET.fromstring(text)
    if root.tag.rsplit('}', 1)[-1] != 'MPD' or root.get('type') == 'dynamic':
        raise ValueError('Only on-demand DASH episodes are supported.')
    values = set()
    for protection in root.iter():
        if protection.get('schemeIdUri', '').lower() == 'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed':
            for child in protection:
                if child.tag.rsplit('}', 1)[-1] == 'pssh' and child.text:
                    values.add(''.join(child.text.split()))
    if len(values) != 1:
        raise ValueError('This title needs the manual workflow: expected one Widevine description.')
    value = values.pop()
    kids = frozenset(key.bytes for key in PSSH(value).key_ids)
    if not kids:
        raise ValueError('The manifest does not identify its content keys.')
    return value, kids


def challenge_kids(body):
    message = SignedMessage()
    message.ParseFromString(body)
    if message.type != SignedMessage.LICENSE_REQUEST:
        return frozenset()
    request = LicenseRequest()
    request.ParseFromString(message.msg)
    keys = set()
    for data in request.content_id.widevine_pssh_data.pssh_data:
        pssh = WidevinePsshData()
        pssh.ParseFromString(data)
        keys.update(pssh.key_ids)
    return frozenset(keys)


class Capture:
    """Pair a successful browser licence request with the same manifest key IDs."""
    def __init__(self):
        self.manifests = {}
        self.licences = {}
        self.error = None

    def response(self, response):
        if response.status != 200:
            return
        request = response.request
        parsed = urlsplit(response.url)
        if response.url == LICENSE and request.method == 'POST':
            try:
                keys = challenge_kids(request.post_data_buffer or b'')
                headers = request.all_headers()
                if keys and headers.get('x-axdrm-message'):
                    self.licences[keys] = {key: value for key, value in headers.items()
                                          if key in ('x-axdrm-message', 'origin', 'referer',
                                                     'user-agent', 'content-type')}
            except Exception:
                self.error = 'Could not read the browser licence request. Use the manual workflow.'
        elif (parsed.scheme == 'https' and parsed.path.lower().endswith('.mpd')
              and parsed.hostname and (parsed.hostname.endswith('.cdn.cra.cz')
                                      or parsed.hostname.endswith('.markiza.sk'))):
            try:
                pssh, keys = manifest_details(response.text())
                self.manifests[keys] = (response.url, pssh)
            except Exception:
                self.error = 'Could not read this episode manifest. Use the manual workflow.'

    def result(self):
        for keys, manifest in reversed(list(self.manifests.items())):
            if keys in self.licences:
                return (*manifest, self.licences[keys])
        return None


def capture_episode(url, notify, cancel, profile=STATE / 'chrome'):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch_persistent_context(
                str(profile), channel='chrome', headless=False,
                ignore_default_args=['--disable-component-update'])
        except Exception:
            raise RuntimeError('Could not open Chrome. Install Google Chrome and close any previous app browser.') from None
        try:
            capture = Capture()
            browser.on('response', capture.response)
            page = browser.pages[0] if browser.pages else browser.new_page()
            notify('In Chrome, sign in if needed and play the episode. Download starts automatically.')
            page.goto(url, wait_until='domcontentloaded', timeout=60_000)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise Cancelled()
                result = capture.result()
                if result:
                    return result
                page.wait_for_timeout(250)
            raise RuntimeError(capture.error or 'No matching episode stream found. Sign in and start playback, then try again.')
        finally:
            browser.close()


def tool_paths(saved):
    found = {}
    roots = [BASE / 'tools', BASE / 'bin', *BASE.parent.glob('Bento4*'), *BASE.parent.glob('aria2*')]
    for name in TOOLS:
        previous = saved.get(name)
        path = previous if previous and Path(previous).is_file() else shutil.which(name)
        if not path:
            for root in roots:
                if root.is_dir():
                    candidate = next(root.rglob(name + '.exe'), None)
                    if candidate:
                        path = str(candidate)
                        break
        if path:
            found[name] = str(Path(path).resolve())
    return found


def run_tool(command, directory, paths, notify, cancel):
    if cancel.is_set():
        raise Cancelled()
    name = command[0]
    command = [paths[name], *command[1:]]
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join(dict.fromkeys(str(Path(path).parent) for path in paths.values())) + os.pathsep + env.get('PATH', '')
    # Keep tool output out of the UI: errors can include signed URLs and keys.
    with tempfile.TemporaryFile() as log:
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log, **kwargs)
        try:
            while process.poll() is None:
                if cancel.wait(0.3):
                    raise Cancelled()
            if process.returncode:
                raise RuntimeError(f'{name} failed (exit {process.returncode}). Check your tools and retry with fresh playback.')
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    STATE.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads((STATE / 'settings.json').read_text())
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    root = tk.Tk()
    root.title('Voyo Downloader')
    root.geometry('680x440')
    root.minsize(600, 440)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Download a Voyo episode', font=('Segoe UI', 17)).pack(anchor='w')
    ttk.Label(frame, text='Paste an episode link. Chrome opens for sign-in and playback.').pack(anchor='w', pady=(8, 18))
    url = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=url)
    entry.pack(fill='x')
    entry.focus()
    device = tk.StringVar(value=settings.get('device', ''))
    output_dir = tk.StringVar(value=settings.get('output_dir', str(Path.home() / 'Downloads' / 'Voyo')))
    setup = ttk.LabelFrame(frame, text='One-time setup', padding=10)
    def toggle_setup():
        if setup.winfo_manager():
            setup.pack_forget()
        else:
            setup.pack(fill='x', after=entry, pady=12)
    row = ttk.Frame(setup)
    row.pack(fill='x')
    ttk.Label(row, text='Device file').pack(side='left')
    ttk.Entry(row, textvariable=device).pack(side='left', fill='x', expand=True, padx=8)
    ttk.Button(row, text='Browse…', command=lambda: device.set(filedialog.askopenfilename(
        title='Select your existing device file', filetypes=[('Widevine device', '*.wvd')]) or device.get())).pack(side='right')
    folder_row = ttk.Frame(setup)
    folder_row.pack(fill='x', pady=(8, 0))
    ttk.Label(folder_row, text='Save to').pack(side='left')
    ttk.Entry(folder_row, textvariable=output_dir).pack(side='left', fill='x', expand=True, padx=8)
    ttk.Button(folder_row, text='Browse…', command=lambda: output_dir.set(filedialog.askdirectory(
        title='Choose download folder', initialdir=output_dir.get()) or output_dir.get())).pack(side='right')
    if not device.get() or not Path(device.get()).is_file():
        toggle_setup()
    status = tk.StringVar(value='Ready. Your device file and browser profile stay on this PC.')
    ttk.Label(frame, textvariable=status, wraplength=610).pack(anchor='w', pady=12)
    progress = ttk.Progressbar(frame, mode='indeterminate')
    progress.pack(fill='x')
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=16)
    events = queue.Queue()
    cancel = threading.Event()
    busy = False
    closing = False

    def notify(message):
        if cancel.is_set():
            raise Cancelled()
        events.put(('status', message))

    def work(link, wvd, output, paths):
        try:
            Device.load(wvd)
            stream, pssh, headers = capture_episode(link, notify, cancel)
            notify('Requesting the episode licence')
            keys = drmdl.request_generic_drmheaderspy(wvd, pssh, LICENSE, '', headers=headers)
            drmdl.download_video(stream, keys, output,
                                 lambda cmd, cwd: run_tool(cmd, cwd, paths, notify, cancel), notify)
            events.put(('done', f'Saved: {output}'))
        except Cancelled:
            events.put(('done', 'Cancelled.'))
        except requests.HTTPError as error:
            code = error.response.status_code if error.response is not None else 'unknown'
            events.put(('error', f'Licence server returned HTTP {code}. Sign in and play again; the device may be rejected.'))
        except requests.RequestException:
            events.put(('error', 'The licence request failed or timed out. Check the connection and retry.'))
        except (RuntimeError, FileExistsError) as error:
            events.put(('error', str(error)))
        except Exception:
            events.put(('error', 'Could not finish. Check the device file and browser, then retry. Use the manual workflow for detailed diagnostics.'))

    def start():
        nonlocal busy
        try:
            link = episode_url(url.get())
            wvd = Path(device.get()).expanduser()
            if not wvd.is_file() or wvd.suffix.lower() != '.wvd':
                raise ValueError('Choose your existing .wvd device file first.')
            paths = tool_paths(settings.get('tools', {}))
            for name in TOOLS:
                if name not in paths:
                    selected = filedialog.askopenfilename(title=f'Locate {name}.exe', filetypes=[('Executable', '*.exe')])
                    if not selected:
                        return
                    if Path(selected).name.lower() != name + '.exe':
                        raise ValueError(f'Choose the file named {name}.exe.')
                    paths[name] = selected
            if not output_dir.get().strip():
                raise ValueError('Choose a download folder in Settings.')
            output = str(next_output(link, output_dir.get()).resolve())
            settings.update(device=str(wvd.resolve()), tools=paths, output_dir=str(Path(output).parent))
            (STATE / 'settings.json').write_text(json.dumps(settings, indent=2))
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot start', str(error))
            return
        busy = True
        cancel.clear()
        start_button.config(state='disabled')
        settings_button.config(state='disabled')
        setup.pack_forget()
        cancel_button.config(state='normal')
        progress.start()
        status.set('Opening Chrome…')
        threading.Thread(target=work, args=(link, str(wvd.resolve()), output, paths), daemon=True).start()

    def stop():
        cancel.set()
        status.set('Stopping… A licence request may take up to 30 seconds to finish.')

    def close():
        nonlocal closing
        closing = True
        if busy:
            stop()
        else:
            root.destroy()

    def poll():
        nonlocal busy
        while not events.empty():
            kind, message = events.get_nowait()
            status.set(message)
            if kind != 'status':
                busy = False
                progress.stop()
                start_button.config(state='normal')
                settings_button.config(state='normal')
                cancel_button.config(state='disabled')
                if closing:
                    root.destroy()
                    return
                if kind == 'error':
                    messagebox.showerror('Download stopped', message)
        root.after(150, poll)

    start_button = ttk.Button(buttons, text='Download', command=start)
    start_button.pack(side='left')
    cancel_button = ttk.Button(buttons, text='Cancel', command=stop, state='disabled')
    cancel_button.pack(side='left', padx=10)
    def open_folder():
        try:
            folder = Path(settings.get('output_dir', output_dir.get())).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(str(folder))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])
        except OSError:
            messagebox.showerror('Cannot open folder', 'Open Settings and choose an available download folder.')
    ttk.Button(buttons, text='Open folder', command=open_folder).pack(side='left')
    settings_button = ttk.Button(buttons, text='Settings', command=toggle_setup)
    settings_button.pack(side='right')
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(150, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
