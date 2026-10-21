"""Door QR join link: login / sign-up / already-in routes, bad code, regenerate, note."""
import unittest
from unittest import mock

import app as thesection  # noqa: E402
import test_costume_scan_gate as _gate


class CostumeJoinQrTests(_gate.CostumeScanGateTests):
    def _code(self):
        return thesection.get_costume_join_code()

    def _admin(self):
        admin = self.app.test_client()
        token = admin.get('/admin/login').headers.get('X-CSRF-Token')
        admin.post('/admin/login', data={'password': thesection.admin_key, 'csrf_token': token})
        return admin

    def _locked(self, client):
        return thesection.COSTUME_SCAN_GATE_MESSAGE in client.get('/costumes').get_data(as_text=True)

    def test_logged_in_join_grants_and_lands_on_contest(self):
        client = self._login()
        self.assertTrue(self._locked(client))
        resp = client.get(f'/costumes/join/{self._code()}')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/costumes'))
        self.assertFalse(self._locked(client))

    def test_logged_out_goes_to_login_then_back_to_contest(self):
        code = self._code()
        members = thesection.load_members()
        members.append(self._member())
        thesection.save_members(members)
        client = self.app.test_client()
        resp = client.get(f'/costumes/join/{code}')
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers['Location']
        self.assertIn('/legacy', loc)
        self.assertIn(f'next=/costumes/join/{code}', loc)
        page = client.get(loc)
        html = page.get_data(as_text=True)
        self.assertIn(f'value="/costumes/join/{code}"', html)
        self.assertIn('value="register"', html)  # create-profile route offered
        login = client.post('/legacy', data={
            'action': 'login', 'email': 'guest@example.com', 'password': 'password123',
            'next': f'/costumes/join/{code}', 'csrf_token': page.headers.get('X-CSRF-Token'),
        })
        self.assertTrue(login.headers['Location'].endswith(f'/costumes/join/{code}'))
        final = client.get(login.headers['Location'])
        self.assertTrue(final.headers['Location'].endswith('/costumes'))
        self.assertFalse(self._locked(client))

    def test_signup_carries_next_and_grants(self):
        code = self._code()
        client = self.app.test_client()
        page = client.get(f'/legacy?next=/costumes/join/{code}')
        resp = client.post('/legacy', data={
            'action': 'register', 'email': 'new@example.com', 'password': 'password123',
            'confirm_password': 'password123', 'next': f'/costumes/join/{code}',
            'csrf_token': page.headers.get('X-CSRF-Token'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith(f'/costumes/join/{code}'))
        final = client.get(resp.headers['Location'])
        self.assertTrue(final.headers['Location'].endswith('/costumes'))
        self.assertIn('new@example.com', thesection.costume_access_emails())
        self.assertFalse(self._locked(client))

    def test_bad_code_rejected(self):
        self._code()
        client = self._login()
        resp = client.get('/costumes/join/not-the-code')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(thesection.costume_access_emails(), [])
        self.assertTrue(self._locked(client))

    def test_no_code_yet_rejects_everything(self):
        client = self._login()
        self.assertEqual(client.get('/costumes/join/anything').status_code, 404)

    def test_regenerate_invalidates_old_code(self):
        old = self._code()
        admin = self._admin()
        resp = admin.post('/admin/costume-qr/regenerate',
                          data={'csrf_token': admin.get('/admin').headers.get('X-CSRF-Token')})
        self.assertEqual(resp.status_code, 302)
        new = thesection.get_costume_join_code(create=False)
        self.assertTrue(new and new != old)
        client = self._login()
        self.assertEqual(client.get(f'/costumes/join/{old}').status_code, 404)
        self.assertEqual(client.get(f'/costumes/join/{new}').status_code, 302)

    def test_regenerate_and_qr_require_admin(self):
        old = self._code()
        client = self._login()
        client.post('/admin/costume-qr/regenerate',
                    data={'csrf_token': client.get('/legacy').headers.get('X-CSRF-Token')})
        self.assertEqual(thesection.get_costume_join_code(create=False), old)
        self.assertNotEqual(client.get('/admin/costume-qr.png').mimetype, 'image/png')

    def test_admin_sees_qr_png_and_print_view(self):
        admin = self._admin()
        png = admin.get('/admin/costume-qr.png')
        self.assertEqual(png.mimetype, 'image/png')
        self.assertTrue(png.data.startswith(b'\x89PNG'))
        dl = admin.get('/admin/costume-qr.png?download=1')
        self.assertIn('attachment', dl.headers.get('Content-Disposition', ''))
        html = admin.get('/admin/costume-qr').get_data(as_text=True)
        self.assertIn('/costumes/join/' + thesection.get_costume_join_code(create=False), html)
        dash = admin.get('/admin').get_data(as_text=True)
        self.assertIn('Regenerate code', dash)
        self.assertNotIn('Grant access', dash)

    def test_open_redirect_blocked(self):
        client = self._login()
        for bad in ('//evil.com', 'https://evil.com', '/\\evil.com'):
            page = client.get('/legacy')
            members_client = self.app.test_client()
            p = members_client.get('/legacy')
            resp = members_client.post('/legacy', data={
                'action': 'login', 'email': 'guest@example.com', 'password': 'password123',
                'next': bad, 'csrf_token': p.headers.get('X-CSRF-Token'),
            })
            self.assertNotIn('evil.com', resp.headers.get('Location', ''))
        self.assertEqual(thesection.safe_next_url('//evil.com', '/x'), '/x')

    def test_join_while_hidden_grants_but_contest_stays_hidden(self):
        code = self._code()
        thesection.set_costume_contest_open(False)
        client = self._login()
        client.get(f'/costumes/join/{code}')
        self.assertIn('guest@example.com', thesection.costume_access_emails())
        self.assertEqual(client.get('/costumes').status_code, 302)

    def test_fake_entries_note_present(self):
        client = self._login()
        client.get(f'/costumes/join/{self._code()}')
        html = client.get('/costumes').get_data(as_text=True)
        self.assertIn('Fake entries will be deleted.', html)
        self.assertIn('Fake entries will be deleted.', client.get('/legacy').get_data(as_text=True))


for _name in list(vars(_gate.CostumeScanGateTests)):
    if _name.startswith('test_') and _name not in vars(CostumeJoinQrTests):
        setattr(CostumeJoinQrTests, _name, None)
