"""Costume contest: members submit entries and vote on favorites."""
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


class CostumeVotingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.members = []
        self.patches = [
            mock.patch.object(thesection, 'tickets_file', os.path.join(root, 'tickets.json')),
            mock.patch.object(thesection, 'members_file', os.path.join(root, 'members.json')),
            mock.patch.object(thesection, 'invites_file', os.path.join(root, 'invites.json')),
            mock.patch.object(thesection, 'exclusive_holds_file', os.path.join(root, 'holds.json')),
            mock.patch.object(thesection, 'events_file', os.path.join(root, 'events.json')),
            mock.patch.object(thesection, 'full_mailing_list_file', os.path.join(root, 'full.json')),
            mock.patch.object(thesection, 'scanner_settings_file', os.path.join(root, 'scanner.json')),
            mock.patch.object(thesection, 'costumes_file', os.path.join(root, 'costumes.json')),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_costumes({'entries': []})
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    def _member(self, email='guest@example.com', password='password123'):
        return {
            'email': email,
            'password_hash': thesection.hash_password(password),
            'saved_tickets': [],
            'discount_code': 'TEST-ABCD',
            'joined_at': datetime.now(timezone.utc).isoformat(),
        }

    def _login(self, email='guest@example.com', password='password123'):
        members = thesection.load_members()
        if not any(m.get('email') == email for m in members):
            members.append(self._member(email=email, password=password))
            thesection.save_members(members)
        client = self.app.test_client()
        token = client.get('/legacy').headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'login',
                'email': email,
                'password': password,
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        return client

    def _csrf(self, client):
        return client.get('/costumes').headers.get('X-CSRF-Token')

    def test_anonymous_can_view_but_not_submit(self):
        client = self.app.test_client()
        page = client.get('/costumes')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Members only', html)
        self.assertIn('Sign in at Member Portal', html)
        self.assertIn('next=/costumes', html)
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        # Guests are sent to member portal (or CSRF may block first without session)
        self.assertIn(resp.status_code, (302, 400))
        store = thesection.load_costumes()
        self.assertEqual(store.get('entries'), [])

    def test_logged_in_member_can_submit_costume(self):
        client = self._login()
        token = self._csrf(client)
        resp = client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire pirate',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/costumes', resp.headers.get('Location', ''))
        entries = thesection.load_costumes()['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['display_name'], 'Alex')
        self.assertEqual(entries[0]['costume'], 'Vampire pirate')
        self.assertEqual(entries[0]['owner_email'], 'guest@example.com')
        self.assertEqual(entries[0]['votes'], [])

    def test_resubmit_updates_same_entry(self):
        client = self._login()
        token = self._csrf(client)
        client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
            },
        )
        first_id = thesection.load_costumes()['entries'][0]['id']
        token = self._csrf(client)
        client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alexandra',
                'costume': 'Ghost pirate',
                'csrf_token': token,
            },
        )
        entries = thesection.load_costumes()['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['id'], first_id)
        self.assertEqual(entries[0]['display_name'], 'Alexandra')
        self.assertEqual(entries[0]['costume'], 'Ghost pirate')

    def test_vote_and_cannot_vote_own(self):
        owner = self._login('owner@example.com')
        token = self._csrf(owner)
        owner.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Owner',
                'costume': 'Dracula',
                'csrf_token': token,
            },
        )
        entry_id = thesection.load_costumes()['entries'][0]['id']

        # Owner cannot vote own
        token = self._csrf(owner)
        resp = owner.post(
            '/costumes',
            data={
                'action': 'vote',
                'entry_id': entry_id,
                'csrf_token': token,
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('cannot vote for your own', resp.get_data(as_text=True).lower())
        self.assertEqual(thesection.load_costumes()['entries'][0]['votes'], [])

        # Another member can vote
        voter = self._login('voter@example.com')
        token = self._csrf(voter)
        resp = voter.post(
            '/costumes',
            data={
                'action': 'vote',
                'entry_id': entry_id,
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        votes = thesection.load_costumes()['entries'][0]['votes']
        self.assertEqual(votes, ['voter@example.com'])

        # Public listing never exposes emails
        html = voter.get('/costumes').get_data(as_text=True)
        self.assertNotIn('owner@example.com', html)
        self.assertNotIn('voter@example.com', html)
        self.assertIn('Dracula', html)
        self.assertIn('>1<', html)  # vote count

    def test_vote_toggle_and_sort_by_votes(self):
        a = self._login('a@example.com')
        token = self._csrf(a)
        a.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'A',
                'costume': 'Cat',
                'csrf_token': token,
            },
        )
        b = self._login('b@example.com')
        token = self._csrf(b)
        b.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'B',
                'costume': 'Bat',
                'csrf_token': token,
            },
        )
        entries = {e['display_name']: e for e in thesection.load_costumes()['entries']}
        a_id = entries['A']['id']
        b_id = entries['B']['id']

        c = self._login('c@example.com')
        token = self._csrf(c)
        c.post('/costumes', data={'action': 'vote', 'entry_id': b_id, 'csrf_token': token})
        token = self._csrf(c)
        c.post('/costumes', data={'action': 'vote', 'entry_id': a_id, 'csrf_token': token})
        # Second vote for B from another member
        d = self._login('d@example.com')
        token = self._csrf(d)
        d.post('/costumes', data={'action': 'vote', 'entry_id': b_id, 'csrf_token': token})

        public = thesection.list_costume_entries_public('c@example.com')
        self.assertEqual([e['display_name'] for e in public], ['B', 'A'])
        self.assertEqual(public[0]['vote_count'], 2)
        self.assertEqual(public[1]['vote_count'], 1)
        self.assertTrue(public[1]['voted_by_me'])

        # Toggle off vote for A
        token = self._csrf(c)
        c.post('/costumes', data={'action': 'unvote', 'entry_id': a_id, 'csrf_token': token})
        votes_a = next(e['votes'] for e in thesection.load_costumes()['entries'] if e['id'] == a_id)
        self.assertEqual(votes_a, [])

    def test_anonymous_vote_redirects_to_portal(self):
        # Seed an entry directly
        thesection.save_costumes({
            'entries': [{
                'id': 'entry1',
                'owner_email': 'owner@example.com',
                'display_name': 'Owner',
                'costume': 'Witch',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'votes': [],
            }]
        })
        client = self.app.test_client()
        token = client.get('/costumes').headers.get('X-CSRF-Token')
        resp = client.post(
            '/costumes',
            data={'action': 'vote', 'entry_id': 'entry1', 'csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers.get('Location', '')
        self.assertTrue('/legacy' in location or '/members' in location)
        self.assertEqual(thesection.load_costumes()['entries'][0]['votes'], [])


if __name__ == '__main__':
    unittest.main()
