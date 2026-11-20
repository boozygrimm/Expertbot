"""Launch legacy handler and experimental production tests without real secrets."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class OfflineProductionTests(unittest.TestCase):
    def run_fixture(self, module):
        root = Path(__file__).resolve().parent.parent
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': os.environ.get('HOME', '/tmp'),
               'PYTHONPATH': str(root), 'PYTHONIOENCODING': 'utf-8', 'PYTHON_DOTENV_DISABLED': '1',
               'MONGO_URI': 'mongodb://127.0.0.1:27017', 'TELEGRAM_TOKEN': '123456:FAKE_TEST_TOKEN'}
        result = subprocess.run([sys.executable, '-m', module], cwd=root, env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scheduler_parity_on_mocked_io(self):
        self.run_fixture('tests.production_characterization_fixture')

    def test_coordinated_production_and_craft(self):
        self.run_fixture('tests.isolated_production_fixture')

    def test_sandbox_start_remove_and_tables_match_python_bot(self):
        self.run_fixture('tests.sandbox_production_parity_fixture')


if __name__ == '__main__':
    unittest.main()
