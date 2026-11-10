import json
from pathlib import Path
import tempfile
import threading
import unittest
from local_resources import resource_stage, ResourceBusy


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=Path(self.temp.name)/'server.json'
        self.config.write_text(json.dumps({'host':'127.0.0.1','port':17860,'models':[{'id':'owned-model'}]}))
        self.calls=[]

    def request(self,port,route,body=None):
        self.calls.append((port,route,body))
        if route=='queue':return {'queue_running':[],'queue_pending':[]}
        if route=='v1/models':return {'data':[{'id':'owned-model'}]}
        if route=='api/ps':return {'models':[{'name':'si-qwen3.5:9b'},{'name':'other-project'}]}
        return {}

    def test_text_stage_releases_audio_and_drawing_cache_but_not_text_model(self):
        with resource_stage('text',request=self.request,audio_config=self.config):pass
        self.assertIn((17860,'v1/tasks/unload_all_models',{}),self.calls)
        self.assertFalse(any(c[1]=='api/generate' for c in self.calls))

    def test_voice_stage_keeps_own_voice_model_and_only_releases_companion_alias(self):
        with resource_stage('voice',request=self.request,audio_config=self.config):pass
        self.assertFalse(any(c[0]==17860 for c in self.calls))
        unloads=[c[2]['model'] for c in self.calls if c[1]=='api/generate']
        self.assertEqual(unloads,['si-qwen3.5:9b'])

    def test_active_external_image_job_is_not_interrupted(self):
        def request(port,route,body=None):
            self.calls.append((port,route,body));return {'queue_running':[[0,'other-job']]}
        with self.assertRaises(ResourceBusy):
            with resource_stage('text',request=request,audio_config=self.config):self.fail('must not start model')
        self.assertEqual(len(self.calls),1)

    def test_unrelated_audio_listener_is_never_unloaded(self):
        def request(port,route,body=None):
            if route=='v1/models':
                self.calls.append((port,route,body));return {'data':[{'id':'not-owned'}]}
            return self.request(port,route,body)
        with resource_stage('image',request=request,audio_config=self.config):pass
        self.assertFalse(any(c[1]=='v1/tasks/unload_all_models' for c in self.calls))

    def test_lock_prevents_two_companion_model_jobs_running_together(self):
        entered=threading.Event();release=threading.Event();second=threading.Event()
        def first():
            with resource_stage('voice',request=self.request,audio_config=self.config):
                entered.set();release.wait(2)
        def next_job():
            with resource_stage('image',request=self.request,audio_config=self.config):second.set()
        a=threading.Thread(target=first);b=threading.Thread(target=next_job)
        a.start();self.assertTrue(entered.wait(1));b.start()
        self.assertFalse(second.wait(.05));release.set();a.join(2);b.join(2)
        self.assertTrue(second.is_set())
