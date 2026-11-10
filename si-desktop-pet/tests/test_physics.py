import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from si_pet.physics import Physics,Body,Platform,visible_segments

class PhysicsTests(unittest.TestCase):
    def make(self,body=None):
        p=Physics();p.set_platforms([Platform('floor',0,1920,1000,True)]);p.body=body or Body(x=300,y=100);return p
    def test_free_fall_accelerates(self):
        p=self.make()
        for _ in range(60):p.step(1/60)
        self.assertAlmostEqual(p.body.y,725,delta=1);self.assertAlmostEqual(p.body.vy,1250,delta=1)
    def test_large_velocity_does_not_tunnel(self):
        p=self.make(Body(x=300,y=950,vy=1800));p.step(.05)
        self.assertEqual(p.body.y,1000);self.assertEqual(p.body.support,'floor')
    def test_first_crossed_window_wins(self):
        p=self.make(Body(x=300,y=400,vy=1800));p.set_platforms([Platform('a',100,500,440),Platform('b',100,500,470)])
        p.step(.05);self.assertEqual(p.body.support,'a')
    def test_upward_passes_through_window(self):
        p=self.make(Body(x=300,y=500,vy=-400));p.set_platforms([Platform('w',100,500,490)])
        p.step(.05);self.assertIsNone(p.body.support);self.assertLess(p.body.y,490)
    def test_window_moves_with_pet(self):
        p=self.make(Body(x=300,y=400,support='w'));p.set_platforms([Platform('w',100,500,400)])
        p.set_platforms([Platform('w',150,550,450)]);self.assertEqual((p.body.x,p.body.y),(350,450))
    def test_window_support_survives_changed_visible_segment(self):
        p=Physics(Body(x=300,y=400,support='window:42:100'))
        p.set_platforms([Platform('window:42:100',200,450,400,origin_x=100)])
        p.set_platforms([Platform('window:42:140',240,470,400,origin_x=100)])
        self.assertEqual(p.body.support,'window:42:140')
        self.assertEqual((p.body.x,p.body.y),(300,400))
    def test_large_window_jump_releases_support(self):
        p=Physics(Body(x=300,y=400,support='window:42:0'))
        p.set_platforms([Platform('window:42:0',200,450,400,origin_x=100)])
        p.set_platforms([Platform('window:42:0',900,1150,400,origin_x=800)])
        self.assertIsNone(p.body.support)
    def test_removed_window_starts_fall(self):
        p=self.make(Body(x=300,y=400,support='w'));p.set_platforms([Platform('w',100,500,400)]);p.set_platforms([])
        p.step(.02);self.assertIsNone(p.body.support);self.assertGreater(p.body.y,400)
    def test_walks_off_window_edge(self):
        p=self.make(Body(x=499,y=400,support='w'));p.set_platforms([Platform('w',100,500,400)]);p.step(.05,100)
        self.assertIsNone(p.body.support);self.assertGreater(p.body.y,400)
    def test_drag_suspends_gravity(self):
        p=self.make(Body(x=300,y=400,dragging=True));p.step(.05);self.assertEqual(p.body.y,400)
    def test_throw_clamps_impulse(self):
        p=self.make();p.release(5000,-5000);self.assertEqual((p.body.vx,p.body.vy),(1100,-900))
    def test_jump_releases_support(self):
        p=self.make(Body(x=300,y=1000,support='floor'));self.assertTrue(p.jump());p.step(.02);self.assertLess(p.body.y,1000)
    def test_negative_monitor_coordinates(self):
        p=self.make(Body(x=-400,y=900,vy=1500));p.set_platforms([Platform('left',-1920,0,950,True)]);p.step(.05)
        self.assertEqual(p.body.support,'left')
    def test_occluded_platform_segments(self):
        self.assertEqual(visible_segments(0,1000,[(200,400),(650,900)]),[(0,200),(400,650),(900,1000)])
    def test_time_step_consistency(self):
        a=self.make();b=self.make()
        for _ in range(30):a.step(1/30)
        for _ in range(60):b.step(1/60)
        self.assertAlmostEqual(a.body.y,b.body.y,places=5)
    def test_15fps_retains_real_time_gravity(self):
        a=self.make();b=self.make()
        for _ in range(15):a.step(1/15)
        for _ in range(60):b.step(1/60)
        self.assertAlmostEqual(a.body.y,b.body.y,places=5)

if __name__=='__main__':unittest.main()
