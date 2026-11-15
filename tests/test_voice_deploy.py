import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from video_prototype import deploy_voice_worker as deployment


class VoiceDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = '/data/video-prototype/code-sign-prod-e1af119'
        self.target = self.root / 'code-voice-prod-be74202'
        (self.target / 'video_prototype').mkdir(parents=True)
        (self.target / 'video_prototype/worker.py').touch()
        self.dropin = self.root / 'smoke.conf'
        self.backup = self.root / 'smoke.conf.before-voice-be74202'
        self.original = '[Service]\nExecStart=\nExecStart=' + self.old + '/video_prototype/worker.py --random-guests\n'
        self.dropin.write_text(self.original)
        self.calls = []
        self.idle_calls = 0
        for p in (patch.object(deployment, 'DROPIN', self.dropin),
                  patch.object(deployment, 'BACKUP', self.backup),
                  patch.object(deployment, 'TARGET', self.target),
                  patch.object(deployment, 'verify_checkout'),
                  patch.object(deployment, 'active_pid', return_value=101),
                  patch.object(deployment, 'worker_key', return_value='not-logged-secret'),
                  patch.object(deployment, 'health_ok', return_value=True),
                  patch.object(deployment, 'command', side_effect=self.fake_command),
                  patch.object(deployment, 'assert_idle', side_effect=self.fake_idle)):
            p.start(); self.addCleanup(p.stop)

    def fake_command(self, *args):
        self.calls.append(args)
        return ''

    def fake_idle(self, pid):
        self.idle_calls += 1
        self.assertEqual(pid, 101)

    def test_success_backups_configuration_and_verifies_voice(self):
        deployment.deploy('a' * 40)
        self.assertEqual(self.backup.read_text(), self.original)
        self.assertIn(str(self.target), self.dropin.read_text())
        self.assertNotIn(self.old, self.dropin.read_text())
        self.assertEqual(self.calls, [('systemctl', 'daemon-reload'), ('systemctl', 'restart', deployment.UNIT)])
        self.assertEqual(deployment.health_ok.call_count, 2)
        self.assertTrue(deployment.health_ok.call_args.kwargs['voice'])
        self.assertGreaterEqual(self.idle_calls, 3)

    def test_failed_health_restores_old_config_and_restarts(self):
        deployment.health_ok.side_effect = [True, False, True]
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy('a' * 40)
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertEqual(self.backup.read_text(), self.original)
        self.assertEqual(self.calls.count(('systemctl', 'restart', deployment.UNIT)), 2)
        self.assertEqual(deployment.health_ok.call_count, 3)

    def test_job_appears_before_restart_restores_without_interrupting(self):
        def busy_after_write(pid):
            self.fake_idle(pid)
            if self.idle_calls == 3:
                raise deployment.DeploymentError('busy')
        deployment.assert_idle.side_effect = busy_after_write
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy('a' * 40)
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertNotIn(('systemctl', 'restart', deployment.UNIT), self.calls)
        self.assertEqual(self.backup.read_text(), self.original)

    def test_busy_before_backup_never_changes_configuration(self):
        deployment.assert_idle.side_effect = deployment.DeploymentError('busy')
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy('a' * 40)
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.calls, [])

    def test_wrong_configuration_or_existing_backup_fails_closed(self):
        self.backup.write_text('old backup')
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy('a' * 40)
        self.assertEqual(self.backup.read_text(), 'old backup')
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertEqual(self.calls, [])

    def test_rollback_failure_is_reported_and_no_success_claimed(self):
        deployment.health_ok.side_effect = [True, False, False]
        with self.assertRaises(deployment.DeploymentError), patch('builtins.print') as printer:
            deployment.deploy('a' * 40)
        self.assertEqual(self.dropin.read_text(), self.original)
        self.assertTrue(any('Откат не подтверждён' in str(c) for c in printer.call_args_list))
        self.assertFalse(any('Новый воркер активен' in str(c) for c in printer.call_args_list))


class VoiceCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'prepared'
        (self.target / 'video_prototype').mkdir(parents=True)
        (self.target / 'video_prototype/worker.py').touch()
        patcher = patch.object(deployment, 'TARGET', self.target)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_only_clean_expected_commit_is_accepted(self):
        commit = 'a' * 40
        with patch.object(deployment, 'command', side_effect=[commit, '']) as command:
            deployment.verify_checkout(self.target, commit)
        self.assertIn('safe.directory=' + str(self.target), command.call_args_list[0].args)
        with patch.object(deployment, 'command', side_effect=['b' * 40]):
            with self.assertRaises(deployment.DeploymentError):
                deployment.verify_checkout(self.target, commit)
        with patch.object(deployment, 'command', side_effect=[commit, ' M worker.py']):
            with self.assertRaises(deployment.DeploymentError):
                deployment.verify_checkout(self.target, commit)
        with self.assertRaises(deployment.DeploymentError):
            deployment.verify_checkout(self.target, 'not-a-hash')
