import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
import main,model_connection as mc

class RouterUiTests(unittest.TestCase):
    def test_explicit_per_run_switch_and_local_effective_label(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            window=main.SiQQWindow()
            try:
                self.assertFalse(window.router_switch.isChecked())
                self.assertFalse(window._model_connection._router_config.enabled)
                ok,_=window._apply_model_connection(mc.LOCAL_PROVIDER,'http://127.0.0.1:11434/v1','si-qwen3.5:9b','')
                self.assertTrue(ok)
                window.router_switch.setChecked(True)
                self.assertTrue(window._model_connection._router_config.enabled)
                self.assertIn('本地分流',window.model_connection_status.text())
                self.assertEqual(window._model_connection._router_config.vision_model,'si-qwen3.5:9b')
                window.legacy_vision_switch.setChecked(True)
                self.assertEqual(window._model_connection._router_config.vision_model,'si-qwen2.5vl:7b')
                self.assertIn('Qwen2.5VL',window.model_connection_status.text())
                self.assertIsNone(window._adapter)
                window.router_switch.setChecked(False)
                self.assertFalse(window._model_connection._router_config.enabled)
                self.assertFalse(window.legacy_vision_switch.isEnabled())
                self.assertIn('si-qwen3.5',window.model_connection_status.text())
            finally:window.close();window.deleteLater();app.processEvents()

    def test_remote_provider_cannot_be_redirected_by_switch(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LOCALAPPDATA':directory}):
            window=main.SiQQWindow()
            try:
                window.router_switch.setChecked(True)
                self.assertIn('当前未启用',window.model_connection_status.text())
                self.assertIsNone(window._adapter)
            finally:window.close();window.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
