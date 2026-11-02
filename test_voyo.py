import base64
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from pywidevine.pssh import PSSH
from pywidevine.license_protocol_pb2 import SignedMessage, LicenseRequest, WidevinePsshData

import drmdl
import voyo

KEY = UUID(int=123).bytes
OTHER_KEY = UUID(int=456).bytes
TOKEN = 'private-token-that-must-not-be-logged'


def manifest(key=KEY, dynamic=False):
    box = PSSH.new(system_id=PSSH.SystemId.Widevine, key_ids=[UUID(bytes=key)]).dumps()
    return (f'<MPD xmlns:cenc="urn:mpeg:cenc:2013" type="{"dynamic" if dynamic else "static"}">'
            '<Period><AdaptationSet><ContentProtection '
            'schemeIdUri="urn:uuid:EDEF8BA9-79D6-4ACE-A3C8-27DCD51D21ED">'
            f'<cenc:pssh>{box}</cenc:pssh></ContentProtection></AdaptationSet></Period></MPD>')


def licence_body(key=KEY):
    request = LicenseRequest()
    request.content_id.widevine_pssh_data.pssh_data.append(WidevinePsshData(key_ids=[key]).SerializeToString())
    return SignedMessage(type=SignedMessage.LICENSE_REQUEST, msg=request.SerializeToString()).SerializeToString()


def response(url, text='', body=b'', status=200):
    request = Mock(method='POST', post_data_buffer=body)
    request.all_headers.return_value = {'x-axdrm-message': TOKEN, 'origin': 'https://media.cms.markiza.sk',
                                       'cookie': 'private-cookie', 'host': 'irrelevant'}
    return Mock(url=url, status=status, text=Mock(return_value=text), request=request)


class CaptureTests(unittest.TestCase):
    def test_episode_links_only(self):
        self.assertEqual(voyo.episode_url(' https://voyo.markiza.sk/relacie/show/epizoda/1 '),
                         'https://voyo.markiza.sk/relacie/show/epizoda/1')
        for value in ('http://voyo.markiza.sk/epizoda/1', 'https://evil.test/epizoda/1',
                      'https://voyo.markiza.sk.evil.test/epizoda/1',
                      'https://user@voyo.markiza.sk/epizoda/1', 'https://voyo.markiza.sk/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                voyo.episode_url(value)

    def test_manifest_rejects_live_and_entities(self):
        for value in (manifest(dynamic=True), '<!DOCTYPE MPD><MPD/>', '<MPD/>', 'bad xml'):
            with self.subTest(value=value), self.assertRaises(Exception):
                voyo.manifest_details(value)

    def test_successful_matching_pair_in_either_order(self):
        mpd = response('https://test.cdn.cra.cz/episode.mpd?secret=yes', text=manifest())
        licence = response(voyo.LICENSE, body=licence_body())
        for pair in ((mpd, licence), (licence, mpd)):
            capture = voyo.Capture()
            capture.response(pair[0])
            self.assertIsNone(capture.result())
            capture.response(pair[1])
            url, pssh, headers = capture.result()
            self.assertEqual(url, mpd.url)
            self.assertEqual(PSSH(pssh).key_ids, [UUID(bytes=KEY)])
            self.assertEqual(headers['x-axdrm-message'], TOKEN)
            self.assertNotIn('content-type', headers)
            self.assertNotIn('cookie', headers)

    def test_certificate_rejected_licence_and_wrong_episode_do_not_match(self):
        capture = voyo.Capture()
        capture.response(response('https://test.cdn.cra.cz/episode.mpd', text=manifest()))
        for candidate in (response(voyo.LICENSE, body=b'\x08\x04'),
                          response(voyo.LICENSE, body=licence_body(), status=403),
                          response(voyo.LICENSE, body=licence_body(OTHER_KEY)),
                          response('https://evil.test/AcquireLicense', body=licence_body())):
            capture.response(candidate)
            self.assertIsNone(capture.result())

    def test_other_host_manifest_ignored(self):
        capture = voyo.Capture()
        capture.response(response('https://cdn.cra.cz.evil.test/episode.mpd', text=manifest()))
        capture.response(response(voyo.LICENSE, body=licence_body()))
        self.assertIsNone(capture.result())

    def test_malformed_request_does_not_expose_body(self):
        capture = voyo.Capture()
        capture.response(response(voyo.LICENSE, body=b'private-token'))
        self.assertIsNone(capture.result())
        self.assertNotIn('private-token', capture.error)


class PipelineTests(unittest.TestCase):
    def test_output_names_preserve_existing_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'downloads'
            link = 'https://voyo.markiza.sk/relacie/show/epizoda/CON?token=secret'
            first = voyo.next_output(link, folder)
            self.assertEqual(first.name, 'Voyo-CON.mp4')
            first.write_bytes(b'keep')
            second = voyo.next_output(link, folder)
            self.assertEqual(second.name, 'Voyo-CON (2).mp4')
            second.touch()
            self.assertEqual(voyo.next_output(link, folder).name, 'Voyo-CON (3).mp4')
            self.assertEqual(first.read_bytes(), b'keep')

    def test_complete_file_and_trimmed_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'episode.mp4'
            commands = []
            def run(command, cwd):
                commands.append(command)
                if command[0] == 'ffmpeg':
                    Path(command[-1]).write_bytes(b'complete movie')
            drmdl.download_video('https://example.invalid/video', 'kid:key\n', output, run, Mock())
            self.assertEqual(output.read_bytes(), b'complete movie')
            self.assertEqual(len(commands), 6)
            self.assertEqual(commands[1][1:3], ['--key', 'kid:key'])
            self.assertIn('-n', commands[-1])

    def test_failure_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'episode.mp4'
            with self.assertRaises(RuntimeError):
                drmdl.download_video('url', 'kid:key', output, Mock(side_effect=RuntimeError('failed')), Mock())
            self.assertFalse(output.exists())

    def test_existing_file_is_not_touched(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'episode.mp4'
            output.write_bytes(b'keep')
            run = Mock()
            with self.assertRaises(FileExistsError):
                drmdl.download_video('url', 'kid:key', output, run, Mock())
            self.assertEqual(output.read_bytes(), b'keep')
            run.assert_not_called()

    def test_late_file_creation_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'episode.mp4'
            def run(command, cwd):
                if command[0] == 'ffmpeg':
                    Path(command[-1]).write_bytes(b'new')
                    output.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                drmdl.download_video('url', 'kid:key', output, run, Mock())
            self.assertEqual(output.read_bytes(), b'existing')

    def test_no_keys_stops_before_download(self):
        run = Mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            drmdl.download_video('url', '\n', Path(directory) / 'test.mp4', run, Mock())
        run.assert_not_called()

    def test_cancel_before_launch(self):
        cancel = threading.Event()
        cancel.set()
        with patch('subprocess.Popen') as popen, self.assertRaises(voyo.Cancelled):
            voyo.run_tool(['test'], '.', {}, Mock(), cancel)
        popen.assert_not_called()

    def test_failed_tool_does_not_expose_output(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError) as error:
            voyo.run_tool(['python', '-c', f'import sys; print({TOKEN!r}); sys.exit(3)'],
                          directory, {'python': sys.executable}, Mock(), threading.Event())
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertIn('exit 3', str(error.exception))

    def test_cancel_running_tool(self):
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        try:
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(voyo.Cancelled):
                voyo.run_tool(['python', '-c', 'import time; time.sleep(60)'], directory,
                              {'python': sys.executable}, Mock(), cancel)
        finally:
            timer.cancel()

    def test_find_tools_in_sibling_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'downloader'
            base.mkdir()
            tools = root / 'Bento4-sdk' / 'bin'
            tools.mkdir(parents=True)
            (tools / 'mp4decrypt.exe').touch()
            with patch.object(voyo, 'BASE', base), patch('shutil.which', return_value=None):
                self.assertEqual(voyo.tool_paths({}), {'mp4decrypt': str((tools / 'mp4decrypt.exe').resolve())})


if __name__ == '__main__':
    unittest.main()
