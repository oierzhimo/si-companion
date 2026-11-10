import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
import main
from local_napcat import ExistingNapCat


class CompanionUiTests(unittest.TestCase):
    def test_existing_connection_populates_only_local_endpoint_and_calls_connect(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            window=main.SiQQWindow()
            try:
                credential=ExistingNapCat(bot_id='12345',token='synthetic-ui-token')
                with mock.patch('local_napcat.existing_connection',return_value=credential), mock.patch.object(window,'_connect') as connect:
                    window._auto_connect_existing()
                self.assertEqual(window.endpoint.text(),'ws://127.0.0.1:3001/')
                self.assertEqual(window.token.text(),'synthetic-ui-token')
                connect.assert_called_once()
                self.assertNotIn('synthetic-ui-token',window.status_label.text())
                self.assertIsNone(window._data_lease)
                window.search_switch.setChecked(False)
                self.assertFalse(window._model_connection._search_enabled)
            finally:
                window.close();window.deleteLater();app.processEvents()


if __name__=='__main__':unittest.main()
