"""Costume voting: members submit entries and rank favorites (1st–3rd)."""
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from PIL import Image

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
            mock.patch.object(thesection, 'costume_photos_dir', os.path.join(root, 'costume_photos')),
            mock.patch.object(thesection, 'get_display_timezone', return_value=timezone.utc),
        ]
        for patcher in self.patches:
            patcher.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_costumes({'entries': [], 'ballots': {}})
        thesection._rate_limit_buckets.clear()
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
        self.assertNotIn('votes', entries[0])
        self.assertEqual(thesection.load_costumes().get('ballots'), {})

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

    def _submit(self, client, display_name, costume):
        token = self._csrf(client)
        resp = client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': display_name,
                'costume': costume,
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        return resp

    def _rank(self, client, rank_1='', rank_2='', rank_3='', follow=False):
        token = self._csrf(client)
        return client.post(
            '/costumes',
            data={
                'action': 'rank',
                'rank_1': rank_1,
                'rank_2': rank_2,
                'rank_3': rank_3,
                'csrf_token': token,
            },
            follow_redirects=follow,
        )

    def test_rank_three_distinct_and_cannot_rank_own(self):
        for email, name, costume in [
            ('a@example.com', 'A', 'Cat'),
            ('b@example.com', 'B', 'Bat'),
            ('c@example.com', 'C', 'Crow'),
            ('d@example.com', 'D', 'Dog'),
        ]:
            self._submit(self._login(email), name, costume)
        entries = {e['display_name']: e for e in thesection.load_costumes()['entries']}
        a_id, b_id, c_id = entries['A']['id'], entries['B']['id'], entries['C']['id']

        voter = self._login('voter@example.com')
        resp = self._rank(voter, rank_1=a_id, rank_2=b_id, rank_3=c_id)
        self.assertEqual(resp.status_code, 302)
        ballots = thesection.load_costumes()['ballots']
        self.assertEqual(ballots['voter@example.com'], [a_id, b_id, c_id])

        # Cannot include own entry
        self._submit(voter, 'Voter', 'Vampire')
        voter_entry = thesection.find_costume_entry_for_email('voter@example.com')
        resp = self._rank(voter, rank_1=voter_entry['id'], rank_2=a_id, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('cannot vote for your own', resp.get_data(as_text=True).lower())
        self.assertEqual(
            thesection.load_costumes()['ballots']['voter@example.com'],
            [a_id, b_id, c_id],
        )

        owner_a = self._login('a@example.com')
        resp = self._rank(owner_a, rank_1=a_id, follow=True)
        self.assertIn('cannot vote for your own', resp.get_data(as_text=True).lower())

        html = voter.get('/costumes').get_data(as_text=True)
        self.assertNotIn('a@example.com', html)
        self.assertNotIn('voter@example.com', html)
        self.assertIn('Cat', html)
        self.assertIn('Costume contest', html)
        self.assertNotIn('>Contest<', html)
        self.assertIn('Ranked by 3–2–1 points', html)

    def test_reject_duplicate_ranks_and_update_ballot(self):
        for email, name, costume in [
            ('a@example.com', 'A', 'Cat'),
            ('b@example.com', 'B', 'Bat'),
            ('c@example.com', 'C', 'Crow'),
        ]:
            self._submit(self._login(email), name, costume)
        entries = {e['display_name']: e for e in thesection.load_costumes()['entries']}
        a_id, b_id, c_id = entries['A']['id'], entries['B']['id'], entries['C']['id']

        voter = self._login('voter@example.com')
        resp = self._rank(voter, rank_1=a_id, rank_2=a_id, follow=True)
        self.assertIn('only appear once', resp.get_data(as_text=True).lower())
        self.assertNotIn('voter@example.com', thesection.load_costumes().get('ballots') or {})

        resp = self._rank(voter, rank_1=a_id, rank_2=b_id)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            thesection.load_costumes()['ballots']['voter@example.com'],
            [a_id, b_id],
        )

        resp = self._rank(voter, rank_1=c_id, rank_2=b_id, rank_3=a_id)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            thesection.load_costumes()['ballots']['voter@example.com'],
            [c_id, b_id, a_id],
        )

        resp = self._rank(voter)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('voter@example.com', thesection.load_costumes().get('ballots') or {})

    def test_borda_scoring_order(self):
        for email, name, costume in [
            ('a@example.com', 'A', 'Cat'),
            ('b@example.com', 'B', 'Bat'),
            ('c@example.com', 'C', 'Crow'),
        ]:
            self._submit(self._login(email), name, costume)
        entries = {e['display_name']: e for e in thesection.load_costumes()['entries']}
        a_id, b_id, c_id = entries['A']['id'], entries['B']['id'], entries['C']['id']

        self._rank(self._login('v1@example.com'), rank_1=a_id, rank_2=b_id, rank_3=c_id)
        self._rank(self._login('v2@example.com'), rank_1=b_id, rank_2=c_id)

        public = thesection.list_costume_entries_public('v1@example.com')
        by_name = {e['display_name']: e for e in public}
        self.assertEqual(by_name['B']['score'], 5)
        self.assertEqual(by_name['A']['score'], 3)
        self.assertEqual(by_name['C']['score'], 3)
        self.assertEqual(public[0]['display_name'], 'B')
        self.assertEqual(by_name['A']['my_rank'], 1)
        self.assertEqual(by_name['B']['my_rank'], 2)
        self.assertEqual(by_name['C']['my_rank'], 3)

        thesection.save_costumes({
            'entries': [
                {
                    'id': 'legacy',
                    'owner_email': 'x@example.com',
                    'display_name': 'X',
                    'costume': 'X',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'votes': ['old@example.com'],
                }
            ],
            'ballots': {},
        })
        store = thesection.load_costumes()
        self.assertNotIn('votes', store['entries'][0])
        self.assertEqual(store.get('ballots'), {})

    def test_anonymous_rank_redirects_to_portal(self):
        thesection.save_costumes({
            'entries': [{
                'id': 'entry1',
                'owner_email': 'owner@example.com',
                'display_name': 'Owner',
                'costume': 'Witch',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }],
            'ballots': {},
        })
        client = self.app.test_client()
        token = client.get('/costumes').headers.get('X-CSRF-Token')
        resp = client.post(
            '/costumes',
            data={
                'action': 'rank',
                'rank_1': 'entry1',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers.get('Location', '')
        self.assertTrue('/legacy' in location or '/members' in location)
        self.assertEqual(thesection.load_costumes().get('ballots'), {})

    def test_copy_has_no_stranded_contest_heading(self):
        client = self.app.test_client()
        html = client.get('/costumes').get_data(as_text=True)
        self.assertIn('Costume contest', html)
        self.assertIn('Costumes', html)
        self.assertNotIn('>Contest<', html)
        
    def _tiny_jpeg_bytes(self, color=(200, 40, 80), size=(64, 48)):
        buf = io.BytesIO()
        Image.new('RGB', size, color=color).save(buf, format='JPEG', quality=85)
        return buf.getvalue()

    def _photo_file(self, filename='costume.jpg', color=(200, 40, 80), size=(64, 48)):
        # Werkzeug EnvironBuilder expects (stream, filename[, content_type])
        return (io.BytesIO(self._tiny_jpeg_bytes(color=color, size=size)), filename, 'image/jpeg')

    def test_photo_upload_happy_path_and_serve(self):
        client = self._login()
        token = self._csrf(client)
        resp = client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire pirate',
                'csrf_token': token,
                'photo': self._photo_file(),
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        entries = thesection.load_costumes()['entries']
        self.assertEqual(len(entries), 1)
        photo = entries[0].get('photo')
        self.assertTrue(photo)
        self.assertTrue(photo.endswith('.jpg'))
        self.assertTrue(thesection.costume_photo_is_safe_filename(photo))
        disk_path = os.path.join(thesection.costume_photos_dir, photo)
        self.assertTrue(os.path.isfile(disk_path))
        self.assertLessEqual(os.path.getsize(disk_path), 500 * 1024)

        # Public board shows photo URL, never emails
        page = client.get('/costumes')
        html = page.get_data(as_text=True)
        self.assertIn(f'/costume-photos/{photo}', html)
        self.assertNotIn('guest@example.com', html)

        served = client.get(f'/costume-photos/{photo}')
        self.assertEqual(served.status_code, 200)
        self.assertIn('image/', served.headers.get('Content-Type', ''))
        self.assertTrue(served.data.startswith(b'\xff\xd8\xff'))

        # Path traversal rejected
        self.assertEqual(client.get('/costume-photos/../costumes.json').status_code, 404)
        self.assertEqual(client.get('/costume-photos/..%2Fcostumes.json').status_code, 404)

    def test_anonymous_cannot_upload_photo(self):
        client = self.app.test_client()
        token = client.get('/costumes').headers.get('X-CSRF-Token')
        resp = client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
                'photo': self._photo_file(),
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 400))
        self.assertEqual(thesection.load_costumes().get('entries'), [])
        photos_dir = thesection.costume_photos_dir
        if os.path.isdir(photos_dir):
            self.assertEqual([n for n in os.listdir(photos_dir) if not n.startswith('.')], [])

    def test_replace_and_remove_photo(self):
        client = self._login()
        token = self._csrf(client)
        client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
                'photo': self._photo_file(color=(10, 20, 30)),
            },
        )
        first = thesection.load_costumes()['entries'][0]['photo']
        first_path = os.path.join(thesection.costume_photos_dir, first)
        self.assertTrue(os.path.isfile(first_path))

        token = self._csrf(client)
        client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
                'photo': self._photo_file(filename='new.jpg', color=(240, 10, 10)),
            },
        )
        second = thesection.load_costumes()['entries'][0]['photo']
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.isfile(first_path))
        self.assertTrue(os.path.isfile(os.path.join(thesection.costume_photos_dir, second)))

        token = self._csrf(client)
        client.post(
            '/costumes',
            data={
                'action': 'submit',
                'display_name': 'Alex',
                'costume': 'Vampire',
                'csrf_token': token,
                'remove_photo': '1',
            },
        )
        entry = thesection.load_costumes()['entries'][0]
        self.assertFalse(entry.get('photo'))
        self.assertFalse(os.path.isfile(os.path.join(thesection.costume_photos_dir, second)))


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

    def _seed_entry(self, entry_id='entry1', owner='owner@example.com', photo=None):
        entry = {
            'id': entry_id,
            'owner_email': owner,
            'display_name': 'Owner',
            'costume': 'Witch',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        if photo:
            entry['photo'] = photo
        thesection.save_costumes({'entries': [entry], 'ballots': {}})
        return entry

    def test_admin_can_remove_costume_entry(self):
        self._seed_entry()
        admin = self._admin_client()
        page = admin.get('/costumes')
        html = page.get_data(as_text=True)
        self.assertIn('Remove', html)
        self.assertIn('admin_remove', html)
        token = page.headers.get('X-CSRF-Token')
        resp = admin.post(
            '/costumes',
            data={
                'action': 'admin_remove',
                'entry_id': 'entry1',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/costumes', resp.headers.get('Location', ''))
        self.assertEqual(thesection.load_costumes().get('entries'), [])

    def test_non_admin_member_cannot_remove_costume(self):
        self._seed_entry()
        member = self._login('member@example.com')
        page = member.get('/costumes')
        html = page.get_data(as_text=True)
        self.assertNotIn('admin_remove', html)
        self.assertNotIn('>Remove<', html.replace('Remove current photo', ''))
        token = self._csrf(member)
        resp = member.post(
            '/costumes',
            data={
                'action': 'admin_remove',
                'entry_id': 'entry1',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(thesection.load_costumes()['entries']), 1)

    def test_anonymous_cannot_remove_costume(self):
        self._seed_entry()
        client = self.app.test_client()
        page = client.get('/costumes')
        html = page.get_data(as_text=True)
        self.assertNotIn('admin_remove', html)
        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/costumes',
            data={
                'action': 'admin_remove',
                'entry_id': 'entry1',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(thesection.load_costumes()['entries']), 1)

    def test_admin_remove_deletes_photo_file(self):
        os.makedirs(thesection.costume_photos_dir, exist_ok=True)
        photo_name = 'entry1_abc123.jpg'
        photo_path = os.path.join(thesection.costume_photos_dir, photo_name)
        with open(photo_path, 'wb') as handle:
            handle.write(self._tiny_jpeg_bytes())
        self.assertTrue(os.path.isfile(photo_path))
        self._seed_entry(photo=photo_name)

        admin = self._admin_client()
        token = self._csrf(admin)
        resp = admin.post(
            '/costumes',
            data={
                'action': 'admin_remove',
                'entry_id': 'entry1',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(thesection.load_costumes().get('entries'), [])
        self.assertFalse(os.path.isfile(photo_path))


if __name__ == '__main__':
    unittest.main()
