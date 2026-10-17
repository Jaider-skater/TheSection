"""Paid tickets belong to the member who started checkout, not whoever opens /success."""
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-123456')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-12')
os.environ.pop('FLASK_ENV', None)
os.environ.pop('RENDER', None)

import app as thesection  # noqa: E402


class TicketRecipientEmailTests(unittest.TestCase):
    def setUp(self):
        self.app = thesection.app
        self.app.config['TESTING'] = True

    def _in_request(self, logged_in=None):
        ctx = self.app.test_request_context('/success')
        ctx.push()
        self.addCleanup(ctx.pop)
        if logged_in:
            thesection.session['legacy_member_email'] = logged_in

    def test_metadata_member_beats_browser_login(self):
        self._in_request(logged_in='someone-else@example.com')
        email = thesection.ticket_recipient_email(
            'typed@example.com', {'member_email': 'Buyer@Example.com'}
        )
        self.assertEqual(email, 'buyer@example.com')

    def test_stripe_email_beats_browser_login(self):
        self._in_request(logged_in='staff@example.com')
        email = thesection.ticket_recipient_email('Typed@Example.com', {'member_email': ''})
        self.assertEqual(email, 'typed@example.com')

    def test_browser_login_is_last_resort(self):
        self._in_request(logged_in='member@example.com')
        self.assertEqual(thesection.ticket_recipient_email(None, {}), 'member@example.com')

    def test_door_sale_never_falls_back_to_staff_login(self):
        self._in_request(logged_in='staff@example.com')
        self.assertIsNone(thesection.ticket_recipient_email(None, {'door_sale': 'true'}))

    def test_no_request_context(self):
        self.assertEqual(
            thesection.ticket_recipient_email(None, {'member_email': 'a@example.com'}),
            'a@example.com',
        )


class FulfillOwnerTests(unittest.TestCase):
    def test_success_in_other_browser_keeps_buyer_as_owner(self):
        recorded = {}

        def fake_record(session_id, ticket_id, email, quantity, **kwargs):
            recorded['email'] = email
            return {'ticket_id': ticket_id, 'email': email, 'quantity': quantity,
                    'event_id': kwargs.get('event_id')}

        checkout = {
            'id': 'cs_live_test',
            'payment_status': 'paid',
            'status': 'complete',
            'metadata': {'member_email': 'buyer@example.com', 'ticket_type': 'vip',
                         'event_id': 'halloween-2026'},
            'customer_details': {'email': 'buyer@example.com'},
            'line_items': {'data': [{'quantity': 1}]},
        }
        app = thesection.app
        with app.test_request_context('/success'):
            thesection.session['legacy_member_email'] = 'other@example.com'
            with mock.patch.object(thesection, 'get_ticket_by_session', return_value=None), \
                    mock.patch.object(thesection, 'record_ticket', side_effect=fake_record), \
                    mock.patch.object(thesection, 'release_exclusive_hold'), \
                    mock.patch.object(thesection, 'get_legacy_member', return_value=None):
                ticket = thesection.fulfill_paid_checkout(checkout)
        self.assertEqual(recorded['email'], 'buyer@example.com')
        self.assertEqual(ticket['email'], 'buyer@example.com')


class CheckoutCustomerCreationTests(unittest.TestCase):
    def test_checkout_always_creates_stripe_customer(self):
        breakdown = {
            'unit_price_cents': 2500, 'stacked_discount_applied': False,
            'member_discount_applied': False, 'bundle_discount_applied': False,
            'legacy_discount_applied': False,
        }
        with thesection.app.test_request_context('/'), \
                mock.patch.object(thesection.stripe.checkout.Session, 'create') as create:
            thesection._create_stripe_checkout_session(
                quantity=1, ticket_type='vip', ticket_meta=thesection.TICKET_TYPES['vip'],
                breakdown=breakdown, sales_event=None, sales_event_id='halloween-2026',
                legacy_member=True, member_email='buyer@example.com',
            )
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['customer_creation'], 'always')
        self.assertEqual(kwargs['customer_email'], 'buyer@example.com')
        self.assertEqual(kwargs['metadata']['member_email'], 'buyer@example.com')


if __name__ == '__main__':
    unittest.main()
