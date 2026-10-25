"""Rate limits key on a trusted IP; a spoofed X-Forwarded-For can't reset them."""
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')

import app as thesection  # noqa: E402


class ClientIpTests(unittest.TestCase):
    def setUp(self):
        thesection._rate_limit_buckets.clear()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def ip(self, headers, production=False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        from werkzeug.test import EnvironBuilder
        env = EnvironBuilder(path='/', headers=headers, environ_base={'REMOTE_ADDR': '10.0.0.1'}).get_environ()
        seen = {}
        def inner(environ, start_response):
            seen['env'] = environ
            return []
        ProxyFix(inner, x_for=1)(env, lambda *a: None)
        with mock.patch.object(thesection, 'IS_PRODUCTION', production):
            with self.app.request_context(seen['env']):
                return thesection.client_ip()

    def test_leftmost_xff_ignored(self):
        self.assertEqual(self.ip({'X-Forwarded-For': '6.6.6.6, 203.0.113.9'}), '203.0.113.9')

    def test_no_xff_uses_socket(self):
        self.assertEqual(self.ip({}), '10.0.0.1')

    def test_production_prefers_cloudflare_ip(self):
        self.assertEqual(
            self.ip({'X-Forwarded-For': '6.6.6.6, 198.51.100.7', 'CF-Connecting-IP': '203.0.113.9'}, True),
            '203.0.113.9')

    def test_cf_header_ignored_off_render(self):
        self.assertEqual(self.ip({'CF-Connecting-IP': '6.6.6.6'}), '10.0.0.1')

    def test_spoofed_xff_cannot_bypass_limit(self):
        client = self.app.test_client()
        codes = []
        for i in range(35):
            r = client.get('/success?session_id=cs_bogus',
                           headers={'X-Forwarded-For': f'6.6.6.{i}, 203.0.113.9'})
            codes.append('Too many requests' in r.get_data(as_text=True))
        self.assertFalse(any(codes[:30]))
        self.assertTrue(all(codes[30:]))

    def test_login_failure_limit_holds_across_spoofed_xff(self):
        with self.app.test_request_context('/', headers={'X-Forwarded-For': '1.1.1.1'}):
            for _ in range(100):
                thesection.rate_limit_record('member_login_fail')
        with self.app.test_request_context('/', headers={'X-Forwarded-For': '9.9.9.9'}):
            self.assertTrue(thesection.rate_limit_blocked('member_login_fail', 100, 300))


if __name__ == '__main__':
    unittest.main()
