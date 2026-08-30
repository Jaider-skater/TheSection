"""Defensive hardening: GET preview, logout, CSP, oversell, export redaction."""
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
SCANNER_EMAIL = 'door@example.com'
SCANNER_PASSWORD = 'scanner-secret-not-real'


class SecurityHardeningTests(unittest.TestCase):
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
            mock.patch.object(thesection, 'verify_login_emails', {SCANNER_EMAIL}),
            mock.patch.object(thesection, 'verify_login_password', SCANNER_PASSWORD),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_scanner_settings({})
        thesection.save_events([
            thesection.normalize_event({
                'id': EVENT_ID,
                'name': 'Halloween',
                'date': '2026-10-24',
                'sales_open': True,
                'ticket_cap': 2,
            }),
        ])
        thesection.set_door_event_id(EVENT_ID)
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _ticket(self, ticket_id, qty=1, scanned=False, session_id=None):
        return {
            'session_id': session_id or f'cs_{ticket_id}',
            'ticket_id': ticket_id,
            'email': 'guest@example.com',
            'quantity': qty,
            'ticket_type': 'general',
            'event_id': EVENT_ID,
            'scanned_at': datetime.now(timezone.utc).isoformat() if scanned else None,
            'admission_as': 'ga' if scanned else None,
            'view_token': 'secret-view-token',
        }

    def _auth_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['verify_authenticated'] = True
            sess['verify_login_email'] = SCANNER_EMAIL
            sess['admin_authenticated'] = True
            sess['legacy_member_email'] = SCANNER_EMAIL
        return client

    def test_get_verify_does_not_set_scanned_at(self):
        thesection.save_tickets([self._ticket('PREVIEW1')])
        client = self._auth_client()
        resp = client.get('/verify/t/PREVIEW1')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Valid ticket', resp.data)
        self.assertIn(b'Not scanned yet', resp.data)
        record = thesection.get_ticket_record('PREVIEW1')
        self.assertIsNone(record.get('scanned_at'))

    def test_post_verify_sets_scanned_at(self):
        thesection.save_tickets([self._ticket('STAMP1')])
        client = self._auth_client()
        page = client.get('/verify/t/STAMP1')
        token = page.headers.get('X-CSRF-Token')
        self.assertTrue(token)
        resp = client.post('/verify/t/STAMP1', headers={'X-CSRF-Token': token})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"You're In!", resp.data)
        record = thesection.get_ticket_record('STAMP1')
        self.assertIsNotNone(record.get('scanned_at'))

    def test_check_ticket_preview_helper_does_not_stamp(self):
        thesection.save_tickets([self._ticket('HELPER1')])
        result = thesection.check_ticket('HELPER1', stamp=False)
        self.assertEqual(result['status'], 'valid')
        self.assertIsNone(thesection.get_ticket_record('HELPER1').get('scanned_at'))
        stamped = thesection.check_ticket('HELPER1', stamp=True)
        self.assertEqual(stamped['status'], 'accepted')
        self.assertIsNotNone(thesection.get_ticket_record('HELPER1').get('scanned_at'))

    def test_verify_logout_clears_session_flags(self):
        client = self._auth_client()
        page = client.get('/verify')
        token = page.headers.get('X-CSRF-Token')
        resp = client.post('/verify/logout', headers={'X-CSRF-Token': token}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        with client.session_transaction() as sess:
            self.assertFalse(sess.get('verify_authenticated'))
            self.assertFalse(sess.get('admin_authenticated'))
            self.assertFalse(sess.get('legacy_member_email'))
            self.assertFalse(sess.get('verify_login_email'))

    def test_fulfill_over_cap_still_issues_and_flags_oversell(self):
        thesection.save_tickets([self._ticket('FULL1', qty=2, session_id='cs_full')])
        paid = {
            'id': 'cs_paid_over',
            'metadata': {'ticket_type': 'general', 'event_id': EVENT_ID},
            'customer_details': {'email': 'buyer@example.com'},
            'line_items': {'data': [{'quantity': 1}]},
            'payment_status': 'paid',
        }
        with self.app.test_request_context('/success'):
            ticket = thesection.fulfill_paid_checkout(paid)
            again = thesection.fulfill_paid_checkout(paid)
        self.assertIsNotNone(ticket)
        self.assertTrue(ticket.get('oversold'))
        self.assertTrue(thesection.get_ticket_record(ticket['ticket_id']))
        # Paid guest success URL still works: same session is idempotent.
        self.assertEqual(again['ticket_id'], ticket['ticket_id'])

    def test_fulfill_within_cap_not_oversold(self):
        paid = {
            'id': 'cs_paid_ok',
            'metadata': {'ticket_type': 'general', 'event_id': EVENT_ID},
            'customer_details': {'email': 'ok@example.com'},
            'line_items': {'data': [{'quantity': 1}]},
            'payment_status': 'paid',
        }
        with self.app.test_request_context('/success'):
            ticket = thesection.fulfill_paid_checkout(paid)
        self.assertFalse(ticket.get('oversold'))

    def test_production_csp_includes_stripe(self):
        with mock.patch.object(thesection, 'IS_PRODUCTION', True):
            client = self.app.test_client()
            resp = client.get('/')
            csp = resp.headers.get('Content-Security-Policy', '')
            self.assertIn('js.stripe.com', csp)
            self.assertIn('checkout.stripe.com', csp)
            self.assertIn("default-src 'self'", csp)
            self.assertIn('cdn.tailwindcss.com', csp)
            self.assertIn('unpkg.com', csp)

    def test_dev_does_not_send_csp(self):
        client = self.app.test_client()
        resp = client.get('/')
        self.assertFalse(resp.headers.get('Content-Security-Policy'))

    def test_admin_exports_strip_session_id(self):
        thesection.save_tickets([self._ticket('EXPORT1', session_id='cs_secret_session')])
        client = self._auth_client()
        json_resp = client.get('/admin/tickets.json')
        self.assertEqual(json_resp.status_code, 200)
        payload = json.loads(json_resp.get_data(as_text=True))
        self.assertEqual(len(payload), 1)
        self.assertNotIn('session_id', payload[0])
        self.assertNotIn('view_token', payload[0])
        self.assertNotIn('cs_secret_session', json_resp.get_data(as_text=True))
        csv_resp = client.get('/admin/tickets.csv')
        csv_text = csv_resp.get_data(as_text=True)
        self.assertNotIn('cs_secret_session', csv_text)
        self.assertIn('oversold', csv_text.splitlines()[0])

    def test_client_ip_uses_remote_addr_not_leftmost_xff(self):
        with self.app.test_request_context(
            '/',
            headers={'X-Forwarded-For': '9.9.9.9, 10.0.0.1'},
            environ_base={'REMOTE_ADDR': '10.0.0.1'},
        ):
            self.assertEqual(thesection.client_ip(), '10.0.0.1')

    def test_scanner_password_still_matches_env_secret(self):
        self.assertTrue(thesection.verify_scanner_credentials(SCANNER_EMAIL, SCANNER_PASSWORD))
        self.assertFalse(thesection.verify_scanner_credentials(SCANNER_EMAIL, 'wrong-password'))


if __name__ == '__main__':
    unittest.main()
