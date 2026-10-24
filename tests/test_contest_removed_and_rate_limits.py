"""Costume contest removed (old URLs go home); crowd-safe login/sign-up limits."""
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


class ContestRemovedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        self.patches = [mock.patch.object(thesection, n, os.path.join(r, n + '.json')) for n in (
            'tickets_file', 'members_file', 'invites_file', 'exclusive_holds_file', 'events_file',
            'full_mailing_list_file', 'scanner_settings_file')]
        for p in self.patches:
            p.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection._rate_limit_buckets.clear()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _add_member(self, email='guest@example.com', pw='password123'):
        m = thesection.load_members()
        m.append({'email': email, 'password_hash': thesection.hash_password(pw), 'saved_tickets': [],
                  'joined_at': datetime.now(timezone.utc).isoformat()})
        thesection.save_members(m)

    def test_old_contest_urls_redirect_home(self):
        c = self.app.test_client()
        for url in ('/costumes', '/costumes/join/abc', '/costume-photos/x.jpg'):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertTrue(r.headers['Location'].rstrip('/').endswith(('', 'localhost')), url)
            self.assertEqual(r.headers['Location'].split('localhost')[-1] or '/', '/')

    def test_no_contest_on_pages(self):
        c = self.app.test_client()
        for url in ('/', '/legacy'):
            html = c.get(url).get_data(as_text=True).lower()
            self.assertNotIn('costume contest', html)
            self.assertNotIn('can we post your costume', html)

    def _login(self, c, email, pw):
        page = c.get('/legacy')
        return c.post('/legacy', data={'action': 'login', 'email': email, 'password': pw,
                                       'csrf_token': page.headers.get('X-CSRF-Token')})

    def test_many_successful_logins_one_ip(self):
        self._add_member()
        for _ in range(40):
            self.assertEqual(self._login(self.app.test_client(), 'guest@example.com', 'password123').status_code, 302)

    def test_per_account_failures_limited(self):
        self._add_member()
        c = self.app.test_client()
        for _ in range(10):
            self.assertEqual(self._login(c, 'guest@example.com', 'bad').status_code, 200)
        self.assertEqual(self._login(c, 'guest@example.com', 'password123').status_code, 429)
        self.assertNotEqual(self._login(c, 'other@example.com', 'x').status_code, 429)

    def test_many_signups_one_ip(self):
        for i in range(30):
            c = self.app.test_client()
            page = c.get('/legacy')
            r = c.post('/legacy', data={'action': 'register', 'email': f'g{i}@example.com',
                                        'password': 'password123', 'confirm_password': 'password123',
                                        'csrf_token': page.headers.get('X-CSRF-Token')})
            self.assertEqual(r.status_code, 302)


if __name__ == '__main__':
    unittest.main()
