import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from si_pet.storage import Store
from si_pet.sync_v1 import SyncError,legacy_event
TEST_TEMP=Path(__file__).resolve().parents[1]/'qa'/'tmp'
TEST_TEMP.mkdir(parents=True,exist_ok=True)

class StorageTests(unittest.TestCase):
    def test_roundtrip_portable_state(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json';s=Store(path);s['affinity']=7;s.log('一起坐了一会儿');s.save()
            loaded=Store(path);self.assertEqual(loaded['affinity'],7);self.assertEqual(loaded['history'][0]['text'],'一起坐了一会儿')
    def test_corrupt_settings_recover(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            p=Path(d)/'settings.json';p.write_text('{broken',encoding='utf-8');self.assertEqual(Store(p)['scale'],2)
    def test_invalid_values_clamped(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            p=Path(d)/'settings.json';p.write_text(json.dumps({'scale':100,'gravity':-1,'fps':'invalid','history':None,'manual':'false'}))
            s=Store(p);self.assertEqual(s['scale'],3);self.assertEqual(s['gravity'],100);self.assertEqual(s['fps'],60);self.assertFalse(s['manual'])

    def test_opt_in_ledger_preserves_legacy_and_records_interactions(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            old=Store(path);old['pats']=2;old['affinity']=2;old.save()
            old_bytes=path.read_bytes()
            store=Store(path);ledger=store.enable_sync_preview()
            self.assertEqual(path.with_suffix('.pre-sync.json').read_bytes(),old_bytes)
            self.assertEqual(path.read_bytes(),old_bytes)
            store.record_sync('pat');store.log('管理员来了');store.save()
            self.assertEqual(store['pats'],3)
            self.assertEqual(store['affinity'],3)
            self.assertEqual(ledger.summary()['pats'],3)
            self.assertEqual(len(ledger.summary()['journal']),1)
            self.assertEqual(Store(path).enable_sync_preview().summary(),ledger.summary())

    def test_opt_in_detects_legacy_app_changes_after_ledger(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            store=Store(path);store.save();store.enable_sync_preview()
            older_app=Store(path);older_app['pats']=1;older_app.save()
            with self.assertRaisesRegex(SyncError,'ahead of the sync ledger'):
                Store(path).enable_sync_preview()

    def test_durable_event_repairs_stale_legacy_projection(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            store=Store(path);store.save();ledger=store.enable_sync_preview()
            ledger.add('pat')  # power loss before settings.json was updated
            recovered=Store(path)
            recovered.enable_sync_preview()
            self.assertEqual(recovered['pats'],1)
            self.assertEqual(Store(path)['pats'],1)

    def test_fresh_blank_install_persists_empty_ledger_with_device_identity(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            store=Store(path)
            ledger=store.enable_sync_preview()
            sidecar=path.with_suffix('.sync-v1.json')
            self.assertTrue(sidecar.exists())
            document=json.loads(sidecar.read_text(encoding='utf-8'))
            self.assertEqual(document['events'],[])
            self.assertEqual(document['device_id'],ledger.device_id)
            self.assertEqual([event for event in ledger.events.values()
                              if event['kind']=='legacy_snapshot'],[])
            self.assertEqual(ledger.summary()['pats'],0)
            # A power cycle must reuse the persisted identity, not mint a new one.
            restored=Store(path).enable_sync_preview()
            self.assertEqual(restored.device_id,ledger.device_id)
            self.assertEqual([event for event in restored.events.values()
                              if event['kind']=='legacy_snapshot'],[])

    def test_zero_value_settings_from_first_exit_still_gets_empty_ledger(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            Store(path).save()  # A normal desktop pet writes these defaults on first exit.
            store=Store(path)
            ledger=store.enable_sync_preview()
            self.assertEqual([event for event in ledger.events.values()
                              if event['kind']=='legacy_snapshot'],[])
            self.assertTrue(path.with_suffix('.pre-sync.json').exists())

    def test_zero_counters_with_legacy_history_keep_stable_migration_event(self):
        original={'pats':0,'snacks':0,'focus_sessions':0,'affinity':0,
                  'history':[{'at':'10-25 09:00','text':'只有手记'}]}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps(original),encoding='utf-8')
            ledger=Store(path).enable_sync_preview()
            baselines=[event for event in ledger.events.values()
                       if event['kind']=='legacy_snapshot']
            self.assertEqual(len(baselines),1)
            expected=legacy_event(original)
            self.assertEqual(baselines[0]['event_id'],expected['event_id'])
            self.assertEqual(baselines[0]['payload']['history'],
                             [{'at':'10-25 09:00','text':'只有手记'}])

    def test_blank_desktop_accepts_existing_pc_nonzero_baseline(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            old_path=Path(d)/'old'/'settings.json'
            old=Store(old_path)
            old['pats']=3;old['snacks']=1;old['focus_sessions']=2;old['affinity']=16
            old['history']=[{'at':'10-25 09:00','text':'旧电脑上的手记'}]
            old.save();old.enable_sync_preview();old.record_sync('snack')
            new=Store(Path(d)/'new'/'settings.json')
            new.enable_sync_preview()
            self.assertEqual(new.import_sync_bundle(old.sync_ledger.export()),2)
            self.assertEqual(new.sync_ledger.summary(),old.sync_ledger.summary())
            self.assertEqual((new['pats'],new['snacks'],new['focus_sessions'],new['affinity']),
                             (3,2,2,19))
            self.assertEqual(old.import_sync_bundle(new.sync_ledger.export()),0)

    def test_two_diverged_nonzero_saves_are_still_rejected_without_merge(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            a_path=Path(d)/'a-settings.json';b_path=Path(d)/'b-settings.json'
            a=Store(a_path);a['pats']=2;a['affinity']=2;a.save();a.enable_sync_preview()
            b=Store(b_path);b['pats']=3;b['affinity']=3;b.save();b.enable_sync_preview()
            with self.assertRaisesRegex(SyncError,'legacy baselines'):
                a.import_sync_bundle(b.sync_ledger.export())
            self.assertEqual((a['pats'],a['affinity']),(2,2))
            self.assertEqual(len(a.sync_ledger.events),1)

    def test_zero_counters_non_dict_history_rejected_without_sidecar(self):
        original={'pats':0,'snacks':0,'focus_sessions':0,'affinity':0,
                  'history':['旧版手记不是字典']}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            store=Store(path)
            with self.assertRaisesRegex(SyncError,'cannot be preserved exactly'):
                store.enable_sync_preview()
            # A refusal must not rewrite the save or leave any migration state.
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(path.with_suffix('.sync-v1.json').exists())
            self.assertFalse(path.with_suffix('.pre-sync.json').exists())
            self.assertIsNone(store.sync_ledger)
            # Without an enabled ledger no peer bundle can project over the
            # local journal either.
            with self.assertRaisesRegex(SyncError,'not enabled'):
                store.import_sync_bundle(b'{}')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(Store(path)['history'],original['history'])

    def test_nonzero_legacy_save_with_non_dict_history_is_not_silently_stripped(self):
        original={'pats':3,'snacks':1,'focus_sessions':0,'affinity':5,
                  'history':['旧版手记不是字典']}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            with self.assertRaisesRegex(SyncError,'cannot be preserved exactly'):
                Store(path).enable_sync_preview()
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(path.with_suffix('.sync-v1.json').exists())
            self.assertFalse(path.with_suffix('.pre-sync.json').exists())

    def test_existing_empty_ledger_rejects_invalid_history_without_overwrite(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            Store(path).enable_sync_preview()  # a5-style empty ledger
            sidecar=path.with_suffix('.sync-v1.json')
            sidecar_before=sidecar.read_bytes()
            # An older app writes a non-dict journal after the sidecar exists.
            path.write_text(json.dumps({'pats':0,'snacks':0,'focus_sessions':0,
                                        'affinity':0,'history':['旧手记']},
                                       ensure_ascii=False),encoding='utf-8')
            settings_before=path.read_bytes()
            with self.assertRaisesRegex(SyncError,'history differs'):
                Store(path).enable_sync_preview()
            self.assertEqual(sidecar.read_bytes(),sidecar_before)
            self.assertEqual(path.read_bytes(),settings_before)

    def test_damaged_dict_history_rejected_without_writes(self):
        broken_rows=[
            [{'at':'10-25 09:00'}],
            [{'at':1025,'text':'手记'}],
            [{'at':'10-25 09:00','text':123}],
            [{'at':'10-25 09:00','text':'手记','extra':'多余字段'}],
            [{'at':'x'*41,'text':'手记'}],
            [{'at':'10-25 09:00','text':'x'*501}],
            [{'at':'10-25 09:00','text':'手记'},'不是字典'],
        ]
        for rows in broken_rows:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
                path=Path(d)/'settings.json'
                original={'pats':0,'snacks':0,'focus_sessions':0,'affinity':0,'history':rows}
                path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
                before=path.read_bytes()
                with self.assertRaisesRegex(SyncError,'cannot be preserved exactly'):
                    Store(path).enable_sync_preview()
                self.assertEqual(path.read_bytes(),before)
                self.assertFalse(path.with_suffix('.sync-v1.json').exists())
                self.assertFalse(path.with_suffix('.pre-sync.json').exists())

    def test_valid_legacy_history_is_seeded_exactly(self):
        rows=[{'at':'10-25 09:00','text':'第一行'},
              {'at':'x'*40,'text':'y'*500}]
        original={'pats':0,'snacks':0,'focus_sessions':0,'affinity':0,'history':rows}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            ledger=Store(path).enable_sync_preview()
            baseline=[event for event in ledger.events.values()
                      if event['kind']=='legacy_snapshot']
            self.assertEqual(len(baseline),1)
            self.assertEqual(baseline[0]['payload']['history'],rows)
            self.assertEqual(path.with_suffix('.pre-sync.json').read_bytes(),before)
            self.assertEqual(path.read_bytes(),before)

    def test_history_rows_beyond_100_are_not_validated(self):
        rows=['早于保留窗口的坏行']+[{'at':'10-25 09:00','text':'手记 {}'.format(i)}
                                  for i in range(100)]
        original={'pats':0,'snacks':0,'focus_sessions':0,'affinity':0,'history':rows}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            ledger=Store(path).enable_sync_preview()
            baseline=[event for event in ledger.events.values()
                      if event['kind']=='legacy_snapshot']
            self.assertEqual(len(baseline),1)
            # Existing save truncation keeps the last 100 rows; validation must
            # not reject a save over rows that seed_legacy never migrates.
            self.assertEqual(baseline[0]['payload']['history'],rows[1:])

    def test_manual_bundle_updates_shared_state_only(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as d:
            original={'pats':2,'snacks':0,'focus_sessions':0,'affinity':2,'history':[]}
            a_path=Path(d)/'a-settings.json';b_path=Path(d)/'b-settings.json'
            for path in (a_path,b_path):path.write_text(json.dumps(original),encoding='utf-8')
            a=Store(a_path);b=Store(b_path)
            a['scale']=1.5;b['scale']=3.0
            a.enable_sync_preview();b.enable_sync_preview()
            a.record_sync('pat');a.log('管理员，画放这里。')
            b.record_sync('snack')
            self.assertEqual(a.import_sync_bundle(b.sync_ledger.export()),1)
            self.assertEqual(b.import_sync_bundle(a.sync_ledger.export()),2)
            self.assertEqual(a['affinity'],b['affinity'])
            self.assertEqual(a['history'],b['history'])
            self.assertEqual((a['scale'],b['scale']),(1.5,3.0))
            self.assertEqual(a.import_sync_bundle(b.sync_ledger.export()),0)
if __name__=='__main__':unittest.main()
