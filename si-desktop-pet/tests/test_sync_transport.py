import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from si_pet.storage import Store
from si_pet.sync_transport import PeerRegistry,LanServer,TransportError,pair,exchange,local_address


class LanTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.a=Store(self.root/'a'/'settings.json')
        self.b=Store(self.root/'b'/'settings.json')
        self.a.enable_sync_preview()
        self.b.enable_sync_preview()
        self.registry=PeerRegistry(self.root/'host',self.b.sync_ledger.device_id,'电脑 B')
        def receive(data):
            self.b.import_sync_bundle(data)
            return self.b.sync_ledger.export()
        self.server=LanServer(self.registry,receive)
        self.invitation=self.server.invitation('127.0.0.1')

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def paired(self):
        return pair(self.invitation,self.a.sync_ledger.device_id,'电脑 A')

    def test_real_tls_exchange_converges_and_duplicate_retry_does_not_count_twice(self):
        peer=self.paired()
        self.a.record_sync('pat')
        self.a.log('管理员，先在这里坐一会儿。')
        self.b.record_sync('snack')
        result=exchange(peer,self.a.sync_ledger.device_id,self.a.sync_ledger.export())
        self.assertEqual(self.a.import_sync_bundle(result),1)
        self.assertEqual(self.a.sync_ledger.summary(),self.b.sync_ledger.summary())
        self.assertEqual(self.a['scale'],2.0)
        self.assertEqual(self.a.import_sync_bundle(exchange(peer,self.a.sync_ledger.device_id,
                                                         self.a.sync_ledger.export())),0)
        self.assertEqual(self.b['pats'],1)
        self.assertEqual(Store(self.b.path).enable_sync_preview().summary(),self.a.sync_ledger.summary())

    def test_invitation_is_single_use_and_expires(self):
        self.paired()
        with self.assertRaises(TransportError):
            pair(self.invitation,'another-device','另一台电脑')
        expired=self.server.invitation('127.0.0.1',lifetime=-1)
        with self.assertRaisesRegex(TransportError,'失效'):
            pair(expired,'another-device','另一台电脑')

    def test_certificate_mismatch_fails_before_consuming_invitation(self):
        wrong=json.loads(self.invitation)
        wrong['fingerprint']='0'*64
        with self.assertRaisesRegex(TransportError,'证书'):
            pair(json.dumps(wrong),'another-device','另一台电脑')
        self.assertIsNotNone(self.registry.invitation)
        self.assertTrue(self.paired()['key'])

    def test_revocation_denies_old_key_without_modifying_events(self):
        peer=self.paired()
        self.registry.revoke(self.a.sync_ledger.device_id)
        self.a.record_sync('pat')
        old=self.b.sync_ledger.export()
        with self.assertRaisesRegex(TransportError,'失效'):
            exchange(peer,self.a.sync_ledger.device_id,self.a.sync_ledger.export())
        self.assertEqual(self.b.sync_ledger.export(),old)
        reloaded=PeerRegistry(self.root/'host',self.b.sync_ledger.device_id,'电脑 B')
        self.assertFalse(reloaded.authorized(self.a.sync_ledger.device_id,peer['key']))

    def test_conflicting_migration_baseline_is_rejected_atomically(self):
        peer=self.paired()
        # A blank installation has no migration baseline. Give the receiving
        # PC its own nonempty legacy save so this remains a true conflict.
        self.b.sync_ledger.seed_legacy({'pats':2,'snacks':0,'focus_sessions':0,
                                        'affinity':4,'history':[]})
        other=Store(self.root/'other.json')
        other['pats']=4
        other.enable_sync_preview()
        payload=json.loads(other.sync_ledger.export())
        # A paired client may send a syntactically valid but incompatible baseline.
        from si_pet.sync_v1 import canonical
        import hashlib
        payload.pop('sha256')
        payload['source_device']=self.a.sync_ledger.device_id
        payload['sha256']=hashlib.sha256(canonical(payload)).hexdigest()
        before=self.b.sync_ledger.export()
        with self.assertRaisesRegex(TransportError,'legacy baselines'):
            exchange(peer,self.a.sync_ledger.device_id,canonical(payload))
        self.assertEqual(before,self.b.sync_ledger.export())

    def test_external_and_non_numeric_addresses_are_not_accepted(self):
        for address in ('8.8.8.8','example.com','0.0.0.0','224.0.0.1','203.0.113.1'):
            with self.assertRaises(TransportError):
                local_address(address)


if __name__=='__main__':
    unittest.main()
