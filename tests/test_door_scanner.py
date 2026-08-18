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


if __name__ == '__main__':
    unittest.main()
