"""Safety checks shared by every systemd deploy script in `video_prototype`.

The deploy_* modules are variations of one script: extract an archive,
switch the smoke drop-in, verify capabilities, roll back. They used to have
five near-identical test files (~99% same lines), so a fix in one scenario had
to be repeated five times. Here one scenario is declared once and driven
against every module that implements it.

Per-module differences are data, not code: which pinned revision it ships,
which capability it must prove, whether it can discover its own previous
version, and whether its retention cleanup blocks a restart.
"""
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from video_prototype import (
    deploy_bulletin_cards,
    deploy_bulletin_manual_video,
    deploy_bulletin_update,
    deploy_bulletin_worker,
    deploy_story_studio,
)

# `own_old`: the script hard-codes its previous install and cannot discover it,
# so the smoke drop-in is built from `module.OLD` rather than a discovered path.
# `retention_blocks`: restarting would start a retention cleanup that deletes
# old video jobs, so a ~31-day-old log must abort the deploy.
SPECS = (
    {
        'name': 'bulletin_cards',
        'module': deploy_bulletin_cards,
        'own_old': False,
        'discovers_old': True,
        'required_cap': 'news-bulletin-multi-video-v1',
        'retention_blocks': False,
        'pins': (('COMMIT', '0c2881c6931357ce9e8b96129b52a69dd4b2de77'),
                 ('ARCHIVE_SHA', '747ed1449128bc58988d0b4d25155166540e2e7b0ead0733f627b01d2a25dacb'),
                 ('OLD_COMMIT', '2ed3e72b98b1dd7e1cf4f2f0580945fe643c8751')),
    },
    {
        'name': 'bulletin_manual_video',
        'module': deploy_bulletin_manual_video,
        'own_old': False,
        'discovers_old': True,
        'required_cap': 'news-bulletin-manual-video-v1',
        'retention_blocks': True,
        'pins': (('COMMIT', '7000538a9773ec61be6161204cb4432395f1fa5e'),
                 ('ARCHIVE_SHA', 'dc8e7d0a89042fd94c5349458880f449e3c4793091c3c7f1d9590ddec534f555'),
                 ('OLD_COMMIT', '0c2881c6931357ce9e8b96129b52a69dd4b2de77')),
    },
    {
        'name': 'story_studio',
        'module': deploy_story_studio,
        'own_old': False,
        'discovers_old': True,
        'required_cap': 'news-story-bulletin-studio-v1',
        'retention_blocks': True,
        'pins': (('COMMIT', '699ec5b56d7766489d2dfa04e2cbaae1e7a02b82'),
                 ('ARCHIVE_SHA', '22452a536e1088f14dee41cdbad62c63131f0d4366c2703fe162624c7e5d597a'),
                 ('OLD_COMMIT', '7000538a9773ec61be6161204cb4432395f1fa5e')),
    },
    {
        'name': 'bulletin_update',
        'module': deploy_bulletin_update,
        'own_old': True,
        'discovers_old': False,
        'required_cap': 'news-bulletin-multi-video-v1',
        'retention_blocks': True,
        'pins': (('COMMIT', '2ed3e72b98b1dd7e1cf4f2f0580945fe643c8751'),
                 ('ARCHIVE_SHA', '8661c8f5267086b5edbd94980a14054098e22097a989b0d2893425ac96a3342e'),
                 ('OLD', '/data/video-prototype/bulletin-code-916kh5ke/'
                         'Expertbot-44ccf03d183282e42174f3623d340f5c7076b5ee')),
    },
    {
        'name': 'bulletin_worker',
        'module': deploy_bulletin_worker,
        'own_old': True,
        'discovers_old': False,
        'required_cap': 'news-bulletin-v1',
        'retention_blocks': True,
        'pins': (('COMMIT', '44ccf03d183282e42174f3623d340f5c7076b5ee'),
                 ('ARCHIVE_SHA', '0efeb50d105fc981cc68c4182955692b846dfb6d13ab4ed17f0e56cd06e94b29'),
                 ('OLD', '/data/video-prototype/news-vision-8HnuhB/'
                         'Expertbot-6283a72b0dc8f307ece4d6c4cb975c5d95a320e8')),
    },
)


def build_archive(members):
    """A tar with the given (name, bytes, type) members; non-regular ones link out."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.linkname = '/etc/passwd'
                tar.addfile(info)
    return buf.getvalue()


def make_cases(spec):
    """One TestCase per scenario, named after the module it drives."""
    deploy = spec['module']
    label = spec['name']

    class ExtractTests(unittest.TestCase):
        def test_verified_archive_extracts_and_invalid_paths_fail(self):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                stage = base / 'bulletin-stage-AbCd1234'
                stage.mkdir()
                dest = base / 'code'
                dest.mkdir()
                root = 'Expertbot-' + deploy.COMMIT
                for name, kind, allowed in [
                        (f'{root}/video_prototype/worker.py', tarfile.REGTYPE, True),
                        (f'{root}/../escape', tarfile.REGTYPE, False),
                        (f'{root}/link', tarfile.SYMTYPE, False)]:
                    blob = build_archive([(name, b'print(1)', kind)])
                    (stage / 'source.tar.gz').write_bytes(blob)
                    with patch.object(deploy, 'BASE', base), \
                         patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(blob).hexdigest()):
                        if allowed:
                            code = deploy.extract_source(stage, dest)
                            self.assertEqual((code / 'video_prototype/worker.py').read_bytes(), b'print(1)')
                        else:
                            with self.assertRaises(deploy.SafetyError):
                                deploy.extract_source(stage, dest)
                (stage / 'source.tar.gz').write_bytes(b'wrong')
                with patch.object(deploy, 'BASE', base), self.assertRaises(deploy.SafetyError):
                    deploy.extract_source(stage, dest)

    class SwitchTests(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self.tmp.cleanup)
            self.root = Path(self.tmp.name)
            self.base = self.root / 'data'
            self.base.mkdir()
            self.system = self.root / 'etc'
            self.system.mkdir()
            self.smoke = self.system / 'smoke.conf'
            if spec['own_old']:
                self.old_code = deploy.OLD
            else:
                self.old_code = self.base / 'bulletin-code-abcdefgh' / ('Expertbot-' + deploy.OLD_COMMIT)
            self.old = ('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '
                        + str(self.old_code / 'video_prototype/worker.py') + ' --port 8088\n').encode()
            self.smoke.write_bytes(self.old)
            self.code = self.base / 'prepared-code'
            self.code.mkdir()
            self.patches = [patch.object(deploy, 'BASE', self.base),
                            patch.object(deploy, 'SMOKE', self.smoke),
                            patch.object(deploy.os, 'geteuid', return_value=0),
                            patch.object(deploy, 'process_snapshot', return_value=(123, 8088, 'fake-secret')),
                            patch.object(deploy, 'quiet'),
                            patch.object(deploy, 'extract_source', return_value=self.code)]
            if spec['discovers_old']:
                self.patches.append(patch.object(deploy, 'discover_old', return_value=self.old_code))
            for started in self.patches:
                started.start()
                self.addCleanup(started.stop)
            self.cmds = []

            def command(*args, **kwargs):
                self.cmds.append(args)
                return ''

            self.cp = patch.object(deploy, 'command', side_effect=command)
            self.cp.start()
            self.addCleanup(self.cp.stop)

            def prop(name):
                if name == 'DropInPaths':
                    return ' '.join(str(self.system / n) for n in deploy.EXPECTED_DROPINS)
                if name == 'ActiveState':
                    return 'active'
                if name == 'MainPID':
                    return '456'
                raise AssertionError(name)

            self.pp = patch.object(deploy, 'prop', side_effect=prop)
            self.pp.start()
            self.addCleanup(self.pp.stop)
            caps = {'animated-sign-v1', 'voice-notes-v1', 'news-story-v1'}
            wanted = getattr(deploy, 'REQUIRED_NEW', {'news-bulletin-v1'})
            self.hp = patch.object(deploy, 'health', side_effect=[caps, caps, caps | wanted])
            self.hp.start()
            self.addCleanup(self.hp.stop)

        def test_success_switches_only_worker_path_and_saves_private_backup(self):
            deploy.deploy(self.base / 'stage')
            self.assertEqual(
                self.smoke.read_bytes(),
                self.old.replace(str(self.old_code / 'video_prototype/worker.py').encode(),
                                 str(self.code / 'video_prototype/worker.py').encode()))
            backups = list(self.base.glob('bulletin-backup-*/smoke.conf'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), self.old)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.cmds.count(('systemctl', 'restart', deploy.UNIT)), 1)

        def test_missing_capability_is_not_reported_as_success(self):
            old_caps = {'animated-sign-v1', 'voice-notes-v1', 'news-story-v1'}
            missing = (old_caps | getattr(deploy, 'REQUIRED_NEW', {'news-bulletin-v1'})) - {spec['required_cap']}
            with patch.object(deploy, 'health', side_effect=[old_caps, old_caps, missing]), \
                 patch.object(deploy.time, 'monotonic', side_effect=[0, 0, 61]), \
                 patch.object(deploy.time, 'sleep'):
                with self.assertRaisesRegex(deploy.SafetyError, 'health'):
                    deploy.deploy(self.base / 'stage')
            self.assertEqual(self.cmds.count(('systemctl', 'restart', deploy.UNIT)), 1)
            self.assertIn(str(self.code).encode(), self.smoke.read_bytes())

        def test_pins_only_the_inspected_previous_and_new_versions(self):
            for attr, expected in spec['pins']:
                with self.subTest(pin=attr):
                    self.assertEqual(str(getattr(deploy, attr)), expected)

        def test_uncertain_restart_never_triggers_second_restart_or_rollback(self):
            def failing(*args, **kwargs):
                self.cmds.append(args)
                if args == ('systemctl', 'restart', deploy.UNIT):
                    raise subprocess.TimeoutExpired(args, 90)
                return ''

            with patch.object(deploy, 'command', side_effect=failing):
                with self.assertRaises(subprocess.TimeoutExpired):
                    deploy.deploy(self.base / 'stage')
            self.assertIn(str(self.code).encode(), self.smoke.read_bytes())
            self.assertEqual(self.cmds.count(('systemctl', 'restart', deploy.UNIT)), 1)
            self.assertEqual(len(list(self.base.glob('bulletin-backup-*/smoke.conf'))), 1)

        def test_failure_before_restart_restores_config_without_restart(self):
            def fail_once(*args, **kwargs):
                self.cmds.append(args)
                if args == ('systemctl', 'daemon-reload') and self.cmds.count(args) == 1:
                    raise OSError()
                return ''

            with patch.object(deploy, 'command', side_effect=fail_once):
                with self.assertRaises(OSError):
                    deploy.deploy(self.base / 'stage')
            self.assertEqual(self.smoke.read_bytes(), self.old)
            self.assertNotIn(('systemctl', 'restart', deploy.UNIT), self.cmds)

        def test_changed_config_and_live_child_abort_before_write(self):
            with patch.object(deploy, 'quiet', side_effect=deploy.SafetyError('busy')):
                with self.assertRaises(deploy.SafetyError):
                    deploy.deploy(self.base / 'stage')
            self.assertEqual(self.smoke.read_bytes(), self.old)
            self.assertEqual(self.cmds, [])
            if spec['own_old']:
                with self.assertRaises(deploy.SafetyError):
                    deploy.changed_smoke(self.old + self.old, self.code)
            else:
                with patch.object(deploy, 'OLD', self.old_code), self.assertRaises(deploy.SafetyError):
                    deploy.changed_smoke(self.old + self.old, self.code)

    class QuietTests(unittest.TestCase):
        def test_unresolved_news_recent_jobs_and_processes_block(self):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                proc, jobs, news = base / 'proc', base / 'jobs', base / 'news'
                for folder in (proc, jobs, news):
                    folder.mkdir()
                pid = 100
                worker = proc / str(pid)
                worker.mkdir()
                (worker / 'status').write_text('Name:\tpython\nPPid:\t1\n')
                (worker / 'cmdline').write_bytes(b'python\0worker\0')
                with patch.object(deploy, 'PROC', proc), patch.object(deploy, 'JOBS', jobs), \
                     patch.object(deploy, 'NEWS_ROOT', news), patch.object(deploy, 'prop', return_value=str(pid)):
                    deploy.quiet(pid)
                    child = proc / '101'
                    child.mkdir()
                    (child / 'status').write_text('PPid:\t100\n')
                    with self.assertRaises(deploy.SafetyError):
                        deploy.quiet(pid)
                    (child / 'status').write_text('PPid:\t1\n')
                    (child / 'cmdline').write_bytes(b'other\0')
                    folder = jobs / str(uuid.uuid4())
                    folder.mkdir()
                    with self.assertRaises(deploy.SafetyError):
                        deploy.quiet(pid)
                    os.utime(folder, (time.time() - 500, time.time() - 500))
                    deploy.quiet(pid)
                    log = folder / 'worker.log'
                    log.write_text('log')
                    old = time.time() - 31 * 24 * 60 * 60
                    os.utime(log, (old, old))
                    os.utime(folder, (old, old))
                    if spec['retention_blocks']:
                        # Restarting would let the worker delete this job.
                        with self.assertRaises(deploy.SafetyError):
                            deploy.quiet(pid)
                    else:
                        deploy.quiet(pid)  # Normal retention cleanup is permitted.
                    recent = time.time() - 500
                    os.utime(log, (recent, recent))
                    os.utime(folder, (recent, recent))
                    deploy.quiet(pid)
                    folder = news / str(uuid.uuid4())
                    folder.mkdir()
                    with self.assertRaises(deploy.SafetyError):
                        deploy.quiet(pid)
                    (folder / 'failed').write_text('failed')
                    deploy.quiet(pid)

    cases = {'ExtractTests': ExtractTests, 'SwitchTests': SwitchTests, 'QuietTests': QuietTests}

    if spec['discovers_old']:
        class DiscoveryTests(unittest.TestCase):
            def test_only_expected_running_version_is_accepted(self):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    proc = base / 'proc'
                    proc.mkdir()
                    pid = proc / '123'
                    pid.mkdir()
                    old = base / 'bulletin-code-abcdefgh' / ('Expertbot-' + deploy.OLD_COMMIT)
                    worker = old / 'video_prototype' / 'worker.py'
                    worker.parent.mkdir(parents=True)
                    worker.write_text('')

                    def prop(key):
                        return {'ActiveState': 'active', 'SubState': 'running',
                                'User': 'user', 'MainPID': '123'}[key]

                    def argv(path):
                        (pid / 'cmdline').write_bytes((str(deploy.PYTHON) + '\0' + str(path) + '\0').encode())

                    with patch.object(deploy, 'BASE', base), patch.object(deploy, 'PROC', proc), \
                         patch.object(deploy, 'prop', side_effect=prop):
                        argv(worker)
                        self.assertEqual(deploy.discover_old(), old)
                        (pid / 'cmdline').write_bytes((str(deploy.PYTHON) + '\0-u\0' + str(worker) + '\0').encode())
                        self.assertEqual(deploy.discover_old(), old)
                        (pid / 'cmdline').write_bytes(
                            (str(deploy.PYTHON) + '\0' + str(worker) + '\0' + str(worker) + '\0').encode())
                        with self.assertRaises(deploy.SafetyError):
                            deploy.discover_old()
                        argv(base / 'unexpected' / 'video_prototype' / 'worker.py')
                        with self.assertRaises(deploy.SafetyError):
                            deploy.discover_old()
                        argv(worker)
                        worker.unlink()
                        worker.symlink_to('/etc/passwd')
                        with self.assertRaises(deploy.SafetyError):
                            deploy.discover_old()

            def test_download_verifies_checksum_and_size(self):
                content = b'synthetic archive'
                response = MagicMock()
                response.__enter__.return_value.read.side_effect = [content, b'']
                opener = MagicMock()
                opener.open.return_value = response
                with tempfile.TemporaryDirectory() as tmp, \
                     patch.object(deploy.urllib.request, 'build_opener', return_value=opener), \
                     patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(content).hexdigest()):
                    deploy.download_archive(Path(tmp))
                    self.assertEqual((Path(tmp) / 'source.tar.gz').read_bytes(), content)
                response.__enter__.return_value.read.side_effect = [content, b'']
                with tempfile.TemporaryDirectory() as tmp, \
                     patch.object(deploy.urllib.request, 'build_opener', return_value=opener), \
                     patch.object(deploy, 'ARCHIVE_SHA', 'bad'):
                    with self.assertRaises(deploy.SafetyError):
                        deploy.download_archive(Path(tmp))

            def test_reuses_only_matching_archive(self):
                blob = b'archive'
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    stage = base / 'bulletin-stage-abcdefgh'
                    stage.mkdir()
                    (stage / 'source.tar.gz').write_bytes(blob)
                    with patch.object(deploy, 'BASE', base), \
                         patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(blob).hexdigest()), \
                         patch.object(deploy, 'download_archive') as download:
                        self.assertEqual(deploy.available_stage(), stage)
                        download.assert_not_called()

        cases['DiscoveryTests'] = DiscoveryTests

    for case_name, case in cases.items():
        case.__name__ = f'{case_name}_{label}'
        case.__qualname__ = case.__name__
    return cases


for _spec in SPECS:
    for _case_name, _case in make_cases(_spec).items():
        globals()[_case.__name__] = _case

del _spec, _case_name, _case
