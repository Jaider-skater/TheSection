"""Protected mailing-list addresses cannot be removed or renamed."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


PROTECTED = (
    'hallieworkshop@gmail.com',
    'thesectionevents@gmail.com',
)


class ProtectedMailingListTests(unittest.TestCase):
    def setUp(self):
        self.invites = []
        self.full_list = []
        self.members = []
        self.patches = [
            mock.patch.object(thesection, 'load_invites', side_effect=lambda: list(self.invites)),
            mock.patch.object(thesection, 'save_invites', side_effect=self._save_invites),
            mock.patch.object(thesection, 'load_full_mailing_list', side_effect=lambda: list(self.full_list)),
            mock.patch.object(thesection, 'save_full_mailing_list', side_effect=self._save_full),
            mock.patch.object(thesection, 'load_members', side_effect=lambda: list(self.members)),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
        ]
        for patcher in self.patches:
            patcher.start()
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()

    def _save_invites(self, invites):
        self.invites = list(invites)
        return True

    def _save_full(self, entries):
        self.full_list = list(entries)
        return True

    def _invite(self, email):
        return {
            'email': email,
            'added_at': datetime.now(timezone.utc).isoformat(),
            'sent_at': None,
            'claimed_at': None,
            'invite_token': None,
            'invite_expires': None,
        }

    def _full_entry(self, email):
        return {
            'email': email,
            'added_at': datetime.now(timezone.utc).isoformat(),
            'source': 'manual',
        }

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

    def test_protected_emails_cannot_be_removed_from_exclusive_list(self):
        self.invites = [
            self._invite(PROTECTED[0]),
            self._invite('guest@example.com'),
        ]
        self.assertFalse(thesection.remove_email_from_invite_list(PROTECTED[0]))
        self.assertFalse(thesection.remove_email_from_invite_list('HallieWorkshop@gmail.com'))
        self.assertTrue(thesection.remove_email_from_invite_list('guest@example.com'))
        remaining = {i['email'] for i in self.invites}
        self.assertEqual(remaining, {PROTECTED[0]})

    def test_protected_emails_cannot_be_removed_from_full_list(self):
        self.full_list = [
            self._full_entry(PROTECTED[1]),
            self._full_entry('guest@example.com'),
        ]
        self.assertFalse(thesection.remove_email_from_full_mailing_list(PROTECTED[1]))
        self.assertTrue(thesection.remove_email_from_full_mailing_list('guest@example.com'))
        remaining = {e['email'] for e in self.full_list}
        self.assertEqual(remaining, {PROTECTED[1]})

    def test_protected_emails_cannot_be_renamed(self):
        self.invites = [self._invite(PROTECTED[0])]
        self.full_list = [self._full_entry(PROTECTED[1])]

        ok, err = thesection.update_email_on_invite_list(PROTECTED[0], 'other@example.com')
        self.assertFalse(ok)
        self.assertIn('protected', err)

        ok, err = thesection.update_email_on_full_mailing_list(PROTECTED[1], 'other@example.com')
        self.assertFalse(ok)
        self.assertIn('protected', err)

        self.assertEqual(self.invites[0]['email'], PROTECTED[0])
        self.assertEqual(self.full_list[0]['email'], PROTECTED[1])

    def test_admin_remove_returns_error_and_keeps_address(self):
        self.invites = [self._invite(PROTECTED[0])]
        self.full_list = [self._full_entry(PROTECTED[1])]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')

        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_email',
                'email': PROTECTED[0],
                'csrf_token': token,
            },
        ).get_data(as_text=True)
        self.assertIn('cannot be removed', html)
        self.assertEqual({i['email'] for i in self.invites}, {PROTECTED[0]})

        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_full_email',
                'email': PROTECTED[1],
                'csrf_token': token,
            },
        ).get_data(as_text=True)
        self.assertIn('cannot be removed', html)
        self.assertEqual({e['email'] for e in self.full_list}, {PROTECTED[1]})

    def test_admin_page_hides_delete_for_protected_emails(self):
        self.invites = [
            self._invite(PROTECTED[0]),
            self._invite('guest@example.com'),
        ]
        self.full_list = [
            self._full_entry(PROTECTED[1]),
            self._full_entry('full@example.com'),
        ]
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)

        self.assertIn('Locked', html)
        self.assertIn('remove_email', html)
        self.assertIn('remove_full_email', html)
        self.assertNotIn(f"Remove {PROTECTED[0]} from exclusive list", html)
        self.assertNotIn(f"Remove {PROTECTED[1]} from full list", html)
        self.assertIn('Remove guest@example.com from exclusive list', html)
        self.assertIn('Remove full@example.com from full list', html)

    def test_delete_confirm_does_not_break_out_of_javascript_string(self):
        crafted = "xss@x.com');alert(1);//.a"
        self.invites = [self._invite(crafted)]
        self.full_list = [self._full_entry(crafted)]
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertNotIn("confirm('Remove " + crafted, html)
        self.assertIn('alert(1)', html)

    def test_normalize_email_list_rejects_invalid_addresses(self):
        parsed = thesection.normalize_email_list(
            "ok@example.com\nnot-an-email\nfoo@x.com');alert(1);//\n<a@b.co>"
        )
        self.assertEqual(parsed, ['ok@example.com'])

    def test_rename_rejects_invalid_email(self):
        self.invites = [self._invite('guest@example.com')]
        ok, err = thesection.update_email_on_invite_list(
            'guest@example.com', "bad@x.com');alert(1);//"
        )
        self.assertFalse(ok)
        self.assertIn('valid', err)
        self.assertEqual(self.invites[0]['email'], 'guest@example.com')

    def test_broadcast_html_escapes_body_and_rejects_subject_newlines(self):
        captured = []

        def fake_send(msg):
            captured.append(msg)

        with mock.patch.object(thesection.mail, 'send', side_effect=fake_send):
            sent, failed = thesection.send_broadcast_email(
                'Hello',
                '<script>alert(1)</script>\nsee you',
                ['a@b.co'],
            )
        self.assertEqual(sent, ['a@b.co'])
        self.assertEqual(failed, [])
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', captured[0].html)
        self.assertNotIn('<script>', captured[0].html)

        captured.clear()
        with mock.patch.object(thesection.mail, 'send', side_effect=fake_send):
            sent, failed = thesection.send_broadcast_email(
                'Hello\nBcc: evil@example.com',
                'Hi',
                ['a@b.co'],
            )
        self.assertEqual(sent, [])
        self.assertEqual(failed, [])
        self.assertEqual(captured, [])


if __name__ == '__main__':
    unittest.main()
