"""Live homepage viewer count for admin. Isolated from checkout/login."""
import os
import time
import unittest
from datetime import timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


class PublicViewingCountTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(thesection, 'load_tickets', return_value=[]),
            mock.patch.object(thesection, 'load_members', return_value=[]),
            mock.patch.object(thesection, 'load_invites', return_value=[]),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
        ]
        for patcher in self.patches:
            patcher.start()
        with thesection._presence_lock:
            thesection._presence_seen.clear()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        with thesection._presence_lock:
            thesection._presence_seen.clear()

    def _admin_client(self):
        client = self.app.test_client()
        token = client.get('/admin/login').headers.get('X-CSRF-Token')
        resp = client.post(
            '/admin/login',
            data={'password': thesection.admin_key, 'csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        return client

    def test_heartbeat_is_public_and_hides_count(self):
        client = self.app.test_client()
        resp = client.get('/api/viewing')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, {'ok': True})
        self.assertNotIn('viewing', data)
        self.assertTrue(client.get_cookie(thesection.VISITOR_COOKIE))

    def test_same_visitor_counts_once(self):
        client = self.app.test_client()
        client.get('/api/viewing')
        client.get('/api/viewing')
        self.assertEqual(thesection.count_public_viewers(), 1)

    def test_two_visitors_count_separately(self):
        a = self.app.test_client()
        b = self.app.test_client()
        a.get('/api/viewing')
        b.get('/api/viewing')
        self.assertEqual(thesection.count_public_viewers(), 2)

    def test_stale_presence_is_not_counted(self):
        with thesection._presence_lock:
            thesection._presence_seen['aa' * 8] = time.time() - 500
        self.assertEqual(thesection.count_public_viewers(), 0)

    def test_count_requires_admin(self):
        guest = self.app.test_client()
        guest.get('/api/viewing')
        denied = guest.get('/admin/viewing.json')
        self.assertIn(denied.status_code, (401, 302))
        self.assertNotEqual((denied.get_json() or {}).get('viewing'), 1)

        admin = self._admin_client()
        data = admin.get('/admin/viewing.json').get_json()
        self.assertEqual(data['viewing'], 1)

    def test_admin_page_shows_viewing_label(self):
        html = self._admin_client().get('/admin').get_data(as_text=True)
        self.assertIn('live-viewing', html)
        self.assertIn('/admin/viewing.json', html)
        self.assertIn('people viewing', html)

    def test_heartbeat_failure_is_not_500(self):
        with mock.patch.object(thesection, 'bump_public_viewer', side_effect=RuntimeError('boom')):
            resp = self.app.test_client().get('/api/viewing')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {'ok': True})

    def test_homepage_sends_heartbeat(self):
        js_path = os.path.join(os.path.dirname(__file__), '..', 'website', 'static', 'js', 'home.js')
        with open(js_path, encoding='utf-8') as handle:
            js = handle.read()
        self.assertIn("fetch('/api/viewing'", js)


if __name__ == '__main__':
    unittest.main()
