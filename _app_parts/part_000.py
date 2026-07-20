from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import stripe
import qrcode
from io import BytesIO, StringIO
import base64
import uuid
import secrets
from flask_mail import Mail, Message
import os
import threading
import json
import ast
import csv
import hashlib
import zipfile
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')

PRODUCTION_BASE_URL = 'https://thesection.onrender.com'


def get_public_base_url():
    configured = (os.getenv('BASE_URL') or '').strip().rstrip('/')
    if configured and 'localhost' not in configured and '127.0.0.1' not in configured:
        if not configured.startswith('http://10.') and not configured.startswith('http://192.168.'):
            return configured
    return PRODUCTION_BASE_URL


def clean_env_value(raw):
    """Strip whitespace and optional surrounding quotes from env values."""
    value = (raw or '').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def secure_equal(left, right):
    """Constant-time string compare that tolerates different lengths."""
    a = left if isinstance(left, str) else ''
    b = right if isinstance(right, str) else ''
    try:
        return secrets.compare_digest(a, b)
    except (TypeError, ValueError):
        return False


base_url = get_public_base_url()
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = clean_env_value(os.getenv('ADMIN_KEY', 'section2024'))
verify_login_email = clean_env_value(os.getenv('VERIFY_LOGIN_EMAIL', '')).lower()
verify_login_password = clean_env_value(os.getenv('VERIFY_LOGIN_PASSWORD', ''))
wallet_team_id = os.getenv('WALLET_TEAM_ID', '')
wallet_pass_type_id = os.getenv('WALLET_PASS_TYPE_ID', 'pass.com.thesection.ticket')
wallet_cert_path = os.getenv('WALLET_CERT_PATH', '')
wallet_key_path = os.getenv('WALLET_KEY_PATH', '')
wallet_wwdr_path = os.getenv('WALLET_WWDR_PATH', '')
wallet_enabled = all([wallet_team_id, wallet_cert_path, wallet_key_path, wallet_wwdr_path])
members_file = os.getenv('MEMBERS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'legacy_members.json'))
scanner_settings_file = os.getenv(
    'SCANNER_SETTINGS_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'scanner_settings.json'),
)
invites_file = os.getenv(
    'INVITES_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'member_invites.json'),
)
INVITE_EXPIRY_DAYS = int(os.getenv('INVITE_EXPIRY_DAYS', '14'))
APP_TIMEZONE = os.getenv('APP_TIMEZONE', 'America/Los_Angeles')
def parse_discount_value(raw, default=0.15):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value > 1:
        return value / 100.0
    return value


bundle_min = int(os.getenv('BUNDLE_MIN') or os.getenv('LEGACY_BUNDLE_MIN', '4'))
bundle_discount = parse_discount_value(os.getenv('BUNDLE_DISCOUNT', '0.10'), 0.10)
vip_bundle_min = int(os.getenv('VIP_BUNDLE_MIN', '5'))
vip_bulk_discount = parse_discount_value(
    os.getenv('VIP_BUNDLE_DISCOUNT')
    or os.getenv('VIP_ADDITIONAL_DISCOUNT')
    or os.getenv('VIP_BULK_DISCOUNT', '0.10'),
    0.10,
)
member_discount = parse_discount_value(os.getenv('MEMBER_DISCOUNT', '0.10'))
# Mailing-list / returning-guest: 20% on a single ticket only; multi-ticket uses member_discount (10%).
returning_guest_discount = parse_discount_value(
    os.getenv('RETURNING_GUEST_DISCOUNT', '0.20'),
    0.20,
)
# Flask session secret (NOT a Stripe key). Use a random string, not sk_.../pk_...
_flask_secret = clean_env_value(os.getenv('SECRET_KEY', 'thesection-legacy-portal-change-me'))
if _flask_secret.startswith(('sk_', 'pk_')):
    print(
        'WARNING: SECRET_KEY looks like a Stripe key. '
        'Put sk_live_... in STRIPE_SECRET_KEY instead. '
        'SECRET_KEY is only for Flask sessions and should be a random string.'
    )
app.secret_key = _flask_secret
tickets_lock = threading.Lock()
members_lock = threading.Lock()
scanner_settings_lock = threading.Lock()
invites_lock = threading.Lock()

TICKET_TYPES = {
    'general': {
        'name': 'General Admission',
        'price_cents': int(os.getenv('GA_PRICE_CENTS', '1000')),
        'description': 'October 24th • 10PM - 2AM @ The Gem',
        'access': None,
    },
    'vip': {
        'name': 'VIP Admission',
        'price_cents': int(os.getenv('VIP_PRICE_CENTS', '2500')),
        'description': 'Includes upstairs seating + priority entry',
        'access': 'Upstairs seating',
    },
}


def resolve_stripe_secret_key():
    """Server-side Stripe calls need sk_... only. Never use pk_... or Flask SECRET_KEY."""
    for env_name in ('STRIPE_SECRET_KEY', 'STRIPE_API_KEY'):
        raw = clean_env_value(os.getenv(env_name, ''))
        if not raw:
            continue
        if raw.startswith('pk_'):
            print(
                f'WARNING: {env_name} is a publishable key (pk_...). '
                f'Ignoring it. Set STRIPE_SECRET_KEY to sk_live_... or sk_test_... '
                f'from https://dashboard.stripe.com/apikeys'
            )
            continue
        if raw.startswith('sk_'):
            return raw, env_name
        print(f'WARNING: {env_name} does not start with sk_ — ignoring')
    return '', None


# Stripe Checkout requires the *secret* key (sk_live_... / sk_test_...).
# Publishable keys (pk_...) go nowhere in this app; do not put them in STRIPE_API_KEY for server use.
stripe.api_key, _stripe_key_source = resolve_stripe_secret_key()
if stripe.api_key.startswith('sk_live_'):
    print(f'Stripe mode: LIVE (from {_stripe_key_source})')
elif stripe.api_key.startswith('sk_test_'):
    print(f'Stripe mode: TEST (sandbox) (from {_stripe_key_source})')
else:
    print(
        'WARNING: No Stripe secret key found. Set STRIPE_SECRET_KEY=sk_live_... on Render '
        '(not SECRET_KEY, and not the pk_live_ publishable key).'
    )

# Email Config (Gmail app password — set MAIL_* env vars on Render)
DEFAULT_MAIL_USERNAME = 'hallieworkshop@gmail.com'
mail_username = (os.getenv('MAIL_USERNAME') or DEFAULT_MAIL_USERNAME).strip()
mail_sender = (os.getenv('MAIL_DEFAULT_SENDER') or mail_username).strip() or mail_username
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = mail_username
app.config['MAIL_PASSWORD'] = (os.getenv('MAIL_PASSWORD') or '').strip()
app.config['MAIL_DEFAULT_SENDER'] = mail_sender
app.config['MAIL_TIMEOUT'] = int(os.getenv('MAIL_TIMEOUT', '10'))
mail = Mail(app)


de