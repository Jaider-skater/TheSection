"""Promo emails: admin-only drafting, approval, and Gmail sending."""
import os
import shutil
import smtplib
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402

DENVER = ZoneInfo('America/Denver')
APP_PASSWORD = 'abcd efgh ijkl mnop'


class FakeSMTP:
    """Stands in for smtplib.SMTP; records every message instead of sending it."""

    registry = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.tls = False
        self.logged_in = None
        FakeSMTP.registry['connections'].append(self)

    def ehlo(self):
        return (250, b'ok')

    def starttls(self, context=None):
        self.tls = True
        return (220, b'ready')

    def login(self, user, password):
        if FakeSMTP.registry.get('fail_login'):
            raise smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted')
        self.logged_in = (user, password)
        return (235, b'ok')

    def send_message(self, msg):
        if not self.tls or not self.logged_in:
            raise AssertionError('sent before STARTTLS/login')
        failing = FakeSMTP.registry.get('fail_for') or set()
        if msg['To'] in failing:
            raise smtplib.SMTPRecipientsRefused({msg['To']: (550, b'no such user')})
        with FakeSMTP.registry['lock']:
            FakeSMTP.registry['sent'].append(msg)
        return {}

    def quit(self):
        return (221, b'bye')

    def close(self):
        return None


class PromoTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='promos-test-')
        self.invites = []
        self.full_list = []
        self.smtp = {'connections': [], 'sent': [], 'lock': threading.Lock()}
        FakeSMTP.registry = self.smtp
        env = {k: v for k, v in os.environ.items() if k not in ('GMAIL_APP_PASSWORD', 'PROMO_EMAIL_ADDRESS')}
        env['PROMO_SEND_DELAY'] = '0'
        self.patches = [
            mock.patch.object(thesection, 'promos_file', os.path.join(self.tmpdir, 'promos.json')),
            mock.patch.object(thesection, 'events_file', os.path.join(self.tmpdir, 'events.json')),
            mock.patch.object(thesection, 'scanner_settings_file', os.path.join(self.tmpdir, 'scanner.json')),
            mock.patch.object(thesection, 'tickets_file', os.path.join(self.tmpdir, 'tickets.json')),
            mock.patch.object(thesection, 'load_invites', side_effect=lambda: list(self.invites)),
            mock.patch.object(thesection, 'load_full_mailing_list', side_effect=lambda: list(self.full_list)),
            mock.patch.object(thesection.smtplib, 'SMTP', FakeSMTP),
            mock.patch.dict(os.environ, env, clear=True),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection._rate_limit_buckets.clear()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # helpers ---------------------------------------------------------------
    def connect_gmail(self):
        os.environ['GMAIL_APP_PASSWORD'] = APP_PASSWORD

    def subscribers(self, full=(), exclusive=()):
        self.full_list = [{'email': e, 'added_at': '2026-01-01T00:00:00+00:00', 'source': 'manual'} for e in full]
        self.invites = [{'email': e, 'added_at': '2026-01-01T00:00:00+00:00'} for e in exclusive]

    def future(self, days=2, hour=10):
        day = datetime.now(DENVER).date() + timedelta(days=days)
        return datetime(day.year, day.month, day.day, hour, 0, tzinfo=DENVER)

    def make_promo(self, status='draft', scheduled=None, audience='all', **extra):
        scheduled = scheduled or self.future()
        promo, error = thesection.create_promo({
            'subject': extra.pop('subject', 'Halloween is coming'),
            'body': extra.pop('body', 'Come party.\n\nTickets: https://thesection.onrender.com/'),
            'audience': audience,
            'scheduled_for': scheduled.isoformat(),
            'image_url': extra.pop('image_url', ''),
            'event_id': extra.pop('event_id', None),
        })
        self.assertIsNone(error)
        if status == 'approved':
            before = scheduled - timedelta(days=1)
            promo, error = thesection.approve_promo(promo['id'], approved_by='test', now=before)
            self.assertIsNone(error)
        return promo

    def admin_client(self):
        client = self.app.test_client()
        token = client.get('/admin/login').headers.get('X-CSRF-Token')
        resp = client.post('/admin/login', data={'password': thesection.admin_key, 'csrf_token': token})
        self.assertEqual(resp.status_code, 302)
        client.csrf = token
        return client

    def recipients_sent(self):
        return sorted(msg['To'] for msg in self.smtp['sent'])


class PromoAccessTests(PromoTestBase):
    def test_promo_pages_redirect_when_not_logged_in(self):
        promo = self.make_promo()
        client = self.app.test_client()
        for path in ('/admin/promos', '/admin/promos/new', f"/admin/promos/{promo['id']}"):
            resp = client.get(path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn('/admin/login', resp.headers['Location'])

    def test_anonymous_post_cannot_change_promos(self):
        promo = self.make_promo(scheduled=self.future())
        client = self.app.test_client()
        token = client.get('/').headers.get('X-CSRF-Token')
        for action in ('approve', 'delete', 'send_now'):
            resp = client.post('/admin/promos', data={'action': action, 'promo_id': promo['id'], 'csrf_token': token})
            self.assertEqual(resp.status_code, 302)
            self.assertIn('/admin/login', resp.headers['Location'])
        resp = client.post('/admin/promos/new', data={'subject': 'x', 'body': 'y', 'csrf_token': token})
        self.assertEqual(resp.status_code, 302)
        stored = thesection.get_promo(promo['id'])
        self.assertEqual(stored['status'], 'draft')
        self.assertEqual(len(thesection.load_promos()), 1)

    def test_admin_post_without_csrf_is_rejected(self):
        promo = self.make_promo()
        client = self.admin_client()
        resp = client.post('/admin/promos', data={'action': 'delete', 'promo_id': promo['id']})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNotNone(thesection.get_promo(promo['id']))

    def test_dashboard_and_menu_link_to_promos(self):
        client = self.admin_client()
        html = client.get('/admin').get_data(as_text=True)
        self.assertIn('/admin/promos', html)
        self.assertIn('Promo emails', html)
        self.assertIn('0 planned promos', html)
        menu = client.get('/admin/events').get_data(as_text=True)
        self.assertLess(menu.index('Mailing Lists'), menu.index('Promo emails'))
        self.assertLess(menu.index('Promo emails'), menu.index('Sign out'))
        guest_home = self.app.test_client().get('/').get_data(as_text=True)
        self.assertNotIn('Promo emails', guest_home)


class PromoCrudTests(PromoTestBase):
    def test_create_edit_delete_through_admin_page(self):
        self.subscribers(full=['a@example.com'])
        client = self.admin_client()
        page = client.get('/admin/promos')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Promo emails', html)
        self.assertIn('Planned', html)
        self.assertIn('Sent', html)

        send_at = self.future(days=5, hour=10)
        resp = client.post('/admin/promos/new', data={
            'csrf_token': client.csrf,
            'subject': 'Doors at 10',
            'body': 'Hello <friends>\n\nhttps://thesection.onrender.com/',
            'audience': 'full',
            'scheduled_for': send_at.strftime('%Y-%m-%dT%H:%M'),
            'image_url': '',
        })
        self.assertEqual(resp.status_code, 302)
        promos = thesection.load_promos()
        self.assertEqual(len(promos), 1)
        promo = promos[0]
        self.assertEqual(promo['status'], 'draft')
        self.assertEqual(promo['audience'], 'full')
        stored_time = datetime.fromisoformat(promo['scheduled_for'])
        self.assertEqual(stored_time, send_at)
        self.assertEqual(stored_time.astimezone(DENVER).hour, 10)
        for key in ('id', 'subject', 'body', 'audience', 'status', 'scheduled_for', 'created_at',
                    'approved_at', 'sent_at', 'recipient_count', 'event_id', 'error'):
            self.assertIn(key, promo)

        html = client.get('/admin/promos').get_data(as_text=True)
        self.assertIn('Doors at 10', html)
        self.assertIn('Hello &lt;friends&gt;', html)  # preview escapes body
        self.assertNotIn('Hello <friends>', html)

        edit = client.get(f"/admin/promos/{promo['id']}")
        self.assertEqual(edit.status_code, 200)
        self.assertIn(send_at.strftime('%Y-%m-%dT%H:%M'), edit.get_data(as_text=True))
        resp = client.post(f"/admin/promos/{promo['id']}", data={
            'csrf_token': client.csrf,
            'subject': 'Doors at 9',
            'body': 'Updated body',
            'audience': 'exclusive',
            'scheduled_for': send_at.strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(resp.status_code, 302)
        updated = thesection.get_promo(promo['id'])
        self.assertEqual(updated['subject'], 'Doors at 9')
        self.assertEqual(updated['audience'], 'exclusive')
        self.assertEqual(updated['body'], 'Updated body')

        resp = client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'delete', 'promo_id': promo['id']})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(thesection.load_promos(), [])

    def test_form_validation_errors(self):
        client = self.admin_client()
        resp = client.post('/admin/promos/new', data={
            'csrf_token': client.csrf, 'subject': '', 'body': 'x', 'audience': 'all',
            'scheduled_for': '2030-01-01T10:00',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Add a subject line.', resp.get_data(as_text=True))
        resp = client.post('/admin/promos/new', data={
            'csrf_token': client.csrf, 'subject': 'Hi', 'body': 'x', 'audience': 'all',
            'scheduled_for': '2030-01-01T10:00', 'image_url': 'javascript:alert(1)',
        })
        self.assertIn('image URL', resp.get_data(as_text=True))
        self.assertEqual(thesection.load_promos(), [])

    def test_approve_unapprove_and_edit_requires_reapproval(self):
        client = self.admin_client()
        promo = self.make_promo(scheduled=self.future(days=3))
        client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'approve', 'promo_id': promo['id']})
        approved = thesection.get_promo(promo['id'])
        self.assertEqual(approved['status'], 'approved')
        self.assertTrue(approved['approved_at'])

        client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'unapprove', 'promo_id': promo['id']})
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'draft')

        client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'approve', 'promo_id': promo['id']})
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'approved')
        client.post(f"/admin/promos/{promo['id']}", data={
            'csrf_token': client.csrf, 'subject': 'Changed', 'body': 'Changed body', 'audience': 'all',
            'scheduled_for': self.future(days=3).strftime('%Y-%m-%dT%H:%M'),
        })
        edited = thesection.get_promo(promo['id'])
        self.assertEqual(edited['subject'], 'Changed')
        self.assertEqual(edited['status'], 'draft')
        self.assertIsNone(edited['approved_at'])

    def test_cannot_approve_with_past_send_time(self):
        promo = self.make_promo(scheduled=datetime.now(DENVER) - timedelta(hours=1))
        result, error = thesection.approve_promo(promo['id'])
        self.assertIsNone(result)
        self.assertIn('already passed', error)
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'draft')

    def test_sent_promos_cannot_be_edited_or_deleted(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'])
        promo = self.make_promo(status='approved')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'sent')
        ok, error = thesection.delete_promo(promo['id'])
        self.assertFalse(ok)
        client = self.admin_client()
        resp = client.get(f"/admin/promos/{promo['id']}")
        self.assertEqual(resp.status_code, 302)


class PromoEventDraftTests(PromoTestBase):
    def add_event(self, days_out=20, **extra):
        day = datetime.now(DENVER).date() + timedelta(days=days_out)
        data = {
            'id': extra.pop('id', 'halloween-test'),
            'name': 'Halloween',
            'date': day.isoformat(),
            'time_start': '22:00',
            'time_end': '02:00',
            'venue': 'The Gem, Idaho Falls',
            'description': 'Costumes encouraged.',
            'details': '21+ • Limited Capacity',
            'flyer_filename': 'halloween_abc123.jpg',
            'sales_open': True,
        }
        data.update(extra)
        thesection.save_events([data])
        return thesection.get_event(data['id'])

    def test_draft_promo_for_event_prefills_real_event_data(self):
        event = self.add_event(days_out=20)
        client = self.admin_client()
        page = client.get('/admin/promos').get_data(as_text=True)
        self.assertIn('Draft promo for event', page)
        self.assertIn('Halloween', page)

        resp = client.post('/admin/promos', data={
            'csrf_token': client.csrf, 'action': 'draft_from_event', 'event_id': event['id'],
        })
        self.assertEqual(resp.status_code, 302)
        promos = thesection.load_promos()
        self.assertEqual(len(promos), 1)
        promo = promos[0]
        self.assertIn(f"/admin/promos/{promo['id']}", resp.headers['Location'])
        self.assertEqual(promo['status'], 'draft')
        self.assertEqual(promo['event_id'], event['id'])
        date_line = thesection.format_event_date_line(event['date'])
        self.assertIn('Halloween', promo['subject'])
        self.assertIn(date_line, promo['subject'])
        body = promo['body']
        self.assertIn(date_line, body)
        self.assertIn('10:00 PM', body)
        self.assertIn('The Gem, Idaho Falls', body)
        self.assertIn('Costumes encouraged.', body)
        self.assertIn(thesection.get_public_base_url() + '/', body)
        flyer = thesection.get_public_base_url() + '/media/flyers/halloween_abc123.jpg'
        self.assertEqual(promo['image_url'], flyer)
        self.assertIn(flyer, body)
        scheduled = datetime.fromisoformat(promo['scheduled_for']).astimezone(DENVER)
        event_day = datetime.strptime(event['date'], '%Y-%m-%d').date()
        self.assertEqual(scheduled.date(), event_day - timedelta(days=3))
        self.assertEqual((scheduled.hour, scheduled.minute), (10, 0))
        self.assertIn(f'src="{flyer}"', thesection.promo_html_body(promo))

        edit_html = client.get(resp.headers['Location']).get_data(as_text=True)
        self.assertIn('Halloween', edit_html)
        self.assertEqual(self.smtp['sent'], [])

    def test_event_draft_for_soon_event_is_never_in_the_past(self):
        event = self.add_event(days_out=1, flyer_filename='')
        promo, error = thesection.draft_promo_from_event(event['id'])
        self.assertIsNone(error)
        scheduled = datetime.fromisoformat(promo['scheduled_for'])
        self.assertGreater(scheduled, datetime.now(timezone.utc))
        self.assertEqual(promo['image_url'], '')
        event_start = thesection._event_start_datetime(event)
        self.assertLess(scheduled, event_start)

    def test_past_events_are_not_offered(self):
        self.add_event(days_out=-3)
        self.assertEqual(thesection.upcoming_events_for_promos(), [])


class PromoSendingTests(PromoTestBase):
    def test_drafts_never_send(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'b@example.com'])
        past = datetime.now(DENVER) - timedelta(days=1)
        draft = self.make_promo(scheduled=past)
        self.assertEqual(thesection.run_due_promos(), [])
        self.assertEqual(thesection.run_due_promos(now=datetime.now(timezone.utc) + timedelta(days=30)), [])
        self.assertIsNone(thesection.claim_promo_for_sending(draft['id'], require_due=False))
        ok, message = thesection.send_promo_now(draft['id'], background=False)
        self.assertFalse(ok)
        client = self.admin_client()
        client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'send_now', 'promo_id': draft['id']})
        self.assertEqual(self.smtp['sent'], [])
        self.assertEqual(self.smtp['connections'], [])
        self.assertEqual(thesection.get_promo(draft['id'])['status'], 'draft')

    def test_approved_promo_waits_until_its_send_time(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'])
        promo = self.make_promo(status='approved', scheduled=self.future(days=2))
        self.assertEqual(thesection.run_due_promos(), [])
        self.assertEqual(self.smtp['sent'], [])
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'approved')

    def test_approved_due_promo_sends_one_email_per_recipient(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'b@example.com', 'shared@example.com'],
                         exclusive=['vip@example.com', 'shared@example.com'])
        promo = self.make_promo(status='approved', audience='all',
                                image_url='https://thesection.onrender.com/media/flyers/x.jpg')
        due = datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1)
        results = thesection.run_due_promos(now=due)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.recipients_sent(),
                         ['a@example.com', 'b@example.com', 'shared@example.com', 'vip@example.com'])
        for msg in self.smtp['sent']:
            self.assertNotIn(',', msg['To'])
            self.assertIsNone(msg['Cc'])
            self.assertIsNone(msg['Bcc'])
            self.assertIn('thesectionevents@gmail.com', msg['From'])
            self.assertEqual(msg['Subject'], 'Halloween is coming')
            plain = msg.get_body(preferencelist=('plain',)).get_content()
            html_part = msg.get_body(preferencelist=('html',)).get_content()
            self.assertIn('unsubscribe', plain)
            self.assertIn('unsubscribe', html_part)
            self.assertIn('Come party.', plain)
            self.assertIn('x.jpg', html_part)
        conn = self.smtp['connections'][0]
        self.assertEqual((conn.host, conn.port), ('smtp.gmail.com', 587))
        self.assertTrue(conn.tls)
        self.assertEqual(conn.logged_in, ('thesectionevents@gmail.com', 'abcdefghijklmnop'))
        final = thesection.get_promo(promo['id'])
        self.assertEqual(final['status'], 'sent')
        self.assertEqual(final['recipient_count'], 4)
        self.assertTrue(final['sent_at'])
        self.assertIsNone(final['error'])

        client = self.admin_client()
        html = client.get('/admin/promos').get_data(as_text=True)
        sent_section = html[html.index('Sent <span'):]
        self.assertIn('Halloween is coming', sent_section)
        self.assertIn('4 recipients', sent_section)

    def test_audience_limits_recipients(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'], exclusive=['vip@example.com'])
        promo = self.make_promo(status='approved', audience='exclusive')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        self.assertEqual(self.recipients_sent(), ['vip@example.com'])

    def test_send_now_button_sends_approved_promo(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'b@example.com'])
        promo = self.make_promo(status='approved', scheduled=self.future(days=4))
        client = self.admin_client()
        resp = client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'send_now', 'promo_id': promo['id']})
        self.assertEqual(resp.status_code, 302)
        deadline = time.time() + 5
        while time.time() < deadline and thesection.get_promo(promo['id'])['status'] != 'sent':
            time.sleep(0.05)
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'sent')
        self.assertEqual(self.recipients_sent(), ['a@example.com', 'b@example.com'])

    def test_no_password_keeps_promos_waiting_without_crashing(self):
        self.subscribers(full=['a@example.com'])
        self.assertFalse(thesection.promo_sending_configured())
        client = self.admin_client()
        promo = self.make_promo(scheduled=self.future(days=2))
        client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'approve', 'promo_id': promo['id']})
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'approved')
        page = client.get('/admin/promos')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Email sending isn’t connected yet', html)
        self.assertIn('GMAIL_APP_PASSWORD', html)
        self.assertIn('Approved · waiting for email setup', html)

        later = datetime.now(timezone.utc) + timedelta(days=10)
        self.assertEqual(thesection.run_due_promos(now=later), [])
        ok, message = thesection.send_promo_now(promo['id'], background=False)
        self.assertFalse(ok)
        self.assertIn('GMAIL_APP_PASSWORD', message)
        resp = client.post('/admin/promos', data={'csrf_token': client.csrf, 'action': 'send_now', 'promo_id': promo['id']},
                           follow_redirects=True)
        self.assertIn('isn’t connected yet', resp.get_data(as_text=True))
        self.assertEqual(self.smtp['connections'], [])
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'approved')

    def test_running_twice_does_not_double_send(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'b@example.com'])
        promo = self.make_promo(status='approved')
        due = datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1)
        thesection.run_due_promos(now=due)
        thesection.run_due_promos(now=due + timedelta(minutes=1))
        ok, _message = thesection.send_promo_now(promo['id'], background=False)
        self.assertFalse(ok)
        self.assertEqual(self.recipients_sent(), ['a@example.com', 'b@example.com'])
        self.assertIsNone(thesection.claim_promo_for_sending(promo['id'], require_due=False))

    def test_concurrent_senders_only_send_once(self):
        self.connect_gmail()
        emails = [f'guest{i}@example.com' for i in range(15)]
        self.subscribers(full=emails)
        promo = self.make_promo(status='approved')
        due = datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1)
        barrier = threading.Barrier(8)

        def scheduler():
            barrier.wait()
            thesection.run_due_promos(now=due)

        def button():
            barrier.wait()
            thesection.send_promo_now(promo['id'], background=False)

        threads = [threading.Thread(target=scheduler) for _ in range(4)]
        threads += [threading.Thread(target=button) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(self.recipients_sent(), sorted(emails))
        final = thesection.get_promo(promo['id'])
        self.assertEqual(final['status'], 'sent')
        self.assertEqual(final['recipient_count'], 15)

    def test_claim_marks_sending_before_delivery(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'])
        promo = self.make_promo(status='approved')
        claimed = thesection.claim_promo_for_sending(promo['id'], require_due=False)
        self.assertEqual(claimed['status'], 'sending')
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'sending')
        self.assertIsNone(thesection.claim_promo_for_sending(promo['id'], require_due=False))
        self.assertEqual(thesection.run_due_promos(now=datetime.now(timezone.utc) + timedelta(days=30)), [])
        self.assertEqual(self.smtp['sent'], [])

    def test_gmail_login_failure_is_recorded_as_failed(self):
        self.connect_gmail()
        self.smtp['fail_login'] = True
        self.subscribers(full=['a@example.com'])
        promo = self.make_promo(status='approved')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        final = thesection.get_promo(promo['id'])
        self.assertEqual(final['status'], 'failed')
        self.assertIn('GMAIL_APP_PASSWORD', final['error'])
        self.assertNotIn('abcd', final['error'])
        self.assertEqual(final['recipient_count'], 0)
        html = self.admin_client().get('/admin/promos').get_data(as_text=True)
        self.assertIn('Failed', html)
        self.assertIn('Move back to draft', html)

    def test_partial_failure_is_recorded(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'bad@example.com'])
        self.smtp['fail_for'] = {'bad@example.com'}
        promo = self.make_promo(status='approved')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        final = thesection.get_promo(promo['id'])
        self.assertEqual(final['status'], 'sent')
        self.assertEqual(final['recipient_count'], 1)
        self.assertEqual(final['failed_count'], 1)
        self.assertIn('1 address(es) failed', final['error'])

    def test_failed_promo_retry_skips_people_who_already_got_it(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com', 'b@example.com'])
        promo = self.make_promo(status='approved')
        claimed = thesection.claim_promo_for_sending(promo['id'], require_due=False)
        thesection._finish_promo(claimed['id'], ['a@example.com'], ['b@example.com'], error='boom')
        # Force a failed state with a@ recorded as delivered.
        with thesection.promo_store_lock():
            promos = thesection.load_promos()
            promos[0]['status'] = 'failed'
            thesection.save_promos(promos)
        back, error = thesection.failed_promo_back_to_draft(promo['id'])
        self.assertIsNone(error)
        new_time = self.future(days=3)
        thesection.update_promo_content(promo['id'], {**back, 'scheduled_for': new_time.isoformat()})
        thesection.approve_promo(promo['id'])
        thesection.run_due_promos(now=new_time + timedelta(minutes=1))
        self.assertEqual(self.recipients_sent(), ['b@example.com'])
        self.assertEqual(thesection.get_promo(promo['id'])['recipient_count'], 2)

    def test_empty_audience_fails_cleanly(self):
        self.connect_gmail()
        promo = self.make_promo(status='approved')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        final = thesection.get_promo(promo['id'])
        self.assertEqual(final['status'], 'failed')
        self.assertIn('Nobody', final['error'])

    def test_promo_for_past_event_is_not_auto_sent(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'])
        past_day = datetime.now(DENVER).date() - timedelta(days=2)
        thesection.save_events([{'id': 'old-night', 'name': 'Old night', 'date': past_day.isoformat()}])
        promo = self.make_promo(status='approved', event_id='old-night', scheduled=self.future(days=1))
        self.assertEqual(thesection.get_promo(promo['id'])['event_id'], 'old-night')
        thesection.run_due_promos(now=datetime.fromisoformat(promo['scheduled_for']) + timedelta(minutes=1))
        self.assertEqual(self.smtp['sent'], [])
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'approved')

    def test_stuck_sending_can_be_marked_failed(self):
        self.connect_gmail()
        self.subscribers(full=['a@example.com'])
        promo = self.make_promo(status='approved')
        thesection.claim_promo_for_sending(promo['id'], require_due=False)
        promo_now = datetime.now(timezone.utc)
        result, error = thesection.mark_stuck_promo_failed(promo['id'], now=promo_now)
        self.assertIsNone(result)
        result, error = thesection.mark_stuck_promo_failed(promo['id'], now=promo_now + timedelta(hours=1))
        self.assertIsNone(error)
        self.assertEqual(thesection.get_promo(promo['id'])['status'], 'failed')

    def test_html_rendering_escapes_and_links(self):
        html_body = thesection.promo_html_body({
            'body': 'Hi <script>alert(1)</script>\n\nTickets: https://thesection.onrender.com/?a=1&b=2.',
            'image_url': 'https://example.com/f.jpg"onerror="x',
        })
        self.assertNotIn('<script>', html_body)
        self.assertIn('&lt;script&gt;', html_body)
        self.assertIn('href="https://thesection.onrender.com/?a=1&amp;b=2"', html_body)
        self.assertNotIn('onerror', html_body)

    def test_scheduler_is_disabled_under_tests(self):
        self.assertFalse(thesection.promo_scheduler_enabled())


if __name__ == '__main__':
    unittest.main()
