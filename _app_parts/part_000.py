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


def ensure_data_dir(path):
    directory = os.path.dirname(path)
    if not directory:
        return True
    try:
        os.makedirs(directory, exist_ok=True)
        return True
    except OSError as e:
        print(f'Failed to create data directory ({directory}):', e)
        return False


def load_tickets():
    if not ensure_data_dir(tickets_file):
        return []
    if not os.path.exists(tickets_file):
        return []
    try:
        with open(tickets_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load tickets file ({tickets_file}):', e)
        return []


def save_tickets(tickets):
    if not ensure_data_dir(tickets_file):
        return False
    try:
        with open(tickets_file, 'w', encoding='utf-8') as f:
            json.dump(tickets, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save tickets file ({tickets_file}):', e)
        return False


def get_ticket_by_session(session_id):
    for ticket in load_tickets():
        if ticket.get('session_id') == session_id:
            return ticket
    return None


def record_ticket(session_id, ticket_id, email, quantity, ticket_type='general', legacy_discount=False):
    ticket_id = normalize_ticket_id(ticket_id)
    if not ticket_id:
        raise ValueError('Invalid ticket id')
    ticket_meta = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') == session_id:
                return ticket

        ticket = {
            'session_id': session_id,
            'ticket_id': ticket_id,
            'email': email,
            'quantity': quantity,
            'ticket_type': ticket_type,
            'access': ticket_meta.get('access'),
            'legacy_discount': legacy_discount,
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': None,
            'email_sent_at': None,
            'verify_url': f"{base_url}/verify/t/{ticket_id}",
        }
        tickets.append(ticket)
        save_tickets(tickets)
        return ticket


def hash_member_code(code):
    return hashlib.sha256(code.strip().upper().encode('utf-8')).hexdigest()


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, stored_hash):
    return check_password_hash(stored_hash, password)


def normalize_discount_code(code):
    if not code:
        return None
    normalized = str(code).strip().upper().replace(' ', '')
    return normalized if normalized.replace('-', '').isalnum() else None


def generate_discount_code(email):
    prefix = ''.join(c for c in email.split('@')[0].upper() if c.isalnum())[:4] or 'MEM'
    return f"{prefix}-{secrets.token_hex(2).upper()}"


def discount_code_taken(code, exclude_email=None):
    normalized = normalize_discount_code(code)
    if not normalized:
        return True
    for member in load_members():
        if exclude_email and member.get('email', '').lower() == exclude_email.strip().lower():
            continue
        if normalize_discount_code(member.get('discount_code', '')) == normalized:
            return True
    return False


def ensure_member_discount_code(member):
    if member.get('discount_code'):
        return member['discount_code']
    code = generate_discount_code(member.get('email', 'member'))
    while discount_code_taken(code, exclude_email=member.get('email')):
        code = generate_discount_code(member.get('email', 'member'))
    with members_lock:
        members = load_members()
        for stored in members:
            if stored.get('email', '').lower() == member.get('email', '').lower():
                stored['discount_code'] = code
                save_members(members)
                break
    member['discount_code'] = code
    return code


def load_members():
    if not ensure_data_dir(members_file):
        return []
    if not os.path.exists(members_file):
        return []
    try:
        with open(members_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else data.get('members', [])
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load members file ({members_file}):', e)
        return []


def save_members(members):
    if not ensure_data_dir(members_file):
        return False
    try:
        with open(members_file, 'w', encoding='utf-8') as f:
            json.dump(members, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save members file ({members_file}):', e)
        return False


def bootstrap_legacy_members():
    bootstrap_email = os.getenv('LEGACY_BOOTSTRAP_EMAIL', '').strip().lower()
    bootstrap_password = (
        os.getenv('LEGACY_BOOTSTRAP_PASSWORD', '').strip()
        or os.getenv('LEGACY_BOOTSTRAP_CODE', '').strip()
    )
    if not bootstrap_email:
        return
    if not bootstrap_password:
        print(
            'LEGACY_BOOTSTRAP_EMAIL is set but LEGACY_BOOTSTRAP_PASSWORD is missing; '
            'member accounts will not auto-recreate after deploys.'
        )
        return
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == bootstrap_email:
                print(f'Bootstrap member already present: {bootstrap_email}')
                return
        bootstrap_discount_code = normalize_discount_code(
            os.getenv('LEGACY_BOOTSTRAP_DISCOUNT_CODE', '')
        ) or generate_discount_code(bootstrap_email)
        while discount_code_taken(bootstrap_discount_code):
            bootstrap_discount_code = generate_discount_code(bootstrap_email)
        members.append({
            'email': bootstrap_email,
            'password_hash': hash_password(bootstrap_password),
            'discount_code': bootstrap_discount_code,
            'saved_tickets': [],
            'joined_at': datetime.now(timezone.utc).isoformat(),
        })
        save_members(members)
        print(f'Bootstrap member created after deploy: {bootstrap_email}')


def log_storage_state():
    members = load_members()
    print(
        'Storage state:',
        f'members_file={members_file}',
        f'exists={os.path.exists(members_file)}',
        f'member_count={len(members)}',
        f'tickets_file={tickets_file}',
        f'tickets_exists={os.path.exists(tickets_file)}',
    )


def get_legacy_member(email):
    if not email:
        return None
    normalized = email.strip().lower()
    for member in load_members():
        if member.get('email', '').lower() == normalized:
            return member
    return None


def verify_legacy_login(email, password):
    member = get_legacy_member(email)
    if not member:
        return False
    if member.get('password_hash'):
        return verify_password(password, member['password_hash'])
    if member.get('code_hash'):
        return member.get('code_hash') == hash_member_code(password)
    return False


PASSWORD_RESET_HOURS = int(os.getenv('PASSWORD_RESET_HOURS', '1'))


def hash_reset_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def set_password_reset_token(email):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=PASSWORD_RESET_HOURS)
    normalized = email.strip().lower()
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == normalized:
                member['password_reset_token'] = hash_reset_token(token)
                member['password_reset_expires'] = expires.isoformat()
                save_members(members)
                return token
    return None


def verify_password_reset_token(email, token):
    member = get_legacy_member(email)
    if not member or not token or not member.get('password_reset_token'):
        return False
    expires_raw = member.get('password_reset_expires')
    if not expires_raw:
        return False
    try:
        expires = datetime.fromisoformat(expires_raw.replace('Z', '+00:00'))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if datetime.now(timezone.utc) > expires:
        return False
    return member['password_reset_token'] == hash_reset_token(token)


def update_member_password(email, new_password):
    normalized = email.strip().lower()
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == normalized:
                member['password_hash'] = hash_password(new_password)
                member.pop('code_hash', None)
                member.pop('password_reset_token', None)
                member.pop('password_reset_expires', None)
                save_members(members)
                return True
    return False


def load_invites():
    if not ensure_data_dir(invites_file):
        return []
    if not os.path.exists(invites_file):
        return []
    try:
        with open(invites_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load invites file ({invites_file}):', e)
        return []


def save_invites(invites):
    if not ensure_data_dir(invites_file):
        return False
    try:
        with open(invites_file, 'w', encoding='utf-8') as f:
            json.dump(invites, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save invites file ({invites_file}):', e)
        return False


def normalize_email_list(raw):
    if not raw:
        return []
    normalized = []
    seen = set()
    for chunk in raw.replace(',', '\n').replace(';', '\n').split('\n'):
        email = chunk.strip().lower()
        if not email or '@' not in email:
            continue
        if email in seen:
            continue
        seen.add(email)
        normalized.append(email)
    return normalized


def get_member_invite(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    for invite in load_invites():
        if invite.get('email', '').strip().lower() == normalized:
            return invite
    return None


def add_emails_to_invite_list(emails):
    added = []
    skipped = []
    with invites_lock:
        invites = load_invites()
        existing = {i.get('email', '').strip().lower() for i in invites}
        for email in emails:
            if email in existing:
                skipped.append(email)
                continue
            invites.append({
                'email': email,
                'added_at': datetime.now(timezone.utc).isoformat(),
                'sent_at': None,
                'claimed_at': None,
                'invite_token': None,
                'invite_expires': None,
            })
            existing.add(email)
            added.append(email)
        save_invites(invites)
    return added, skipped


def remove_email_from_invite_list(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        updated = [i for i in invites if i.get('email', '').strip().lower() != normalized]
        if len(updated) == len(invites):
            return False
        save_invites(updated)
        return True


def set_member_invite_token(email):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['invite_token'] = hash_reset_token(token)
                invite['invite_expires'] = expires.isoformat()
                save_invites(invites)
                return token
    return None


def verify_member_invite_token(email, token):
    member = get_member_invite(email)
    if not member or not token or not member.get('invite_token'):
        return False
    if member.get('claimed_at'):
        return False
    expires_raw = member.get('invite_expires')
    if not expires_raw:
        return False
    try:
        expires = datetime.fromisoformat(expires_raw.replace('Z', '+00:00'))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if datetime.now(timezone.utc) > expires:
        return False
    return invite['invite_token'] == hash_reset_token(token)
