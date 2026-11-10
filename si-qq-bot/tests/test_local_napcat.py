import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_napcat import existing_connection


class LocalNapcatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.data=self.root/'data';self.config=self.root/'config'
        self.data.mkdir();self.config.mkdir()
        self.state={'app_id':'12345','admin_openid':'67890'}
        self.server={'enable':True,'host':'127.0.0.1','port':3001,'token':'synthetic-secret'}
        self.save()
    def save(self):
        (self.data/'si_qq_core_state.json').write_text(json.dumps(self.state))
        (self.config/'onebot11_12345.json').write_text(json.dumps({'network':{'websocketServers':[self.server]}}))
    def test_reads_only_bound_account_loopback_and_excludes_token_from_repr(self):
        (self.config/'onebot11_67890.json').write_text('not valid and must never be read')
        result=existing_connection(self.data,config_dir=self.config)
        self.assertEqual(result.bot_id,'12345');self.assertEqual(result.token,'synthetic-secret')
        self.assertNotIn('synthetic-secret',repr(result))
    def test_no_binding_never_chooses_another_account(self):
        self.state['admin_openid']=None;self.save()
        with self.assertRaises(ValueError):existing_connection(self.data,config_dir=self.config)
    def test_external_or_unauthed_endpoint_is_rejected(self):
        for change in ({'host':'0.0.0.0'},{'host':'remote.example'},{'port':3002},{'token':''},{'path':'/other'},{'enable':False}):
            with self.subTest(change=change):
                base=dict(self.server);self.server.update(change);self.save()
                with self.assertRaises(ValueError):existing_connection(self.data,config_dir=self.config)
                self.server=base
    def test_malformed_config_does_not_modify_any_file(self):
        path=self.config/'onebot11_12345.json';path.write_bytes(b'{bad')
        with self.assertRaises(ValueError):existing_connection(self.data,config_dir=self.config)
        self.assertEqual(path.read_bytes(),b'{bad')


if __name__=='__main__':unittest.main()
