"""App time is Mountain (Idaho Falls) and the move off Pacific shifts nothing stored."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


class AppTimezoneTests(unittest.TestCase):
    def setUp(self):
        self._saved_tz = thesection._display_tz
        thesection._display_tz = None
        self.addCleanup(setattr, thesection, '_display_tz', self._saved_tz)

    def test_default_app_timezone_is_mountain(self):
        with open(thesection.__file__, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn("os.getenv('APP_TIMEZONE', 'America/Boise')", source)
        self.assertNotIn('America/Los_Angeles', source)

    def test_display_timezone_uses_mountain_offsets(self):
        with mock.patch.object(thesection, 'APP_TIMEZONE', 'America/Boise'):
            tz = thesection.get_display_timezone()
        summer = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).astimezone(tz)
        winter = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc).astimezone(tz)
        self.assertEqual(summer.strftime('%Z'), 'MDT')
        self.assertEqual(winter.strftime('%Z'), 'MST')
        self.assertEqual(
            thesection.format_display_datetime('2026-10-25T04:00:00+00:00'),
            '2026-10-24 22:00',
        )

    def test_bad_app_timezone_falls_back_to_mountain(self):
        with mock.patch.object(thesection, 'APP_TIMEZONE', 'Not/AZone'):
            tz = thesection.get_display_timezone()
        self.assertEqual(getattr(tz, 'key', None), 'America/Boise')

    def test_sept_8_validity_cutoff_keeps_original_instant(self):
        cutoff = thesection.ticket_validity_cutoff()
        self.assertEqual(cutoff, datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc))
        just_before = {'purchased_at': '2026-09-08T06:30:00+00:00'}
        at_cutoff = {'purchased_at': '2026-09-08T07:00:00+00:00'}
        self.assertFalse(thesection.ticket_is_valid_purchase(just_before))
        self.assertTrue(thesection.ticket_is_valid_purchase(at_cutoff))

    def test_other_cutoff_dates_use_app_timezone_midnight(self):
        with mock.patch.object(thesection, 'TICKET_VALIDITY_CUTOFF_DATE', '2026-11-01'), \
                mock.patch.object(thesection, 'APP_TIMEZONE', 'America/Boise'):
            cutoff = thesection.ticket_validity_cutoff()
        self.assertEqual(
            cutoff.astimezone(timezone.utc),
            datetime(2026, 11, 1, 0, 0, tzinfo=ZoneInfo('America/Boise')).astimezone(timezone.utc),
        )

    def test_halloween_wall_clock_times_are_unchanged(self):
        event = thesection.normalize_event({
            'id': 'halloween-2026',
            'name': 'Halloween',
            'date': '2026-10-24',
            'time_start': '22:00',
            'time_end': '02:00',
            'sales_open': True,
        })
        self.assertEqual(event['date'], '2026-10-24')
        self.assertEqual(event['time_start'], '22:00')
        self.assertEqual(
            thesection.format_event_time_line(event['time_start'], event['time_end']),
            thesection.format_event_time_line('22:00', '02:00'),
        )
        self.assertTrue(
            thesection.format_event_time_line('22:00', '02:00').startswith('10'),
        )


if __name__ == '__main__':
    unittest.main()
