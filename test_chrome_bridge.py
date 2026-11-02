import base64
import io
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

import chrome_bridge as bridge
import voyo
import install_chrome
from test_voyo import licence_body, manifest, OTHER_KEY, TOKEN

EPISODE = 'https://voyo.markiza.sk/relacie/show/epizoda/1'
MANIFEST = {'type': 'capture', 'kind': 'manifest',
            'url': 'https://test.cdn.cra.cz/episode.mpd', 'text': manifest()}
LICENSE = {'type': 'capture', 'kind': 'license', 'url': voyo.LICENSE,
           'body': base64.b64encode(licence_body()).decode(),
           'headers': {'x-axdrm-message': TOKEN}}


def frames(*messages):
    stream = io.BytesIO()
    for message in messages:
        bridge.send_message(stream, message)
    stream.seek(0)
    return stream


class BridgeTests(unittest.TestCase):
    def test_matching_capture_opens_window_without_browser_in_both_orders(self):
        for records in ((MANIFEST, LICENSE), (LICENSE, MANIFEST)):
            output, window = io.BytesIO(), Mock()
            bridge.serve(frames({'type': 'start', 'url': EPISODE}, *records), output, window)
            window.assert_called_once()
            episode, capture = window.call_args.kwargs['captured']
            self.assertEqual(episode, EPISODE)
            self.assertEqual(capture[0], MANIFEST['url'])
            self.assertEqual(capture[2], LICENSE['headers'])
            output.seek(0)
            self.assertEqual([bridge.read_message(output)['type'] for _ in range(3)],
                             ['listening', 'ready', 'closed'])
            self.assertNotIn(TOKEN.encode(), output.getvalue())

    def test_wrong_episode_keys_then_cancel_never_opens_window(self):
        window = Mock()
        wrong = dict(LICENSE, body=base64.b64encode(licence_body(OTHER_KEY)).decode())
        bridge.serve(frames({'type': 'start', 'url': EPISODE}, MANIFEST, wrong,
                            {'type': 'cancel'}), io.BytesIO(), window)
        window.assert_not_called()

    def test_rejects_foreign_urls_and_extra_headers(self):
        for value in (dict(MANIFEST, url='https://cdn.cra.cz.evil.test/a.mpd'),
                      dict(MANIFEST, url='http://test.cdn.cra.cz/a.mpd'),
                      dict(MANIFEST, url='https://user@test.cdn.cra.cz/a.mpd'),
                      dict(MANIFEST, url='https://test.cdn.cra.cz:444/a.mpd'),
                      dict(LICENSE, url='https://evil.test/AcquireLicense'),
                      dict(LICENSE, body='not base64!'),
                      dict(LICENSE, headers={'cookie': 'secret'}),
                      dict(LICENSE, headers={'origin': 'a\r\nb'})):
            with self.subTest(value=value['url']), self.assertRaises(ValueError):
                bridge.capture_response(value)

    def test_rejects_non_voyo_start(self):
        with self.assertRaises(ValueError):
            bridge.serve(frames({'type': 'start', 'url': 'https://example.com/epizoda/1'}),
                         io.BytesIO(), Mock())

    def test_framing_limits_truncation_and_json_shape(self):
        for data, error in ((struct.pack('<I', bridge.MAX_MESSAGE + 1), ValueError),
                            (struct.pack('<I', 0), ValueError), (b'\x01', EOFError),
                            (struct.pack('<I', 10) + b'{}', EOFError),
                            (struct.pack('<I', 2) + b'[]', ValueError)):
            with self.subTest(data=data), self.assertRaises(error):
                bridge.read_message(io.BytesIO(data))

    def test_fragmented_pipe_reads(self):
        class Fragmented(io.BytesIO):
            def read(self, size):
                return super().read(min(size, 2))
        message = {'type': 'start', 'url': EPISODE}
        self.assertEqual(bridge.read_message(Fragmented(frames(message).getvalue())), message)

    def test_extension_registration_identity(self):
        self.assertRegex(bridge.extension_id(), r'^[a-p]{32}$')
        permissions = json.loads((bridge.BASE / 'chrome-extension/manifest.json').read_text())
        self.assertNotIn('cookies', permissions['permissions'])
        self.assertNotIn('<all_urls>', permissions['host_permissions'])

    def test_native_process_roundtrip_and_origin_check(self):
        command = [sys.executable, str(bridge.BASE / 'chrome_bridge.py')]
        result = subprocess.run(command + [f'chrome-extension://{bridge.extension_id()}/'],
                                input=frames({'type': 'start', 'url': EPISODE},
                                             {'type': 'cancel'}).getvalue(),
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(bridge.read_message(io.BytesIO(result.stdout)), {'type': 'listening'})
        self.assertEqual(result.stderr, b'')
        rejected = subprocess.run(command + ['chrome-extension://wrong/'], input=b'',
                                  capture_output=True, timeout=20)
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(rejected.stdout, b'')

    def test_installer_registers_current_user_and_exact_extension(self):
        registry = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(install_chrome, 'BASE', Path(directory)), \
                patch.object(install_chrome, 'os', SimpleNamespace(name='nt')), \
                patch.dict(sys.modules, {'winreg': registry}), patch('builtins.print'):
            install_chrome.main()
            installed = json.loads((Path(directory) / 'chrome-native-host.json').read_text())
            self.assertEqual(installed['allowed_origins'], [f'chrome-extension://{bridge.extension_id()}/'])
            self.assertEqual(installed['path'], str(Path(directory) / 'Native-Voyo.cmd'))
            registry.CreateKey.assert_called_once_with(registry.HKEY_CURRENT_USER,
                r'Software\Google\Chrome\NativeMessagingHosts\com.deijvoo.voyo')
            self.assertEqual(registry.SetValueEx.call_args.args[-1], str(Path(directory) / 'chrome-native-host.json'))


if __name__ == '__main__':
    unittest.main()
