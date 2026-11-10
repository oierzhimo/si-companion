"""Unit tests for pending_media.PendingGroupMedia.

Everything here is synthetic: fake events, a fake monotonic clock and a
handful of threads.  No real QQ data, files, network or services are used.
"""

import json
import threading
import unittest

from pending_media import PendingGroupMedia, PendingMediaBatch


class FakeClock:
    """Deterministic stand-in for ``time.monotonic``."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def image_data(name="a.jpg"):
    return {"file": name, "url": "https://example.invalid/" + name}


def make_event(
    *,
    user_id=111,
    group_id=222,
    message_id=1,
    message=None,
    self_id=999,
    post_type="message",
    message_type="group",
    sub_type="normal",
):
    if message is None:
        message = [{"type": "image", "data": image_data()}]
    return {
        "post_type": post_type,
        "message_type": message_type,
        "sub_type": sub_type,
        "self_id": self_id,
        "user_id": user_id,
        "group_id": group_id,
        "message_id": message_id,
        "message": message,
    }


def make_buffer(clock=None, **kwargs):
    if clock is None:
        clock = FakeClock()
    return PendingGroupMedia(clock=clock, **kwargs), clock


class TestBasicOfferTake(unittest.TestCase):
    def test_image_offered_then_taken_by_same_sender_and_group(self):
        buf, _clock = make_buffer()
        event = make_event()
        self.assertIs(buf.offer(event, 999), True)

        # Offering alone never performs the take: the data is only staged.
        self.assertEqual(buf.stats()["pairs"], 1)
        self.assertEqual(buf.stats()["items"], 1)

        batch = buf.take(222, 111)
        self.assertIsInstance(batch, PendingMediaBatch)
        self.assertIsInstance(batch, list)
        self.assertEqual(len(batch), 1)

        item = batch[0]
        self.assertEqual(set(item), {"message_id", "segments"})
        self.assertEqual(item["message_id"], "1")
        self.assertEqual(
            item["segments"],
            [{"type": "image", "data": image_data()}],
        )
        self.assertEqual(buf.stats(), {
            "pairs": 0, "items": 0, "bytes": 0, "dedup_keys": 1,
        })

    def test_take_is_empty_when_nothing_staged(self):
        buf, _clock = make_buffer()
        self.assertEqual(buf.take(222, 111), [])
        self.assertEqual(buf.take("abc", 111), [])

    def test_take_only_returns_the_exact_pair(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(user_id=111, group_id=222), 999))
        self.assertTrue(buf.offer(make_event(
            user_id=111, group_id=333, message_id=2), 999))
        self.assertTrue(buf.offer(make_event(
            user_id=444, group_id=222, message_id=3), 999))

        self.assertEqual(buf.take(222, 555), [])
        self.assertEqual(buf.take(333, 444), [])
        self.assertEqual(len(buf.take(222, 111)), 1)
        self.assertEqual(len(buf.take(333, 111)), 1)
        self.assertEqual(len(buf.take(222, 444)), 1)
        self.assertEqual(buf.take(222, 444), [])
        self.assertEqual(buf.stats()["items"], 0)

    def test_multiple_messages_arrive_in_order(self):
        buf, _clock = make_buffer()
        for message_id in ("a", "b", "c"):
            self.assertTrue(buf.offer(
                make_event(message_id=message_id), 999))
        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["a", "b", "c"],
        )

    def test_ids_are_canonicalised_including_leading_zeroes(self):
        buf, _clock = make_buffer()
        event = make_event(
            self_id="0999", user_id="00111", group_id="000222", message_id="0007"
        )
        self.assertTrue(buf.offer(event, 999))
        # Offering the integer form is a duplicate because it canonicalises
        # to the same message id.
        self.assertFalse(buf.offer(
            make_event(user_id=111, group_id=222, message_id=7), 999))

        batch = buf.take("222", "111")
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["message_id"], "7")

    def test_duplicate_message_is_rejected(self):
        buf, _clock = make_buffer()
        event = make_event(message_id=55)
        self.assertTrue(buf.offer(event, 999))
        self.assertFalse(buf.offer(event, 999))
        self.assertFalse(buf.offer(make_event(message_id="55"), 999))
        self.assertEqual(len(buf.take(222, 111)), 1)

        # Same message id from another sender or group is not a duplicate.
        self.assertTrue(buf.offer(
            make_event(user_id=112, message_id=55), 999))
        self.assertTrue(buf.offer(
            make_event(group_id=223, message_id=55), 999))

    def test_same_message_id_allowed_again_after_ttl(self):
        buf, clock = make_buffer(ttl_seconds=10)
        self.assertTrue(buf.offer(make_event(message_id=5), 999))
        clock.advance(10.0)
        self.assertTrue(buf.offer(make_event(message_id=5), 999))
        self.assertEqual(len(buf.take(222, 111)), 1)

    def test_ttl_expiry_uses_injected_clock(self):
        buf, clock = make_buffer(ttl_seconds=10)
        self.assertTrue(buf.offer(make_event(), 999))
        clock.advance(9.999)
        self.assertEqual(len(buf.take(222, 111)), 1)

        buf2, clock2 = make_buffer(ttl_seconds=10)
        self.assertTrue(buf2.offer(make_event(), 999))
        clock2.advance(10.0)
        self.assertEqual(buf2.take(222, 111), [])
        # Expired pair is not retained.
        self.assertEqual(buf2.stats()["pairs"], 0)
        self.assertEqual(buf2.stats()["items"], 0)

    def test_expired_media_not_attached_to_later_mention(self):
        buf, clock = make_buffer(ttl_seconds=5)
        self.assertTrue(buf.offer(make_event(message_id="old"), 999))
        clock.advance(5.5)
        self.assertEqual(buf.take(222, 111), [])
        # Dedup entry has expired too, so the same event may be re-offered.
        self.assertTrue(buf.offer(make_event(message_id="old"), 999))

    def test_offer_returns_false_for_no_eligible_segments(self):
        buf, _clock = make_buffer()
        only_text = [{"type": "text", "data": {"text": "hello @Si"}}]
        only_at = [{"type": "at", "data": {"qq": "999"}}]
        only_reply = [{"type": "reply", "data": {"id": "42"}}]
        mixed = only_text + only_at + only_reply
        for index, message in enumerate(
            (only_text, only_at, only_reply, mixed, [])
        ):
            self.assertFalse(buf.offer(
                make_event(message_id=index, message=message), 999))
        self.assertEqual(buf.stats(), {
            "pairs": 0, "items": 0, "bytes": 0, "dedup_keys": 0,
        })

    def test_eligible_segments_preserve_order_and_skip_text(self):
        buf, _clock = make_buffer()
        message = [
            {"type": "image", "data": {"file": "one.jpg"}},
            {"type": "text", "data": {"text": "look"}},
            {"type": "at", "data": {"qq": "999"}},
            {"type": "file", "data": {"file": "report.pdf", "url": "u"}},
            {"type": "face", "data": {"id": "14"}},
            {"type": "reply", "data": {"id": "77"}},
            {"type": "future_media_v9", "data": {"opaque": [1, 2, 3]}},
        ]
        self.assertTrue(buf.offer(make_event(message=message), 999))
        segments = buf.take(222, 111)[0]["segments"]
        self.assertEqual(
            [segment["type"] for segment in segments],
            ["image", "file", "face", "future_media_v9"],
        )
        self.assertEqual(segments[0]["data"], {"file": "one.jpg"})
        self.assertEqual(segments[3]["data"], {"opaque": [1, 2, 3]})

    def test_unknown_future_segment_types_are_eligible(self):
        buf, _clock = make_buffer()
        message = [
            {"type": "sticker", "data": {"file": "s.gif"}},
            {"type": "brand_new_type", "data": {"x": True}},
        ]
        self.assertTrue(buf.offer(make_event(message=message), 999))
        segments = buf.take(222, 111)[0]["segments"]
        self.assertEqual(
            [segment["type"] for segment in segments],
            ["sticker", "brand_new_type"],
        )

    def test_segment_without_data_is_accepted_with_empty_dict(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(
            make_event(message=[{"type": "image"}]), 999))
        self.assertEqual(
            buf.take(222, 111)[0]["segments"],
            [{"type": "image", "data": {}}],
        )

    def test_item_is_plain_json_and_mutation_safe_after_offer(self):
        buf, _clock = make_buffer()
        data = {"file": "a.jpg", "nested": {"tags": ["x", "y"]}}
        message = [{"type": "image", "data": data}]
        event = make_event(message=message)
        self.assertTrue(buf.offer(event, 999))

        # Mutate everything the caller still holds.
        data["file"] = "evil.jpg"
        data["nested"]["tags"].append("z")
        data["nested"]["tags"][0] = "changed"
        message[0]["type"] = "file"
        message.append({"type": "image", "data": {"file": "extra.jpg"}})
        event["user_id"] = 12345
        event["group_id"] = 54321
        event["message_id"] = "hacked"

        batch = buf.take(222, 111)
        self.assertEqual(len(batch), 1)
        item = batch[0]
        self.assertEqual(item["message_id"], "1")
        self.assertEqual(
            item["segments"],
            [{"type": "image",
              "data": {"file": "a.jpg", "nested": {"tags": ["x", "y"]}}}],
        )
        # The serialised form really is ordinary JSON.
        json.dumps(batch)

    def test_mutation_of_taken_batch_does_not_affect_restore(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)

        batch.append({"message_id": "extra", "segments": []})
        batch[0]["message_id"] = "hacked"
        batch[0]["segments"].clear()

        self.assertEqual(buf.restore(222, 111, batch), 1)
        restored = buf.take(222, 111)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["message_id"], "1")
        self.assertEqual(
            restored[0]["segments"],
            [{"type": "image", "data": image_data()}],
        )


class TestEventValidation(unittest.TestCase):
    def test_rejects_non_message_or_non_group_events(self):
        buf, _clock = make_buffer()
        variants = [
            make_event(post_type="notice"),
            make_event(post_type="meta_event"),
            make_event(message_type="private"),
            make_event(message_type="guild"),
            make_event(sub_type="anonymous"),
            make_event(sub_type="notice"),
        ]
        for index, event in enumerate(variants):
            self.assertFalse(buf.offer(event, 999), index)
        self.assertEqual(buf.stats()["items"], 0)

    def test_rejects_forged_or_missing_self_id(self):
        buf, _clock = make_buffer()
        cases = [
            make_event(self_id=1000),          # forged (not the bot)
            make_event(self_id="9999"),        # different bot
            make_event(self_id=None),          # missing
            make_event(self_id=True),          # bool id
            make_event(self_id=999.0),         # float id
            make_event(self_id="+999"),        # signed
            make_event(self_id="-1"),          # negative
        ]
        for index, event in enumerate(cases):
            self.assertFalse(buf.offer(event, 999), index)
        # A canonical string form of the bot id still matches.
        self.assertTrue(buf.offer(make_event(self_id="000999"), 999))

    def test_rejects_bot_id_that_is_not_decimal(self):
        buf, _clock = make_buffer()
        for bot_id in (None, True, 999.0, "abc", "9 99", "-1", 10 ** 40):
            self.assertFalse(buf.offer(make_event(), bot_id), bot_id)

    def test_rejects_self_authored_messages(self):
        buf, _clock = make_buffer()
        self.assertFalse(buf.offer(make_event(user_id=999), 999))
        self.assertFalse(buf.offer(make_event(user_id="0999"), 999))
        self.assertEqual(buf.stats()["items"], 0)

    def test_rejects_bad_user_and_group_ids(self):
        buf, _clock = make_buffer()
        bad_values = [
            True, False, None, 1.5, -5, "abc", "12a", " 123", "123 ",
            "+123", "-123", "1" * 33, "", 10 ** 40,
        ]
        for value in bad_values:
            self.assertFalse(buf.offer(make_event(user_id=value), 999), value)
            self.assertFalse(buf.offer(make_event(group_id=value), 999), value)
        self.assertEqual(buf.stats()["items"], 0)

    def test_rejects_bad_message_ids(self):
        buf, _clock = make_buffer()
        bad_values = [
            True, False, None, 1.5, "", " ",
            "x" * 129,
            "has\nnewline",
            "has\x00nul",
            "has\ttab",
            "unicode-é",
            " leading",
            "trailing ",
            10 ** 200,
        ]
        for value in bad_values:
            self.assertFalse(buf.offer(make_event(message_id=value), 999), value)
        self.assertEqual(buf.stats()["items"], 0)
        # Printable punctuation is fine.
        self.assertTrue(buf.offer(make_event(message_id="a_b-c:1.2"), 999))

    def test_signed_onebot_message_ids_are_supported_and_deduplicated(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(message_id=-123), 999))
        self.assertFalse(buf.offer(make_event(message_id='-123'), 999))
        self.assertEqual(buf.take(222,111)[0]['message_id'], '-123')

    def test_rejects_malformed_message_lists_and_segments(self):
        buf, _clock = make_buffer()
        cases = [
            make_event(message="not a list"),
            make_event(message={"type": "image"}),
            make_event(message=[{"type": "image"}, "not a dict"]),
            make_event(message=[{"type": "image"}, None]),
            make_event(message=[{"data": {"file": "x"}}]),      # no type
            make_event(message=[{"type": "", "data": {}}]),      # empty type
            make_event(message=[{"type": 5, "data": {}}]),       # non-str
            make_event(message=[{"type": "im age", "data": {}}]),  # space in type
            make_event(message=[{"type": "x" * 65, "data": {}}]),
            make_event(message=[{"type": "image", "data": []}]),  # data not dict
            make_event(message=[{"type": "image", "data": "x"}]),
        ]
        for index, event in enumerate(cases):
            self.assertFalse(buf.offer(event, 999), index)
        self.assertEqual(buf.stats()["items"], 0)

    def test_segment_count_limit(self):
        buf, _clock = make_buffer(max_segments=2)
        two = [{"type": "image", "data": {}}, {"type": "image", "data": {}}]
        three = two + [{"type": "image", "data": {}}]
        self.assertTrue(buf.offer(make_event(message=two), 999))
        self.assertFalse(buf.offer(make_event(message_id=2, message=three), 999))

    def test_nonfinite_numbers_rejected(self):
        buf, _clock = make_buffer()
        for value in (float("nan"), float("inf"), float("-inf")):
            event = make_event(message=[
                {"type": "image", "data": {"value": value}},
            ], message_id="n" + str(value))
            self.assertFalse(buf.offer(event, 999))
            event2 = make_event(message=[
                {"type": "file", "data": {"nested": [value]}},
            ], message_id="m" + str(value))
            self.assertFalse(buf.offer(event2, 999))
        self.assertEqual(buf.stats()["items"], 0)

    def test_cyclic_structures_rejected(self):
        buf, _clock = make_buffer()
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        for index, value in enumerate((cyclic_list, cyclic_dict)):
            self.assertFalse(buf.offer(
                make_event(message=[{"type": "image", "data": {"v": value}}],
                           message_id=index), 999))
        self.assertEqual(buf.stats()["items"], 0)

    def test_deeply_nested_json_rejected(self):
        buf, _clock = make_buffer()
        deep = {}
        cursor = deep
        for _ in range(20):
            cursor["next"] = {}
            cursor = cursor["next"]
        self.assertFalse(buf.offer(
            make_event(message=[{"type": "file", "data": deep}]), 999))
        self.assertEqual(buf.stats()["items"], 0)

        # Shallow data is still accepted.
        shallow = {"next": {"next": {"value": 1}}}
        self.assertTrue(buf.offer(
            make_event(message_id=2, message=[{"type": "file", "data": shallow}]),
            999))

    def test_huge_strings_and_ints_rejected(self):
        buf, _clock = make_buffer()
        self.assertFalse(buf.offer(make_event(message=[
            {"type": "file", "data": {"file": "x" * 100000}},
        ]), 999))
        self.assertFalse(buf.offer(make_event(message_id=2, message=[
            {"type": "file", "data": {"huge": 10 ** 200}},
        ]), 999))
        self.assertFalse(buf.offer(make_event(message_id=3, message=[
            {"type": "file", "data": {"nested": ["y" * 9000]}},
        ]), 999))
        self.assertEqual(buf.stats()["items"], 0)

    def test_non_json_values_and_custom_objects_rejected(self):
        buf, _clock = make_buffer()
        class Custom:
            pass

        class DictSubclass(dict):
            pass

        values = [
            object(),
            Custom(),
            b"bytes",
            bytearray(b"x"),
            {"a", "b"},
            ("tuple",),
            DictSubclass({"x": 1}),
            complex(1, 2),
        ]
        for index, value in enumerate(values):
            self.assertFalse(buf.offer(make_event(message=[
                {"type": "image", "data": {"v": value}},
            ], message_id=index), 999), repr(value))
        self.assertEqual(buf.stats()["items"], 0)

    def test_non_string_json_keys_rejected(self):
        buf, _clock = make_buffer()
        for index, key in enumerate((1, True, None, (1, 2))):
            self.assertFalse(buf.offer(make_event(message=[
                {"type": "image", "data": {key: "v"}},
            ], message_id=index), 999), repr(key))

    def test_lone_surrogate_strings_rejected(self):
        buf, _clock = make_buffer()
        self.assertFalse(buf.offer(make_event(message=[
            {"type": "image", "data": {"file": "\ud800"}},
        ]), 999))
        self.assertEqual(buf.stats()["items"], 0)

    def test_event_subclasses_rejected(self):
        buf, _clock = make_buffer()
        class EventSubclass(dict):
            pass

        event = EventSubclass(make_event())
        self.assertFalse(buf.offer(event, 999))
        self.assertFalse(buf.offer(None, 999))
        self.assertFalse(buf.offer([], 999))
        self.assertFalse(buf.offer("event", 999))

    def test_oversized_message_is_dropped_whole(self):
        buf, _clock = make_buffer(max_total_bytes=300)
        # First segment fits; second pushes the message over the byte budget.
        message = [
            {"type": "image", "data": {"file": "a"}},
            {"type": "file", "data": {"file": "x" * 400}},
        ]
        self.assertFalse(buf.offer(make_event(message=message), 999))
        self.assertEqual(buf.stats(), {
            "pairs": 0, "items": 0, "bytes": 0, "dedup_keys": 0,
        })
        # No partial attachment is retained, so a later mention gets nothing.
        self.assertEqual(buf.take(222, 111), [])

    def test_per_segment_byte_limit(self):
        buf, _clock = make_buffer(
            max_segment_bytes=100, max_total_bytes=100000,
        )
        self.assertFalse(buf.offer(make_event(message=[
            {"type": "file", "data": {"file": "x" * 200}},
        ]), 999))
        self.assertTrue(buf.offer(make_event(message_id=2, message=[
            {"type": "file", "data": {"file": "ok"}},
        ]), 999))

    def test_offer_is_silent_and_idempotent_only_once(self):
        buf, _clock = make_buffer()
        event = make_event()
        self.assertIs(buf.offer(event, 999), True)
        # A second offer does not grow the buffer and returns False.
        self.assertIs(buf.offer(event, 999), False)
        self.assertEqual(buf.stats()["items"], 1)


class TestBounds(unittest.TestCase):
    def test_flood_stays_within_pair_item_and_byte_bounds(self):
        buf, _clock = make_buffer(
            max_pairs=3, max_items_per_pair=2, max_total_bytes=200000,
        )
        for index in range(200):
            event = make_event(
                user_id=1000 + index % 7,
                group_id=2000 + index % 5,
                message_id=index,
                message=[{"type": "image", "data": {"file": "f%d" % index}}],
            )
            self.assertTrue(buf.offer(event, 999))

        stats = buf.stats()
        self.assertLessEqual(stats["pairs"], 3)
        self.assertLessEqual(stats["items"], 6)
        self.assertLessEqual(stats["bytes"], 200000)

        # Every surviving pair still holds at most max_items_per_pair and
        # taking one pair never exposes another pair's items.
        total = 0
        for group in range(2000, 2005):
            for user in range(1000, 1007):
                taken = buf.take(group, user)
                self.assertLessEqual(len(taken), 2)
                for item in taken:
                    self.assertIsInstance(item["message_id"], str)
                total += len(taken)
        self.assertEqual(total, stats["items"])
        self.assertEqual(buf.stats()["items"], 0)

    def test_oldest_pair_is_evicted(self):
        buf, _clock = make_buffer(max_pairs=1)
        self.assertTrue(buf.offer(
            make_event(group_id=222, message_id="first"), 999))
        self.assertTrue(buf.offer(
            make_event(group_id=333, message_id="second"), 999))
        self.assertEqual(buf.take(222, 111), [])
        self.assertEqual(
            [item["message_id"] for item in buf.take(333, 111)], ["second"])

    def test_oldest_item_within_pair_is_evicted(self):
        buf, _clock = make_buffer(max_items_per_pair=2)
        for message_id in (1, 2, 3):
            self.assertTrue(buf.offer(
                make_event(message_id=message_id), 999))
        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["2", "3"],
        )

    def test_byte_budget_evicts_oldest_across_pairs(self):
        buf, _clock = make_buffer(
            max_pairs=64, max_items_per_pair=4, max_total_bytes=1000,
        )
        for index in range(50):
            self.assertTrue(buf.offer(
                make_event(
                    user_id=1000 + index,
                    group_id=222,
                    message_id=index,
                    message=[{"type": "image",
                              "data": {"file": "f%d.jpg" % index}}],
                ),
                999,
            ))
        stats = buf.stats()
        self.assertLessEqual(stats["bytes"], 1000)
        self.assertLess(stats["items"], 50)

        remaining = []
        for index in range(50):
            remaining.extend(buf.take(222, 1000 + index))
        remaining_ids = [item["message_id"] for item in remaining]
        self.assertIn("49", remaining_ids)     # newest is kept
        self.assertNotIn("0", remaining_ids)   # oldest was evicted

    def test_default_buffer_stays_bounded_under_media_flood(self):
        buf, _clock = make_buffer()
        for index in range(500):
            self.assertTrue(buf.offer(
                make_event(
                    user_id=1 + index % 80,
                    group_id=1 + index % 80,
                    message_id=index,
                ),
                999,
            ))
        stats = buf.stats()
        self.assertLessEqual(stats["pairs"], 64)
        self.assertLessEqual(stats["items"], 64 * 16)
        self.assertLessEqual(stats["bytes"], 2 * 1024 * 1024)


class TestRestore(unittest.TestCase):
    def test_restore_after_full_queue_preserves_order(self):
        buf, clock = make_buffer(ttl_seconds=100, max_items_per_pair=8)
        for message_id in ("a", "b"):
            self.assertTrue(buf.offer(
                make_event(message_id=message_id), 999))
        batch = buf.take(222, 111)
        self.assertEqual([item["message_id"] for item in batch], ["a", "b"])

        # While the reader's group queue was full, newer media arrived.
        clock.advance(5)
        self.assertTrue(buf.offer(make_event(message_id="c"), 999))

        self.assertEqual(buf.restore(222, 111, batch), 2)
        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["a", "b", "c"],
        )

    def test_restore_does_not_extend_original_expiry(self):
        buf, clock = make_buffer(ttl_seconds=100)
        self.assertTrue(buf.offer(make_event(message_id="a"), 999))
        clock.advance(50)
        batch = buf.take(222, 111)

        clock.advance(40)  # t = 90, item expires at t = 100
        self.assertEqual(buf.restore(222, 111, batch), 1)
        clock.advance(9.5)  # t = 99.5
        self.assertEqual(len(buf.take(222, 111)), 1)

        buf2, clock2 = make_buffer(ttl_seconds=100)
        self.assertTrue(buf2.offer(make_event(), 999))
        clock2.advance(50)
        batch2 = buf2.take(222, 111)
        clock2.advance(51)  # t = 101, past the original expiry
        self.assertEqual(buf2.restore(222, 111, batch2), 0)
        self.assertEqual(buf2.stats()["items"], 0)

    def test_restore_of_expired_batch_is_a_no_op(self):
        buf, clock = make_buffer(ttl_seconds=10)
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        clock.advance(10)
        self.assertEqual(buf.restore(222, 111, batch), 0)
        self.assertEqual(buf.take(222, 111), [])

    def test_double_restore_only_inserts_once(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        self.assertEqual(buf.restore(222, 111, batch), 1)
        self.assertEqual(buf.restore(222, 111, batch), 0)
        self.assertEqual(len(buf.take(222, 111)), 1)

    def test_restore_copy_shares_reservations(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        clone = batch.copy()
        self.assertIsInstance(clone, PendingMediaBatch)
        self.assertEqual(clone, batch)
        self.assertEqual(buf.restore(222, 111, clone), 1)
        # The original batch was consumed through the shared reservations.
        self.assertEqual(buf.restore(222, 111, batch), 0)

    def test_restore_wrong_pair_is_rejected(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        with self.assertRaises(ValueError):
            buf.restore(333, 111, batch)
        with self.assertRaises(ValueError):
            buf.restore(222, 112, batch)
        # Still restorable for the original pair.
        self.assertEqual(buf.restore(222, 111, batch), 1)

    def test_restore_requires_batch_object(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        with self.assertRaises(TypeError):
            buf.restore(222, 111, list(batch))
        with self.assertRaises(TypeError):
            buf.restore(222, 111, None)
        with self.assertRaises(ValueError):
            buf.restore("bad-id", 111, batch)

    def test_restore_ignores_forged_reservation_payloads(self):
        buf, _clock = make_buffer()
        forged = PendingMediaBatch(key=("222", "111"))
        forged._reservations = ("not-a-reservation", object(), None)
        self.assertEqual(buf.restore(222, 111, forged), 0)
        malformed = PendingMediaBatch(key=("222", "111"))
        malformed._reservations = "not-iterable"
        self.assertEqual(buf.restore(222, 111, malformed), 0)

    def test_restore_respects_item_bounds(self):
        buf, _clock = make_buffer(max_items_per_pair=2)
        for message_id in ("1", "2"):
            self.assertTrue(buf.offer(
                make_event(message_id=message_id), 999))
        batch = buf.take(222, 111)

        for message_id in ("3", "4"):
            self.assertTrue(buf.offer(
                make_event(message_id=message_id), 999))
        self.assertEqual(buf.restore(222, 111, batch), 2)

        # Restored items are older, so the per-pair cap keeps the two newest.
        stats = buf.stats()
        self.assertEqual(stats["items"], 2)
        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["3", "4"],
        )

    def test_restore_recomputes_size_and_respects_byte_budget(self):
        buf, _clock = make_buffer(max_total_bytes=400)
        self.assertTrue(buf.offer(
            make_event(message=[{"type": "image",
                                 "data": {"file": "a.jpg"}}]), 999))
        batch = buf.take(222, 111)
        # Tamper with the (private) reservation size; restore must not trust
        # it and must recompute from the segments.
        for reservation in batch._reservations:
            reservation.size = 0
        self.assertEqual(buf.restore(222, 111, batch), 1)
        self.assertGreater(buf.stats()["bytes"], 0)
        self.assertLessEqual(buf.stats()["bytes"], 400)

    def test_restored_items_are_fresh_copies(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        batch = buf.take(222, 111)
        self.assertEqual(buf.restore(222, 111, batch), 1)
        again = buf.take(222, 111)
        self.assertIsNot(again[0], batch[0])
        again[0]["segments"][0]["data"]["file"] = "changed"
        self.assertEqual(buf.restore(222, 111, again), 1)
        final = buf.take(222, 111)
        self.assertEqual(final[0]["segments"][0]["data"]["file"], "a.jpg")


class TestClearAndStats(unittest.TestCase):
    def test_clear_removes_items_dedup_and_reservations(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(message_id="a"), 999))
        self.assertTrue(buf.offer(
            make_event(user_id=112, message_id="b"), 999))
        batch = buf.take(222, 111)

        buf.clear()
        self.assertEqual(buf.stats(), {
            "pairs": 0, "items": 0, "bytes": 0, "dedup_keys": 0,
        })
        self.assertEqual(buf.take(222, 112), [])
        # A batch taken before clear can no longer be restored.
        self.assertEqual(buf.restore(222, 111, batch), 0)
        # Dedup state was cleared, so the same event can be offered again.
        self.assertTrue(buf.offer(make_event(message_id="a"), 999))

    def test_empty_pairs_are_not_retained(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        self.assertEqual(buf.stats()["pairs"], 1)
        buf.take(222, 111)
        stats = buf.stats()
        self.assertEqual(stats["pairs"], 0)
        self.assertEqual(stats["items"], 0)
        self.assertEqual(stats["bytes"], 0)

    def test_stats_exposes_counts_only(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(
            make_event(user_id=987654321, group_id=123456789), 999))
        stats = buf.stats()
        self.assertEqual(
            set(stats), {"pairs", "items", "bytes", "dedup_keys"})
        for value in stats.values():
            self.assertIsInstance(value, int)
        text = repr(stats)
        self.assertNotIn("987654321", text)
        self.assertNotIn("123456789", text)
        self.assertNotIn("a.jpg", text)

    def test_stats_bytes_return_to_zero_after_take(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        self.assertGreater(buf.stats()["bytes"], 0)
        buf.take(222, 111)
        self.assertEqual(buf.stats()["bytes"], 0)


class TestConstructor(unittest.TestCase):
    def test_defaults_are_accepted(self):
        buf = PendingGroupMedia()
        self.assertEqual(
            buf.stats(), {
                "pairs": 0, "items": 0, "bytes": 0, "dedup_keys": 0,
            })

    def test_invalid_configuration_is_rejected(self):
        bad_kwargs = [
            {"ttl_seconds": 0},
            {"ttl_seconds": -1},
            {"ttl_seconds": True},
            {"ttl_seconds": float("nan")},
            {"ttl_seconds": float("inf")},
            {"max_pairs": 0},
            {"max_pairs": -3},
            {"max_pairs": True},
            {"max_items_per_pair": 0},
            {"max_total_bytes": 0},
            {"max_segments": 0},
            {"max_segment_bytes": 0},
            {"max_string_chars": 0},
            {"max_json_depth": 0},
            {"max_json_nodes": 0},
            {"clock": None},
            {"clock": "not callable"},
        ]
        for kwargs in bad_kwargs:
            with self.assertRaises((ValueError, TypeError), msg=repr(kwargs)):
                PendingGroupMedia(**kwargs)

    def test_custom_limits_are_effective(self):
        buf, _clock = make_buffer(
            ttl_seconds=1, max_pairs=1, max_items_per_pair=1,
            max_total_bytes=5000, max_segments=1,
        )
        self.assertTrue(buf.offer(make_event(), 999))
        self.assertTrue(buf.offer(
            make_event(user_id=112, message_id=2), 999))
        # max_pairs=1: the first pair was evicted.
        self.assertEqual(buf.take(222, 111), [])
        self.assertEqual(len(buf.take(222, 112)), 1)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_offer_take_restore_and_stats(self):
        buf, _clock = make_buffer(
            max_pairs=4, max_items_per_pair=4, max_total_bytes=100000,
        )
        failures = []
        start = threading.Barrier(6)

        def worker(worker_id):
            try:
                start.wait(timeout=5)
                for index in range(60):
                    event = make_event(
                        user_id=100 + worker_id,
                        group_id=200 + worker_id % 3,
                        message_id=worker_id * 1000 + index,
                    )
                    buf.offer(event, 999)
                    taken = buf.take(200 + worker_id % 3, 100 + (worker_id + 1) % 6)
                    if taken:
                        buf.restore(200 + worker_id % 3, 100 + (worker_id + 1) % 6, taken)
                    buf.stats()
                    if index % 37 == 36:
                        buf.clear()
            except Exception as exc:  # pragma: no cover - failure path
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(failures, [])
        stats = buf.stats()
        self.assertLessEqual(stats["pairs"], 4)
        self.assertLessEqual(stats["items"], 16)
        self.assertLessEqual(stats["bytes"], 100000)

    def test_concurrent_workers_cannot_steal_other_senders_media(self):
        buf, _clock = make_buffer(max_pairs=8, max_items_per_pair=8)
        for index in range(40):
            self.assertTrue(buf.offer(
                make_event(user_id=100 + index % 4, message_id=index), 999))

        stolen = []
        lock = threading.Lock()

        def worker(sender):
            for _ in range(50):
                for item in buf.take(222, sender):
                    with lock:
                        stolen.append((sender, item["message_id"]))

        threads = [threading.Thread(target=worker, args=(100 + i,))
                   for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        # Every taken item must have been offered by the same sender.
        for sender, message_id in stolen:
            self.assertEqual(int(message_id) % 4, sender - 100)


class TestBatchObject(unittest.TestCase):
    def test_empty_take_returns_empty_batch(self):
        buf, _clock = make_buffer()
        batch = buf.take(222, 111)
        self.assertIsInstance(batch, PendingMediaBatch)
        self.assertEqual(batch, [])
        self.assertFalse(batch)
        self.assertEqual(batch.pair, ("222", "111"))
        self.assertEqual(batch.restorable, 0)

    def test_restorable_count_tracks_reservations(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(message_id="a"), 999))
        self.assertTrue(buf.offer(make_event(message_id="b"), 999))
        batch = buf.take(222, 111)
        self.assertEqual(batch.restorable, 2)
        self.assertEqual(buf.restore(222, 111, batch), 2)
        self.assertEqual(batch.restorable, 0)

    def test_batch_items_are_independent_plain_json(self):
        buf, _clock = make_buffer()
        message = [
            {"type": "image", "data": {"file": "a.jpg",
                                       "flags": [1, 2, {"deep": True}]}},
            {"type": "file", "data": {"file": "b.pdf"}},
        ]
        self.assertTrue(buf.offer(make_event(message=message), 999))
        batch = buf.take(222, 111)
        encoded = json.dumps(batch)
        decoded = json.loads(encoded)
        self.assertEqual(decoded, [
            {"message_id": "1", "segments": [
                {"type": "image", "data": {"file": "a.jpg",
                                           "flags": [1, 2, {"deep": True}]}},
                {"type": "file", "data": {"file": "b.pdf"}},
            ]},
        ])
        for item in batch:
            self.assertEqual(set(item), {"message_id", "segments"})
            for segment in item["segments"]:
                self.assertEqual(set(segment), {"type", "data"})
                self.assertIsInstance(segment["data"], dict)

    def test_take_after_take_is_empty(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(), 999))
        self.assertEqual(len(buf.take(222, 111)), 1)
        self.assertEqual(buf.take(222, 111), [])


class TestNoCrossPairDisclosure(unittest.TestCase):
    def test_same_sender_in_another_group_is_not_attached(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(
            user_id=111, group_id=222, message_id="g222"), 999))
        self.assertTrue(buf.offer(make_event(
            user_id=111, group_id=333, message_id="g333"), 999))

        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["g222"],
        )
        self.assertEqual(
            [item["message_id"] for item in buf.take(333, 111)],
            ["g333"],
        )

    def test_another_sender_in_same_group_is_not_attached(self):
        buf, _clock = make_buffer()
        self.assertTrue(buf.offer(make_event(
            user_id=111, group_id=222, message_id="u111"), 999))
        self.assertTrue(buf.offer(make_event(
            user_id=112, group_id=222, message_id="u112"), 999))

        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 111)],
            ["u111"],
        )
        self.assertEqual(
            [item["message_id"] for item in buf.take(222, 112)],
            ["u112"],
        )




if __name__ == "__main__":
    unittest.main()
