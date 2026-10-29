import contextlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import drmdl


class CliTests(unittest.TestCase):
    def run_cli(self, extra=(), answers=()):
        output = io.StringIO()
        argv = ['drmdl.py', '-out', 'test.mp4', '-url', 'https://example.invalid/video',
                '-pssh', 'test', '-lic', 'https://example.invalid/license',
                '-xdtcd', 'private-token', '-channel', 'test-channel',
                '-live_token', 'test-token', '-pid', 'test-pid', *extra]
        cdm = Mock()
        cdm.get_license_challenge.return_value = b'test-challenge'
        cdm.get_keys.return_value = [SimpleNamespace(
            type='CONTENT', kid=UUID(int=1), key=b'\xab' * 16)]
        response = Mock(content=b'test-license')
        response.json.return_value = {
            'license': 'test-license',
            'ServiceResponse': {'OutData': {'LicenseInfo': 'test-license'}}}
        process = Mock(returncode=0)
        process.communicate.return_value = (b'', b'')
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(output))
            stack.enter_context(patch.object(sys, 'argv', argv))
            prompt = stack.enter_context(patch('builtins.input', side_effect=answers))
            stack.enter_context(patch('pywidevine.pssh.PSSH'))
            stack.enter_context(patch('pywidevine.device.Device.load'))
            stack.enter_context(patch('pywidevine.cdm.Cdm.from_device', return_value=cdm))
            post = stack.enter_context(patch('requests.post', return_value=response))
            stack.enter_context(patch('httpx.get', side_effect=AssertionError('Unexpected network access')))
            stack.enter_context(patch('httpx.post', side_effect=AssertionError('Unexpected network access')))
            popen = stack.enter_context(patch('subprocess.Popen', return_value=process))
            stack.enter_context(patch.object(drmdl.DRMHeaders, 'json_data', {}, create=True))
            stack.enter_context(patch.object(drmdl.DRMHeaders, 'cookies', {}, create=True))
            runpy.run_path(str(Path(drmdl.__file__)), run_name='__main__')
        return output.getvalue(), post, popen, prompt

    def test_default_mode_without_country(self):
        output, post, popen, prompt = self.run_cli()
        post.assert_called_once()
        self.assertNotIn('proxies', post.call_args.kwargs)
        self.assertEqual(popen.call_count, 6)
        self.assertIn('Done', output)
        prompt.assert_not_called()

    def test_explicit_modes_without_country(self):
        for mode in range(1, 8):
            with self.subTest(mode=mode):
                _, post, popen, prompt = self.run_cli(['-selection', str(mode)])
                post.assert_called_once()
                self.assertEqual(popen.call_count, 6)
                prompt.assert_not_called()

    def test_invalid_modes_rejected(self):
        for mode in ('0', '8', 'abc'):
            with self.subTest(mode=mode), self.assertRaises(SystemExit) as error:
                self.run_cli(['-selection', mode])
            self.assertEqual(error.exception.code, 2)

    def test_empty_pid_is_prompted(self):
        _, post, _, prompt = self.run_cli(['-selection', '4', '-pid', ''], ['replacement-pid'])
        prompt.assert_called_once_with('Enter PID:')
        self.assertEqual(post.call_args.kwargs['json']['getRawWidevineLicense']['releasePid'],
                         'replacement-pid')

    def test_progress_does_not_print_keys_or_command_arguments(self):
        for flags in ([], ['--verbose']):
            with self.subTest(flags=flags):
                output, _, _, _ = self.run_cli(flags)
                self.assertNotIn('ab' * 16, output)
                self.assertNotIn('https://example.invalid/video', output)
                self.assertNotIn('private-token', output)
                self.assertEqual('Creating temporary directory' in output, bool(flags))


class ProcessTests(unittest.TestCase):
    def run_process(self, source):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(drmdl, 'args', SimpleNamespace(verbose=False), create=True), \
                contextlib.redirect_stdout(output):
            try:
                drmdl.start_process([sys.executable, '-c', source], directory)
            except SystemExit as error:
                return error.code, output.getvalue()
        return 0, output.getvalue()

    def test_success(self):
        code, output = self.run_process('print("ok")')
        self.assertEqual(code, 0)
        self.assertIn('Process completed successfully.', output)

    def test_failure_preserves_stderr(self):
        code, output = self.run_process('import sys; sys.stderr.write("actual failure"); sys.exit(3)')
        self.assertEqual(code, 1)
        self.assertIn('actual failure', output)
        self.assertIn('(3)', output)

    def test_failure_handles_non_utf8_stderr(self):
        code, output = self.run_process('import sys; sys.stderr.buffer.write(b"bad: \\xff"); sys.exit(2)')
        self.assertEqual(code, 1)
        self.assertIn('bad:', output)


if __name__ == '__main__':
    unittest.main()
