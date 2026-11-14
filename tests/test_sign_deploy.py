import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from video_prototype import deploy_sign_worker as deploy


class SignDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root/'code-sign-prod-test'
        (self.target/'video_prototype').mkdir(parents=True)
        (self.target/'video_prototype/worker.py').touch()
        self.dropin = self.root/'smoke.conf'
        self.original = '[Service]\nExecStart=\nExecStart=' + deploy.OLD + '/video_prototype/worker.py\n'
        self.dropin.write_text(self.original)
        self.env = self.root/'.env'; self.env.write_text('VIDEO_WORKER_KEY="123456789012345678901234"\n')
        self.kwargs = {'dropin': self.dropin, 'env_file': self.env, 'target_prefix': str(self.root/'code-sign-prod-')}

    def test_success_backups_and_changes_expected_path(self):
        with patch.object(deploy, 'systemctl') as ctl, patch.object(deploy, 'check_health', return_value=True):
            deploy.deploy(self.target, **self.kwargs)
        self.assertIn(str(self.target), self.dropin.read_text())
        self.assertEqual(self.dropin.with_name('smoke.conf.before-sign').read_text(), self.original)
        self.assertEqual(ctl.call_count, 4)

    def test_health_failure_restores_configuration(self):
        with patch.object(deploy, 'systemctl') as ctl, patch.object(deploy, 'check_health', return_value=False):
            with self.assertRaises(RuntimeError):
                deploy.deploy(self.target, **self.kwargs)
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertEqual(ctl.call_count, 7)

    def test_unexpected_config_does_not_change_or_restart(self):
        self.dropin.write_text('[Service]\n')
        with patch.object(deploy, 'systemctl') as ctl:
            with self.assertRaises(ValueError):
                deploy.deploy(self.target, **self.kwargs)
        self.assertEqual(self.dropin.read_text(), '[Service]\n')
        ctl.assert_not_called()
