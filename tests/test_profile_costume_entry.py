"""Member profile/portal: show, edit, and delete own costume contest entry."""
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


class ProfileCostumeEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
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
        return client.get('/legacy').headers.get('X-CSRF-Token')

    def _seed_entry(self, entry_id='entry1', owner='guest@example.com', photo=None, votes=None):
        entry = {
            'id': entry_id,
            'owner_email': owner,
            'display_name': 'Alex',
            'costume': 'Vampire pirate',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'votes': list(votes or []),
        }
        if photo:
            entry['photo'] = photo
        store = thesection.load_costumes()
        entries = store.setdefault('entries', [])
        entries.append(entry)
        thesection.save_costumes(store)
        return entry

    def _tiny_jpeg_bytes(self, color=(200, 40, 80), size=(64, 48)):
        buf = io.BytesIO()
        Image.new('RGB', size, color=color).save(buf, format='JPEG', quality=85)
        return buf.getvalue()

    def _photo_file(self, filename='costume.jpg', color=(200, 40, 80)):
        return (io.BytesIO(self._tiny_jpeg_bytes(color=color)), filename, 'image/jpeg')

    def test_profile_shows_costume_entry(self):
        self._seed_entry(votes=['voter@example.com'])
        client = self._login()
        page = client.get('/legacy')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Costume contest', html)
        self.assertIn('Alex', html)
        self.assertIn('Vampire pirate', html)
        self.assertIn('1 vote', html)
        self.assertIn('costume_submit', html)
        self.assertIn('costume_delete', html)
        self.assertIn('Edit entry', html)
        self.assertIn('Delete entry', html)

    def test_profile_shows_cta_when_no_entry(self):
        client = self._login()
        page = client.get('/legacy')
        html = page.get_data(as_text=True)
        self.assertIn('Costume contest', html)
        self.assertIn('You have not entered the costume contest yet.', html)
        self.assertIn('Enter a costume', html)
        self.assertIn('costume_submit', html)
        self.assertNotIn('costume_delete', html)

    def test_owner_can_edit_costume_from_profile(self):
        self._seed_entry()
        client = self._login()
        token = self._csrf(client)
        resp = client.post(
            '/legacy',
            data={
                'action': 'costume_submit',
                'display_name': 'Alexandra',
                'costume': 'Ghost pirate',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers.get('Location', '')
        self.assertTrue('/legacy' in location or '/members' in location)
        self.assertIn('costume_saved=1', location)
        entries = thesection.load_costumes()['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['display_name'], 'Alexandra')
        self.assertEqual(entries[0]['costume'], 'Ghost pirate')
        self.assertEqual(entries[0]['owner_email'], 'guest@example.com')

        # Same data visible on /costumes
        costumes_page = client.get('/costumes').get_data(as_text=True)
        self.assertIn('Alexandra', costumes_page)
        self.assertIn('Ghost pirate', costumes_page)

        # Success flash on portal
        portal = client.get(location if location.startswith('/') else '/legacy?costume_saved=1')
        self.assertIn('Costume saved', portal.get_data(as_text=True))

    def test_owner_can_edit_photo_from_profile(self):
        self._seed_entry()
        client = self._login()
        token = self._csrf(client)
        resp = client.post(
            '/legacy',
            data={
                'action': 'costume_submit',
                'display_name': 'Alex',
                'costume': 'Vampire pirate',
                'csrf_token': token,
                'photo': self._photo_file(),
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        entry = thesection.load_costumes()['entries'][0]
        photo = entry.get('photo')
        self.assertTrue(photo)
        self.assertTrue(os.path.isfile(os.path.join(thesection.costume_photos_dir, photo)))

        token = self._csrf(client)
        client.post(
            '/legacy',
            data={
                'action': 'costume_submit',
                'display_name': 'Alex',
                'costume': 'Vampire pirate',
                'csrf_token': token,
                'remove_photo': '1',
            },
        )
        entry = thesection.load_costumes()['entries'][0]
        self.assertFalse(entry.get('photo'))
        self.assertFalse(os.path.isfile(os.path.join(thesection.costume_photos_dir, photo)))

    def test_owner_can_delete_costume_from_profile(self):
        os.makedirs(thesection.costume_photos_dir, exist_ok=True)
        photo_name = 'entry1_abc123.jpg'
        photo_path = os.path.join(thesection.costume_photos_dir, photo_name)
        with open(photo_path, 'wb') as handle:
            handle.write(self._tiny_jpeg_bytes())
        self._seed_entry(photo=photo_name)

        client = self._login()
        token = self._csrf(client)
        resp = client.post(
            '/legacy',
            data={
                'action': 'costume_delete',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('costume_deleted=1', resp.headers.get('Location', ''))
        self.assertEqual(thesection.load_costumes().get('entries'), [])
        self.assertFalse(os.path.isfile(photo_path))

        # Costumes board also empty
        board = client.get('/costumes').get_data(as_text=True)
        self.assertIn('No costumes yet', board)

    def test_other_user_cannot_delete_someone_elses_via_profile(self):
        self._seed_entry(owner='owner@example.com')
        # Intruder logged in as different member — delete only removes their own (none)
        intruder = self._login('intruder@example.com')
        token = self._csrf(intruder)
        resp = intruder.post(
            '/legacy',
            data={
                'action': 'costume_delete',
                'entry_id': 'entry1',  # crafted; ignored — keyed by session email
                'csrf_token': token,
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True).lower()
        self.assertIn('do not have a costume entry', html)
        entries = thesection.load_costumes()['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['owner_email'], 'owner@example.com')

        # Intruder edit creates their own entry, does not overwrite owner's
        token = self._csrf(intruder)
        intruder.post(
            '/legacy',
            data={
                'action': 'costume_submit',
                'display_name': 'Thief',
                'costume': 'Stolen look',
                'csrf_token': token,
            },
        )
        by_owner = {
            e['owner_email']: e for e in thesection.load_costumes()['entries']
        }
        self.assertEqual(by_owner['owner@example.com']['costume'], 'Vampire pirate')
        self.assertEqual(by_owner['intruder@example.com']['costume'], 'Stolen look')

    def test_anonymous_blocked_from_profile_costume_actions(self):
        self._seed_entry(owner='owner@example.com')
        client = self.app.test_client()
        page = client.get('/legacy')
        html = page.get_data(as_text=True)
        self.assertNotIn('costume_submit', html)
        self.assertNotIn('costume_delete', html)
        self.assertNotIn('Costume contest', html)

        token = page.headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'costume_delete',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers.get('Location', '')
        self.assertTrue('/legacy' in location or '/members' in location)
        self.assertEqual(len(thesection.load_costumes()['entries']), 1)

        token = client.get('/legacy').headers.get('X-CSRF-Token')
        resp = client.post(
            '/legacy',
            data={
                'action': 'costume_submit',
                'display_name': 'Hacker',
                'costume': 'Nope',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(thesection.load_costumes()['entries']), 1)
        self.assertEqual(
            thesection.load_costumes()['entries'][0]['owner_email'],
            'owner@example.com',
        )


if __name__ == '__main__':
    unittest.main()
