"""Visibility pauses motion, while focus and durable clocks keep running."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from si_pet.controller import Controller
from si_pet.physics import Platform
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.pet_window import PetWindow


class VisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=patch('si_pet.controller.time.monotonic',return_value=1000)
        self.now=self.clock.start();self.addCleanup(self.clock.stop)
        self.store=Store(Path(self.tmp.name)/'settings.json')
        self.store['window_edges']=False;self.store['auto_dock']=False
        self.ctrl=Controller(self.store,Sprites())
        self.ctrl.physics.set_platforms([Platform('floor:fixed',0,2000,700,True)])
        b=self.ctrl.physics.body
        b.x=400;b.y=700;b.support='floor:fixed';b.vx=65;b.vy=0
        self.ctrl.action='walk';self.ctrl.decision_at=999
        self.ctrl.next_check_in=10000

    def test_hidden_and_fullscreen_bodies_do_not_wander(self):
        for index,flag in enumerate(('hidden','presentation_suppressed')):
            with self.subTest(flag=flag):
                self.now.return_value=1000+index*2
                self.ctrl.hidden=False;self.ctrl.presentation_suppressed=False
                self.ctrl.update(0)
                b=self.ctrl.physics.body
                b.x=400;b.y=700;b.vx=65;b.vy=0;b.support='floor:fixed'
                self.ctrl.action='walk'
                setattr(self.ctrl,flag,True)
                before=(b.x,b.y)
                for step in range(60):
                    self.now.return_value=1000+index*2+step/60
                    self.ctrl.update(1/60)
                self.assertEqual((b.x,b.y),before)
                self.assertEqual((b.vx,b.vy),(0,0))
                setattr(self.ctrl,flag,False)

    def test_focus_completes_and_time_is_saved_while_hidden(self):
        self.ctrl.focus(1);self.ctrl.hidden=True
        spoken=[];self.ctrl.speech.connect(spoken.append)
        before=(self.ctrl.physics.body.x,self.ctrl.physics.body.y)
        self.now.return_value=1061;self.ctrl.update(.1)
        self.assertEqual(self.store['focus_sessions'],1)
        self.assertEqual(self.store['total_seconds'],61)
        self.assertEqual(spoken,[])
        self.assertEqual((self.ctrl.physics.body.x,self.ctrl.physics.body.y),before)
        self.ctrl.update(.1)
        self.assertEqual(self.store['focus_sessions'],1)

    def test_restoring_visibility_settles_before_autonomous_walk(self):
        self.ctrl.hidden=True;self.ctrl.update(.016)
        self.now.return_value=1100;self.ctrl.hidden=False
        self.ctrl.update(.016)
        self.assertEqual(self.ctrl.action,'idle')
        self.assertGreaterEqual(self.ctrl.decision_at,1103)
        self.assertEqual(self.ctrl.physics.body.x,400)

    def test_manual_hide_cancels_drag_and_preserves_manual_hide(self):
        pet=PetWindow(self.ctrl,lambda:None);pet.timer.stop()
        self.addCleanup(pet.close);self.addCleanup(pet.bubble.close)
        self.ctrl.physics.body.dragging=True;pet._drag=True;pet._press=QPoint(10,10)
        pet.hide_pet()
        self.assertTrue(self.ctrl.hidden)
        self.assertFalse(self.ctrl.physics.body.dragging)
        self.assertFalse(pet._drag);self.assertIsNone(pet._press)

    def test_fullscreen_mid_drag_does_not_leave_drag_stuck(self):
        pet=PetWindow(self.ctrl,lambda:None);pet.timer.stop()
        self.addCleanup(pet.close);self.addCleanup(pet.bubble.close)
        self.ctrl.physics.body.dragging=True;pet._drag=True;pet._press=QPoint(10,10)
        screen=SimpleNamespace(name=lambda:'DISPLAY-1')
        with patch('si_pet.pet_window.QApplication.screenAt',return_value=screen),patch('si_pet.pet_window.native.monitor_device_for_screen',return_value='DISPLAY-1'),patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value='DISPLAY-1'):
            pet.tick()
        self.assertTrue(self.ctrl.presentation_suppressed)
        self.assertFalse(self.ctrl.physics.body.dragging)
        self.assertFalse(pet._drag);self.assertIsNone(pet._press)
        pet._fullscreen_check_at=0
        with patch('si_pet.pet_window.native.foreground_fullscreen_monitor',return_value=None):
            pet.tick()
        self.assertFalse(self.ctrl.presentation_suppressed)
        self.assertFalse(self.ctrl.physics.body.dragging)
