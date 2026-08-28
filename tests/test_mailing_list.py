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
        self.log = []
        self.mail_sent = []
        self.patches = [
            mock.patch.object(thesection, 'load_invites', side_effect=lambda: list(self.invites)),
            mock.patch.object(thesection, 'save_invites', side_effect=self._save_invites),
            mock.patch.object(thesection, 'load_full_mailing_list', side_effect=lambda: list(self.full_list)),
            mock.patch.object(thesection, 'save_full_mailing_list', side_effect=self._save_full),
            mock.patch.object(thesection, 'load_mailing_list_log', side_effect=lambda: list(self.log)),
            mock.patch.object(thesection, 'save_mailing_list_log', side_effect=self._save_log),
            mock.patch.object(thesection, 'load_members', side_effect=lambda: list(self.members)),
            mock.patch.object(thesection, 'save_members', side_effect=lambda members: True),
            mock.patch.object(thesection, 'load_tickets', return_value=[]),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
            mock.patch.object(thesection.mail, 'send', side_effect=self._capture_mail_send),
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

    def _save_log(self, entries):
        self.log = list(entries)
        return True

    def _capture_mail_send(self, msg):
        self.mail_sent.append(msg)

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
        self.assertIn('locked', html.lower())
        self.assertEqual({i['email'] for i in self.invites}, {PROTECTED[0]})

        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_full_email',
                'email': PROTECTED[1],
                'csrf_token': token,
            },
        ).get_data(as_text=True)
        self.assertIn('locked', html.lower())
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
        self.assertNotIn('Select all', html)
        self.assertIn('Remove selected', html)
        self.assertIn('Deletion log', html)
        self.assertIn('Sent log', html)
        self.assertIn('confirm-dialog', html)
        self.assertIn('Select guest@example.com', html)
        self.assertIn('Select full@example.com', html)
        self.assertNotIn(f'Select {PROTECTED[0]}', html)
        self.assertNotIn(f'Select {PROTECTED[1]}', html)

    def test_bulk_remove_skips_protected_and_deletes_the_rest(self):
        self.invites = [
            self._invite(PROTECTED[0]),
            self._invite('guest@example.com'),
            self._invite('other@example.com'),
        ]
        self.full_list = [
            self._full_entry(PROTECTED[1]),
            self._full_entry('full@example.com'),
            self._full_entry('plus@example.com'),
        ]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')

        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_emails',
                'csrf_token': token,
                'emails': [PROTECTED[0], 'guest@example.com', 'other@example.com'],
            },
        ).get_data(as_text=True)
        self.assertIn('Removed 2 emails', html)
        self.assertIn('locked', html.lower())
        self.assertEqual({i['email'] for i in self.invites}, {PROTECTED[0]})

        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_full_emails',
                'csrf_token': token,
                'emails': [PROTECTED[1], 'full@example.com', 'plus@example.com'],
            },
        ).get_data(as_text=True)
        self.assertIn('Removed 2 emails from full list', html)
        self.assertEqual({e['email'] for e in self.full_list}, {PROTECTED[1]})
        logged_emails = {email for entry in self.log for email in entry.get('emails', [])}
        self.assertEqual(
            logged_emails,
            {'guest@example.com', 'other@example.com', 'full@example.com', 'plus@example.com'},
        )
        self.assertNotIn(PROTECTED[0], logged_emails)
        self.assertNotIn(PROTECTED[1], logged_emails)
        self.assertEqual(len(self.log), 4)
        self.assertTrue(all(entry.get('at') for entry in self.log))
        self.assertEqual(self.mail_sent, [])

    def test_backup_log_restore_puts_emails_back(self):
        self.invites = [self._invite('guest@example.com')]
        self.assertTrue(thesection.remove_email_from_invite_list('guest@example.com'))
        self.assertEqual(self.invites, [])
        self.assertEqual(len(self.log), 1)
        entry_id = self.log[0]['id']

        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        html = client.post(
            '/admin/mailing-list',
            data={
                'action': 'restore_log',
                'log_id': entry_id,
                'csrf_token': token,
            },
        ).get_data(as_text=True)
        self.assertIn('Restored', html)
        self.assertEqual({i['email'] for i in self.invites}, {'guest@example.com'})
        self.assertTrue(self.log[0].get('restored_at'))

        html = client.get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('guest@example.com', html)
        self.assertIn('Restored', html)
        self.assertEqual(self.mail_sent, [])

    def test_remove_and_restore_do_not_send_invite_links(self):
        invite = self._invite('guest@example.com')
        invite['sent_at'] = '2026-01-01T00:00:00+00:00'
        self.invites = [invite]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_email',
                'email': 'guest@example.com',
                'csrf_token': token,
            },
        )
        self.assertEqual(self.invites, [])
        self.assertEqual(self.mail_sent, [])
        self.assertEqual(self.log[0]['emails'], ['guest@example.com'])
        self.assertTrue(self.log[0]['at'])

        ok, _msg = thesection.restore_mailing_list_removal(self.log[0]['id'])
        self.assertTrue(ok)
        self.assertEqual(self.mail_sent, [])
        self.assertEqual(self.invites[0]['email'], 'guest@example.com')
        self.assertEqual(self.invites[0].get('sent_at'), '2026-01-01T00:00:00+00:00')

    def test_delete_confirm_does_not_break_out_of_javascript_string(self):
        crafted = "xss@x.com');alert(1);//.a"
        self.invites = [self._invite(crafted)]
        self.full_list = [self._full_entry(crafted)]
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertNotIn("confirm('Remove " + crafted, html)
        self.assertIn('xss@x.com&#39;);alert(1);//.a', html)
        self.assertNotIn(f'value="{crafted}"', html)

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

    def test_broadcast_and_invite_are_written_to_send_log(self):
        sent, failed = thesection.send_broadcast_email(
            'Halloween is on',
            'See you there',
            ['a@b.co', 'c@d.co'],
        )
        self.assertEqual(sent, ['a@b.co', 'c@d.co'])
        self.assertEqual(failed, [])

        self.assertTrue(
            thesection.send_member_invite_email('guest@example.com', 'token')
        )

        sends = [entry for entry in self.log if entry.get('action') == 'send']
        self.assertEqual(len(sends), 3)
        by_email = {entry['emails'][0]: entry for entry in sends}
        self.assertEqual(by_email['a@b.co']['kind'], 'broadcast')
        self.assertEqual(by_email['a@b.co']['subject'], 'Halloween is on')
        self.assertEqual(by_email['c@d.co']['status'], 'sent')
        self.assertEqual(by_email['guest@example.com']['kind'], 'invite')
        self.assertTrue(by_email['guest@example.com']['at'])

        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('Halloween is on', html)
        self.assertIn('a@b.co', html)
        self.assertIn('guest@example.com', html)
        self.assertIn('Broadcast', html)
        self.assertIn('Invite', html)


if __name__ == '__main__':
    unittest.main()
