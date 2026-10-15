"""Costume photo-use consent: default off, set/revoke, admin-only view and export."""
import os
import unittest

import test_profile_costume_entry as _profile_tests

import app as thesection  # noqa: E402


class CostumePhotoConsentTests(_profile_tests.ProfileCostumeEntryTests):
    def _admin(self):
        client = self.app.test_client()
        token = client.get('/admin/login').headers.get('X-CSRF-Token')
        client.post('/admin/login', data={'password': thesection.admin_key, 'csrf_token': token})
        return client

    def _submit_costumes(self, client, consent):
        data = {'action': 'submit', 'display_name': 'Alex', 'costume': 'Vampire pirate',
                'csrf_token': self._csrf(client)}
        if consent:
            data['photo_consent'] = '1'
        return client.post('/costumes', data=data)

    def _submit_profile(self, client, consent):
        data = {'action': 'costume_submit', 'display_name': 'Alex', 'costume': 'Vampire pirate',
                'csrf_token': self._csrf(client)}
        if consent:
            data['photo_consent'] = '1'
        return client.post('/legacy', data=data)

    def _entry(self, email='guest@example.com'):
        return thesection.find_costume_entry_for_email(email)

    def test_default_false_and_checkbox_unchecked(self):
        client = self._login()
        html = client.get('/costumes').get_data(as_text=True)
        self.assertIn(thesection.COSTUME_PHOTO_CONSENT_TEXT, html)
        self.assertIn('change this anytime from your profile', html)
        self.assertNotIn('checked\n', html.split('name="photo_consent"')[1].split('>')[0])
        self._submit_costumes(client, False)
        e = self._entry()
        self.assertFalse(e['photo_consent'])
        self.assertNotIn('photo_consent_at', e)

    def test_set_on_create_records_time_and_version(self):
        client = self._login()
        self._submit_costumes(client, True)
        e = self._entry()
        self.assertTrue(e['photo_consent'])
        self.assertTrue(e['photo_consent_at'])
        self.assertEqual(e['photo_consent_version'], thesection.COSTUME_PHOTO_CONSENT_VERSION)
        self.assertEqual(e['photo_consent_text'], thesection.COSTUME_PHOTO_CONSENT_TEXT)

    def test_profile_create_edit_and_revoke(self):
        client = self._login()
        self._submit_profile(client, False)
        self.assertFalse(self._entry()['photo_consent'])
        self._submit_profile(client, True)
        e = self._entry()
        self.assertTrue(e['photo_consent'])
        first_at = e['photo_consent_at']
        self._submit_profile(client, True)
        self.assertEqual(self._entry()['photo_consent_at'], first_at)
        self._submit_profile(client, False)
        e = self._entry()
        self.assertFalse(e['photo_consent'])
        self.assertGreaterEqual(e['photo_consent_at'], first_at)
        html = client.get('/legacy').get_data(as_text=True)
        self.assertIn('Photo permission: not given', html)

    def test_existing_entries_default_false(self):
        self._seed_entry()
        e = self._entry()
        self.assertFalse(e['photo_consent'])
        self.assertEqual(e['costume'], 'Vampire pirate')

    def test_not_public_and_admin_only(self):
        self._seed_entry(entry_id='a', owner='other@example.com')
        client = self._login()
        self._submit_costumes(client, True)
        html = client.get('/costumes').get_data(as_text=True)
        self.assertNotIn('Photo OK', html)
        for entry in thesection.list_costume_entries_public():
            self.assertNotIn('photo_consent', entry)
        anon = self.app.test_client()
        for path in ('/admin/costume-consent', '/admin/costume-consent.csv'):
            self.assertEqual(client.get(path).status_code, 302)
            self.assertEqual(anon.get(path).status_code, 302)

    def test_admin_view_filter_and_csv(self):
        self._seed_entry(entry_id='a', owner='other@example.com')
        client = self._login()
        self._submit_costumes(client, True)
        admin = self._admin()
        page = admin.get('/admin/costume-consent').get_data(as_text=True)
        self.assertIn('Photo OK', page)
        self.assertIn('1</span> of 2', page)
        self.assertIn('other@example.com', page)
        filtered = admin.get('/admin/costume-consent?consented=1').get_data(as_text=True)
        self.assertNotIn('other@example.com', filtered)
        dash = admin.get('/admin').get_data(as_text=True)
        self.assertIn('1 Photo OK', dash)
        csv_resp = admin.get('/admin/costume-consent.csv')
        self.assertEqual(csv_resp.status_code, 200)
        body = csv_resp.get_data(as_text=True)
        self.assertIn('name,costume,member_email,photo_link,consent_time_utc', body)
        self.assertIn('guest@example.com', body)
        self.assertNotIn('other@example.com', body)


# Avoid re-running the base class tests via inheritance
for _n in [n for n in dir(_profile_tests.ProfileCostumeEntryTests) if n.startswith('test_')]:
    if not hasattr(CostumePhotoConsentTests, _n) or _n not in CostumePhotoConsentTests.__dict__:
        setattr(CostumePhotoConsentTests, _n, None)
