import hashlib
import io
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from video_prototype import deploy_news_trial as deploy


class NewsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.unit = root / 'bender-video.service'
        self.smoke = root / 'smoke.conf'
        self.tts = root / 'tts-gemini.conf'
        self.news = root / 'zz-news-owner-trial.conf'
        self.backup = root / 'backup'
        self.backup.mkdir()
        self.old = f'[Service]\nExecStart=\nExecStart=/usr/bin/python3 {deploy.OLD_CODE}/video_prototype/worker.py --port 8088\n'.encode()
        contents = {self.unit: b'[Service]\n', self.smoke: self.old, self.tts: b'[Service]\n# key file stays untouched\n'}
        for path, data in contents.items():
            path.write_bytes(data)
            (self.backup / path.name).write_bytes(data)
        expected = {path: hashlib.sha256(data).hexdigest() for path, data in contents.items()}
        changes = {'SERVICE': self.unit, 'DROPIN': self.smoke, 'TTS': self.tts,
                   'NEWS': self.news, 'BACKUP': self.backup, 'EXPECTED': expected,
                   'CODE': root / 'code', 'RUNTIME': root / 'runtime/bin/python'}
        for name, value in changes.items():
            p = patch.object(deploy, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.commands = []
        def command(*args):
            self.commands.append(args)
            return ''
        def prop(name):
            return {'DropInPaths': f'{self.smoke} {self.tts}', 'MainPID': '123',
                    'ActiveState': 'active'}[name]
        for name, value in [('verify_source', lambda: None), ('process_info', lambda: (123, 'fake', 8088)),
                            ('command', command), ('prop', prop)]:
            p = patch.object(deploy, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)

    def test_success_changes_only_paths_and_adds_opt_in(self):
        with patch.object(deploy, 'health', return_value=True):
            deploy.deploy()
        result = self.smoke.read_bytes()
        self.assertIn(str(deploy.CODE).encode(), result)
        self.assertIn(str(deploy.RUNTIME).encode(), result)
        self.assertNotIn(deploy.OLD_CODE.encode(), result)
        self.assertEqual(self.news.read_bytes(), deploy.NEWS_CONTENT)
        self.assertEqual((self.backup / 'smoke.conf').read_bytes(), self.old)
        self.assertEqual(self.commands[-1], ('systemctl', 'restart', deploy.UNIT))

    def test_failed_new_health_restores_old_config_and_restarts_old(self):
        with patch.object(deploy, 'health', side_effect=[True, False, True]):
            with self.assertRaises(deploy.DeploymentError):
                deploy.deploy()
        self.assertEqual(self.smoke.read_bytes(), self.old)
        self.assertFalse(self.news.exists())
        self.assertEqual(self.commands.count(('systemctl', 'restart', deploy.UNIT)), 2)

    def test_failed_restart_restores_config_and_attempts_old_start(self):
        calls = []
        def failing_restart(*args):
            calls.append(args)
            if args == ('systemctl', 'restart', deploy.UNIT) and calls.count(args) == 1:
                raise subprocess.CalledProcessError(1, args)
            return ''
        with patch.object(deploy, 'health', side_effect=[True, True]), \
             patch.object(deploy, 'command', side_effect=failing_restart):
            with self.assertRaises(deploy.DeploymentError):
                deploy.deploy()
        self.assertEqual(self.smoke.read_bytes(), self.old)
        self.assertFalse(self.news.exists())
        self.assertEqual(calls.count(('systemctl', 'restart', deploy.UNIT)), 2)

    def test_old_health_failure_does_not_touch_service(self):
        with patch.object(deploy, 'health', return_value=False):
            with self.assertRaises(deploy.DeploymentError): deploy.deploy()
        self.assertEqual(self.smoke.read_bytes(), self.old)
        self.assertFalse(self.news.exists())
        self.assertEqual(self.commands, [])

    def test_changed_configuration_aborts_before_changes(self):
        self.tts.write_text('changed')
        with self.assertRaises(deploy.DeploymentError):
            deploy.deploy()
        self.assertEqual(self.smoke.read_bytes(), self.old)
        self.assertFalse(self.news.exists())
        self.assertEqual(self.commands, [])

    def test_changed_backup_aborts(self):
        (self.backup / 'smoke.conf').write_text('bad')
        with self.assertRaises(deploy.DeploymentError):
            deploy.deploy()
        self.assertEqual(self.commands, [])

    def test_existing_dropin_aborts_without_overwriting(self):
        self.news.write_text('existing')
        with self.assertRaises(deploy.DeploymentError):
            deploy.deploy()
        self.assertEqual(self.news.read_text(), 'existing')
        self.assertEqual(self.commands, [])

    def test_unexpected_command_is_rejected(self):
        for data in (b'[Service]\nExecStart=/bin/true\n', self.old + self.old,
                     self.old.replace(b'--port 8088', b'--port 8088 \\\n')):
            with self.assertRaises(deploy.DeploymentError):
                deploy.updated_smoke(data)


class SourceValidationTests(unittest.TestCase):
    def test_source_is_pinned_and_modified_or_extra_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            code = stage / ('Expertbot-' + deploy.COMMIT)
            code.mkdir()
            (code / 'worker.py').write_bytes(b'trusted')
            runtime = stage / 'runtime'
            runtime.write_bytes(b'fake')
            runtime.chmod(0o755)
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode='w:gz') as tar:
                info = tarfile.TarInfo('Expertbot-' + deploy.COMMIT + '/worker.py')
                info.size = len(b'trusted')
                tar.addfile(info, io.BytesIO(b'trusted'))
            archive = raw.getvalue()
            (stage / 'source.tar.gz').write_bytes(archive)
            with patch.multiple(deploy, STAGE=stage, CODE=code, RUNTIME=runtime,
                                ARCHIVE_SHA=hashlib.sha256(archive).hexdigest()):
                deploy.verify_source()
                (code / 'worker.py').write_bytes(b'modified')
                with self.assertRaises(deploy.DeploymentError): deploy.verify_source()
                (code / 'worker.py').write_bytes(b'trusted')
                (code / 'extra.py').write_text('bad')
                with self.assertRaises(deploy.DeploymentError): deploy.verify_source()


if __name__ == '__main__':
    unittest.main()
