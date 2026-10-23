"""Shared venue Wi-Fi: one IP, a crowd. Valid joins / logins must not be blocked."""
import unittest

import app as thesection  # noqa: E402
import test_costume_scan_gate as _gate


class CrowdRateLimitTests(_gate.CostumeScanGateTests):
    def test_valid_join_never_limited(self):
        code = thesection.get_costume_join_code()
        client = self.app.test_client()
        for _ in range(100):
            self.assertEqual(client.get(f'/costumes/join/{code}').status_code, 302)

    def test_invalid_codes_limited_after_20(self):
        thesection.get_costume_join_code()
        client = self.app.test_client()
        for _ in range(20):
            self.assertEqual(client.get('/costumes/join/nope').status_code, 404)
        self.assertEqual(client.get('/costumes/join/nope').status_code, 429)
        code = thesection.get_costume_join_code(create=False)
        self.assertEqual(client.get(f'/costumes/join/{code}').status_code, 302)

    def _post_login(self, client, email, pw):
        page = client.get('/legacy')
        return client.post('/legacy', data={'action': 'login', 'email': email, 'password': pw,
                                            'csrf_token': page.headers.get('X-CSRF-Token')})

    def test_many_successful_logins_one_ip(self):
        members = thesection.load_members()
        members.append(self._member())
        thesection.save_members(members)
        for _ in range(40):
            resp = self._post_login(self.app.test_client(), 'guest@example.com', 'password123')
            self.assertEqual(resp.status_code, 302)

    def test_per_account_failures_still_limited(self):
        members = thesection.load_members()
        members.append(self._member())
        thesection.save_members(members)
        client = self.app.test_client()
        for _ in range(10):
            self.assertEqual(self._post_login(client, 'guest@example.com', 'wrong').status_code, 200)
        self.assertEqual(self._post_login(client, 'guest@example.com', 'password123').status_code, 429)
        # Another guest on same IP unaffected
        self.assertNotEqual(self._post_login(client, 'other@example.com', 'x').status_code, 429)

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
