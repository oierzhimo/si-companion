"""Cross-device convergence and migration checks for the offline protocol."""
import contextlib
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from si_pet import sync_v1
from si_pet.sync_v1 import Ledger, SyncError, canonical, legacy_event, validate_event

TEST_TEMP = Path(__file__).resolve().parents[1] / "qa" / "tmp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
SHARED_PREFERENCES = Path(__file__).resolve().parent / "fixtures" / "shared_preferences_vectors.json"
COUNTERS = ("pats", "snacks", "focus_sessions", "affinity")
LEGACY_SNAPSHOT = {"pats": 3, "snacks": 2, "focus_sessions": 1,
                   "affinity": 14, "history": [{"at": "10-25 09:00", "text": "一起画画"}]}


def shared_vectors():
    return json.loads(SHARED_PREFERENCES.read_text(encoding="utf-8"))["vectors"]


def vector_bundle(events, source="vector-source"):
    body = {"protocol": 1, "source_device": source, "events": events}
    body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return canonical(body)


def temp_path(ledger):
    return ledger.path.with_suffix(ledger.path.suffix + ".tmp")


@contextlib.contextmanager
def injected_temp_write_failure(path):
    """Fail only the ledger's temp-file write, leaving os.replace real."""
    original = Path.write_bytes

    def failing_write(target, data, *args, **kwargs):
        if target == path:
            raise OSError("injected temp write failure")
        return original(target, data, *args, **kwargs)

    with mock.patch.object(Path, "write_bytes", failing_write):
        yield


@contextlib.contextmanager
def injected_replace_failure():
    """Fail the atomic rename after the temp file was written."""
    with mock.patch.object(sync_v1.os, "replace",
                           side_effect=OSError("injected replace failure")):
        yield


class SyncTests(unittest.TestCase):
    def test_copied_legacy_save_has_one_baseline(self):
        old = {"pats": 3.0, "snacks": 2.0, "focus_sessions": 1.0,
               "affinity": 14.0, "history": [{"at": "10-25 09:00", "text": "一起画画"}]}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            a = Ledger(Path(directory) / "a.json", "pc-a")
            b = Ledger(Path(directory) / "b.json", "pc-b")
            self.assertEqual(a.seed_legacy(old), b.seed_legacy(old))
            a.add("pat")
            b.add("snack")
            self.assertEqual(a.import_bundle(b.export()), 1)
            self.assertEqual(b.import_bundle(a.export()), 1)
            self.assertEqual(a.summary(), b.summary())
            self.assertEqual(a.summary()["affinity"], 18)
            self.assertEqual(a.summary()["pats"], 4)
            self.assertEqual(a.summary()["snacks"], 3)

    def test_offline_order_duplicate_and_reload_converge(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            a = Ledger(Path(directory) / "a.json", "pc-a")
            b = Ledger(Path(directory) / "b.json", "phone-b")
            c = Ledger(Path(directory) / "c.json", "tablet-c")
            a.add("pat")
            a.add("journal", {"text": "管理员，画先放这里。"})
            b.add("focus_completed", {"minutes": 25})
            c.add("snack")
            c.add("pref_set", {"key": "preferred_name", "value": "管理员"})
            bundles = [a.export(), b.export(), c.export()]
            for ledger, order in ((a, (2, 1, 0)), (b, (0, 2, 1)), (c, (1, 0, 2))):
                for index in order:
                    ledger.import_bundle(bundles[index])
                for index in order:
                    self.assertEqual(ledger.import_bundle(bundles[index]), 0)
            expected = a.summary()
            self.assertEqual(expected["affinity"], 9)
            self.assertEqual(len(expected["journal"]), 1)
            self.assertEqual(expected["preferences"]["preferred_name"], "管理员")
            self.assertEqual(b.summary(), expected)
            self.assertEqual(c.summary(), expected)
            self.assertEqual(Ledger(a.path).summary(), expected)

    def test_conflict_rejected_without_partial_merge(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            a = Ledger(Path(directory) / "a.json", "pc-a")
            b = Ledger(Path(directory) / "b.json", "pc-b")
            original = a.add("pat")
            b.import_bundle(a.export())
            forged = dict(original, kind="snack")
            body = {"protocol": 1, "source_device": "pc-b",
                    "events": [b.add("journal", {"text": "good"}), forged]}
            import hashlib
            body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
            before = a.summary()
            with self.assertRaisesRegex(SyncError, "Conflicting event ID"):
                a.import_bundle(canonical(body))
            self.assertEqual(a.summary(), before)

    def test_tampered_bundle_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            a = Ledger(Path(directory) / "a.json", "pc-a")
            b = Ledger(Path(directory) / "b.json", "pc-b")
            a.add("pat")
            tampered = json.loads(a.export())
            tampered["events"][0]["kind"] = "snack"
            with self.assertRaisesRegex(SyncError, "checksum"):
                b.import_bundle(canonical(tampered))
            self.assertEqual(b.summary()["affinity"], 0)

    def test_malformed_event_rejected_without_state_change(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            source = Ledger(Path(directory) / "source.json", "pc-a")
            target = Ledger(Path(directory) / "target.json", "pc-b")
            invalid = source.add("pref_set", {"key": "preferred_name", "value": "管理员"})
            invalid["payload"]["key"] = []
            body = {"protocol": 1, "source_device": "pc-a", "events": [invalid]}
            import hashlib
            body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
            with self.assertRaises(SyncError):
                target.import_bundle(canonical(body))
            self.assertEqual(target.events, {})

    def test_empty_persisted_ledger_accepts_one_nonzero_baseline(self):
        snapshot = {"pats": 3, "snacks": 1, "focus_sessions": 2,
                    "affinity": 16, "history": [{"at": "10-25 09:00", "text": "旧电脑"}]}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            fresh_path = Path(directory) / "fresh.json"
            fresh = Ledger(fresh_path, "fresh-desktop")
            fresh.save()  # Persisted empty identity, like a mobile install.
            old = Ledger(Path(directory) / "old.json", "pc-old")
            old.seed_legacy(snapshot)
            self.assertEqual(fresh.import_bundle(old.export()), 1)
            self.assertEqual(fresh.summary()["pats"], 3)
            self.assertEqual(fresh.summary()["affinity"], 16)
            self.assertEqual(fresh.import_bundle(old.export()), 0)
            self.assertEqual(Ledger(fresh_path).device_id, "fresh-desktop")

    def test_existing_zero_baseline_still_conflicts_with_nonzero_baseline(self):
        # a3/a4 could persist a zero-valued baseline for a blank save. Do not
        # auto-merge it: two different legacy baselines stay incompatible.
        blank = {"pats": 0, "snacks": 0, "focus_sessions": 0,
                 "affinity": 0, "history": []}
        snapshot = {"pats": 3, "snacks": 1, "focus_sessions": 2,
                    "affinity": 16, "history": [{"at": "10-25 09:00", "text": "旧电脑"}]}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            zero = Ledger(Path(directory) / "zero.json", "legacy-zero")
            zero.seed_legacy(blank)
            old = Ledger(Path(directory) / "old.json", "pc-old")
            old.seed_legacy(snapshot)
            with self.assertRaisesRegex(SyncError, "legacy baselines"):
                zero.import_bundle(old.export())
            self.assertEqual(zero.summary()["pats"], 0)
            self.assertEqual(len(zero.events), 1)

    def test_legacy_id_stable_from_same_snapshot(self):
        old = {"pats": 2, "snacks": 1, "focus_sessions": 0,
               "affinity": 5, "history": []}
        self.assertEqual(legacy_event(old)["event_id"], legacy_event(dict(old))["event_id"])

    def test_diverged_legacy_copies_cannot_double_count(self):
        old = {"pats": 2, "snacks": 1, "focus_sessions": 0,
               "affinity": 5, "history": []}
        changed = dict(old, pats=3, affinity=6)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            a = Ledger(Path(directory) / "a.json", "pc-a")
            b = Ledger(Path(directory) / "b.json", "pc-b")
            a.seed_legacy(old)
            b.seed_legacy(changed)
            with self.assertRaisesRegex(SyncError, "legacy baselines"):
                a.import_bundle(b.export())
            self.assertEqual(a.summary()["affinity"], 5)
            self.assertEqual(len(a.events), 1)

    def test_shared_preference_vectors_match_summary(self):
        for vector in shared_vectors():
            with self.subTest(vector=vector["name"]), \
                    tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
                path = Path(directory) / "ledger.json"
                ledger = Ledger(path, "vector-local")
                ledger.save()  # Persist the empty identity so reload is meaningful.
                if vector["events"]:
                    bundle = vector_bundle(vector["events"])
                    self.assertEqual(ledger.import_bundle(bundle), len(vector["events"]))
                    self.assertEqual(ledger.import_bundle(bundle), 0)
                summary = ledger.summary()
                self.assertEqual(summary["preferences"], vector["preferences"])
                self.assertEqual({key: summary[key] for key in COUNTERS}, vector["counts"])
                self.assertEqual(Ledger(path).summary(), summary)
                self.assertEqual(Ledger(path).summary()["preferences"],
                                 vector["preferences"])

    def test_shared_preference_import_order_is_irrelevant(self):
        for vector in shared_vectors():
            if not vector["events"]:
                continue
            with self.subTest(vector=vector["name"]), \
                    tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
                forward = Ledger(Path(directory) / "forward.json", "vector-local")
                self.assertEqual(forward.import_bundle(vector_bundle(vector["events"])),
                                 len(vector["events"]))
                reverse = Ledger(Path(directory) / "reverse.json", "vector-local")
                for event in reversed(vector["events"]):
                    self.assertEqual(reverse.import_bundle(vector_bundle([event])), 1)
                self.assertEqual(reverse.summary(), forward.summary())
                self.assertEqual(reverse.summary()["preferences"], vector["preferences"])
                before = forward.export()
                self.assertEqual(forward.import_bundle(vector_bundle(vector["events"])), 0)
                self.assertEqual(forward.export(), before)
                self.assertEqual(forward.summary()["preferences"],
                                 vector["preferences"])

    def test_shared_preference_conflict_is_rejected_without_overwrite(self):
        vector = next(item for item in shared_vectors() if item["events"])
        original = next(event for event in vector["events"] if event["kind"] == "pref_set")
        forged = json.loads(json.dumps(original))
        forged["payload"]["value"] += "-conflict"
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            ledger = Ledger(Path(directory) / "ledger.json", "vector-local")
            ledger.import_bundle(vector_bundle(vector["events"]))
            before_summary, before_export = ledger.summary(), ledger.export()
            with self.assertRaisesRegex(SyncError, "Conflicting event ID"):
                ledger.import_bundle(vector_bundle([forged]))
            self.assertEqual(ledger.summary(), before_summary)
            self.assertEqual(ledger.export(), before_export)
            self.assertEqual(ledger.summary()["preferences"], vector["preferences"])


class LedgerAtomicityTests(unittest.TestCase):
    """A failed durable write must not leak into memory or a later export."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def make_ledger(self, name, device_id):
        return Ledger(self.directory / name, device_id)

    def failure(self, mode, ledger):
        if mode == "temp-write":
            return injected_temp_write_failure(temp_path(ledger))
        return injected_replace_failure()

    def snapshot(self, ledger):
        return {"events": json.loads(json.dumps(ledger.events)),
                "sequence": ledger.sequence,
                "lamport": ledger.lamport,
                "summary": ledger.summary(),
                "export": ledger.export(),
                "disk": ledger.path.read_bytes() if ledger.path.exists() else None}

    def assert_unchanged(self, ledger, before):
        self.assertEqual(self.snapshot(ledger), before)

    def assert_reload_matches(self, ledger):
        reloaded = Ledger(ledger.path)
        self.assertEqual(reloaded.events, ledger.events)
        self.assertEqual(reloaded.sequence, ledger.sequence)
        self.assertEqual(reloaded.lamport, ledger.lamport)
        self.assertEqual(reloaded.export(), ledger.export())
        self.assertEqual(reloaded.summary(), ledger.summary())
        return reloaded

    def test_add_failure_keeps_state_and_retry_persists_one_event(self):
        for mode in ("temp-write", "replace"):
            with self.subTest(mode=mode):
                ledger = self.make_ledger(f"add-{mode}.json", "pc-a")
                ledger.save()
                before = self.snapshot(ledger)
                with self.assertRaises(OSError):
                    with self.failure(mode, ledger):
                        ledger.add("pat")
                self.assert_unchanged(ledger, before)

                event = ledger.add("pat")
                self.assertEqual(event["event_id"], "pc-a:1")
                self.assertEqual(ledger.sequence, 1)
                self.assertEqual(ledger.lamport, 1)
                self.assertEqual(ledger.events, {event["event_id"]: event})
                reloaded = self.assert_reload_matches(ledger)
                self.assertEqual(len(reloaded.events), 1)
                self.assertEqual(reloaded.summary()["pats"], 1)

    def test_seed_failure_keeps_state_and_retry_persists_one_baseline(self):
        for mode in ("temp-write", "replace"):
            with self.subTest(mode=mode):
                ledger = self.make_ledger(f"seed-{mode}.json", "pc-seed")
                ledger.save()
                before = self.snapshot(ledger)
                with self.assertRaises(OSError):
                    with self.failure(mode, ledger):
                        ledger.seed_legacy(LEGACY_SNAPSHOT)
                self.assert_unchanged(ledger, before)
                self.assertEqual(ledger.events, {})

                event_id = ledger.seed_legacy(LEGACY_SNAPSHOT)
                self.assertEqual(event_id, legacy_event(LEGACY_SNAPSHOT)["event_id"])
                self.assertEqual(len(ledger.events), 1)
                with self.assertRaisesRegex(SyncError, "empty ledger"):
                    ledger.seed_legacy(LEGACY_SNAPSHOT)
                reloaded = self.assert_reload_matches(ledger)
                self.assertEqual(len(reloaded.events), 1)
                self.assertEqual(reloaded.summary()["pats"], 3)
                self.assertEqual(reloaded.summary()["affinity"], 14)

    def test_import_failure_keeps_state_and_retry_applies_events_once(self):
        for mode in ("temp-write", "replace"):
            with self.subTest(mode=mode):
                target = self.make_ledger(f"import-{mode}.json", "pc-target")
                local = target.add("pat")
                source = self.make_ledger(f"import-source-{mode}.json", "pc-source")
                source.add("snack")
                source.add("journal", {"text": "来自另一台电脑"})
                bundle = source.export()
                before = self.snapshot(target)

                with self.assertRaises(OSError):
                    with self.failure(mode, target):
                        target.import_bundle(bundle)
                self.assert_unchanged(target, before)
                self.assertEqual(target.events, {local["event_id"]: local})

                self.assertEqual(target.import_bundle(bundle), 2)
                self.assertEqual(target.import_bundle(bundle), 0)
                self.assertEqual(len(target.events), 3)
                reloaded = self.assert_reload_matches(target)
                self.assertEqual(reloaded.summary()["snacks"], 1)
                self.assertEqual(len(reloaded.summary()["journal"]), 1)

    def test_mocked_save_is_called_without_arguments_and_commits_on_success(self):
        ledger = self.make_ledger("mock-save.json", "pc-mock")
        with mock.patch.object(ledger, "save") as mocked_save:
            event = ledger.add("pat")
        mocked_save.assert_called_once_with()
        self.assertFalse(ledger.path.exists())
        self.assertEqual(ledger.sequence, 1)
        self.assertEqual(ledger.lamport, 1)
        self.assertEqual(ledger.events, {event["event_id"]: event})

    def test_mocked_save_failure_is_reraised_without_memory_change(self):
        ledger = self.make_ledger("mock-failure.json", "pc-mock")
        ledger.save()
        before = self.snapshot(ledger)
        with mock.patch.object(ledger, "save",
                               side_effect=OSError("mocked save failure")) as mocked_save:
            with self.assertRaisesRegex(OSError, "mocked save failure"):
                ledger.add("pat")
        mocked_save.assert_called_once_with()
        self.assert_unchanged(ledger, before)

    def test_concurrent_add_import_export_stay_consistent(self):
        ledger = self.make_ledger("concurrent.json", "pc-concurrent")
        ledger.save()
        bundles = []
        for index in range(3):
            source = self.make_ledger(f"concurrent-source-{index}.json", f"source-{index}")
            source.add("snack")
            bundles.append(source.export())

        failures = []
        start = threading.Barrier(5)

        def add_worker():
            try:
                start.wait()
                for _ in range(10):
                    ledger.add("pat")
            except BaseException as exc:  # Surface worker errors on the main thread.
                failures.append(exc)

        def import_worker(bundle):
            try:
                start.wait()
                for _ in range(5):
                    ledger.import_bundle(bundle)
            except BaseException as exc:
                failures.append(exc)

        def read_worker():
            try:
                start.wait()
                for _ in range(50):
                    json.loads(ledger.export())
                    ledger.summary()
                    ledger.journal_rows()
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=add_worker, daemon=True) for _ in range(2)]
        threads += [threading.Thread(target=import_worker, args=(bundle,), daemon=True)
                    for bundle in bundles[:2]]
        threads.append(threading.Thread(target=read_worker, daemon=True))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "worker deadlocked")
        self.assertEqual(failures, [])
        self.assertEqual(len(ledger.events), 22)
        self.assertEqual(ledger.sequence, 20)
        self.assertEqual(ledger.summary()["pats"], 20)
        self.assertEqual(ledger.summary()["snacks"], 2)
        self.assert_reload_matches(ledger)


class PreferenceBatchTests(unittest.TestCase):
    """Batch pref_set writes must be all-or-nothing, ordered and isolated."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def make_ledger(self, name="ledger.json", device_id="pc-batch"):
        return Ledger(self.directory / name, device_id)

    def snapshot(self, ledger):
        return {"events": json.loads(json.dumps(ledger.events)),
                "sequence": ledger.sequence,
                "lamport": ledger.lamport,
                "summary": ledger.summary(),
                "export": ledger.export(),
                "disk": ledger.path.read_bytes() if ledger.path.exists() else None}

    def assert_unchanged(self, ledger, before):
        self.assertEqual(self.snapshot(ledger), before)

    def assert_reload_matches(self, ledger):
        reloaded = Ledger(ledger.path)
        self.assertEqual(reloaded.events, ledger.events)
        self.assertEqual(reloaded.sequence, ledger.sequence)
        self.assertEqual(reloaded.lamport, ledger.lamport)
        self.assertEqual(reloaded.export(), ledger.export())
        self.assertEqual(reloaded.summary(), ledger.summary())
        return reloaded

    def failure(self, mode, ledger):
        if mode == "temp-write":
            return injected_temp_write_failure(temp_path(ledger))
        if mode == "replace":
            return injected_replace_failure()
        if mode == "save":
            return mock.patch.object(ledger, "save",
                                     side_effect=OSError("injected save failure"))
        return mock.patch.object(ledger, "_commit",
                                 side_effect=OSError("injected commit failure"))

    def assert_complete_batches(self, events):
        pref_events = [event for event in events if event["kind"] == "pref_set"]
        by_sequence = {event["sequence"]: event for event in pref_events}
        self.assertEqual(len(by_sequence), len(pref_events),
                         "duplicate sequence exposed")
        names, styles = {}, {}
        for event in pref_events:
            token = event["payload"]["value"].split("-", 1)[1]
            if event["payload"]["key"] == "preferred_name":
                names[token] = event
            else:
                styles[token] = event
        self.assertEqual(set(names), set(styles),
                         "only half of a preference batch is visible")
        for token, name_event in names.items():
            style_event = styles[token]
            self.assertEqual(style_event["sequence"], name_event["sequence"] + 1)
            self.assertEqual(style_event["lamport"], name_event["lamport"] + 1)
            self.assertEqual(style_event["occurred_at"], name_event["occurred_at"])
            self.assertEqual(style_event["event_id"],
                             f"{name_event['device_id']}:{style_event['sequence']}")

    def test_batch_is_one_ordered_protocol_batch(self):
        ledger = self.make_ledger()
        ledger.save()
        items = [("greeting_style", "warm"), ("preferred_name", "管理员")]
        events = ledger.add_preferences_batch(
            items, occurred_at="2026-10-27T12:00:00Z")
        self.assertEqual(len(events), 2)
        self.assertEqual([event["payload"]["key"] for event in events],
                         ["greeting_style", "preferred_name"])
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual([event["lamport"] for event in events], [1, 2])
        self.assertEqual([event["event_id"] for event in events],
                         ["pc-batch:1", "pc-batch:2"])
        self.assertEqual({event["protocol"] for event in events}, {1})
        self.assertEqual({event["device_id"] for event in events}, {"pc-batch"})
        self.assertEqual({event["occurred_at"] for event in events},
                         {"2026-10-27T12:00:00Z"})
        for event in events:
            validate_event(event)
        self.assertEqual((ledger.sequence, ledger.lamport), (2, 2))
        self.assertEqual(json.loads(ledger.export())["events"],
                         [ledger.events[key] for key in sorted(ledger.events)])
        summary = ledger.summary()
        self.assertEqual(summary["preferences"],
                         {"greeting_style": "warm", "preferred_name": "管理员"})
        reloaded = self.assert_reload_matches(ledger)
        self.assertEqual(reloaded.summary(), summary)

    def test_empty_value_and_eighty_character_unicode_value_survive(self):
        ledger = self.make_ledger()
        ledger.save()
        emoji = "😀" * 80
        events = ledger.add_preferences_batch(
            [("preferred_name", ""), ("greeting_style", emoji)])
        self.assertEqual(events[0]["payload"]["value"], "")
        self.assertEqual(len(events[1]["payload"]["value"]), 80)
        exported = json.loads(ledger.export())["events"]
        self.assertEqual([event["payload"] for event in exported],
                         [events[0]["payload"], events[1]["payload"]])
        self.assertEqual(ledger.summary()["preferences"],
                         {"preferred_name": "", "greeting_style": emoji})
        reloaded = self.assert_reload_matches(ledger)
        self.assertEqual(reloaded.summary()["preferences"],
                         {"preferred_name": "", "greeting_style": emoji})

    def test_invalid_batches_and_timestamps_have_no_side_effects(self):
        ledger = self.make_ledger()
        ledger.add("journal", {"text": "keep"})
        ledger.add("pref_set", {"key": "preferred_name", "value": "original"})
        before = self.snapshot(ledger)
        invalid_items = (
            ("empty", []),
            ("mapping", {"preferred_name": "x"}),
            ("generator", iter([("preferred_name", "x")])),
            ("string-items", "preferred_name"),
            ("set-items", {("preferred_name", "x")}),
            ("duplicate-key", [("preferred_name", "a"), ("preferred_name", "b")]),
            ("unknown-key", [("timezone", "UTC")]),
            ("too-many", [("preferred_name", "a"), ("greeting_style", "b"),
                          ("preferred_name", "c")]),
            ("non-string-key", [(1, "a")]),
            ("non-string-value", [("preferred_name", 1)]),
            ("value-too-long", [("greeting_style", "x" * 81)]),
            ("lone-surrogate", [("preferred_name", "\ud800")]),
            ("short-pair", [("preferred_name",)]),
            ("long-pair", [("preferred_name", "x", "y")]),
            ("string-pair", ["ab"]),
            ("mapping-item", [{"preferred_name": "x", "greeting_style": "y"}]),
            ("single-pair", ("preferred_name", "x")),
        )
        for label, items in invalid_items:
            with self.subTest(label=label):
                with self.assertRaises(SyncError):
                    ledger.add_preferences_batch(items)
                self.assert_unchanged(ledger, before)
        invalid_timestamps = (
            ("bad-text", "not-a-timestamp"),
            ("naive", "2026-10-27T12:00:00"),
            ("too-long", "2" * 41),
            ("non-string", 0),
            ("lone-surrogate", "\ud800"),
        )
        for label, occurred_at in invalid_timestamps:
            with self.subTest(label=label):
                with self.assertRaises(SyncError):
                    ledger.add_preferences_batch(
                        [("greeting_style", "warm")], occurred_at=occurred_at)
                self.assert_unchanged(ledger, before)
        events = ledger.add_preferences_batch([("greeting_style", "warm")])
        self.assertEqual([event["sequence"] for event in events], [3])

    def test_write_failures_preserve_memory_disk_and_exports(self):
        for mode in ("temp-write", "replace", "save", "commit"):
            with self.subTest(mode=mode):
                ledger = self.make_ledger(f"failure-{mode}.json", "pc-batch")
                ledger.add("journal", {"text": "既有"})
                before = self.snapshot(ledger)
                with self.assertRaises(OSError):
                    with self.failure(mode, ledger):
                        ledger.add_preferences_batch(
                            [("preferred_name", "新名字"),
                             ("greeting_style", "warm")],
                            occurred_at="2026-10-27T12:00:00Z")
                self.assert_unchanged(ledger, before)
                self.assertEqual(ledger.summary()["preferences"], {})
                self.assertEqual(json.loads(ledger.export())["events"],
                                 [ledger.events[key]
                                  for key in sorted(ledger.events)])
                self.assertIsNone(ledger._staged)
                events = ledger.add_preferences_batch(
                    [("preferred_name", "新名字"), ("greeting_style", "warm")])
                self.assertEqual([event["sequence"] for event in events], [2, 3])
                reloaded = self.assert_reload_matches(ledger)
                self.assertEqual(reloaded.summary()["preferences"],
                                 {"preferred_name": "新名字",
                                  "greeting_style": "warm"})

    def test_failure_never_creates_a_new_ledger_file(self):
        for mode in ("temp-write", "replace", "save", "commit"):
            with self.subTest(mode=mode):
                path = self.directory / f"absent-{mode}.json"
                ledger = Ledger(path, "pc-absent")
                before = self.snapshot(ledger)
                with self.assertRaises(OSError):
                    with self.failure(mode, ledger):
                        ledger.add_preferences_batch(
                            [("preferred_name", "x"), ("greeting_style", "y")])
                self.assertFalse(path.exists())
                self.assert_unchanged(ledger, before)
                self.assertEqual((ledger.events, ledger.sequence, ledger.lamport),
                                 ({}, 0, 0))
                events = ledger.add_preferences_batch(
                    [("preferred_name", "x"), ("greeting_style", "y")])
                self.assertTrue(path.exists())
                self.assertEqual([event["sequence"] for event in events], [1, 2])
                self.assert_reload_matches(ledger)

    def test_batch_uses_exactly_one_commit_and_one_save(self):
        ledger = self.make_ledger()
        real_commit, real_save = ledger._commit, ledger.save
        with mock.patch.object(
                ledger, "add",
                side_effect=AssertionError("ordinary add used")) as ordinary_add:
            with mock.patch.object(ledger, "_commit", wraps=real_commit) as commit:
                with mock.patch.object(ledger, "save", wraps=real_save) as save:
                    events = ledger.add_preferences_batch(
                        [("preferred_name", "x"), ("greeting_style", "y")])
        ordinary_add.assert_not_called()
        self.assertEqual(commit.call_count, 1)
        save.assert_called_once_with()
        self.assertEqual(len(events), 2)
        self.assertTrue(ledger.path.exists())
        reloaded = Ledger(ledger.path)
        self.assertEqual(reloaded.summary(), ledger.summary())
        self.assertEqual(len(reloaded.events), 2)

    def test_returned_events_are_detached_from_ledger(self):
        ledger = self.make_ledger()
        events = ledger.add_preferences_batch(
            [("preferred_name", "原始"), ("greeting_style", "warm")])
        internal = ledger.events["pc-batch:1"]
        self.assertIsNot(internal, events[0])
        before_export, before_summary = ledger.export(), ledger.summary()
        events[0]["payload"]["value"] = "篡改"
        events[0]["sequence"] = 999
        events[0]["event_id"] = "pc-batch:999"
        events.append({"protocol": 1})
        self.assertEqual(internal["payload"]["value"], "原始")
        self.assertEqual(internal["sequence"], 1)
        self.assertEqual(ledger.export(), before_export)
        self.assertEqual(ledger.summary(), before_summary)
        self.assertEqual(Ledger(ledger.path).summary(), before_summary)

    def test_storage_limits_reject_without_global_pollution(self):
        original_events = sync_v1.MAX_EVENTS
        original_bundle = sync_v1.MAX_BUNDLE_BYTES
        ledger = self.make_ledger("limits.json", "pc-limits")
        ledger.add("pat")
        before = self.snapshot(ledger)
        with mock.patch.object(sync_v1, "MAX_EVENTS", 2):
            with self.assertRaisesRegex(SyncError, "Ledger full"):
                ledger.add_preferences_batch(
                    [("preferred_name", "x"), ("greeting_style", "y")])
        self.assertEqual(sync_v1.MAX_EVENTS, original_events)
        self.assert_unchanged(ledger, before)
        with mock.patch.object(sync_v1, "MAX_BUNDLE_BYTES", 1):
            with self.assertRaisesRegex(SyncError, "Bundle too large"):
                ledger.add_preferences_batch(
                    [("preferred_name", "x"), ("greeting_style", "y")])
        self.assertEqual(sync_v1.MAX_BUNDLE_BYTES, original_bundle)
        self.assert_unchanged(ledger, before)

        boundary = self.make_ledger("capacity.json", "pc-capacity")
        boundary.add("pat")
        with mock.patch.object(sync_v1, "MAX_EVENTS", 3):
            events = boundary.add_preferences_batch(
                [("preferred_name", "x"), ("greeting_style", "y")])
        self.assertEqual(len(events), 2)
        self.assertEqual(len(boundary.events), 3)
        self.assertEqual(sync_v1.MAX_EVENTS, original_events)

    def test_safe_integer_boundary_is_rejected_without_global_changes(self):
        limit = 2**53 - 1
        for label, sequence, lamport in (("sequence", limit - 1, 0),
                                         ("lamport", 0, limit - 1)):
            with self.subTest(label=label):
                ledger = self.make_ledger(f"js-{label}.json", "pc-js")
                ledger.save()
                ledger.sequence = sequence
                ledger.lamport = lamport
                before = self.snapshot(ledger)
                with self.assertRaisesRegex(SyncError, "safe integer"):
                    ledger.add_preferences_batch(
                        [("preferred_name", "a"), ("greeting_style", "b")])
                self.assert_unchanged(ledger, before)

        ledger = self.make_ledger("js-edge.json", "pc-js-edge")
        ledger.save()
        ledger.sequence = limit - 1
        events = ledger.add_preferences_batch([("preferred_name", "edge")])
        self.assertEqual(events[0]["sequence"], limit)
        self.assertEqual(ledger.sequence, limit)
        self.assert_reload_matches(ledger)
        before = self.snapshot(ledger)
        with self.assertRaisesRegex(SyncError, "safe integer"):
            ledger.add_preferences_batch([("greeting_style", "overflow")])
        self.assert_unchanged(ledger, before)

    def test_concurrent_batches_and_adds_never_expose_half_batch(self):
        batches, adds = 20, 20
        ledger = self.make_ledger("concurrent-batch.json", "pc-concurrent-batch")
        ledger.save()
        failures = []
        start = threading.Barrier(3)

        def batch_worker():
            try:
                start.wait(timeout=30)
                for index in range(batches):
                    ledger.add_preferences_batch(
                        [("preferred_name", f"name-{index}"),
                         ("greeting_style", f"style-{index}")])
            except BaseException as exc:
                failures.append(exc)

        def add_worker():
            try:
                start.wait(timeout=30)
                for _ in range(adds):
                    ledger.add("pat")
            except BaseException as exc:
                failures.append(exc)

        def observe_worker():
            try:
                start.wait(timeout=30)
                for _ in range(100):
                    events = json.loads(ledger.export())["events"]
                    self.assert_complete_batches(events)
                    preferences = ledger.summary()["preferences"]
                    if preferences:
                        self.assertEqual(set(preferences),
                                         {"preferred_name", "greeting_style"})
                        self.assertEqual(
                            preferences["preferred_name"].split("-", 1)[1],
                            preferences["greeting_style"].split("-", 1)[1])
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=batch_worker, daemon=True),
                   threading.Thread(target=add_worker, daemon=True),
                   threading.Thread(target=observe_worker, daemon=True)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertFalse(any(thread.is_alive() for thread in threads),
                         "worker deadlocked")
        self.assertEqual(failures, [])
        total = batches * 2 + adds
        self.assertEqual(len(ledger.events), total)
        self.assertEqual((ledger.sequence, ledger.lamport), (total, total))
        self.assertEqual(sorted(event["sequence"] for event in ledger.events.values()),
                         list(range(1, total + 1)))
        self.assertEqual(sorted(event["lamport"] for event in ledger.events.values()),
                         list(range(1, total + 1)))
        self.assert_complete_batches(json.loads(ledger.export())["events"])
        self.assertEqual(ledger.summary()["preferences"],
                         {"preferred_name": f"name-{batches - 1}",
                          "greeting_style": f"style-{batches - 1}"})
        self.assert_reload_matches(ledger)


if __name__ == "__main__":
    unittest.main()
