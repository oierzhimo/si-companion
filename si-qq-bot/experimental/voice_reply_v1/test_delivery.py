import unittest
from delivery import Delivery


class DeliveryTests(unittest.TestCase):
    def test_sequential_ack_and_completion(self):
        s = Delivery(2).begin().acknowledge('success')
        self.assertEqual((s.phase, s.confirmed), ('ready', 1))
        s = s.begin().acknowledge('success')
        self.assertEqual((s.phase, s.confirmed), ('done', 2))
        with self.assertRaises(ValueError):
            s.begin()

    def test_only_first_known_failure_allows_text_fallback(self):
        self.assertTrue(Delivery(2).begin().acknowledge('failure').text_fallback_allowed)
        s = Delivery(2).begin().acknowledge('success').begin().acknowledge('failure')
        self.assertFalse(s.text_fallback_allowed)
        with self.assertRaises(ValueError):
            s.begin()

    def test_timeout_or_restart_never_allows_auto_retry(self):
        for s in (Delivery(2).begin().acknowledge('unknown'),
                  Delivery(2).begin().recover_after_restart()):
            self.assertEqual(s.phase, 'unknown')
            self.assertFalse(s.text_fallback_allowed)
            with self.assertRaises(ValueError):
                s.begin()

    def test_invalid_restored_state_is_rejected(self):
        for kwargs in ({'total': 0}, {'total': 2, 'confirmed': 2, 'phase': 'ready'},
                       {'total': 2, 'confirmed': 0, 'phase': 'done'}):
            with self.assertRaises(ValueError):
                Delivery(**kwargs)


if __name__ == '__main__':
    unittest.main()
