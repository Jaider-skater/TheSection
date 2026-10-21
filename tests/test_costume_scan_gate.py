"""Costume contest is only for guests whose ticket was scanned at the door (admins always)."""
import os
import unittest
from unittest import mock

import app as thesection  # noqa: E402
import test_costume_voting as _voting

REAL_SCAN_CHECK = thesection.member_has_scanned_contest_ticket


class CostumeScanGateTests(_voting.CostumeVotingTests):
    def setUp(self):
        super().setUp()
        real = mock.patch.object(thesection, 'member_has_scanned_contest_ticket', REAL_SCAN_CHECK)
        real.start()
        self.addCleanup(real.stop)
        hallo = mock.patch.object(thesection, 'costume_contest_event_id', return_value='halloween-2026')
        hallo.start()
        self.addCleanup(hallo.stop)

    def _ticket(self, ticket_id='AAAA1111BBBB2222', email='guest@example.com',
                event_id='halloween-2026', scan=False):
        thesection.record_ticket('cs_' + ticket_id, ticket_id, email, 1, event_id=event_id)
        if scan:
            self.assertTrue(thesection.mark_ticket_scanned(ticket_id))

    def _submit(self, client):
        return client.post('/costumes', data={
            'action': 'submit', 'display_name': 'Alex', 'costume': 'Vampire',
            'csrf_token': self._csrf(client),
        })

    def test_unscanned_ticket_sees_lock_and_cannot_submit(self):
        self._ticket(scan=False)
        client = self._login()
        page = client.get('/costumes').get_data(as_text=True)
        self.assertIn(thesection.COSTUME_SCAN_GATE_MESSAGE, page)
        self.assertEqual(self._submit(client).status_code, 403)
        self.assertEqual(thesection.load_costumes()['entries'], [])
        self.assertNotIn('Costume contest</a>', client.get('/').get_data(as_text=True))
        self.assertNotIn('id="costume-entry"', client.get('/legacy').get_data(as_text=True))

    def test_profile_submit_blocked_without_scan(self):
        client = self._login()
        client.post('/legacy', data={
            'action': 'costume_submit', 'display_name': 'Alex', 'costume': 'Vampire',
            'csrf_token': client.get('/legacy').headers.get('X-CSRF-Token'),
        })
        self.assertEqual(thesection.load_costumes()['entries'], [])

    def test_scanned_ticket_unlocks_everything(self):
        self._ticket(scan=True)
        client = self._login()
        page = client.get('/costumes').get_data(as_text=True)
        self.assertNotIn(thesection.COSTUME_SCAN_GATE_MESSAGE, page)
        self.assertEqual(self._submit(client).status_code, 302)
        self.assertEqual(len(thesection.load_costumes()['entries']), 1)
        self.assertIn('Costume contest</a>', client.get('/').get_data(as_text=True))
        self.assertIn('id="costume-entry"', client.get('/legacy').get_data(as_text=True))

    def test_saved_ticket_bought_under_other_email_counts(self):
        self._ticket(email='friend@example.com', scan=True)
        members = thesection.load_members()
        client = self._login()
        members = thesection.load_members()
        for m in members:
            if m['email'] == 'guest@example.com':
                m['saved_tickets'] = ['AAAA1111BBBB2222']
        thesection.save_members(members)
        self.assertNotIn(thesection.COSTUME_SCAN_GATE_MESSAGE, client.get('/costumes').get_data(as_text=True))

    def test_scan_for_other_event_does_not_count(self):
        self._ticket(event_id='spring-2027', scan=True)
        client = self._login()
        self.assertIn(thesection.COSTUME_SCAN_GATE_MESSAGE, client.get('/costumes').get_data(as_text=True))

    def test_scanner_count_reset_keeps_access(self):
        self._ticket(scan=True)
        thesection.reset_admission_counts('halloween-2026')
        client = self._login()
        self.assertNotIn(thesection.COSTUME_SCAN_GATE_MESSAGE, client.get('/costumes').get_data(as_text=True))

    def test_stored_grant_and_revoke(self):
        self.assertTrue(thesection.set_costume_access_email('Guest@Example.com', True))
        client = self._login()
        self.assertNotIn(thesection.COSTUME_SCAN_GATE_MESSAGE, client.get('/costumes').get_data(as_text=True))
        self.assertTrue(thesection.set_costume_access_email('guest@example.com', False))
        self.assertIn(thesection.COSTUME_SCAN_GATE_MESSAGE, client.get('/costumes').get_data(as_text=True))

    def test_admin_always_has_access_even_when_hidden(self):
        thesection.set_costume_contest_open(False)
        admin = self.app.test_client()
        token = admin.get('/admin/login').headers.get('X-CSRF-Token')
        admin.post('/admin/login', data={'password': thesection.admin_key, 'csrf_token': token})
        page = admin.get('/costumes')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(thesection.COSTUME_SCAN_GATE_MESSAGE, page.get_data(as_text=True))

    def test_hidden_contest_still_hidden_for_scanned_guest(self):
        self._ticket(scan=True)
        thesection.set_costume_contest_open(False)
        client = self._login()
        resp = client.get('/costumes', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_photo_not_served_to_unscanned(self):
        os.makedirs(thesection.costume_photos_dir, exist_ok=True)
        name = 'entry1_abc123.jpg'
        with open(os.path.join(thesection.costume_photos_dir, name), 'wb') as fh:
            fh.write(self._tiny_jpeg_bytes())
        client = self._login()
        self.assertEqual(client.get(f'/costume-photos/{name}').status_code, 404)
        self._ticket(scan=True)
        self.assertEqual(client.get(f'/costume-photos/{name}').status_code, 200)


# Don't re-run the inherited voting tests from this module.
for _name in list(vars(_voting.CostumeVotingTests)):
    if _name.startswith('test_') and _name not in vars(CostumeScanGateTests):
        setattr(CostumeScanGateTests, _name, None)

if __name__ == '__main__':
    unittest.main()
