"""Backing out of / expiring a 20% checkout frees the perk; paid or unconfirmed sessions keep it."""
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')

from tests.test_exclusive_discount import ExclusiveDiscountTests, EVENT_ID, thesection  # noqa: E402

EMAIL = 'vip@example.com'


class CancelReleaseTests(ExclusiveDiscountTests):
    def _hold(self, sid='cs_test_1'):
        self.assertTrue(thesection.reserve_exclusive_single_rate(EMAIL, EVENT_ID))
        thesection.bind_exclusive_hold(EMAIL, EVENT_ID, sid)
        self.assertTrue(thesection.exclusive_hold_active(EMAIL, EVENT_ID))

    def test_cancel_return_expires_session_and_frees_hold(self):
        self._hold()
        token = thesection.exclusive_cancel_token(EMAIL, EVENT_ID)
        with mock.patch.object(thesection.stripe.checkout.Session, 'retrieve',
                               return_value={'id': 'cs_test_1', 'status': 'open', 'payment_status': 'unpaid'}), \
             mock.patch.object(thesection.stripe.checkout.Session, 'expire') as exp:
            r = self.app.test_client().get(f'/checkout/canceled?t={token}')
        self.assertEqual(r.status_code, 302)
        exp.assert_called_once_with('cs_test_1')
        self.assertFalse(thesection.exclusive_hold_active(EMAIL, EVENT_ID))
        self.assertTrue(thesection.reserve_exclusive_single_rate(EMAIL, EVENT_ID))

    def test_cancel_keeps_hold_if_paid(self):
        self._hold()
        with mock.patch.object(thesection.stripe.checkout.Session, 'retrieve',
                               return_value={'id': 'cs_test_1', 'status': 'complete', 'payment_status': 'paid'}), \
             mock.patch.object(thesection.stripe.checkout.Session, 'expire') as exp:
            self.assertFalse(thesection.release_exclusive_hold_on_cancel(EMAIL, EVENT_ID))
        exp.assert_not_called()
        self.assertTrue(thesection.exclusive_hold_active(EMAIL, EVENT_ID))

    def test_cancel_keeps_hold_if_stripe_unreachable(self):
        self._hold()
        with mock.patch.object(thesection.stripe.checkout.Session, 'retrieve', side_effect=Exception('down')):
            self.assertFalse(thesection.release_exclusive_hold_on_cancel(EMAIL, EVENT_ID))
        self.assertTrue(thesection.exclusive_hold_active(EMAIL, EVENT_ID))

    def test_bad_token_does_nothing(self):
        self._hold()
        r = self.app.test_client().get('/checkout/canceled?t=forged')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(thesection.exclusive_hold_active(EMAIL, EVENT_ID))

    def test_expired_webhook_frees_only_matching_session(self):
        self._hold('cs_a')
        self.assertFalse(thesection.release_exclusive_hold_for_session('cs_other'))
        self.assertTrue(thesection.exclusive_hold_active(EMAIL, EVENT_ID))
        self.assertTrue(thesection.release_exclusive_hold_for_session('cs_a'))
        self.assertFalse(thesection.exclusive_hold_active(EMAIL, EVENT_ID))

    def test_second_reservation_blocked_while_first_open(self):
        self._hold()
        self.assertFalse(thesection.reserve_exclusive_single_rate(EMAIL, EVENT_ID))


del ExclusiveDiscountTests


if __name__ == "__main__":
    unittest.main()
