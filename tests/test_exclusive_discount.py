"""Security tests for exclusive 20% pricing, holds, and signup."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


EVENT_ID = 'halloween-2026'


class ExclusiveDiscountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.patches = [
            mock.patch.object(thesection, 'tickets_file', os.path.join(root, 'tickets.json')),
            mock.patch.object(thesection, 'members_file', os.path.join(root, 'members.json')),
            mock.patch.object(thesection, 'invites_file', os.path.join(root, 'invites.json')),
            mock.patch.object(thesection, 'exclusive_holds_file', os.path.join(root, 'holds.json')),
            mock.patch.object(thesection, 'events_file', os.path.join(root, 'events.json')),
            mock.patch.object(thesection, 'full_mailing_list_file', os.path.join(root, 'full.json')),
            mock.patch.object(thesection, 'scanner_settings_file', os.path.join(root, 'scanner.json')),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_invites([])
        thesection.save_exclusive_holds({})
        thesection.save_events([{
            'id': EVENT_ID,
            'name': 'Halloween',
            'date': '2026-10-24',
            'sales_open': True,
            'ticket_cap': 200,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }])
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _write_member(self, email, *, exclusive=False, purchases=False):
        member = {
            'email': email,
            'password_hash': thesection.hash_password('password123'),
            'saved_tickets': [],
            'discount_code': 'TEST-ABCD',
            'joined_at': datetime.now(timezone.utc).isoformat(),
        }
        if exclusive:
            member['returning_guest_discount'] = True
        thesection.save_members([member])
        if purchases:
            thesection.save_tickets([{
                'session_id': 'cs_past',
                'ticket_id': 'PASTTICKET',
                'email': email,
                'quantity': 2,
                'ticket_type': 'general',
                'legacy_discount': False,
                'exclusive_single_rate': False,
                'event_id': EVENT_ID,
            }])
        return member

    def _login_ctx(self, email):
        return self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '127.0.0.1'})

    def _with_login(self, email, fn):
        with self.app.test_request_context('/'):
            thesection.session['legacy_member_email'] = email
            return fn()

    def test_parse_discount_value_caps_at_90_percent(self):
        self.assertEqual(thesection.parse_discount_value('200'), 0.90)
        self.assertEqual(thesection.parse_discount_value('20'), 0.20)
        self.assertEqual(thesection.parse_discount_value('0.2'), 0.20)
        self.assertEqual(thesection.parse_discount_value('-1', 0.15), 0.15)

    def test_regular_member_gets_10_not_20(self):
        self._write_member('member@example.com', exclusive=False, purchases=True)

        def run():
            rate = thesection.active_member_discount_rate(1, event_id=EVENT_ID)
            total = thesection.calculate_total_cents('general', 1, True, EVENT_ID)
            return rate, total

        rate, total = self._with_login('member@example.com', run)
        self.assertAlmostEqual(rate, 0.10)
        self.assertEqual(total, 900)

    def test_exclusive_single_gets_20(self):
        self._write_member('vip@example.com', exclusive=True)

        def run():
            self.assertTrue(thesection.exclusive_single_rate_available(
                thesection.get_logged_in_member(), EVENT_ID, 1
            ))
            return thesection.calculate_total_cents('general', 1, True, EVENT_ID)

        self.assertEqual(self._with_login('vip@example.com', run), 800)

    def test_exclusive_qty_two_gets_10(self):
        self._write_member('vip@example.com', exclusive=True)

        def run():
            self.assertFalse(thesection.exclusive_single_rate_available(
                thesection.get_logged_in_member(), EVENT_ID, 2
            ))
            return thesection.calculate_total_cents('general', 2, True, EVENT_ID)

        self.assertEqual(self._with_login('vip@example.com', run), 1800)

    def test_exclusive_used_once_then_10(self):
        self._write_member('vip@example.com', exclusive=True)
        thesection.save_tickets([{
            'session_id': 'cs_used',
            'ticket_id': 'USEDTICKET',
            'email': 'vip@example.com',
            'quantity': 1,
            'ticket_type': 'general',
            'legacy_discount': True,
            'exclusive_single_rate': True,
            'event_id': EVENT_ID,
        }])

        def run():
            member = thesection.get_logged_in_member()
            self.assertTrue(thesection.exclusive_single_rate_used_for_event(member, EVENT_ID))
            self.assertFalse(thesection.exclusive_single_rate_available(member, EVENT_ID, 1))
            return thesection.calculate_total_cents('general', 1, True, EVENT_ID)

        self.assertEqual(self._with_login('vip@example.com', run), 900)

    def test_guest_cannot_force_member_discount(self):
        with self.app.test_request_context('/api/pricing?apply_member_discount=1'):
            self.assertFalse(thesection.resolve_member_discount_application(True))
            self.assertEqual(thesection.active_member_discount_rate(1, event_id=EVENT_ID), 0.0)
            self.assertEqual(thesection.calculate_total_cents('general', 1, True, EVENT_ID), 1000)

    def test_discount_code_query_does_not_change_price(self):
        self._write_member('vip@example.com', exclusive=True)
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['legacy_member_email'] = 'nobody@example.com'
        resp = client.get(
            f'/api/pricing?ticket_type=general&quantity=1&apply_member_discount=1'
            f'&discount_code=TEST-ABCD&event_id={EVENT_ID}'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['total_cents'], 1000)
        self.assertFalse(data['returning_guest_single_ticket_rate'])

    def test_parallel_hold_blocks_second_20(self):
        self._write_member('vip@example.com', exclusive=True)
        self.assertTrue(thesection.reserve_exclusive_single_rate('vip@example.com', EVENT_ID))
        self.assertFalse(thesection.reserve_exclusive_single_rate('vip@example.com', EVENT_ID))

        def run():
            member = thesection.get_logged_in_member()
            self.assertFalse(thesection.exclusive_single_rate_available(member, EVENT_ID, 1))
            return thesection.calculate_total_cents('general', 1, True, EVENT_ID)

        self.assertEqual(self._with_login('vip@example.com', run), 900)

    def test_missing_event_id_does_not_grant_20(self):
        self._write_member('vip@example.com', exclusive=True)
        with mock.patch.object(thesection, 'get_sales_event_id', return_value=None):
            member = {'email': 'vip@example.com', 'returning_guest_discount': True}
            self.assertFalse(thesection.exclusive_single_rate_available(member, None, 1))

    def test_logged_out_checkout_rejected(self):
        client = self.app.test_client()
        page = client.get('/')
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/create-checkout-session',
            json={'quantity': 1, 'ticket_type': 'general', 'apply_member_discount': True},
            headers={'X-CSRF-Token': token},
        )
        self.assertEqual(resp.status_code, 401)

    def test_exclusive_email_cannot_self_register(self):
        thesection.add_emails_to_invite_list(['special@example.com'])
        client = self.app.test_client()
        page = client.get('/legacy')
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'register',
                'email': 'special@example.com',
                'password': 'password123',
                'confirm_password': 'password123',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'exclusive invite', resp.data)
        self.assertIsNone(thesection.get_legacy_member('special@example.com'))

    def test_invalid_email_cannot_register(self):
        client = self.app.test_client()
        page = client.get('/legacy')
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'register',
                'email': 'not-an-email',
                'password': 'password123',
                'confirm_password': 'password123',
                'csrf_token': token,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'valid email', resp.data)
        self.assertIsNone(thesection.get_legacy_member('not-an-email'))

    def test_public_register_does_not_get_exclusive_flag(self):
        client = self.app.test_client()
        page = client.get('/legacy')
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'register',
                'email': 'normal@example.com',
                'password': 'password123',
                'confirm_password': 'password123',
                'csrf_token': token,
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        member = thesection.get_legacy_member('normal@example.com')
        self.assertIsNotNone(member)
        self.assertFalse(member.get('returning_guest_discount'))
        self.assertFalse(thesection.exclusive_single_rate_available(member, EVENT_ID, 1))

    def test_is_valid_email(self):
        self.assertTrue(thesection.is_valid_email('a@b.co'))
        self.assertTrue(thesection.is_valid_email('user.name+tag@sub.domain.co.uk'))
        self.assertFalse(thesection.is_valid_email(''))
        self.assertFalse(thesection.is_valid_email('nope'))
        self.assertFalse(thesection.is_valid_email("foo@x.com');alert(1);//"))
        self.assertFalse(thesection.is_valid_email('<a@b.co>'))
        self.assertFalse(thesection.is_valid_email('a@b'))


if __name__ == '__main__':
    unittest.main()
