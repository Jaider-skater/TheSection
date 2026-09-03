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
            {'ga': 0, 'vip': 1, 'total': 1},
        )
        thesection.set_door_event_id('halloween-2026')
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 2, 'vip': 0, 'total': 2},
        )

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
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 4)
        self.assertTrue(thesection.apply_one_time_sales_counter_reset())
        self.assertEqual(thesection.compute_ticket_sales_counts('halloween-2026')['sold'], 0)
        self.assertFalse(thesection.apply_one_time_sales_counter_reset())


if __name__ == '__main__':
    unittest.main()
