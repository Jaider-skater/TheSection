"""Homepage shows coming-soon teasers directly under NEXT EVENT."""
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


class HomepageTeaserTests(unittest.TestCase):
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
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _event(self, event_id, name, date, sales_open, headline=None):
        return thesection.normalize_event({
            'id': event_id,
            'name': name,
            'headline': headline or name,
            'date': date,
            'venue': 'The Gem, Idaho Falls',
            'sales_open': sales_open,
            'created_at': datetime.now(timezone.utc).isoformat(),
        })

    def test_coerce_sales_open_treats_form_zero_as_teaser(self):
        self.assertFalse(thesection.coerce_sales_open('0'))
        self.assertFalse(thesection.coerce_sales_open('false'))
        self.assertFalse(thesection.coerce_sales_open(False))
        self.assertTrue(thesection.coerce_sales_open('1'))
        self.assertTrue(thesection.coerce_sales_open(True))

    def test_coming_soon_event_is_a_teaser_not_on_sale(self):
        thesection.save_events([
            self._event('halloween-2026', 'Halloween', '2026-10-24', True),
            self._event('nye-2026', 'New Year', '2026-12-31', '0'),
        ])
        on_sale = thesection.list_on_sale_events()
        teasers = thesection.list_teaser_events()
        self.assertEqual([event['id'] for event in on_sale], ['halloween-2026'])
        self.assertEqual([event['id'] for event in teasers], ['nye-2026'])

    def test_halloween_beats_christmas_even_if_christmas_is_featured(self):
        thesection.save_events([
            self._event('christmas-2026', 'Christmas', '2026-12-25', True, 'December 25th'),
            self._event('halloween-2026', 'Halloween', '2026-10-24', True, 'October 24th'),
        ])
        thesection.set_featured_event_id('christmas-2026')
        chosen = thesection.pick_next_event()
        self.assertEqual(chosen['id'], 'halloween-2026')
        self.assertEqual(thesection.get_sales_event_id(), 'halloween-2026')

        html = self.app.test_client().get('/').get_data(as_text=True)
        next_block = html[html.find('NEXT EVENT'):html.find('ALSO ON SALE')]
        self.assertIn('October 24th', next_block)
        self.assertNotIn('December 25th', next_block)
        self.assertIn('December 25th', html[html.find('ALSO ON SALE'):])

    def test_past_christmas_does_not_beat_upcoming_halloween(self):
        thesection.save_events([
            self._event('christmas-2025', 'Christmas', '2025-12-25', True, 'December 25th'),
            self._event('halloween-2026', 'Halloween', '2026-10-24', True, 'October 24th'),
        ])
        with mock.patch.object(thesection, 'today_iso', return_value='2026-08-18'):
            chosen = thesection.pick_next_event()
        self.assertEqual(chosen['id'], 'halloween-2026')

    def test_homepage_puts_teasers_under_next_event(self):
        thesection.save_events([
            self._event('halloween-2026', 'Halloween', '2026-10-24', True, 'October 24th'),
            self._event('sau', 'sau', '2026-11-15', False, 'sau'),
            self._event('late-sale', 'Late Sale', '2026-12-01', True, 'December 1st'),
        ])
        thesection.set_featured_event_id('late-sale')
        client = self.app.test_client()
        html = client.get('/').get_data(as_text=True)
        next_block = html[html.find('NEXT EVENT'):html.find('Coming soon')]
        self.assertIn('October 24th', next_block)

        next_at = html.find('NEXT EVENT')
        coming_at = html.find('Coming soon')
        also_at = html.find('ALSO ON SALE')
        sau_at = html.find('sau')

        self.assertNotEqual(next_at, -1)
        self.assertNotEqual(coming_at, -1)
        self.assertNotEqual(sau_at, -1)
        self.assertLess(next_at, coming_at)
        self.assertLess(coming_at, sau_at)
        self.assertLess(coming_at, also_at)
        self.assertIn('Tickets soon', html)
        self.assertNotIn('Nothing announced yet', html)

    def test_teasers_still_show_when_nothing_is_on_sale(self):
        thesection.save_events([
            self._event('sau', 'sau', '2026-11-15', False, 'sau'),
        ])
        client = self.app.test_client()
        html = client.get('/').get_data(as_text=True)
        self.assertIn('Coming soon', html)
        self.assertIn('sau', html)
        self.assertNotIn('Get Tickets', html)


if __name__ == '__main__':
    unittest.main()
