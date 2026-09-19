"""Door scanner only accepts tickets for the selected night."""
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


class DoorScannerTests(unittest.TestCase):
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
        thesection.save_scanner_settings({})
        thesection.save_events([
            thesection.normalize_event({
                'id': 'halloween-2026',
                'name': 'Halloween',
                'date': '2026-10-24',
                'sales_open': True,
            }),
            thesection.normalize_event({
                'id': 'christmas-2026',
                'name': 'Christmas',
                'date': '2026-12-25',
                'sales_open': True,
            }),
        ])

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _ticket(self, ticket_id, event_id, scanned=False):
        return {
            'session_id': f'cs_{ticket_id}',
            'ticket_id': ticket_id,
            'email': 'guest@example.com',
            'quantity': 1,
            'ticket_type': 'general',
            'event_id': event_id,
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': datetime.now(timezone.utc).isoformat() if scanned else None,
            'admission_as': 'ga' if scanned else None,
        }

    def test_halloween_ticket_rejected_at_christmas_door(self):
        thesection.save_tickets([self._ticket('HALLOWEEN1', 'halloween-2026')])
        thesection.set_door_event_id('christmas-2026')
        result = thesection.check_ticket('HALLOWEEN1')
        self.assertEqual(result['status'], 'wrong_event')
        self.assertEqual(result['ticket_event_name'], 'Halloween')
        self.assertEqual(result['door_event_name'], 'Christmas')

    def test_christmas_ticket_accepted_at_christmas_door(self):
        thesection.save_tickets([self._ticket('XMAS1', 'christmas-2026')])
        thesection.set_door_event_id('christmas-2026')
        result = thesection.check_ticket('XMAS1')
        self.assertEqual(result['status'], 'accepted')

    def test_untagged_ticket_is_halloween_only(self):
        thesection.save_tickets([self._ticket('OLD1', '')])
        thesection.set_door_event_id('christmas-2026')
        self.assertEqual(thesection.check_ticket('OLD1')['status'], 'wrong_event')
        thesection.set_door_event_id('halloween-2026')
        self.assertEqual(thesection.check_ticket('OLD1')['status'], 'accepted')

    def test_no_door_event_rejects(self):
        thesection.save_tickets([self._ticket('HALLOWEEN1', 'halloween-2026')])
        thesection.save_scanner_settings({})
        result = thesection.check_ticket('HALLOWEEN1')
        self.assertEqual(result['status'], 'wrong_event')

    def test_one_time_reset_clears_scans_and_stamps_legacy(self):
        thesection.save_tickets([
            self._ticket('USED1', 'halloween-2026', scanned=True),
            self._ticket('OLD2', ''),
        ])
        thesection.save_scanner_settings({})
        cleared = thesection.apply_one_time_unused_ticket_reset()
        self.assertGreaterEqual(cleared, 1)
        tickets = {t['ticket_id']: t for t in thesection.load_tickets()}
        self.assertIsNone(tickets['USED1'].get('scanned_at'))
        self.assertEqual(tickets['OLD2']['event_id'], 'halloween-2026')
        # Second run is a no-op
        self.assertEqual(thesection.apply_one_time_unused_ticket_reset(), 0)

    def test_qr_url_extracts_ticket_id(self):
        self.assertEqual(
            thesection.extract_ticket_id_from_url(
                'https://thesection.onrender.com/verify/t/ABC123DEF'
            ),
            'ABC123DEF',
        )
        self.assertEqual(
            thesection.parse_scanned_ticket(
                'https://evil.example/verify/t/ABC123DEF?k=nope'
            ),
            'ABC123DEF',
        )
        self.assertEqual(thesection.parse_scanned_ticket('abc-123-def'), 'ABC123DEF')

    def test_json_write_roundtrip(self):
        path = os.path.join(self.tmp.name, 'roundtrip.json')
        self.assertTrue(thesection._locked_json_write(path, {'ok': True, 'n': 1}))
        self.assertEqual(thesection._locked_json_read(path, {}), {'ok': True, 'n': 1})
        self.assertTrue(thesection._locked_json_write(path, {'ok': False, 'n': 2}))
        self.assertEqual(thesection._locked_json_read(path, {}), {'ok': False, 'n': 2})

    def test_door_counts_only_include_tonight(self):
        halloween = self._ticket('HAL1', 'halloween-2026', scanned=True)
        halloween['quantity'] = 2
        christmas = self._ticket('XMAS1', 'christmas-2026', scanned=True)
        christmas['ticket_type'] = 'vip'
        christmas['admission_as'] = 'vip'
        thesection.save_tickets([halloween, christmas])

        thesection.set_door_event_id('christmas-2026')
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 0, 'vip': 1, 'total': 1, 'free_girls': 0},
        )
        thesection.set_door_event_id('halloween-2026')
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 2, 'vip': 0, 'total': 2, 'free_girls': 0},
        )

    def test_free_girl_adds_to_running_count_and_tracks_separately(self):
        thesection.set_door_event_id('halloween-2026')
        first = thesection.add_free_girls(1)
        self.assertEqual(first['free_girls'], 1)
        self.assertEqual(first['ga'], 1)
        self.assertEqual(first['total'], 1)
        second = thesection.add_free_girls(2)
        self.assertEqual(second['free_girls'], 3)
        self.assertEqual(second['ga'], 3)
        self.assertEqual(second['total'], 3)
        self.assertEqual(second['vip'], 0)

        thesection.set_door_event_id('christmas-2026')
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 0, 'vip': 0, 'total': 0, 'free_girls': 0},
        )
        thesection.add_free_girls(1)
        self.assertEqual(thesection.compute_admission_counts()['free_girls'], 1)

        thesection.set_door_event_id('halloween-2026')
        self.assertEqual(thesection.compute_admission_counts()['free_girls'], 3)

        undone = thesection.add_free_girls(-1)
        self.assertEqual(undone['free_girls'], 2)
        self.assertEqual(undone['ga'], 2)
        self.assertEqual(undone['total'], 2)

        thesection.reset_admission_counts()
        reset_counts = thesection.compute_admission_counts()
        self.assertEqual(reset_counts['free_girls'], 0)
        self.assertEqual(reset_counts['ga'], 0)
        self.assertEqual(reset_counts['total'], 0)

    def test_free_girl_requires_door_event(self):
        thesection.save_scanner_settings({})
        self.assertIsNone(thesection.add_free_girls(1))

    def test_free_girl_api_and_scanner_page(self):
        thesection.set_door_event_id('halloween-2026')
        paid = self._ticket('PAID1', 'halloween-2026', scanned=True)
        paid['quantity'] = 2
        thesection.save_tickets([paid])
        app = thesection.app
        app.config['TESTING'] = True
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=True):
            client = app.test_client()
            page = client.get('/verify')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'Add free girl', page.data)
            self.assertIn(b'total-free-girls', page.data)
            token = page.headers.get('X-CSRF-Token')
            resp = client.post(
                '/api/admission-totals/free-girl',
                json={'delta': 1},
                headers={'X-CSRF-Token': token},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data['free_girls'], 1)
            self.assertEqual(data['ga'], 3)
            self.assertEqual(data['total'], 3)
            undo = client.post(
                '/api/admission-totals/free-girl',
                json={'delta': -1},
                headers={'X-CSRF-Token': token},
            )
            self.assertEqual(undo.get_json()['free_girls'], 0)
            self.assertEqual(undo.get_json()['ga'], 2)
            self.assertNotIn(b'Undone', page.data)
            self.assertIn(b'Door sale', page.data)
            self.assertIn(b'door-ga-btn', page.data)
            self.assertIn(b'Tap to pay', page.data)
            self.assertIn(b'/api/door-payment-intent', page.data)
            self.assertIn(b'overflow-y-auto', page.data)
            self.assertIn(b'door-pay-wallet-hint', page.data)

    def test_apple_pay_domain_file_is_public(self):
        client = thesection.app.test_client()
        resp = client.get('/.well-known/apple-developer-merchantid-domain-association')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'<!DOCTYPE html>', resp.data[:80])
        self.assertGreater(len(resp.data), 100)

    def test_door_price_is_online_plus_five(self):
        self.assertEqual(thesection.door_surcharge_cents(), 500)
        self.assertEqual(thesection.door_unit_price_cents('general'), 1500)
        self.assertEqual(thesection.door_unit_price_cents('vip'), 3000)

    def test_door_payment_intent_charges_surcharge(self):
        thesection.set_door_event_id('halloween-2026')

        def fake_create(**kwargs):
            return mock.Mock(
                id='cs_door_1',
                client_secret='cs_door_1_secret',
                url=None,
            )

        with mock.patch.object(thesection.stripe, 'api_key', 'sk_test_door'), \
             mock.patch.object(thesection, 'stripe_publishable_key', 'pk_test_door'), \
             mock.patch.object(thesection.stripe.checkout.Session, 'create', side_effect=fake_create) as create:
            session = thesection.build_door_checkout_session(1, 'general', 'halloween-2026', embedded=True)
            self.assertEqual(session.client_secret, 'cs_door_1_secret')
            kwargs = create.call_args.kwargs
            self.assertEqual(kwargs['ui_mode'], 'embedded')
            self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 1500)
            self.assertEqual(kwargs['metadata']['door_sale'], 'true')
            self.assertEqual(kwargs['metadata']['event_id'], 'halloween-2026')

        app = thesection.app
        app.config['TESTING'] = True
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=True), \
             mock.patch.object(thesection.stripe, 'api_key', 'sk_test_door'), \
             mock.patch.object(thesection, 'stripe_publishable_key', 'pk_test_door'), \
             mock.patch.object(thesection.stripe.checkout.Session, 'create', side_effect=fake_create):
            client = app.test_client()
            token = client.get('/verify').headers.get('X-CSRF-Token')
            resp = client.post(
                '/api/door-payment-intent',
                json={'ticket_type': 'vip', 'quantity': 1},
                headers={'X-CSRF-Token': token},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data['client_secret'], 'cs_door_1_secret')
            self.assertEqual(data['amount'], 3000)
            self.assertEqual(data['session_id'], 'cs_door_1')

    def test_door_payment_complete_admits_walkup(self):
        thesection.set_door_event_id('halloween-2026')
        paid = {
            'id': 'cs_door_paid',
            'payment_status': 'paid',
            'metadata': {
                'ticket_type': 'general',
                'legacy_discount': 'false',
                'member_email': '',
                'event_id': 'halloween-2026',
                'exclusive_single_rate': 'false',
                'door_sale': 'true',
            },
            'customer_details': {'email': 'walkup@example.com'},
            'line_items': {'data': [{'quantity': 1}]},
        }
        app = thesection.app
        app.config['TESTING'] = True
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=True), \
             mock.patch.object(thesection.stripe, 'api_key', 'sk_test_door'), \
             mock.patch.object(thesection.stripe.checkout.Session, 'retrieve', return_value=paid), \
             mock.patch.object(thesection, 'deliver_ticket_email', return_value=True):
            client = app.test_client()
            token = client.get('/verify').headers.get('X-CSRF-Token')
            resp = client.post(
                '/api/door-payment-complete',
                json={'session_id': 'cs_door_paid'},
                headers={'X-CSRF-Token': token},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()['status'], 'accepted')
        tickets = thesection.load_tickets()
        self.assertEqual(len(tickets), 1)
        self.assertTrue(tickets[0].get('door_sale'))
        self.assertTrue(tickets[0].get('scanned_at'))
        self.assertEqual(thesection.compute_admission_counts()['ga'], 1)
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 1)

    def test_stripe_app_tap_to_pay_counts_ga_and_vip(self):
        thesection.set_door_event_id('halloween-2026')
        ga_pi = {
            'id': 'pi_taptopay_ga',
            'status': 'succeeded',
            'amount': 1500,
            'payment_method_types': ['card_present'],
            'latest_charge': {'payment_method_details': {'type': 'card_present'}},
            'metadata': {},
        }
        ga_ticket = thesection.fulfill_in_person_door_payment(ga_pi)
        self.assertEqual(ga_ticket['ticket_type'], 'general')
        self.assertTrue(ga_ticket.get('door_sale'))
        self.assertTrue(ga_ticket.get('scanned_at'))
        self.assertEqual(thesection.compute_admission_counts()['ga'], 1)
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 1)

        vip_pi = {
            'id': 'pi_taptopay_vip',
            'status': 'succeeded',
            'amount': 3000,
            'payment_method_types': ['card_present'],
            'latest_charge': {'payment_method_details': {'type': 'card_present'}},
            'metadata': {},
        }
        vip_ticket = thesection.fulfill_in_person_door_payment(vip_pi)
        self.assertEqual(vip_ticket['ticket_type'], 'vip')
        counts = thesection.compute_admission_counts()
        self.assertEqual(counts['ga'], 1)
        self.assertEqual(counts['vip'], 1)
        self.assertEqual(counts['total'], 2)
        again = thesection.fulfill_in_person_door_payment(ga_pi)
        self.assertEqual(again['ticket_id'], ga_ticket['ticket_id'])
        self.assertEqual(thesection.compute_admission_counts()['ga'], 1)

    def test_online_card_payment_is_not_counted_as_door_tap(self):
        thesection.set_door_event_id('halloween-2026')
        online = {
            'id': 'pi_online_fifteen',
            'status': 'succeeded',
            'amount': 1500,
            'payment_method_types': ['card'],
            'latest_charge': {'payment_method_details': {'type': 'card'}},
            'metadata': {},
        }
        self.assertIsNone(thesection.fulfill_in_person_door_payment(online))
        self.assertEqual(thesection.compute_admission_counts()['total'], 0)

        wrong_amount = {
            'id': 'pi_taptopay_ten',
            'status': 'succeeded',
            'amount': 1000,
            'payment_method_types': ['card_present'],
            'latest_charge': {'payment_method_details': {'type': 'card_present'}},
            'metadata': {},
        }
        self.assertIsNone(thesection.fulfill_in_person_door_payment(wrong_amount))
        self.assertEqual(thesection.compute_admission_counts()['total'], 0)

    def test_scanner_login_survives_dropped_flask_session(self):
        app = thesection.app
        app.config['TESTING'] = True
        with mock.patch.object(thesection, 'verify_login_emails', {'staff@example.com'}), \
             mock.patch.object(thesection, 'verify_login_password', 'doorpass12'), \
             mock.patch.object(thesection, 'IS_PRODUCTION', False):
            client = app.test_client()
            token = client.get('/verify/login').headers.get('X-CSRF-Token')
            login = client.post(
                '/verify/login',
                data={
                    'email': 'staff@example.com',
                    'password': 'doorpass12',
                    'csrf_token': token,
                },
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 302)
            self.assertTrue(client.get_cookie(thesection.SCANNER_LOGIN_COOKIE))
            with client.session_transaction() as sess:
                sess.clear()
            page = client.get('/verify')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'Door Scanner', page.data)

    def test_fulfill_paid_checkout_is_idempotent_and_rejects_unpaid(self):
        unpaid = {
            'id': 'cs_unpaid',
            'payment_status': 'unpaid',
            'metadata': {'ticket_type': 'general', 'event_id': 'halloween-2026'},
            'customer_details': {'email': 'buyer@example.com'},
            'line_items': {'data': [{'quantity': 1}]},
        }
        with self.assertRaises(ValueError):
            thesection.fulfill_paid_checkout(unpaid)
        self.assertEqual(thesection.load_tickets(), [])

        paid = {
            'id': 'cs_paid_1',
            'payment_status': 'paid',
            'metadata': {
                'ticket_type': 'general',
                'legacy_discount': 'false',
                'member_email': 'buyer@example.com',
                'event_id': 'halloween-2026',
                'exclusive_single_rate': 'false',
            },
            'customer_details': {'email': 'buyer@example.com'},
            'line_items': {'data': [{'quantity': 2}]},
        }
        first = thesection.fulfill_paid_checkout(paid)
        second = thesection.fulfill_paid_checkout(paid)
        self.assertEqual(first['ticket_id'], second['ticket_id'])
        tickets = thesection.load_tickets()
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]['quantity'], 2)
        self.assertEqual(tickets[0]['event_id'], 'halloween-2026')
        self.assertEqual(tickets[0]['email'], 'buyer@example.com')

    def test_ticket_email_is_only_claimed_once(self):
        thesection.record_ticket(
            'cs_mail', 'MAIL1', 'buyer@example.com', 1, event_id='halloween-2026'
        )
        self.assertTrue(thesection.claim_ticket_email_delivery('cs_mail'))
        self.assertFalse(thesection.claim_ticket_email_delivery('cs_mail'))
        thesection.mark_email_sent('cs_mail')
        self.assertFalse(thesection.claim_ticket_email_delivery('cs_mail'))

    def test_issued_ticket_can_be_scanned_once(self):
        ticket = thesection.record_ticket(
            'cs_live', 'LIVE1', 'buyer@example.com', 2,
            ticket_type='general', event_id='halloween-2026',
        )
        self.assertEqual(ticket['ticket_id'], 'LIVE1')
        thesection.set_door_event_id('halloween-2026')
        first = thesection.check_ticket('LIVE1')
        self.assertEqual(first['status'], 'accepted')
        self.assertEqual(first['quantity'], 2)
        second = thesection.check_ticket('LIVE1')
        self.assertEqual(second['status'], 'used')

    def test_sales_counter_reset_ignores_old_purchases(self):
        thesection.save_tickets([{
            **self._ticket('OLD3', 'halloween-2026'),
            'purchased_at': '2026-01-01T00:00:00+00:00',
            'quantity': 4,
        }])
        thesection.set_door_event_id('halloween-2026')
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 0)
        self.assertTrue(thesection.apply_one_time_sales_counter_reset())
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 0)
        self.assertFalse(thesection.apply_one_time_sales_counter_reset())

    def test_order_quantity_follows_ticket_cap_remaining(self):
        thesection.save_events([
            thesection.normalize_event({
                'id': 'halloween-2026',
                'name': 'Halloween',
                'date': '2026-10-24',
                'sales_open': True,
                'ticket_cap': 10,
            }),
        ])
        thesection.save_tickets([{
            **self._ticket('SOLD8', 'halloween-2026'),
            'quantity': 8,
        }])
        self.assertEqual(thesection.max_order_quantity('halloween-2026'), 2)
        self.assertEqual(thesection.clamp_quantity(20, event_id='halloween-2026'), 2)
        self.assertEqual(
            thesection.get_ticket_availability('halloween-2026')['max_quantity'],
            2,
        )

    def test_order_quantity_caps_per_checkout(self):
        thesection.save_events([
            thesection.normalize_event({
                'id': 'halloween-2026',
                'name': 'Halloween',
                'date': '2026-10-24',
                'sales_open': True,
                'ticket_cap': 300,
            }),
        ])
        thesection.save_tickets([{
            **self._ticket('SOLD2', 'halloween-2026'),
            'quantity': 2,
        }])
        self.assertEqual(thesection.max_order_quantity('halloween-2026'), 10)
        self.assertEqual(thesection.clamp_quantity(298, event_id='halloween-2026'), 10)
        self.assertEqual(
            thesection.get_ticket_availability('halloween-2026')['max_quantity'],
            10,
        )

    def test_tickets_before_cutoff_are_void_and_omitted_from_sales(self):
        old = self._ticket('OLDVOID', 'halloween-2026')
        old['purchased_at'] = '2026-09-07T12:00:00+00:00'
        fresh = self._ticket('NEWOK', 'halloween-2026')
        fresh['purchased_at'] = '2026-09-08T12:00:00+00:00'
        thesection.save_tickets([old, fresh])
        thesection.set_door_event_id('halloween-2026')
        self.assertFalse(thesection.ticket_is_valid_purchase(old))
        self.assertTrue(thesection.ticket_is_valid_purchase(fresh))
        self.assertEqual(thesection.check_ticket('OLDVOID')['status'], 'void')
        self.assertEqual(thesection.check_ticket('NEWOK')['status'], 'accepted')
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 1)


if __name__ == '__main__':
    unittest.main()
