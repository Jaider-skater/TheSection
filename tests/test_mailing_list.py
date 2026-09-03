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
        self.tickets = []
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
            mock.patch.object(thesection, 'load_tickets', side_effect=lambda: list(self.tickets)),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
            mock.patch.object(thesection.mail, 'send', side_effect=self._capture_mail_send),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection._delivered_broadcasts.clear()
        thesection._inflight_broadcasts.clear()
        thesection._rate_limit_buckets.clear()
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
        self.assertIn('Sent emails', html)
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
            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Hello',
                '<script>alert(1)</script>\nsee you',
                ['a@b.co'],
            )
        self.assertEqual(sent, ['a@b.co'])
        self.assertEqual(failed, [])
        self.assertEqual(skipped, [])
        self.assertEqual(pending, [])
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', captured[0].html)
        self.assertNotIn('<script>', captured[0].html)

        captured.clear()
        with mock.patch.object(thesection.mail, 'send', side_effect=fake_send):
            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Hello\nBcc: evil@example.com',
                'Hi',
                ['a@b.co'],
            )
        self.assertEqual(sent, [])
        self.assertEqual(failed, [])
        self.assertEqual(skipped, [])
        self.assertEqual(pending, [])
        self.assertEqual(captured, [])

    def test_broadcast_and_invite_are_written_to_send_log(self):
        sent, failed, skipped, pending = thesection.send_broadcast_email(
            'Halloween is on',
            'See you there',
            ['a@b.co', 'c@d.co'],
        )
        self.assertEqual(sent, ['a@b.co', 'c@d.co'])
        self.assertEqual(failed, [])
        self.assertEqual(skipped, [])
        self.assertEqual(pending, [])

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
        self.assertIn('See you there', html)
        snapshots = [entry for entry in self.log if entry.get('action') == 'message']
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]['body'], 'See you there')

    def test_page_email_lists_are_scrollable(self):
        self.invites = [self._invite('guest@example.com')]
        self.full_list = [self._full_entry('full@example.com')]
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('mail-scroll', html)
        self.assertNotIn('max-h-[28rem] overflow-auto', html)
        self.assertIn('data-list="exclusive"', html)
        self.assertIn('data-list="full"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('drawer-panel open', html)

    def test_send_broadcast_mail_failure_does_not_500(self):
        self.invites = [self._invite('guest@example.com')]
        thesection._rate_limit_buckets.clear()
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        with mock.patch.object(thesection.mail, 'send', side_effect=RuntimeError('smtp down')):
            resp = client.post(
                '/admin/mailing-list',
                data={
                    'action': 'send_broadcast',
                    'list_exclusive': '1',
                    'subject': 'Hello',
                    'body': 'There',
                    'csrf_token': token,
                },
            )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn('Internal Server Error', html)
        self.assertIn('failed', html.lower())

    def test_delete_still_works_when_log_write_raises(self):
        self.invites = [self._invite('guest@example.com')]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        with mock.patch.object(thesection, 'save_mailing_list_log', side_effect=RuntimeError('disk')):
            resp = client.post(
                '/admin/mailing-list',
                data={
                    'action': 'remove_email',
                    'email': 'guest@example.com',
                    'csrf_token': token,
                },
            )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn('Internal Server Error', html)
        self.assertIn('Removed guest@example.com', html)
        self.assertEqual(self.invites, [])

    def test_delete_and_send_exceptions_render_error_not_500(self):
        self.invites = [self._invite('guest@example.com')]
        thesection._rate_limit_buckets.clear()
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        with mock.patch.object(
            thesection, 'remove_emails_from_invite_list', side_effect=RuntimeError('boom')
        ):
            resp = client.post(
                '/admin/mailing-list',
                data={
                    'action': 'remove_email',
                    'email': 'guest@example.com',
                    'csrf_token': token,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('try again', resp.get_data(as_text=True).lower())

        with mock.patch.object(
            thesection, 'send_broadcast_email', side_effect=RuntimeError('boom')
        ):
            resp = client.post(
                '/admin/mailing-list',
                data={
                    'action': 'send_broadcast',
                    'list_exclusive': '1',
                    'subject': 'Hello',
                    'body': 'There',
                    'csrf_token': token,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('try again', resp.get_data(as_text=True).lower())

    def test_remove_survives_null_email_records(self):
        self.invites = [
            {'email': None, 'added_at': '2026-01-01T00:00:00+00:00'},
            self._invite('guest@example.com'),
        ]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        resp = client.get('/admin/mailing-list')
        self.assertEqual(resp.status_code, 200)
        resp = client.post(
            '/admin/mailing-list',
            data={
                'action': 'remove_email',
                'email': 'guest@example.com',
                'csrf_token': token,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Internal Server Error', resp.get_data(as_text=True))
        remaining = {i.get('email') for i in self.invites}
        self.assertEqual(remaining, {None})

    def test_same_broadcast_does_not_send_twice(self):
        captured = []

        def fake_send(msg):
            captured.append(msg)

        with mock.patch.object(thesection.mail, 'send', side_effect=fake_send):
            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Halloween is on',
                'See you there',
                ['a@b.co', 'c@d.co'],
            )
            self.assertEqual(sent, ['a@b.co', 'c@d.co'])
            self.assertEqual(failed, [])
            self.assertEqual(skipped, [])
            self.assertEqual(pending, [])
            self.assertEqual(len(captured), 2)

            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Halloween is on',
                'See you there',
                ['a@b.co', 'c@d.co', 'new@e.co'],
            )
        self.assertEqual(sent, ['new@e.co'])
        self.assertEqual(failed, [])
        self.assertEqual(set(skipped), {'a@b.co', 'c@d.co'})
        self.assertEqual(pending, [])
        self.assertEqual(len(captured), 3)

        sent, failed, skipped, pending = thesection.send_broadcast_email(
            'Different night',
            'See you there',
            ['a@b.co'],
        )
        self.assertEqual(sent, ['a@b.co'])
        self.assertEqual(skipped, [])
        self.assertEqual(pending, [])

    def test_duplicate_recipients_are_only_sent_once(self):
        sent, failed, skipped, pending = thesection.send_broadcast_email(
            'Hello',
            'There',
            ['A@b.co', 'a@b.co', 'a@b.co'],
        )
        self.assertEqual(sent, ['a@b.co'])
        self.assertEqual(failed, [])
        self.assertEqual(skipped, [])
        self.assertEqual(pending, [])
        self.assertEqual(len(self.mail_sent), 1)

    def test_address_on_both_lists_is_only_sent_once(self):
        self.invites = [self._invite('both@example.com')]
        self.full_list = [self._full_entry('both@example.com')]
        recipients = thesection.resolve_broadcast_recipients({'exclusive', 'full'})
        self.assertEqual(recipients, ['both@example.com'])
        sent, failed, skipped, pending = thesection.send_broadcast_email(
            'Hello', 'There', recipients
        )
        self.assertEqual(sent, ['both@example.com'])
        self.assertEqual(len(self.mail_sent), 1)

        sent2, failed2, skipped2, pending2 = thesection.send_broadcast_email(
            'Hello', 'There', recipients
        )
        self.assertEqual(sent2, [])
        self.assertEqual(skipped2, ['both@example.com'])
        self.assertEqual(len(self.mail_sent), 1)

    def test_failed_broadcast_can_be_retried_once_it_succeeds_it_cannot(self):
        with mock.patch.object(
            thesection.mail, 'send', side_effect=RuntimeError('smtp down')
        ):
            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Hello', 'There', ['a@b.co']
            )
        self.assertEqual(sent, [])
        self.assertEqual(failed, ['a@b.co'])
        self.assertEqual(self.mail_sent, [])

        sent2, failed2, skipped2, pending2 = thesection.send_broadcast_email(
            'Hello', 'There', ['a@b.co']
        )
        self.assertEqual(sent2, ['a@b.co'])
        self.assertEqual(failed2, [])
        self.assertEqual(skipped2, [])
        self.assertEqual(len(self.mail_sent), 1)

        sent3, failed3, skipped3, pending3 = thesection.send_broadcast_email(
            'Hello', 'There', ['a@b.co']
        )
        self.assertEqual(sent3, [])
        self.assertEqual(skipped3, ['a@b.co'])
        self.assertEqual(len(self.mail_sent), 1)

    def test_successful_send_is_not_resent_if_log_write_fails(self):
        with mock.patch.object(thesection, 'log_mailing_list_send', return_value=False):
            sent, failed, skipped, pending = thesection.send_broadcast_email(
                'Hello', 'There', ['a@b.co']
            )
        self.assertEqual(sent, ['a@b.co'])
        self.assertEqual(failed, [])
        sent2, failed2, skipped2, pending2 = thesection.send_broadcast_email(
            'Hello', 'There', ['a@b.co']
        )
        self.assertEqual(sent2, [])
        self.assertEqual(skipped2, ['a@b.co'])
        self.assertEqual(len(self.mail_sent), 1)

    def test_invite_is_not_resent_after_success(self):
        self.invites = [self._invite('guest@example.com')]
        self.assertEqual(thesection.invites_ready_to_send(), ['guest@example.com'])

        self.assertTrue(thesection.send_member_invite_email('guest@example.com', 'token'))
        self.assertTrue(thesection.mark_member_invite_sent('guest@example.com'))
        self.assertEqual(thesection.invites_ready_to_send(), [])

        before = len(self.mail_sent)
        result = thesection.send_pending_member_invites()
        self.assertEqual(result['sent'], [])
        self.assertEqual(self.mail_sent[before:], [])

        self.assertTrue(thesection.send_member_invite_email('guest@example.com', 'token-2'))
        self.assertEqual(len(self.mail_sent), before)

    def test_admin_broadcast_repeat_skips_already_sent(self):
        self.invites = [self._invite('guest@example.com')]
        thesection._rate_limit_buckets.clear()
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        data = {
            'action': 'send_broadcast',
            'list_exclusive': '1',
            'subject': 'Doors at 9',
            'body': 'Come through',
            'csrf_token': token,
        }
        first = client.post('/admin/mailing-list', data=data)
        self.assertEqual(first.status_code, 200)
        self.assertIn('Sent broadcast to 1 address', first.get_data(as_text=True))
        self.assertEqual(len(self.mail_sent), 1)

        second = client.post('/admin/mailing-list', data=data)
        self.assertEqual(second.status_code, 200)
        html = second.get_data(as_text=True)
        self.assertIn('already sent', html.lower())
        self.assertEqual(len(self.mail_sent), 1)

    def test_page_blocks_double_submit(self):
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn("form.dataset.submitting === '1'", html)
        self.assertIn('form.requestSubmit()', html)
        self.assertNotIn('confirmOk.disabled = true', html)
        self.assertIn('fillBroadcastForm', html)
        self.assertIn('data-send-mode="remaining"', html)

    def test_mailing_list_shows_ticket_purchase_counts(self):
        self.invites = [self._invite('guest@example.com'), self._invite('none@example.com')]
        self.full_list = [self._full_entry('full@example.com')]
        self.tickets = [
            {'email': 'guest@example.com', 'quantity': 2},
            {'email': 'Guest@example.com', 'quantity': 1},
            {'email': 'full@example.com', 'quantity': 4},
        ]
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('Tickets', html)
        self.assertIn('data-tickets="3"', html)
        self.assertIn('data-tickets="4"', html)
        self.assertIn('data-tickets="0"', html)
        self.assertEqual(
            thesection.ticket_quantities_by_email(self.tickets),
            {'guest@example.com': 3, 'full@example.com': 4},
        )

    def test_count_signups_and_invites(self):
        claimed = self._invite('joined@example.com')
        claimed['claimed_at'] = datetime.now(timezone.utc).isoformat()
        self.invites = [
            self._invite('pending@example.com'),
            claimed,
            self._invite('member@example.com'),
        ]
        self.members = [{'email': 'member@example.com'}]
        signups, invites = thesection.count_signups_and_invites()
        self.assertEqual(signups, 2)
        self.assertEqual(invites, 3)

    def test_broadcast_timeout_does_not_mark_leftovers_failed(self):
        recipients = [f'user{i}@example.com' for i in range(6)]
        clock = {'t': 0}

        def fake_monotonic():
            return clock['t']

        def tick_send(msg):
            clock['t'] += 0.03

        with mock.patch.object(thesection.time, 'monotonic', side_effect=fake_monotonic):
            with mock.patch.object(thesection.mail, 'send', side_effect=tick_send):
                sent, failed, skipped, pending = thesection.send_broadcast_email(
                    'Tonight',
                    'Doors at 9',
                    recipients,
                    continue_in_background=False,
                    request_budget=0.05,
                )
        self.assertEqual(failed, [])
        self.assertEqual(skipped, [])
        self.assertTrue(pending)
        self.assertEqual(len(sent) + len(pending), 6)
        self.assertLess(len(sent), 6)
        failed_logged = [
            entry for entry in self.log
            if entry.get('action') == 'send' and entry.get('status') == 'failed'
        ]
        self.assertEqual(failed_logged, [])

        sent2, failed2, skipped2, pending2 = thesection.send_broadcast_email(
            'Tonight',
            'Doors at 9',
            recipients,
            continue_in_background=False,
        )
        self.assertEqual(failed2, [])
        self.assertEqual(pending2, [])
        self.assertEqual(set(skipped2), set(sent))
        self.assertEqual(set(sent2), set(pending))

    def test_inflight_addresses_are_not_sent_again(self):
        recipients = [f'user{i}@example.com' for i in range(6)]
        clock = {'t': 0}

        def fake_monotonic():
            return clock['t']

        def tick_send(msg):
            clock['t'] += 0.03

        with mock.patch.object(thesection.time, 'monotonic', side_effect=fake_monotonic):
            with mock.patch.object(thesection.mail, 'send', side_effect=tick_send):
                with mock.patch.object(thesection, '_continue_broadcast_in_background'):
                    sent, failed, skipped, pending = thesection.send_broadcast_email(
                        'Tonight',
                        'Doors at 9',
                        recipients,
                        request_budget=0.05,
                    )
        self.assertTrue(pending)
        before = len(self.mail_sent)
        sent2, failed2, skipped2, pending2 = thesection.send_broadcast_email(
            'Tonight',
            'Doors at 9',
            recipients,
            continue_in_background=False,
        )
        self.assertEqual(sent2, [])
        self.assertTrue(set(pending) <= set(skipped2))
        self.assertEqual(len(self.mail_sent), before)

    def test_broadcast_timeout_continues_in_background(self):
        recipients = [f'user{i}@example.com' for i in range(6)]
        clock = {'t': 0}

        def fake_monotonic():
            return clock['t']

        def tick_send(msg):
            clock['t'] += 0.03

        with mock.patch.dict(os.environ, {'BROADCAST_REQUEST_BUDGET': '0.05'}):
            with mock.patch.object(thesection.time, 'monotonic', side_effect=fake_monotonic):
                with mock.patch.object(thesection.mail, 'send', side_effect=tick_send):
                    sent, failed, skipped, pending = thesection.send_broadcast_email(
                        'Tonight', 'Doors at 9', recipients
                    )
                    self.assertEqual(failed, [])
                    self.assertTrue(pending)
                    self.assertTrue(thesection.wait_for_broadcast_background(timeout=8))
        already = thesection.emails_already_sent_message(
            'broadcast', 'Tonight', 'Doors at 9'
        )
        self.assertEqual(already, set(recipients))

    def test_broadcast_reuses_smtp_connection(self):
        captured = []

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def send(self, msg):
                captured.append(msg)

        previous = self.app.config['TESTING']
        self.app.config['TESTING'] = False
        self.app.config['MAIL_DEFAULT_SENDER'] = 'hallie@example.com'
        self.app.config['MAIL_USERNAME'] = 'hallie@example.com'
        self.app.config['MAIL_PASSWORD'] = 'secret'
        self.app.config['MAIL_SERVER'] = 'smtp.gmail.com'
        try:
            with mock.patch.object(thesection.mail, 'connect', return_value=FakeConn()):
                sent, failed, skipped, pending = thesection.send_broadcast_email(
                    'Hello', 'There', ['a@b.co', 'c@d.co']
                )
        finally:
            self.app.config['TESTING'] = previous
        self.assertEqual(sent, ['a@b.co', 'c@d.co'])
        self.assertEqual(failed, [])
        self.assertEqual(pending, [])
        self.assertEqual(len(captured), 2)
        self.assertEqual(self.mail_sent, [])

    def test_admin_broadcast_resume_skips_rate_limit(self):
        self.invites = [self._invite('guest@example.com'), self._invite('plus@example.com')]
        thesection._rate_limit_buckets.clear()
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        data = {
            'action': 'send_broadcast',
            'list_exclusive': '1',
            'subject': 'Doors at 9',
            'body': 'Come through',
            'csrf_token': token,
        }
        first = client.post('/admin/mailing-list', data=data)
        self.assertEqual(first.status_code, 200)
        self.assertIn('Sent broadcast to 2 addresses', first.get_data(as_text=True))

        self.invites.append(self._invite('new@example.com'))
        with mock.patch.object(thesection, 'rate_limit_allow', return_value=False):
            second = client.post('/admin/mailing-list', data=data)
        self.assertEqual(second.status_code, 200)
        html = second.get_data(as_text=True)
        self.assertNotIn('Broadcast limit reached', html)
        self.assertIn('already sent', html.lower())
        self.assertIn('Sent broadcast to 1 address', html)
        self.assertEqual(len(self.mail_sent), 3)

    def test_admin_new_broadcast_still_rate_limited(self):
        self.invites = [self._invite('guest@example.com')]
        client = self._admin_client()
        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        with mock.patch.object(thesection, 'rate_limit_allow', return_value=False):
            resp = client.post(
                '/admin/mailing-list',
                data={
                    'action': 'send_broadcast',
                    'list_exclusive': '1',
                    'subject': 'Brand new',
                    'body': 'Hello',
                    'csrf_token': token,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Broadcast limit reached', resp.get_data(as_text=True))
        self.assertEqual(self.mail_sent, [])

    def test_legacy_send_rows_appear_without_saved_body(self):
        self.log = [
            {
                'id': 'aaaaaaa1',
                'action': 'send',
                'kind': 'broadcast',
                'subject': 'Doors at 9',
                'status': 'sent',
                'fingerprint': 'abc123abc123abcd',
                'emails': ['got@example.com'],
                'at': '2026-09-01T00:00:00+00:00',
            },
            {
                'id': 'aaaaaaa2',
                'action': 'send',
                'kind': 'broadcast',
                'subject': 'Doors at 9',
                'status': 'failed',
                'fingerprint': 'abc123abc123abcd',
                'emails': ['missed@example.com'],
                'at': '2026-09-01T00:00:00+00:00',
            },
        ]
        rows = thesection.broadcast_messages_for_admin()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['subject'], 'Doors at 9')
        self.assertFalse(rows[0]['has_body'])
        self.assertEqual(rows[0]['sent'], ['got@example.com'])
        self.assertEqual(rows[0]['failed'], ['missed@example.com'])
        html = self._admin_client().get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('Doors at 9', html)
        self.assertIn('got@example.com', html)
        self.assertIn('missed@example.com', html)
        self.assertIn('Click to load the subject', html)
        self.assertIn('fill-email-card', html)
        self.assertNotIn('name="message_id"', html)

    def test_broadcast_saves_message_and_retry_sends_remaining(self):
        self.invites = [
            self._invite('got@example.com'),
            self._invite('missed@example.com'),
        ]
        sent, failed, skipped, pending = thesection.send_broadcast_email(
            'Doors at 9',
            'Come through',
            ['got@example.com'],
            lists={'exclusive'},
        )
        self.assertEqual(sent, ['got@example.com'])
        self.assertEqual(failed, [])
        snapshots = [entry for entry in self.log if entry.get('action') == 'message']
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]['subject'], 'Doors at 9')
        self.assertEqual(snapshots[0]['body'], 'Come through')
        self.assertEqual(set(snapshots[0]['lists']), {'exclusive'})

        rows = thesection.broadcast_messages_for_admin()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sent_count'], 1)
        self.assertEqual(rows[0]['remaining'], ['missed@example.com'])

        thesection._rate_limit_buckets.clear()
        client = self._admin_client()
        html = client.get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('Come through', html)
        self.assertIn('Send to remaining', html)
        self.assertIn('missed@example.com', html)
        self.assertIn("form.dataset.submitting === '1'", html)

        token = client.get('/admin/mailing-list').headers.get('X-CSRF-Token')
        resp = client.post(
            '/admin/mailing-list',
            data={
                'action': 'retry_broadcast',
                'message_id': snapshots[0]['id'],
                'csrf_token': token,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('missed it', resp.get_data(as_text=True).lower())
        self.assertEqual(
            [msg.recipients[0] for msg in self.mail_sent],
            ['got@example.com', 'missed@example.com'],
        )

        again = client.post(
            '/admin/mailing-list',
            data={
                'action': 'retry_broadcast',
                'message_id': snapshots[0]['id'],
                'csrf_token': token,
            },
        )
        self.assertIn('already got this email', again.get_data(as_text=True).lower())
        self.assertEqual(len(self.mail_sent), 2)

    def test_admin_and_mailing_list_show_signups_vs_invites(self):
        claimed = self._invite('joined@example.com')
        claimed['claimed_at'] = datetime.now(timezone.utc).isoformat()
        self.invites = [self._invite('pending@example.com'), claimed]
        client = self._admin_client()
        admin_html = client.get('/admin').get_data(as_text=True)
        self.assertIn('1 signup vs 2 invites', admin_html)
        mailing_html = client.get('/admin/mailing-list').get_data(as_text=True)
        self.assertIn('1 signup vs 2 invites', mailing_html)


if __name__ == '__main__':
    unittest.main()
