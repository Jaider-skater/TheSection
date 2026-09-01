"""Member login should survive Stripe Checkout return (dropped Flask session)."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


class MemberSessionStripeReturnTests(unittest.TestCase):
    def setUp(self):
        self.members = []
        self.patches = [
            mock.patch.object(thesection, 'load_members', side_effect=lambda: list(self.members)),
            mock.patch.object(thesection, 'save_members', side_effect=self._save_members),
            mock.patch.object(thesection, 'load_tickets', return_value=[]),
            mock.patch.object(thesection, 'load_invites', return_value=[]),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
        ]
        for patcher in self.patches:
            patcher.start()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()

    def _save_members(self, members):
        self.members = list(members)
        return True

    def _member(self, email='guest@example.com'):
        return {
            'email': email,
            'password_hash': thesection.hash_password('password123'),
            'saved_tickets': [],
            'discount_code': 'TEST-ABCD',
            'joined_at': datetime.now(timezone.utc).isoformat(),
        }

    def _login(self):
        self.members = [self._member()]
        client = self.app.test_client()
        token = client.get('/legacy').headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'login',
                'email': 'guest@example.com',
                'password': 'password123',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        return client

    def test_login_sets_stripe_surviving_cookie(self):
        client = self._login()
        self.assertTrue(client.get_cookie(thesection.MEMBER_LOGIN_COOKIE))
        status = client.get('/api/member-status').get_json()
        self.assertTrue(status['logged_in'])
        self.assertEqual(status['email'], 'guest@example.com')

    def test_login_survives_dropped_flask_session_after_stripe(self):
        client = self._login()
        with client.session_transaction() as sess:
            sess.clear()
        status = client.get('/api/member-status').get_json()
        self.assertTrue(status['logged_in'])
        self.assertEqual(status['email'], 'guest@example.com')

    def test_logout_clears_stripe_surviving_cookie(self):
        client = self._login()
        token = client.get('/legacy').headers.get('X-CSRF-Token')
        client.post('/logout', data={'csrf_token': token, 'next': '/'})
        self.assertFalse(client.get_cookie(thesection.MEMBER_LOGIN_COOKIE))
        status = client.get('/api/member-status').get_json()
        self.assertFalse(status['logged_in'])

    def test_success_page_keeps_signed_in_member(self):
        client = self._login()
        with self.app.test_request_context('/success'):
            html = thesection.render_template(
                'success.html',
                error=None,
                ticket_data='abc',
                ticket_id='TICKET1',
                quantity=1,
                ticket_type='general',
                access=None,
                email='guest@example.com',
                email_sent=True,
                wallet_enabled=False,
                view_token='tok',
                ticket_view_url='/t/TICKET1',
                member_logged_in=True,
            )
        self.assertIn("You're still signed in", html)
        self.assertNotIn('Create an account or sign in', html)
        self.assertTrue(client.get_cookie(thesection.MEMBER_LOGIN_COOKIE))


if __name__ == '__main__':
    unittest.main()
