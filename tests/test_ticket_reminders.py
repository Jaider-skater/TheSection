"""7-day ticket reminder emails."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from test_promos import PromoTestBase, DENVER  # noqa: F401
import app as thesection

BOISE = thesection.ZoneInfo('America/Boise')


class TicketReminderTests(PromoTestBase):
    def setUp(self):
        super().setUp()
        self.event = {'id': 'halloween-2026', 'name': 'Halloween', 'date': '2026-10-24',
                      'time_start': '22:00', 'time_end': '02:00', 'venue': 'The Gem'}
        other = {'id': 'other-night', 'name': 'Other', 'date': '2026-12-05', 'time_start': '21:00'}
        thesection.save_events([self.event, other])
        self.send_at = datetime(2026, 10, 17, 12, 0, tzinfo=BOISE)
        valid = '2026-10-01T12:00:00+00:00'
        thesection.save_tickets([
            {'ticket_id': 'TS-AAA111', 'email': 'a@example.com', 'event_id': 'halloween-2026', 'purchased_at': valid, 'view_token': 'k1'},
            {'ticket_id': 'TS-AAA222', 'email': 'A@Example.com', 'event_id': 'halloween-2026', 'purchased_at': valid, 'view_token': 'k2'},
            {'ticket_id': 'TS-BBB111', 'email': 'b@example.com', 'event_id': 'halloween-2026', 'purchased_at': valid, 'view_token': 'k3'},
            {'ticket_id': 'TS-OLD111', 'email': 'old@example.com', 'event_id': 'halloween-2026', 'purchased_at': '2026-08-01T12:00:00+00:00'},
            {'ticket_id': 'TS-OTH111', 'email': 'other@example.com', 'event_id': 'other-night', 'purchased_at': valid},
        ])

    def configure_mail(self):
        thesection.app.config.update(MAIL_USERNAME='thesectionevents@gmail.com',
                                     MAIL_PASSWORD='abcd efgh ijkl mnop',
                                     MAIL_DEFAULT_SENDER='thesectionevents@gmail.com')

    def test_send_time_is_noon_boise_seven_days_before(self):
        at = thesection.ticket_reminder_send_time(self.event)
        self.assertEqual(at, self.send_at)
        self.assertEqual(at.astimezone(timezone.utc).hour, 18)  # MDT

    def test_only_valid_tickets_for_event_deduped(self):
        recips = thesection.ticket_reminder_recipients('halloween-2026')
        self.assertEqual(sorted(recips), ['a@example.com', 'b@example.com'])
        self.assertEqual(len(recips['a@example.com']), 2)

    def test_not_due_before_send_time(self):
        self.configure_mail()
        self.assertEqual(thesection.run_due_ticket_reminders(self.send_at - timedelta(minutes=1)), 0)
        self.assertEqual(self.smtp['sent'], [])

    def test_sends_once_individually(self):
        self.configure_mail()
        self.assertEqual(thesection.run_due_ticket_reminders(self.send_at + timedelta(minutes=1)), 2)
        self.assertEqual(self.recipients_sent(), ['a@example.com', 'b@example.com'])
        msg = next(m for m in self.smtp['sent'] if m['To'] == 'a@example.com')
        body = msg.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('/members', body)
        self.assertIn('/t/TSAAA111?k=k1', body)
        self.assertIn('/t/TSAAA222?k=k2', body)
        self.assertIn('The Gem', body)
        self.assertNotIn('b@example.com', str(msg))
        # later passes (other workers / restarts) never resend, new buyers don't get it
        tickets = thesection.load_tickets()
        tickets.append({'ticket_id': 'TS-NEW111', 'email': 'new@example.com', 'event_id': 'halloween-2026', 'purchased_at': '2026-10-18T00:00:00+00:00'})
        thesection.save_tickets(tickets)
        self.assertEqual(thesection.run_due_ticket_reminders(self.send_at + timedelta(hours=2)), 0)
        self.assertEqual(len(self.smtp['sent']), 2)
        entry = thesection.load_ticket_reminders()['halloween-2026']
        self.assertEqual(entry['status'], 'sent')
        self.assertEqual(entry['sent_count'], 2)

    def test_late_catchup_but_not_after_start(self):
        self.configure_mail()
        start = datetime(2026, 10, 24, 22, 0, tzinfo=BOISE)
        self.assertEqual(thesection.run_due_ticket_reminders(start + timedelta(minutes=1)), 0)
        self.assertEqual(thesection.run_due_ticket_reminders(start - timedelta(hours=3)), 2)

    def test_disabled_toggle(self):
        self.configure_mail()
        self.assertTrue(thesection.set_ticket_reminder_enabled('halloween-2026', False))
        self.assertEqual(thesection.run_due_ticket_reminders(self.send_at + timedelta(minutes=1)), 0)
        self.assertEqual(self.smtp['sent'], [])

    def test_mail_not_configured_sends_nothing(self):
        self.assertEqual(thesection.run_due_ticket_reminders(self.send_at + timedelta(minutes=1)), 0)
        self.assertEqual(self.smtp['connections'], [])

    def test_login_failure_recorded_and_partial_resume(self):
        self.configure_mail()
        self.smtp['fail_login'] = True
        now = self.send_at + timedelta(minutes=1)
        self.assertEqual(thesection.run_due_ticket_reminders(now), 0)
        entry = thesection.load_ticket_reminders()['halloween-2026']
        self.assertEqual(entry['status'], 'failed')
        self.assertIn('Gmail rejected', entry['error'])
        self.smtp['fail_login'] = False
        # pretend a then-crashed pass already delivered to a@
        data = thesection.load_ticket_reminders()
        data['halloween-2026']['delivered_to'] = ['a@example.com']
        data['halloween-2026']['finished_at'] = (now - timedelta(hours=1)).isoformat()
        thesection.save_ticket_reminders(data)
        self.assertEqual(thesection.run_due_ticket_reminders(now + timedelta(minutes=5)), 1)
        self.assertEqual(self.recipients_sent(), ['b@example.com'])

    def test_admin_page_shows_reminders_and_toggle(self):
        client = self.admin_client()
        with mock.patch.object(thesection, 'promo_today', return_value=datetime(2026, 10, 14).date()):
            html = client.get('/admin/promos').get_data(as_text=True)
        self.assertIn('Ticket reminders', html)
        self.assertIn('Sat Oct 17, 2026 at 12:00 PM MDT', html)
        self.assertIn('Email sending isn', html)
        resp = client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'reminder_toggle',
                                                   'event_id': 'halloween-2026', 'enabled': '0'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(thesection.ticket_reminders_enabled_for('halloween-2026'))


if __name__ == '__main__':
    unittest.main()
