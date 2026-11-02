"""Chrome native messaging: accept one Voyo capture, then open the local window."""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

MAX_MESSAGE = 6_000_000
BASE = Path(__file__).resolve().parent
HOST = 'com.deijvoo.voyo'


def extension_id():
    manifest = json.loads((BASE / 'chrome-extension' / 'manifest.json').read_text())
    digest = hashlib.sha256(base64.b64decode(manifest['key'], validate=True)).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(char, 16)) for char in digest)


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size - len(result))
        if not part:
            raise EOFError('Chrome disconnected.')
        result.extend(part)
    return bytes(result)


def read_message(stream):
    size = struct.unpack('<I', read_exact(stream, 4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError('Invalid message size.')
    message = json.loads(read_exact(stream, size))
    if not isinstance(message, dict):
        raise ValueError('Expected an object.')
    return message


def send_message(stream, message):
    data = json.dumps(message).encode('utf-8')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def capture_response(message):
    from voyo import LICENSE
    url = message.get('url', '')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError('Unsupported URL.')
    if message.get('kind') == 'license' and url == LICENSE:
        body = base64.b64decode(message['body'], validate=True)
        if len(body) > 1_000_000:
            raise ValueError('Request too large.')
        headers = message['headers']
        allowed = {'x-axdrm-message', 'origin', 'referer', 'user-agent', 'content-type'}
        if (not isinstance(headers, dict) or not set(headers) <= allowed
                or any(not isinstance(value, str) or len(value) > 65536
                       or '\r' in value or '\n' in value for value in headers.values())):
            raise ValueError('Invalid headers.')
        request = SimpleNamespace(method='POST', post_data_buffer=body, all_headers=lambda: headers)
        return SimpleNamespace(status=200, url=url, request=request)
    if (message.get('kind') == 'manifest' and parsed.hostname
            and (parsed.hostname.endswith('.cdn.cra.cz') or parsed.hostname.endswith('.markiza.sk'))
            and parsed.path.lower().endswith('.mpd')):
        content = message['text']
        if not isinstance(content, str) or len(content) > 5_000_000:
            raise ValueError('Manifest too large.')
        return SimpleNamespace(status=200, url=url, request=None, text=lambda: content)
    raise ValueError('Unsupported capture.')


def serve(source, destination, show_window):
    from voyo import Capture, episode_url
    start = read_message(source)
    if start.get('type') != 'start':
        raise ValueError('Expected start.')
    episode = episode_url(start.get('url', ''))
    capture = Capture()
    send_message(destination, {'type': 'listening'})
    # Bound retained state even if a tab repeatedly retries playback.
    for _ in range(100):
        message = read_message(source)
        if message.get('type') == 'cancel':
            return
        if message.get('type') != 'capture':
            raise ValueError('Unexpected message.')
        capture.response(capture_response(message))
        result = capture.result()
        if result:
            send_message(destination, {'type': 'ready'})
            show_window(captured=(episode, result))
            send_message(destination, {'type': 'closed'})
            return
    raise ValueError('Too many playback requests. Try again.')


def main():
    if len(sys.argv) < 2 or sys.argv[1] != f'chrome-extension://{extension_id()}/':
        return 1
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    original_stdout = sys.stdout
    # stdout belongs exclusively to Chrome's framing protocol.
    with open(os.devnull, 'w') as quiet:
        sys.stdout = quiet
        try:
            import voyo
            serve(source, destination, voyo.main)
        except EOFError:
            pass
        except Exception:
            send_message(destination, {'type': 'error', 'message':
                'Could not read this episode. Reload Voyo and try again.'})
        finally:
            sys.stdout = original_stdout
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
