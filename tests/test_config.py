import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from overwatch.config import load_backend_env


class BackendConfigTests(unittest.TestCase):
    def test_loads_backend_values_without_overriding_process_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'GEMINI_API_KEY': ''}, clear=True):
            path = Path(temp) / '.env'
            path.write_text('# backend\nGEMINI_API_KEY=ignored-file-key\nGEMINI_MODEL="gemini-test"\nWATCHER_CRUSTDATA_TOKEN=fixture\nPATH=must-not-load\n', encoding='utf-8')
            load_backend_env(path)
            self.assertEqual(os.environ['GEMINI_API_KEY'], '')
            self.assertEqual(os.environ['GEMINI_MODEL'], 'gemini-test')
            self.assertEqual(os.environ['WATCHER_CRUSTDATA_TOKEN'], 'fixture')
            self.assertNotIn('PATH', os.environ)

    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            load_backend_env(Path(temp) / '.env')
            self.assertNotIn('GEMINI_API_KEY', os.environ)

    def test_values_are_literal_and_never_shell_expanded(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / '.env'
            path.write_text("GEMINI_API_KEY='$(do-not-execute)${HOME}'\n", encoding='utf-8')
            load_backend_env(path)
            self.assertEqual(os.environ['GEMINI_API_KEY'], '$(do-not-execute)${HOME}')

    def test_malformed_file_does_not_echo_secret_or_partially_load(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / '.env'
            path.write_text('GEMINI_MODEL=gemini-test\nGEMINI_API_KEY="secret-value\n', encoding='utf-8')
            with self.assertRaises(ValueError) as caught:
                load_backend_env(path)
            self.assertNotIn('secret-value', str(caught.exception))
            self.assertNotIn('GEMINI_MODEL', os.environ)
