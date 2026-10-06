"""Door Scanner: count resets can be restored from the reset log."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


class ResetRestoreTests(unittest.TestCase):
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
        ]
        for patcher in self.patches:
            patcher.start()
        thesection.save_tickets([])
        thesection.save_members([])
        thesection.save_scanner_settings({})
        thesection.save_events([
            thesection.normalize_event({
                'id': 'halloween-2026', 'name': 'Halloween',
                'date': '2026-10-24', 'sales_open': True,
            }),
            thesection.normalize_event({
                'id': 'christmas-2026', 'name': 'Christmas',
                'date': '2026-12-25', 'sales_open': True,
            }),
        ])
        thesection.set_door_event_id('halloween-2026')
        self._n = 0

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def _scan(self, event_id='halloween-2026', ticket_type='general', quantity=1, when=None):
        """Add an already-scanned ticket (scanned now unless `when` given)."""
        self._n += 1
        scanned = (when or datetime.now(timezone.utc)).isoformat()
        tickets = thesection.load_tickets()
        tickets.append({
            'session_id': f'cs_{self._n}',
            'ticket_id': f'T{self._n:05d}',
            'email': 'guest@example.com',
            'quantity': quantity,
            'ticket_type': ticket_type,
            'event_id': event_id,
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': scanned,
            'admission_as': 'vip' if ticket_type == 'vip' else 'ga',
        })
        thesection.save_tickets(tickets)

    def _seed_tonight(self):
        """GA 5 tickets + 2 free girls (GA 7), VIP 3 -> total 10."""
        self._scan(quantity=3)
        self._scan(quantity=2)
        self._scan(ticket_type='vip', quantity=3)
        thesection.add_free_girls(2)
        counts = thesection.compute_admission_counts()
        self.assertEqual(counts, {'ga': 7, 'vip': 3, 'total': 10, 'free_girls': 2})

    def _latest_entry(self):
        return thesection.get_reset_history(annotate=True)[-1]

    def _staff_client(self):
        app = thesection.app
        app.config['TESTING'] = True
        client = app.test_client()
        token = client.get('/verify').headers.get('X-CSRF-Token')
        return client, token

    # -- core behaviour ----------------------------------------------------
    def test_reset_keeps_records_and_logs_restore_metadata(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 0, 'vip': 0, 'total': 0, 'free_girls': 0},
        )
        # Records are untouched: tickets keep scanned_at, comps stay on disk.
        self.assertTrue(all(t.get('scanned_at') for t in thesection.load_tickets()))
        settings = thesection.load_scanner_settings()
        self.assertEqual(len(settings['free_girls_entries']), 2)
        entry = self._latest_entry()
        self.assertEqual(entry['event_id'], 'halloween-2026')
        self.assertEqual(entry['event_name'], 'Halloween')
        self.assertIsNone(entry['previous_epoch'])
        self.assertEqual((entry['ga'], entry['vip'], entry['total'], entry['free_girls']), (7, 3, 10, 2))
        self.assertTrue(entry['can_restore'])

    def test_restore_returns_counts(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        ok, error, status = thesection.restore_reset_history_entry(self._latest_entry()['id'])
        self.assertTrue(ok, error)
        self.assertEqual(status, 200)
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 7, 'vip': 3, 'total': 10, 'free_girls': 2},
        )
        entry = self._latest_entry()
        self.assertTrue(entry['restored_at'])
        self.assertFalse(entry['can_restore'])

    def test_scans_and_free_girls_after_reset_are_preserved(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        self._scan(quantity=1)
        self._scan(ticket_type='vip', quantity=2)
        thesection.add_free_girls(1)
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 2, 'vip': 2, 'total': 4, 'free_girls': 1},
        )
        thesection.restore_reset_history_entry(self._latest_entry()['id'])
        # before-reset values + everything counted since
        self.assertEqual(
            thesection.compute_admission_counts(),
            {'ga': 9, 'vip': 5, 'total': 14, 'free_girls': 3},
        )
        # Free-girl Undo still works on the restored count.
        undone = thesection.add_free_girls(-1)
        self.assertEqual(undone['free_girls'], 2)

    def test_no_double_restore(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        entry_id = self._latest_entry()['id']
        self.assertTrue(thesection.restore_reset_history_entry(entry_id)[0])
        ok, error, status = thesection.restore_reset_history_entry(entry_id)
        self.assertFalse(ok)
        self.assertEqual(status, 409)
        self.assertIn('already restored', error)
        self.assertEqual(thesection.compute_admission_counts()['total'], 10)

    def test_undo_restore_rezeroes_at_original_reset_time(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        self._scan(quantity=1)
        entry_id = self._latest_entry()['id']
        thesection.restore_reset_history_entry(entry_id)
        self.assertTrue(self._latest_entry()['can_undo_restore'])
        ok, error, _ = thesection.undo_reset_restore(entry_id)
        self.assertTrue(ok, error)
        # Back to "reset in force": only the post-reset scan counts.
        self.assertEqual(thesection.compute_admission_counts()['total'], 1)
        entry = self._latest_entry()
        self.assertNotIn('restored_at', entry)
        self.assertTrue(entry['can_restore'])
        self.assertFalse(thesection.undo_reset_restore(entry_id)[0])

    def test_older_reset_waits_for_newer_reset_then_chains(self):
        t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
        self._scan(quantity=4, when=t0)                      # counted before reset A
        with mock.patch.object(thesection, 'datetime', wraps=datetime) as fake_dt:
            fake_dt.now.return_value = t0 + timedelta(minutes=5)
            thesection.reset_admission_counts()              # A: was 4
        self._scan(quantity=2, when=t0 + timedelta(minutes=10))
        with mock.patch.object(thesection, 'datetime', wraps=datetime) as fake_dt:
            fake_dt.now.return_value = t0 + timedelta(minutes=15)
            thesection.reset_admission_counts()              # B: was 2
        self._scan(quantity=1)                               # after B
        history = thesection.get_reset_history(annotate=True)
        entry_a, entry_b = history[-2], history[-1]
        self.assertEqual((entry_a['total'], entry_b['total']), (4, 2))
        self.assertFalse(entry_a['can_restore'])
        self.assertEqual(entry_a['restore_block'], 'superseded')
        self.assertTrue(entry_b['can_restore'])

        ok, error, status = thesection.restore_reset_history_entry(entry_a['id'])
        self.assertFalse(ok)
        self.assertEqual(status, 409)
        self.assertEqual(thesection.compute_admission_counts()['total'], 1)

        self.assertTrue(thesection.restore_reset_history_entry(entry_b['id'])[0])
        self.assertEqual(thesection.compute_admission_counts()['total'], 3)   # 2 + 1
        self.assertTrue(thesection.get_reset_history(annotate=True)[-2]['can_restore'])
        self.assertTrue(thesection.restore_reset_history_entry(entry_a['id'])[0])
        self.assertEqual(thesection.compute_admission_counts()['total'], 7)   # 4 + 2 + 1

    def test_reset_and_restore_are_per_door_event(self):
        self._scan(quantity=3)                                   # Halloween
        self._scan(event_id='christmas-2026', quantity=5)        # Christmas
        thesection.reset_admission_counts()                      # Halloween door
        self.assertEqual(thesection.compute_admission_counts('halloween-2026')['total'], 0)
        self.assertEqual(thesection.compute_admission_counts('christmas-2026')['total'], 5)
        entry_id = self._latest_entry()['id']
        thesection.set_door_event_id('christmas-2026')
        # Restoring from another door night only touches that reset's event.
        self.assertTrue(thesection.restore_reset_history_entry(entry_id)[0])
        self.assertEqual(thesection.compute_admission_counts('halloween-2026')['total'], 3)
        self.assertEqual(thesection.compute_admission_counts('christmas-2026')['total'], 5)

    def test_legacy_global_epoch_is_respected_and_restored_to(self):
        legacy_epoch = datetime.now(timezone.utc) - timedelta(minutes=20)
        self._scan(quantity=9, when=legacy_epoch - timedelta(minutes=1))   # before old global reset
        self._scan(quantity=2, when=legacy_epoch + timedelta(minutes=1))
        thesection.save_scanner_settings({
            **thesection.load_scanner_settings(),
            'counting_epoch': legacy_epoch.isoformat(),
            'reset_history': [{'id': 'old1', 'reset_at': legacy_epoch.isoformat(),
                               'ga': 9, 'vip': 0, 'total': 9, 'free_girls': 0}],
        })
        self.assertEqual(thesection.compute_admission_counts()['total'], 2)
        legacy = thesection.get_reset_history(annotate=True)[0]
        self.assertFalse(legacy['can_restore'])
        self.assertEqual(legacy['restore_block'], 'legacy')
        self.assertEqual(thesection.restore_reset_history_entry('old1')[2], 409)

        thesection.reset_admission_counts()
        self.assertEqual(thesection.compute_admission_counts()['total'], 0)
        thesection.restore_reset_history_entry(self._latest_entry()['id'])
        # Back to the legacy epoch, not "count everything ever".
        self.assertEqual(thesection.compute_admission_counts()['total'], 2)

    def test_delete_still_works_and_deleting_in_force_entry_blocks_older(self):
        self._scan(quantity=4)
        thesection.reset_admission_counts()
        self._scan(quantity=1)
        thesection.reset_admission_counts()
        history = thesection.get_reset_history(annotate=True)
        older, newer = history[-2], history[-1]
        self.assertTrue(thesection.delete_reset_history_entry(newer['id']))
        remaining = thesection.get_reset_history(annotate=True)
        self.assertEqual([row['id'] for row in remaining], [older['id']])
        # The older reset is not the one in force, so it can never jump past
        # the deleted newer reset.
        self.assertFalse(remaining[0]['can_restore'])
        self.assertFalse(thesection.restore_reset_history_entry(older['id'])[0])
        self.assertEqual(thesection.compute_admission_counts()['total'], 0)

    # -- HTTP: auth, CSRF, page --------------------------------------------
    def test_restore_api_requires_staff(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        entry_id = self._latest_entry()['id']
        app = thesection.app
        app.config['TESTING'] = True
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=False):
            client = app.test_client()
            token = client.get('/verify/login').headers.get('X-CSRF-Token')
            for path in ('/api/admission-totals/reset-history/restore',
                         '/api/admission-totals/reset-history/undo-restore'):
                resp = client.post(path, json={'id': entry_id}, headers={'X-CSRF-Token': token})
                self.assertEqual(resp.status_code, 401)
        self.assertEqual(thesection.compute_admission_counts()['total'], 0)
        self.assertNotIn('restored_at', self._latest_entry())

    def test_restore_api_requires_csrf(self):
        self._seed_tonight()
        thesection.reset_admission_counts()
        entry_id = self._latest_entry()['id']
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=True):
            client, token = self._staff_client()
            missing = client.post('/api/admission-totals/reset-history/restore', json={'id': entry_id})
            self.assertEqual(missing.status_code, 400)
            wrong = client.post('/api/admission-totals/reset-history/restore',
                                json={'id': entry_id}, headers={'X-CSRF-Token': 'nope'})
            self.assertEqual(wrong.status_code, 400)
            self.assertEqual(thesection.compute_admission_counts()['total'], 0)
            ok = client.post('/api/admission-totals/reset-history/restore',
                             json={'id': entry_id}, headers={'X-CSRF-Token': token})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.get_json()['total'], 10)

    def test_restore_api_flow_and_page(self):
        self._seed_tonight()
        with mock.patch.object(thesection, 'verify_auth_configured', return_value=True), \
             mock.patch.object(thesection, 'verify_authenticated', return_value=True):
            client, token = self._staff_client()
            headers = {'X-CSRF-Token': token}
            reset = client.post('/api/admission-totals/reset', headers=headers).get_json()
            self.assertEqual(reset['total'], 0)
            entry = reset['reset_history'][-1]
            self.assertTrue(entry['can_restore'])
            self._scan(quantity=1)
            resp = client.post('/api/admission-totals/reset-history/restore',
                               json={'id': entry['id']}, headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual((data['ga'], data['vip'], data['total'], data['free_girls']), (8, 3, 11, 2))
            self.assertTrue(data['reset_history'][-1]['restored_at'])
            self.assertTrue(data['reset_history'][-1]['can_undo_restore'])
            again = client.post('/api/admission-totals/reset-history/restore',
                                json={'id': entry['id']}, headers=headers)
            self.assertEqual(again.status_code, 409)
            self.assertEqual(again.get_json()['total'], 11)
            missing = client.post('/api/admission-totals/reset-history/restore',
                                  json={'id': 'nope'}, headers=headers)
            self.assertEqual(missing.status_code, 404)
            undo = client.post('/api/admission-totals/reset-history/undo-restore',
                               json={'id': entry['id']}, headers=headers)
            self.assertEqual(undo.status_code, 200)
            self.assertEqual(undo.get_json()['total'], 1)
            deleted = client.delete('/api/admission-totals/reset-history',
                                    json={'id': entry['id']}, headers=headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.get_json()['reset_history'], [])

            page = client.get('/verify')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'restoreResetHistoryEntry', page.data)
            self.assertIn(b'Undo restore', page.data)
            self.assertIn(b'Restored ${', page.data)
            self.assertIn(b'id="reset-help"', page.data)
            self.assertIn(b'Tap <span class="text-emerald-400 font-semibold">Restore</span>', page.data)
            self.assertIn(b'removes the Restore option', page.data)


if __name__ == '__main__':
    unittest.main()
