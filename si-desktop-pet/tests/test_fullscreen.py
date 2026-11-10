import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from si_pet.native import covers_monitor,monitor_device_for_screen
from si_pet.controller import Controller
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.pet_window import PetWindow


class FullscreenTests(unittest.TestCase):
    def test_friendly_qt_name_maps_by_physical_origin(self):
        # Mixed-DPI Windows origins are not scaled by the screen's DPR.
        screen=SimpleNamespace(name=lambda:'Friendly Monitor',geometry=lambda:SimpleNamespace(x=lambda:-3840,y=lambda:0))
        with patch('si_pet.native.monitor_origins',return_value={'DISPLAY-A':(0,0),'DISPLAY-B':(-3840,0)}):
            self.assertEqual(monitor_device_for_screen(screen),'DISPLAY-B')
        with patch('si_pet.native.monitor_origins',return_value={}):
            self.assertIsNone(monitor_device_for_screen(screen))
        with patch('si_pet.native.monitor_origins',return_value={'A':(-3840,0),'B':(-3840,0)}):
            self.assertIsNone(monitor_device_for_screen(screen))

    def test_full_monitor_not_maximized_work_area(self):
        self.assertTrue(covers_monitor((-1920,0,0,1080),(-1920,0,0,1080)))
        self.assertTrue(covers_monitor((-1921,1,1,1079),(-1920,0,0,1080)))
        self.assertFalse(covers_monitor((0,0,1920,1040),(0,0,1920,1080)))
        self.assertFalse(covers_monitor((0,0,1900,1080),(0,0,1920,1080)))

    def test_same_monitor_suppresses_and_restore_respects_user_hide(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            ctrl=Controller(Store(Path(folder)/'settings.json'),Sprites())
            pet=PetWindow(ctrl,lambda:None);pet.timer.stop();pet.show()
            spoken=[];ctrl.speech.connect(spoken.append)
            screen=SimpleNamespace(name=lambda:'DISPLAY-1')
            with patch('si_pet.pet_window.QApplication.screenAt',return_value=screen), patch('si_pet.pet_window.native.monitor_device_for_screen',return_value='DISPLAY-1'), patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value='DISPLAY-1'):
                pet._fullscreen_check_at=0;pet.tick()
                self.assertTrue(ctrl.presentation_suppressed);self.assertFalse(pet.isVisible())
                ctrl.say('不能打断全屏',True);self.assertEqual(spoken,[])
            with patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value=None):
                pet._fullscreen_check_at=0;pet.tick()
                self.assertFalse(ctrl.presentation_suppressed);self.assertTrue(pet.isVisible())
            # A fullscreen application on another screen must not hide this pet.
            with patch('si_pet.pet_window.QApplication.screenAt',return_value=screen), patch('si_pet.pet_window.native.monitor_device_for_screen',return_value='DISPLAY-1'), patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value='DISPLAY-2'):
                pet._fullscreen_check_at=0;pet.tick();self.assertTrue(pet.isVisible())
            ctrl.hidden=True;ctrl.presentation_suppressed=True;pet.hide()
            with patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value=None):
                pet._fullscreen_check_at=0;pet.tick();self.assertFalse(pet.isVisible())
            # Switching off the option restores a temporarily suppressed pet.
            ctrl.hidden=False;ctrl.presentation_suppressed=True;ctrl.store['hide_fullscreen']=False
            with patch('si_pet.pet_window.native.foreground_fullscreen_monitor') as probe:
                pet._fullscreen_check_at=0;pet.tick();probe.assert_not_called()
                self.assertTrue(pet.isVisible())
            pet.hide();pet.bubble.hide();pet.deleteLater();app.processEvents()


if __name__=='__main__':unittest.main()
