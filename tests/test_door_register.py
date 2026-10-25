"""Door register: tally Stripe-app walk-up sales on the Door Scanner."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


class DoorRegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.patches = [
            mock.patch.object(thesection, name, os.path.join(root, f'{name}.json'))
            for name in ('tickets_file', 'members_file', 'invites_file', 'exclusive_holds_file',
                         'events_file', 'full_mailing_list_file', 'scanner_settings_file')
        ] + [
            mock.patch.object(thesection, 'verify_auth_configured', return_value=True),
            mock.patch.object(thesection, 'verify_authenticated', return_value=True),
        ]
        for p in self.patches:
            p.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_scanner_settings({})
        thesection.save_events([thesection.normalize_event({
            'id': 'xmas', 'name': 'Christmas', 'date': '2026-12-25', 'sales_open': True, 'ticket_cap': 5,
        })])
        thesection.set_door_event_id('xmas')
        thesection.app.config['TESTING'] = True
        self.client = thesection.app.test_client()
        page = self.client.get('/verify')
        self.assertEqual(page.status_code, 200)
        self.page = page.data
        self.csrf = page.headers.get('X-CSRF-Token')

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def sell(self, ticket_type='general', quantity=1, token='tok_aaaaaaaa1', csrf=True):
        headers = {'X-CSRF-Token': self.csrf} if csrf else {}
        return self.client.post('/api/door-register/sale', json={
            'ticket_type': ticket_type, 'quantity': quantity, 'token': token,
        }, headers=headers)

    def test_page_has_register(self):
        for marker in (b'door-register', b'door-sold-btn', b'door-qty-plus', b'door-type-vip', b'door-recent-list'):
            self.assertIn(marker, self.page)

    def test_sale_counts_everywhere(self):
        r = self.sell('vip', 2)
        self.assertEqual(r.status_code, 200, r.get_json())
        t = r.get_json()['admission_totals']
        self.assertEqual(t['tickets_sold'], 2)
        self.assertEqual(t['tickets_remaining'], 3)
        self.assertEqual(t['vip'], 2)
        reg = t['door_register']
        self.assertEqual(reg['door_tickets'], 2)
        self.assertEqual(reg['door_cents'], 2 * thesection.door_unit_price_cents('vip'))
        self.assertEqual(reg['online_tickets'], 0)
        self.assertEqual(len(reg['recent']), 1)
        avail = thesection.get_ticket_availability('xmas')
        self.assertEqual(avail['remaining'], 3)

    def test_double_tap_is_idempotent(self):
        self.sell(token='same_token_1')
        r = self.sell(token='same_token_1')
        self.assertTrue(r.get_json()['duplicate'])
        self.assertEqual(r.get_json()['admission_totals']['tickets_sold'], 1)

    def test_blocks_past_capacity(self):
        self.assertEqual(self.sell(quantity=4, token='tok_cap_0001').status_code, 200)
        r = self.sell(quantity=2, token='tok_cap_0002')
        self.assertEqual(r.status_code, 409)
        self.assertIn('Only 1 left', r.get_json()['error'])
        self.assertEqual(self.sell(quantity=1, token='tok_cap_0003').status_code, 200)
        self.assertEqual(self.sell(quantity=1, token='tok_cap_0004').status_code, 409)

    def test_void_reverts_and_logs(self):
        r = self.sell(quantity=3, token='tok_void_001')
        ticket_id = r.get_json()['ticket_id']
        v = self.client.post('/api/door-register/void', json={'ticket_id': ticket_id},
                             headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(v.status_code, 200)
        t = v.get_json()['admission_totals']
        self.assertEqual(t['tickets_sold'], 0)
        self.assertEqual(t['ga'], 0)
        self.assertEqual(t['door_register']['void_count'], 1)
        self.assertEqual(t['door_register']['voids'][0]['quantity'], 3)
        # Retrying the voided token does not re-record it.
        self.assertEqual(self.sell(quantity=3, token='tok_void_001').status_code, 409)
        again = self.client.post('/api/door-register/void', json={'ticket_id': ticket_id},
                                 headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(again.status_code, 404)

    def test_void_only_register_sales(self):
        thesection.record_ticket('cs_online', 'ONLINE1', 'a@b.c', 1, event_id='xmas')
        v = self.client.post('/api/door-register/void', json={'ticket_id': 'ONLINE1'},
                             headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(v.status_code, 404)

    def test_requires_csrf(self):
        self.assertIn(self.sell(csrf=False).status_code, (400, 403))
        self.assertEqual(thesection.load_tickets(), [])

    def test_requires_auth(self):
        with mock.patch.object(thesection, 'verify_authenticated', return_value=False):
            self.assertEqual(self.sell().status_code, 401)

    def test_rejects_bad_input(self):
        self.assertEqual(self.sell(token='x').status_code, 400)
        self.assertEqual(self.sell(ticket_type='free', token='tok_bad_0001').status_code, 400)
        self.assertEqual(self.sell(quantity=0, token='tok_bad_0002').status_code, 400)

    def test_online_vs_door_split(self):
        thesection.record_ticket('cs_online', 'ONLINE1', 'a@b.c', 2, event_id='xmas')
        self.sell(token='tok_split_01')
        reg = thesection.get_door_register_state('xmas')
        self.assertEqual(reg['online_tickets'], 2)
        self.assertEqual(reg['door_tickets'], 1)
