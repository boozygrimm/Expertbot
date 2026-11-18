import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
from video_prototype import deploy_bulletin_cards as deploy


class ExtractTests(unittest.TestCase):
    def make_archive(self, members):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            for name, data, kind in members:
                info = tarfile.TarInfo(name); info.type = kind
                if kind == tarfile.REGTYPE: info.size = len(data); tar.addfile(info, io.BytesIO(data))
                else: info.linkname = '/etc/passwd'; tar.addfile(info)
        return buf.getvalue()

    def test_verified_archive_extracts_and_invalid_paths_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); stage = base/'bulletin-stage-AbCd1234'; stage.mkdir()
            dest = base/'code'; dest.mkdir()
            root = 'Expertbot-' + deploy.COMMIT
            for name, kind, success in [(f'{root}/video_prototype/worker.py', tarfile.REGTYPE, True),
                                        (f'{root}/../escape', tarfile.REGTYPE, False),
                                        (f'{root}/link', tarfile.SYMTYPE, False)]:
                blob = self.make_archive([(name,b'print(1)',kind)])
                (stage/'source.tar.gz').write_bytes(blob)
                with patch.object(deploy, 'BASE', base), patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(blob).hexdigest()):
                    if success:
                        code = deploy.extract_source(stage,dest)
                        self.assertEqual((code/'video_prototype/worker.py').read_bytes(), b'print(1)')
                    else:
                        with self.assertRaises(deploy.SafetyError): deploy.extract_source(stage,dest)
            (stage/'source.tar.gz').write_bytes(b'wrong')
            with patch.object(deploy, 'BASE', base), self.assertRaises(deploy.SafetyError):
                deploy.extract_source(stage,dest)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.base = self.root/'data'; self.base.mkdir()
        self.system = self.root/'etc'; self.system.mkdir()
        self.smoke = self.system/'smoke.conf'
        self.old_code = self.base/'bulletin-code-abcdefgh'/('Expertbot-' + deploy.OLD_COMMIT)
        self.old = ('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '
                    + str(self.old_code/'video_prototype/worker.py') + ' --port 8088\n').encode()
        self.smoke.write_bytes(self.old)
        self.code = self.base/'prepared-code'; self.code.mkdir()
        self.patches = [patch.object(deploy, 'BASE', self.base), patch.object(deploy, 'SMOKE', self.smoke),
                        patch.object(deploy.os, 'geteuid', return_value=0),
                        patch.object(deploy, 'process_snapshot', return_value=(123,8088,'fake-secret')),
                        patch.object(deploy, 'quiet'), patch.object(deploy, 'extract_source', return_value=self.code),
                        patch.object(deploy, 'discover_old', return_value=self.old_code)]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.cmds = []
        def command(*args, **kwargs): self.cmds.append(args); return ''
        self.cp = patch.object(deploy, 'command', side_effect=command); self.cp.start(); self.addCleanup(self.cp.stop)
        def prop(name):
            if name == 'DropInPaths': return ' '.join(str(self.system/n) for n in deploy.EXPECTED_DROPINS)
            if name == 'ActiveState': return 'active'
            if name == 'MainPID': return '456'
            raise AssertionError(name)
        self.pp = patch.object(deploy, 'prop', side_effect=prop); self.pp.start(); self.addCleanup(self.pp.stop)
        caps = {'animated-sign-v1','voice-notes-v1','news-story-v1'}
        self.hp = patch.object(deploy, 'health', side_effect=[caps, caps, caps | deploy.REQUIRED_NEW])
        self.hp.start(); self.addCleanup(self.hp.stop)

    def test_success_switches_only_worker_path_and_saves_private_backup(self):
        deploy.deploy(self.base/'stage')
        self.assertEqual(self.smoke.read_bytes(), self.old.replace(str(self.old_code/'video_prototype/worker.py').encode(),
                                                                 str(self.code/'video_prototype/worker.py').encode()))
        backups = list(self.base.glob('bulletin-backup-*/smoke.conf'))
        self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),self.old)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cmds.count(('systemctl','restart',deploy.UNIT)), 1)

    def test_no_multi_video_capability_is_not_reported_as_success(self):
        old_caps = {'animated-sign-v1', 'voice-notes-v1', 'news-story-v1'}
        missing = (old_caps | deploy.REQUIRED_NEW) - {'news-bulletin-multi-video-v1'}
        with patch.object(deploy, 'health', side_effect=[old_caps, old_caps, missing]), \
             patch.object(deploy.time, 'monotonic', side_effect=[0, 0, 61]), \
             patch.object(deploy.time, 'sleep'):
            with self.assertRaisesRegex(deploy.SafetyError, 'health'):
                deploy.deploy(self.base/'stage')
        self.assertEqual(self.cmds.count(('systemctl', 'restart', deploy.UNIT)), 1)
        self.assertIn(str(self.code).encode(), self.smoke.read_bytes())

    def test_pins_only_the_inspected_previous_and_new_versions(self):
        self.assertEqual(deploy.COMMIT, '0c2881c6931357ce9e8b96129b52a69dd4b2de77')
        self.assertEqual(deploy.ARCHIVE_SHA, '747ed1449128bc58988d0b4d25155166540e2e7b0ead0733f627b01d2a25dacb')
        self.assertEqual(deploy.OLD_COMMIT, '2ed3e72b98b1dd7e1cf4f2f0580945fe643c8751')

    def test_uncertain_restart_never_triggers_second_restart_or_rollback(self):
        def failing(*args, **kwargs):
            self.cmds.append(args)
            if args == ('systemctl','restart',deploy.UNIT): raise subprocess.TimeoutExpired(args, 90)
            return ''
        with patch.object(deploy,'command',side_effect=failing):
            with self.assertRaises(subprocess.TimeoutExpired): deploy.deploy(self.base/'stage')
        self.assertIn(str(self.code).encode(), self.smoke.read_bytes())
        self.assertEqual(self.cmds.count(('systemctl','restart',deploy.UNIT)),1)
        self.assertEqual(len(list(self.base.glob('bulletin-backup-*/smoke.conf'))),1)

    def test_failure_before_restart_restores_config_without_restart(self):
        def fail_once(*args, **kwargs):
            self.cmds.append(args)
            if args == ('systemctl','daemon-reload') and self.cmds.count(args) == 1: raise OSError()
            return ''
        with patch.object(deploy, 'command', side_effect=fail_once):
            with self.assertRaises(OSError): deploy.deploy(self.base/'stage')
        self.assertEqual(self.smoke.read_bytes(),self.old)
        self.assertNotIn(('systemctl','restart',deploy.UNIT),self.cmds)

    def test_changed_config_and_live_child_abort_before_write(self):
        with patch.object(deploy, 'quiet', side_effect=deploy.SafetyError('busy')):
            with self.assertRaises(deploy.SafetyError): deploy.deploy(self.base/'stage')
        self.assertEqual(self.smoke.read_bytes(),self.old)
        self.assertEqual(self.cmds,[])
        with patch.object(deploy, 'OLD', self.old_code), self.assertRaises(deploy.SafetyError):
            deploy.changed_smoke(self.old+self.old,self.code)


class QuietTests(unittest.TestCase):
    def test_unresolved_news_recent_jobs_and_processes_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); proc=base/'proc'; proc.mkdir(); jobs=base/'jobs'; jobs.mkdir(); news=base/'news'; news.mkdir()
            pid=100; worker=proc/str(pid); worker.mkdir()
            (worker/'status').write_text('Name:\tpython\nPPid:\t1\n')
            (worker/'cmdline').write_bytes(b'python\0worker\0')
            with patch.object(deploy, 'PROC',proc), patch.object(deploy,'JOBS',jobs), \
                 patch.object(deploy,'NEWS_ROOT',news), patch.object(deploy,'prop',return_value=str(pid)):
                deploy.quiet(pid)
                child=proc/'101'; child.mkdir(); (child/'status').write_text('PPid:\t100\n')
                with self.assertRaises(deploy.SafetyError): deploy.quiet(pid)
                (child/'status').write_text('PPid:\t1\n'); (child/'cmdline').write_bytes(b'other\0')
                folder=jobs/str(uuid.uuid4()); folder.mkdir()
                with self.assertRaises(deploy.SafetyError): deploy.quiet(pid)
                os.utime(folder,(time.time()-500,time.time()-500)); deploy.quiet(pid)
                log=folder/'worker.log'; log.write_text('log')
                old=time.time()-31*24*60*60; os.utime(log,(old,old)); os.utime(folder,(old,old))
                deploy.quiet(pid)  # Normal retention cleanup is permitted.
                now=time.time()-500; os.utime(log,(now,now)); os.utime(folder,(now,now)); deploy.quiet(pid)
                folder=news/str(uuid.uuid4()); folder.mkdir()
                with self.assertRaises(deploy.SafetyError): deploy.quiet(pid)
                (folder/'failed').write_text('failed'); deploy.quiet(pid)


class DiscoveryTests(unittest.TestCase):
    def test_only_expected_running_version_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); proc = base/'proc'; proc.mkdir(); pid = proc/'123'; pid.mkdir()
            old = base/'bulletin-code-abcdefgh'/('Expertbot-' + deploy.OLD_COMMIT)
            worker = old/'video_prototype'/'worker.py'; worker.parent.mkdir(parents=True); worker.write_text('')
            def prop(key):
                return {'ActiveState': 'active', 'SubState': 'running', 'User': 'user', 'MainPID': '123'}[key]
            def argv(path):
                (pid/'cmdline').write_bytes((str(deploy.PYTHON)+'\0'+str(path)+'\0').encode())
            with patch.object(deploy, 'BASE', base), patch.object(deploy, 'PROC', proc), patch.object(deploy, 'prop', side_effect=prop):
                argv(worker)
                self.assertEqual(deploy.discover_old(), old)
                (pid/'cmdline').write_bytes((str(deploy.PYTHON)+'\0-u\0'+str(worker)+'\0').encode())
                self.assertEqual(deploy.discover_old(), old)
                (pid/'cmdline').write_bytes((str(deploy.PYTHON)+'\0'+str(worker)+'\0'+str(worker)+'\0').encode())
                with self.assertRaises(deploy.SafetyError): deploy.discover_old()
                argv(base/'unexpected'/'video_prototype'/'worker.py')
                with self.assertRaises(deploy.SafetyError): deploy.discover_old()
                argv(worker); worker.unlink(); worker.symlink_to('/etc/passwd')
                with self.assertRaises(deploy.SafetyError): deploy.discover_old()

    def test_download_verifies_checksum_and_size(self):
        from unittest.mock import MagicMock
        content = b'synthetic archive'
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [content, b'']
        opener = MagicMock(); opener.open.return_value = response
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(deploy.urllib.request, 'build_opener', return_value=opener), \
             patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(content).hexdigest()):
            deploy.download_archive(Path(tmp))
            self.assertEqual((Path(tmp)/'source.tar.gz').read_bytes(), content)
        response.__enter__.return_value.read.side_effect = [content, b'']
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(deploy.urllib.request, 'build_opener', return_value=opener), \
             patch.object(deploy, 'ARCHIVE_SHA', 'bad'):
            with self.assertRaises(deploy.SafetyError): deploy.download_archive(Path(tmp))


    def test_reuses_only_matching_archive(self):
        blob = b'archive'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); stage = base/'bulletin-stage-abcdefgh'; stage.mkdir()
            (stage/'source.tar.gz').write_bytes(blob)
            with patch.object(deploy, 'BASE', base), \
                 patch.object(deploy, 'ARCHIVE_SHA', hashlib.sha256(blob).hexdigest()), \
                 patch.object(deploy, 'download_archive') as download:
                self.assertEqual(deploy.available_stage(), stage)
                download.assert_not_called()
