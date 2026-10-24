"""Shared test base for email (ticket reminder) tests: temp files + fake SMTP."""
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
            mock.patch.dict(thesection.app.config, {
                'MAIL_SERVER': 'smtp.gmail.com', 'MAIL_PORT': 587, 'MAIL_USE_TLS': True,
                'MAIL_USERNAME': '', 'MAIL_PASSWORD': '', 'MAIL_DEFAULT_SENDER': '',
            }),
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


