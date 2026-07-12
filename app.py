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


base_url = get_public_base_url()
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = os.getenv('ADMIN_KEY', 'section2024')
verify_login_email = os.getenv('VERIFY_LOGIN_EMAIL', '').strip().lower()
verify_login_password = os.getenv('VERIFY_LOGIN_PASSWORD') or ''
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
app.secret_key = os.getenv('SECRET_KEY', 'thesection-legacy-portal-change-me')
tickets_lock = threading.Lock()
members_lock = threading.Lock()
scanner_settings_lock = threading.Lock()

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

# Stripe
stripe.api_key = "sk_test_51TmPE8GVxxcKcZp9PetRAcpNnLTlSqR3Xfa9h1SZyrtgGdzD09M2WC3QNCOfGhaSQJR0vmrSUYI8WGmHovmSy29u00JF8TStpp"   # Your "The Section" key

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


def member_has_past_purchases(member):
    if not member:
        return False
    email = member.get('email', '').strip().lower()
    if email:
        for ticket in load_tickets():
            if ticket.get('email', '').lower() == email:
                return True
    for ticket_id in member.get('saved_tickets', []):
        if get_ticket_record(ticket_id):
            return True
    return False


def member_discount_active():
    if not is_legacy_member_logged_in():
        return False
    member = get_logged_in_member()
    return bool(member and member_has_past_purchases(member))


def resolve_member_discount_application(requested):
    if not requested:
        return False
    return member_discount_active()


def sync_member_tickets_from_email(member):
    email = member.get('email', '').strip().lower()
    if not email:
        return
    for ticket in load_tickets():
        ticket_id = ticket.get('ticket_id')
        if ticket.get('email', '').lower() == email and ticket_id:
            add_saved_ticket_for_member(email, ticket_id)


def is_legacy_member_logged_in():
    email = session.get('legacy_member_email')
    return bool(email and get_legacy_member(email))


def get_logged_in_member():
    email = session.get('legacy_member_email')
    if not email:
        return None
    return get_legacy_member(email)


def ticket_recipient_email(stripe_email=None, metadata=None):
    logged_in_email = (session.get('legacy_member_email') or '').strip().lower()
    if logged_in_email:
        return logged_in_email
    if metadata:
        meta_email = (metadata.get('member_email') or '').strip().lower()
        if meta_email:
            return meta_email
    normalized = (stripe_email or '').strip().lower()
    return normalized or None


def bulk_discount_rate(ticket_type):
    if ticket_type == 'vip':
        return vip_bulk_discount
    return bundle_discount


def bulk_discount_applies(ticket_type, quantity):
    minimum = vip_bundle_min if ticket_type == 'vip' else bundle_min
    return quantity >= minimum


def calculate_bulk_total_cents(ticket_type, quantity):
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    base_total = base * quantity
    if bulk_discount_applies(ticket_type, quantity):
        return int(base_total * (1 - bulk_discount_rate(ticket_type)))
    return base_total


def calculate_total_cents(ticket_type, quantity, apply_member_discount=False):
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    base_total = base * quantity

    if not apply_member_discount or member_discount <= 0:
        return calculate_bulk_total_cents(ticket_type, quantity)

    if bulk_discount_applies(ticket_type, quantity):
        return int(base_total * (1 - bulk_discount_rate(ticket_type) - member_discount))

    return int(base_total * (1 - member_discount))


def calculate_unit_price(ticket_type, quantity, apply_member_discount=False):
    if quantity < 1:
        quantity = 1
    return calculate_total_cents(ticket_type, quantity, apply_member_discount) // quantity


def pricing_breakdown(ticket_type, quantity, apply_member_discount=False):
    base = TICKET_TYPES[ticket_type]['price_cents']
    base_total_cents = base * quantity
    bulk_only_total = calculate_bulk_total_cents(ticket_type, quantity)
    total_cents = calculate_total_cents(ticket_type, quantity, apply_member_discount)
    unit_price = total_cents // quantity

    bulk_savings_active = bulk_only_total < base_total_cents
    member_requested = apply_member_discount and member_discount > 0
    stacked_discount_applied = (
        bulk_savings_active and member_requested and total_cents < bulk_only_total
    )

    member_only_total = (
        int(base_total_cents * (1 - member_discount))
        if member_requested
        else None
    )

    bundle_discount_applied = bulk_savings_active and not stacked_discount_applied
    vip_bundle_applied = bundle_discount_applied and ticket_type == 'vip'
    member_discount_applied = (
        member_requested
        and not stacked_discount_applied
        and not bulk_savings_active
        and member_only_total is not None
        and total_cents == member_only_total
    )

    combined_discount_percent = None
    if stacked_discount_applied and bulk_discount_applies(ticket_type, quantity):
        combined_discount_percent = int(
            (bulk_discount_rate(ticket_type) + member_discount) * 100
        )

    bulk_min = vip_bundle_min if ticket_type == 'vip' else bundle_min
    bulk_percent = int(bulk_discount_rate(ticket_type) * 100)

    return {
        'ticket_type': ticket_type,
        'quantity': quantity,
        'unit_price_cents': unit_price,
        'total_cents': total_cents,
        'base_total_cents': base_total_cents,
        'base_unit_price_cents': base,
        'vip_bundle_applied': vip_bundle_applied,
        'bundle_discount_applied': bundle_discount_applied,
        'member_discount_applied': member_discount_applied,
        'stacked_discount_applied': stacked_discount_applied,
        'combined_discount_percent': combined_discount_percent,
        'legacy_discount_applied': total_cents < base_total_cents,
        'bundle_min': bulk_min,
        'bundle_discount_percent': bulk_percent,
        'member_discount_percent': int(member_discount * 100),
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
    }


def add_saved_ticket_for_member(email, ticket_id):
    normalized_id = normalize_ticket_id(ticket_id)
    if not normalized_id:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == email.strip().lower():
                saved = member.setdefault('saved_tickets', [])
                if normalized_id not in saved:
                    saved.append(normalized_id)
                    save_members(members)
                return True
    return False


def remove_saved_ticket_for_member(email, ticket_id):
    normalized_id = normalize_ticket_id(ticket_id)
    if not normalized_id:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == email.strip().lower():
                saved = member.get('saved_tickets', [])
                if normalized_id in saved:
                    saved.remove(normalized_id)
                    save_members(members)
                return True
    return False


def ticket_result_meta(record):
    ticket_type = record.get('ticket_type', 'general')
    access = record.get('access') or TICKET_TYPES.get(ticket_type, {}).get('access')
    return {
        'ticket_type': ticket_type,
        'access': access,
        'is_vip': ticket_type == 'vip',
    }


def normalize_ticket_id(ticket_id):
    if not ticket_id:
        return None
    normalized = str(ticket_id).strip().upper().replace('-', '')
    return normalized if normalized.isalnum() else None


def get_ticket_record(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return None
    for ticket in load_tickets():
        stored = normalize_ticket_id(ticket.get('ticket_id'))
        if stored == normalized:
            return ticket
    return None


def mark_ticket_scanned(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return False
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if normalize_ticket_id(ticket.get('ticket_id')) == normalized:
                if ticket.get('scanned_at'):
                    return False
                ticket['scanned_at'] = datetime.now(timezone.utc).isoformat()
                save_tickets(tickets)
                return True
    return False


def parse_iso_datetime(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def get_counting_epoch():
    settings = load_scanner_settings()
    return parse_iso_datetime(settings.get('counting_epoch'))


def get_reset_history():
    settings = load_scanner_settings()
    history = settings.get('reset_history', [])
    return history if isinstance(history, list) else []


def ticket_counts_for_current_period(scanned_at):
    scanned = parse_iso_datetime(scanned_at)
    if not scanned:
        return False
    counting_epoch = get_counting_epoch()
    if counting_epoch is None:
        return True
    return scanned >= counting_epoch


def reset_admission_counts():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    counts = compute_admission_counts()

    with scanner_settings_lock:
        settings = load_scanner_settings()
        history = settings.get('reset_history', [])
        if not isinstance(history, list):
            history = []
        history.append({
            'reset_at': now_iso,
            'ga': counts['ga'],
            'vip': counts['vip'],
            'total': counts['total'],
        })
        settings['reset_history'] = history
        settings['counting_epoch'] = now_iso
        save_scanner_settings(settings)

    return {
        'reset_at': now_iso,
        'ga': counts['ga'],
        'vip': counts['vip'],
        'total': counts['total'],
    }


def require_admin():
    return request.args.get('key') == admin_key


def verify_auth_configured():
    return bool(verify_login_email and verify_login_password)


def is_scanner_admin_member():
    if not verify_login_email:
        return False
    member = get_logged_in_member()
    if not member:
        return False
    member_email = (member.get('email') or '').strip().lower()
    return secrets.compare_digest(member_email, verify_login_email)


def verify_scanner_session_authenticated():
    if session.get('verify_authenticated') is not True:
        return False
    logged_email = (session.get('verify_login_email') or '').strip().lower()
    return secrets.compare_digest(logged_email, verify_login_email)


def verify_authenticated():
    if not verify_auth_configured():
        return False
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def verify_scanner_credentials(email, password):
    if not verify_auth_configured():
        return False
    email_ok = secrets.compare_digest(
        (email or '').strip().lower(),
        verify_login_email,
    )
    password_ok = secrets.compare_digest(password or '', verify_login_password)
    return email_ok and password_ok


def protect_scanner_response():
    if not verify_auth_configured():
        message = 'Scanner login is not configured. Set VERIFY_LOGIN_EMAIL and VERIFY_LOGIN_PASSWORD.'
        if request.method == 'POST' or request.is_json:
            return jsonify({'error': message}), 503
        return render_template('verify_login.html', error=message), 503

    if verify_authenticated():
        return None

    if request.method == 'POST' or request.is_json:
        return jsonify({'error': 'Unauthorized'}), 401

    next_url = request.full_path if request.query_string else request.path
    if next_url.endswith('?'):
        next_url = next_url[:-1]
    return redirect(url_for('verify_login', next=next_url))


def build_qr_png_bytes(ticket_id):
    qr_payload = f"{base_url}/verify/t/{ticket_id}"
    qr = qrcode.QRCode(version=1, box_size=12, border=4)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return buffered.getvalue()


def build_qr_image(ticket_id):
    return base64.b64encode(build_qr_png_bytes(ticket_id)).decode()


def ticket_display_url(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return None
    return f"{base_url}/t/{normalized}"


def make_pass_icon_png():
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (87, 87), color=(24, 24, 27))
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, 66, 66), fill='white')
    draw.rectangle((28, 28, 36, 36), fill='black')
    draw.rectangle((50, 28, 58, 36), fill='black')
    draw.rectangle((28, 50, 36, 58), fill='black')
    draw.rectangle((50, 50, 58, 58), fill='black')
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def sign_wallet_manifest(manifest_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = os.path.join(tmp, 'manifest.json')
        signature_path = os.path.join(tmp, 'signature')
        with open(manifest_path, 'wb') as f:
            f.write(manifest_bytes)

        result = subprocess.run(
            [
                'openssl', 'smime', '-binary', '-sign',
                '-signer', wallet_cert_path,
                '-inkey', wallet_key_path,
                '-certfile', wallet_wwdr_path,
                '-in', manifest_path,
                '-out', signature_path,
                '-outform', 'DER',
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            print('Wallet signing failed:', result.stderr.decode('utf-8', errors='ignore'))
            return None

        with open(signature_path, 'rb') as f:
            return f.read()


def build_wallet_pass(ticket_id, quantity):
    if not wallet_enabled:
        return None

    verify_url = f"{base_url}/verify/t/{ticket_id}"
    guest_label = '1 guest' if quantity == 1 else f'{quantity} guests'
    pass_json = {
        'formatVersion': 1,
        'passTypeIdentifier': wallet_pass_type_id,
        'teamIdentifier': wallet_team_id,
        'organizationName': 'The Section',
        'description': 'The Section Ticket',
        'serialNumber': normalize_ticket_id(ticket_id),
        'foregroundColor': 'rgb(255, 255, 255)',
        'backgroundColor': 'rgb(24, 24, 27)',
        'labelColor': 'rgb(161, 161, 170)',
        'barcodes': [{
            'format': 'PKBarcodeFormatQR',
            'message': verify_url,
            'messageEncoding': 'iso-8859-1',
            'altText': ticket_id,
        }],
        'eventTicket': {
            'primaryFields': [{
                'key': 'event',
                'label': 'EVENT',
                'value': 'The Section',
            }],
            'secondaryFields': [
                {
                    'key': 'guests',
                    'label': 'GUESTS',
                    'value': guest_label,
                },
                {
                    'key': 'ticket',
                    'label': 'TICKET',
                    'value': ticket_id,
                },
            ],
            'backFields': [{
                'key': 'verify',
                'label': 'VERIFY',
                'value': verify_url,
            }],
        },
    }

    icon_png = make_pass_icon_png()
    files = {
        'pass.json': json.dumps(pass_json, indent=2).encode('utf-8'),
        'icon.png': icon_png,
        'icon@2x.png': icon_png,
        'logo.png': icon_png,
        'logo@2x.png': icon_png,
    }
    manifest = {
        name: hashlib.sha1(data).hexdigest()
        for name, data in files.items()
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode('utf-8')
    files['manifest.json'] = manifest_bytes

    signature = sign_wallet_manifest(manifest_bytes)
    if not signature:
        return None

    files['signature'] = signature

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


bootstrap_legacy_members()
log_storage_state()


def extract_ticket_id_from_url(raw):
    for marker in ('/verify/t/', '/t/'):
        if marker in raw:
            ticket_id = raw.split(marker)[-1].split('?')[0].split('/')[0].strip()
            return normalize_ticket_id(ticket_id)
    return None


def load_scanner_settings():
    if not ensure_data_dir(scanner_settings_file):
        return {}
    if not os.path.exists(scanner_settings_file):
        return {}
    try:
        with open(scanner_settings_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load scanner settings ({scanner_settings_file}):', e)
        return {}


def save_scanner_settings(settings):
    if not ensure_data_dir(scanner_settings_file):
        return False
    try:
        with open(scanner_settings_file, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save scanner settings ({scanner_settings_file}):', e)
        return False


def parse_max_capacity(raw):
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_max_capacity():
    settings = load_scanner_settings()
    return parse_max_capacity(settings.get('max_capacity'))


def set_max_capacity(value):
    normalized = parse_max_capacity(value)
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized is None:
            settings.pop('max_capacity', None)
        else:
            settings['max_capacity'] = normalized
        save_scanner_settings(settings)
    return normalized


def compute_admission_counts():
    ga = 0
    vip = 0
    for ticket in load_tickets():
        scanned_at = ticket.get('scanned_at')
        if not scanned_at or not ticket_counts_for_current_period(scanned_at):
            continue
        qty = int(ticket.get('quantity') or 1)
        if ticket.get('ticket_type') == 'vip':
            vip += qty
        else:
            ga += qty
    return {'ga': ga, 'vip': vip, 'total': ga + vip}


def admission_capacity_remaining():
    max_capacity = get_max_capacity()
    if not max_capacity:
        return None
    counts = compute_admission_counts()
    return max(0, max_capacity - counts['total'])


def get_admission_totals():
    counts = compute_admission_counts()
    max_capacity = get_max_capacity()
    capacity_reached = bool(max_capacity and counts['total'] >= max_capacity)
    spots_remaining = None
    if max_capacity:
        spots_remaining = max(0, max_capacity - counts['total'])
    return {
        **counts,
        'max_capacity': max_capacity,
        'capacity_reached': capacity_reached,
        'spots_remaining': spots_remaining,
        'reset_history': get_reset_history(),
    }


def check_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return {'status': 'invalid', 'ticket_id': ticket_id or None, 'quantity': 0, 'ticket_type': None, 'access': None, 'is_vip': False}

    record = get_ticket_record(normalized)
    if not record:
        return {'status': 'invalid', 'ticket_id': normalized, 'quantity': 0, 'ticket_type': None, 'access': None, 'is_vip': False}

    quantity = int(record.get('quantity') or 1)
    display_id = record.get('ticket_id', normalized)
    meta = ticket_result_meta(record)

    if record.get('scanned_at'):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    remaining = admission_capacity_remaining()
    if remaining is not None and quantity > remaining:
        return {'status': 'sold_out', 'ticket_id': display_id, 'quantity': quantity, **meta}

    if not mark_ticket_scanned(normalized):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    return {'status': 'accepted', 'ticket_id': display_id, 'quantity': quantity, **meta}


def parse_scanned_ticket(raw):
    if not raw:
        return None

    raw = raw.strip()

    ticket_id = extract_ticket_id_from_url(raw)
    if ticket_id:
        return ticket_id

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get('ticket_id'):
            return normalize_ticket_id(data['ticket_id'])
    except json.JSONDecodeError:
        pass

    try:
        data = ast.literal_eval(raw)
        if isinstance(data, dict) and data.get('ticket_id'):
            return normalize_ticket_id(data['ticket_id'])
    except (ValueError, SyntaxError):
        pass

    return normalize_ticket_id(raw)


def mark_email_sent(session_id):
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') == session_id:
                ticket['email_sent_at'] = datetime.now(timezone.utc).isoformat()
                save_tickets(tickets)
                return


def send_ticket_email(customer_email, ticket_id, quantity, ticket_data, ticket_type='general', access=None):
    view_url = ticket_display_url(ticket_id)
    type_label = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['name']
    with app.app_context():
        try:
            msg = Message(
                "Your The Section Tickets",
                sender=app.config['MAIL_DEFAULT_SENDER'],
                recipients=[customer_email],
            )
            access_line = f"Access: {access}\n" if access else ''
            msg.body = (
                f"You're in for The Section!\n\n"
                f"Ticket type: {type_label}\n"
                f"Ticket ID: {ticket_id}\n"
                f"Guests: {quantity}\n"
                f"{access_line}\n"
                f"Show the attached QR code at the door.\n"
                f"Or open this link on your phone to view your ticket:\n{view_url}\n"
            )
            msg.attach("ticket-qr.png", "image/png", base64.b64decode(ticket_data))
            mail.send(msg)
            print(f"Ticket email sent to {customer_email}")
            return True
        except Exception as e:
            print(f"Email failed for {customer_email}:", str(e))
            return False


def deliver_ticket_email(session_id, customer_email, ticket_id, quantity, ticket_data, ticket_type='general', access=None):
    if not customer_email:
        return False

    record = get_ticket_by_session(session_id)
    if record and record.get('email_sent_at'):
        return True

    if record:
        ticket_type = record.get('ticket_type', ticket_type)
        access = record.get('access', access)

    result = {'sent': False}

    def _send():
        result['sent'] = send_ticket_email(
            customer_email, ticket_id, quantity, ticket_data, ticket_type, access
        )
        if result['sent']:
            mark_email_sent(session_id)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    thread.join(timeout=app.config['MAIL_TIMEOUT'] + 2)
    return result['sent']


def build_password_reset_url(email, token, reset_url=None):
    if reset_url:
        return reset_url
    query = urlencode({'email': email, 'token': token})
    return f"{get_public_base_url()}/reset-password?{query}"


def mail_from_address():
    sender = app.config['MAIL_DEFAULT_SENDER']
    return ('The Section', sender) if sender else sender


def send_password_reset_email(customer_email, token, reset_url=None):
    reset_url = build_password_reset_url(customer_email, token, reset_url)
    hours_label = f'{PASSWORD_RESET_HOURS} hour{"s" if PASSWORD_RESET_HOURS != 1 else ""}'
    plain_body = (
        'You requested a password reset for your The Section member account.\n\n'
        f'Open this link to choose a new password (expires in {hours_label}):\n'
        f'{reset_url}\n\n'
        'If you did not request this, you can ignore this email.\n'
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        '<p>You requested a password reset for your member account.</p>'
        f'<p><a href="{reset_url}" style="display:inline-block;padding:12px 18px;'
        'background:#111;color:#fff;text-decoration:none;border-radius:10px;">'
        'Choose a new password</a></p>'
        f'<p style="color:#555;font-size:14px;">This link expires in {hours_label}.</p>'
        f'<p style="color:#555;font-size:14px;">If the button does not work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all;">{reset_url}</span></p>'
        '<p style="color:#555;font-size:14px;">If you did not request this, you can ignore this email.</p>'
        '</div>'
    )
    with app.app_context():
        try:
            msg = Message(
                'The Section — member password link',
                sender=mail_from_address(),
                recipients=[customer_email],
            )
            msg.body = plain_body
            msg.html = html_body
            mail.send(msg)
            print(f"Password reset email sent to {customer_email}")
            return True
        except Exception as e:
            print(f"Password reset email failed for {customer_email}:", str(e))
            return False


def deliver_password_reset_email(customer_email, token, reset_url=None):
    sent = send_password_reset_email(customer_email, token, reset_url)
    if not sent:
        print(f"Password reset email not confirmed for {customer_email}")
    return sent


@app.route('/')
def home():
    return render_template('home.html', show_scanner_link=is_scanner_admin_member())


@app.route('/api/member-status')
def member_status():
    member = get_logged_in_member()
    discount_code = None
    discount_eligible = False
    if member:
        discount_eligible = member_has_past_purchases(member)
        discount_code = member.get('discount_code') or ensure_member_discount_code(member)
    return jsonify({
        'logged_in': bool(member),
        'email': session.get('legacy_member_email'),
        'discount_code': discount_code,
        'member_discount_eligible': discount_eligible,
        'member_discount_percent': int(member_discount * 100),
        'bundle_min': bundle_min,
        'bundle_discount_percent': int(bundle_discount * 100),
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
        'ticket_types': {
            key: {
                'name': meta['name'],
                'price_cents': meta['price_cents'],
                'access': meta.get('access'),
            }
            for key, meta in TICKET_TYPES.items()
        },
    })


@app.route('/api/pricing')
def pricing():
    ticket_type = request.args.get('ticket_type', 'general')
    quantity = max(1, int(request.args.get('quantity', 1)))
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    apply_member = resolve_member_discount_application(
        request.args.get('apply_member_discount', '').lower() in ('1', 'true', 'yes')
    )
    return jsonify(pricing_breakdown(ticket_type, quantity, apply_member))


def build_checkout_session(quantity, ticket_type, apply_member_discount=False):
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    quantity = max(1, int(quantity))

    legacy_member = is_legacy_member_logged_in()
    apply_member = resolve_member_discount_application(apply_member_discount)
    breakdown = pricing_breakdown(ticket_type, quantity, apply_member)
    unit_price = breakdown['unit_price_cents']
    ticket_meta = TICKET_TYPES[ticket_type]
    description = ticket_meta['description']
    if breakdown['stacked_discount_applied']:
        member = get_logged_in_member()
        code = member.get('discount_code') if member else None
        combined = breakdown.get('combined_discount_percent')
        if combined:
            description += f' · {combined}% off (bulk + member)'
        if code:
            description += f' · member code {code}'
    elif breakdown['member_discount_applied']:
        member = get_logged_in_member()
        code = member.get('discount_code') if member else None
        if code:
            description += f' · {breakdown["member_discount_percent"]}% member code {code}'
        else:
            description += f' · {breakdown["member_discount_percent"]}% member discount'
    elif breakdown['bundle_discount_applied']:
        bulk_min = breakdown['bundle_min']
        description += f' · {breakdown["bundle_discount_percent"]}% bulk discount ({bulk_min}+ tickets)'

    member = get_logged_in_member()
    member_email = (member.get('email') or '').strip().lower() if member else ''

    print(f"Creating {ticket_type} session for {quantity} tickets @ {unit_price}c")

    checkout_kwargs = {
        'payment_method_types': ['card'],
        'line_items': [{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': f"The Section - {ticket_meta['name']}",
                    'description': description,
                },
                'unit_amount': unit_price,
            },
            'quantity': quantity,
        }],
        'mode': 'payment',
        'metadata': {
            'ticket_type': ticket_type,
            'legacy_member': 'true' if legacy_member else 'false',
            'legacy_discount': 'true' if breakdown['legacy_discount_applied'] else 'false',
            'member_email': member_email,
        },
        'success_url': f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}",
        'cancel_url': f"{base_url}/",
    }
    if member_email:
        checkout_kwargs['customer_email'] = member_email

    return stripe.checkout.Session.create(**checkout_kwargs)


@app.route('/api/checkout-intent', methods=['GET', 'POST', 'DELETE'])
def checkout_intent():
    if request.method == 'POST':
        data = request.get_json() or {}
        ticket_type = data.get('ticket_type', 'general')
        if ticket_type not in TICKET_TYPES:
            ticket_type = 'general'
        session['checkout_intent'] = {
            'quantity': max(1, int(data.get('quantity', 1))),
            'ticket_type': ticket_type,
            'apply_member_discount': bool(data.get('apply_member_discount')),
        }
        return jsonify({'ok': True})
    if request.method == 'DELETE':
        session.pop('checkout_intent', None)
        return jsonify({'ok': True})
    return jsonify(session.get('checkout_intent') or {})


@app.route('/checkout/resume')
def checkout_resume():
    if not is_legacy_member_logged_in():
        return redirect(url_for('legacy_portal', next='/checkout/resume'))
    intent = session.pop('checkout_intent', None)
    if not intent:
        return redirect('/?open_tickets=1')
    try:
        checkout_session = build_checkout_session(
            intent.get('quantity', 1),
            intent.get('ticket_type', 'general'),
            apply_member_discount=intent.get('apply_member_discount', False),
        )
        return redirect(checkout_session.url)
    except Exception as e:
        print("Error resuming checkout:", str(e))
        return redirect('/?open_tickets=1')


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if not is_legacy_member_logged_in():
        return jsonify({'error': 'Sign in to your member account before purchasing tickets.'}), 401

    try:
        data = request.get_json()
        quantity = max(1, int(data.get('quantity', 1)))
        ticket_type = data.get('ticket_type', 'general')
        apply_member_discount = bool(data.get('apply_member_discount'))
        checkout_session = build_checkout_session(
            quantity, ticket_type, apply_member_discount=apply_member_discount,
        )
        print("Session created successfully:", checkout_session.url)
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        print("Error creating session:", str(e))
        return jsonify({'error': str(e)}), 500

# Replace your current /success route with this cleaner version:
@app.route('/success')
def success():
    session_id = request.args.get('session_id')
    print("Success page called with session_id:", session_id)

    if not session_id:
        return render_template('success.html', error="Missing session ID")

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])

        metadata = checkout_session.metadata or {}
        stripe_email = None
        if checkout_session.customer_details:
            stripe_email = checkout_session.customer_details.email

        existing_ticket = get_ticket_by_session(session_id)
        if existing_ticket:
            ticket_id = existing_ticket['ticket_id']
            quantity = existing_ticket['quantity']
            ticket_type = existing_ticket.get('ticket_type', 'general')
            access = existing_ticket.get('access')
            delivery_email = ticket_recipient_email(existing_ticket.get('email'), metadata)
        else:
            quantity = 1
            ticket_id = uuid.uuid4().hex[:12].upper()
            ticket_type = metadata.get('ticket_type', 'general')
            if ticket_type not in TICKET_TYPES:
                ticket_type = 'general'
            legacy_discount = metadata.get('legacy_discount') == 'true'

            if checkout_session.line_items and checkout_session.line_items.data:
                quantity = checkout_session.line_items.data[0].quantity

            delivery_email = ticket_recipient_email(stripe_email, metadata)

            record_ticket(
                session_id, ticket_id, delivery_email, quantity,
                ticket_type=ticket_type, legacy_discount=legacy_discount,
            )
            access = TICKET_TYPES[ticket_type].get('access')

            if delivery_email:
                purchased_member = get_legacy_member(delivery_email)
                if purchased_member:
                    add_saved_ticket_for_member(delivery_email, ticket_id)
                    if member_has_past_purchases(purchased_member):
                        ensure_member_discount_code(purchased_member)

        ticket_data = build_qr_image(ticket_id)

        email_sent = deliver_ticket_email(
            session_id, delivery_email, ticket_id, quantity, ticket_data, ticket_type, access
        )

        return render_template('success.html',
                               email=delivery_email,
                               email_sent=email_sent,
                               ticket_data=ticket_data,
                               ticket_id=ticket_id,
                               quantity=quantity,
                               ticket_type=ticket_type,
                               access=access,
                               wallet_enabled=wallet_enabled)

    except Exception as e:
        print("SUCCESS ROUTE CRASH:", str(e))
        return render_template('success.html', error=str(e))

@app.route('/wallet/<ticket_id>.pkpass')
def download_wallet_pass(ticket_id):
    if not wallet_enabled:
        return (
            'Apple Wallet is not configured yet. Screenshot your ticket or download the QR code instead.',
            503,
        )

    record = get_ticket_record(ticket_id)
    if not record:
        return 'Ticket not found', 404

    quantity = int(record.get('quantity') or 1)
    pkpass = build_wallet_pass(record.get('ticket_id', ticket_id), quantity)
    if not pkpass:
        return 'Could not create Apple Wallet pass. Use screenshot or download instead.', 503

    filename = f"thesection-{normalize_ticket_id(ticket_id)}.pkpass"
    return Response(
        pkpass,
        mimetype='application/vnd.apple.pkpass',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@app.route('/t/<ticket_id>')
def show_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return render_template('ticket_view.html', error='Invalid ticket'), 404
    record = get_ticket_record(normalized)
    if not record:
        return render_template('ticket_view.html', error='Ticket not found'), 404
    meta = ticket_result_meta(record)
    return render_template(
        'ticket_view.html',
        ticket={
            'ticket_id': record.get('ticket_id', normalized),
            'quantity': int(record.get('quantity') or 1),
            'scanned': bool(record.get('scanned_at')),
            **meta,
        },
        ticket_data=build_qr_image(normalized),
    )


@app.route('/api/admission-totals')
def admission_totals():
    guard = protect_scanner_response()
    if guard:
        return guard
    return jsonify(get_admission_totals())


@app.route('/api/admission-totals/reset', methods=['POST'])
def reset_admission_totals():
    guard = protect_scanner_response()
    if guard:
        return guard
    recorded = reset_admission_counts()
    totals = get_admission_totals()
    return jsonify({'recorded': recorded, **totals})


@app.route('/api/scanner-settings', methods=['GET', 'POST'])
def scanner_settings():
    guard = protect_scanner_response()
    if guard:
        return guard

    if request.method == 'POST':
        data = request.get_json() or {}
        max_capacity = set_max_capacity(data.get('max_capacity'))
        totals = get_admission_totals()
        return jsonify({'max_capacity': max_capacity, **totals})

    return jsonify({'max_capacity': get_max_capacity(), **get_admission_totals()})


@app.route('/verify/login', methods=['GET', 'POST'])
def verify_login():
    if request.method == 'POST':
        if not verify_auth_configured():
            return render_template(
                'verify_login.html',
                error='Scanner login is not configured. Set VERIFY_LOGIN_EMAIL and VERIFY_LOGIN_PASSWORD.',
            ), 503

        email = request.form.get('email') or ''
        password = request.form.get('password') or ''
        if verify_scanner_credentials(email, password):
            session['verify_authenticated'] = True
            session['verify_login_email'] = verify_login_email
            next_url = (request.form.get('next') or '').strip()
            if not next_url or not next_url.startswith('/'):
                next_url = url_for('verify_ticket')
            return redirect(next_url)

        return render_template(
            'verify_login.html',
            error='Invalid email or password',
            next_url=request.form.get('next', ''),
        )

    if verify_authenticated():
        return redirect(url_for('verify_ticket'))

    next_url = request.args.get('next', '')
    return render_template('verify_login.html', next_url=next_url)


@app.route('/verify/logout', methods=['POST'])
def verify_logout():
    session.pop('verify_authenticated', None)
    session.pop('verify_login_email', None)
    if request.is_json or request.headers.get('X-Scanner-Logout') == '1':
        return jsonify({'ok': True})
    return redirect(url_for('verify_login'))


@app.route('/verify/t/<ticket_id>')
def verify_ticket_native(ticket_id):
    guard = protect_scanner_response()
    if guard:
        return guard

    result = check_ticket(ticket_id)
    return render_template(
        'verify_result.html',
        admission_totals=get_admission_totals(),
        **result,
    )


@app.route('/verify', methods=['GET', 'POST'])
def verify_ticket():
    guard = protect_scanner_response()
    if guard:
        return guard

    if request.method == 'POST':
        ticket_data = request.form.get('ticket_data') or request.json.get('ticket_data') if request.is_json else None
        ticket_id = parse_scanned_ticket(ticket_data)
        if not ticket_id:
            return "Invalid ticket"

        result = check_ticket(ticket_id)
        if request.is_json:
            return jsonify({**result, 'admission_totals': get_admission_totals()})
        if result['status'] == 'accepted':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            type_label = 'VIP' if result.get('is_vip') else 'GA'
            return f"✅ {type_label} — {qty} {guest_word} admitted"
        if result['status'] == 'used':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            return f"❌ Already used ({qty} {guest_word})"
        if result['status'] == 'sold_out':
            return '❌ Max capacity reached — congrats on selling this place out!'
        return "Invalid ticket"

    return render_template('verify.html', admission_totals=get_admission_totals())


def portal_context(member=None, saved_ticket_details=None, error=None, success=None, next_url='', active_tab='login'):
    logged_in = member or get_logged_in_member()
    if logged_in:
        sync_member_tickets_from_email(logged_in)
        logged_in = get_logged_in_member()
        if not logged_in.get('discount_code'):
            ensure_member_discount_code(logged_in)
            logged_in = get_logged_in_member()
        saved_ticket_details = []
        for ticket_id in logged_in.get('saved_tickets', []):
            record = get_ticket_record(ticket_id)
            if record:
                saved_ticket_details.append({
                    'ticket_id': ticket_id,
                    'quantity': record.get('quantity', 1),
                    'ticket_type': record.get('ticket_type', 'general'),
                    'purchased_at': record.get('purchased_at', ''),
                    'scanned': bool(record.get('scanned_at')),
                    'view_url': ticket_display_url(ticket_id),
                })
    return {
        'error': error,
        'success': success,
        'member': logged_in,
        'saved_ticket_details': saved_ticket_details or [],
        'has_past_purchases': member_has_past_purchases(logged_in) if logged_in else False,
        'bundle_min': bundle_min,
        'bundle_discount_percent': int(bundle_discount * 100),
        'member_discount_percent': int(member_discount * 100),
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
        'next_url': next_url,
        'active_tab': active_tab,
    }


@app.route('/legacy/reset-password', methods=['GET', 'POST'])
@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = (
        request.form.get('email', '').strip().lower()
        or request.args.get('email', '').strip().lower()
    )
    token = request.form.get('token', '') or request.args.get('token', '')
    error = None

    if not email or not token:
        return redirect(url_for('legacy_portal'))

    token_valid = verify_password_reset_token(email, token)
    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not token_valid:
            error = 'This reset link is invalid or has expired. Request a new one from the member portal.'
        elif new_password != confirm_password:
            error = 'Passwords do not match.'
        elif len(new_password) < 8:
            error = 'Password must be at least 8 characters.'
        elif update_member_password(email, new_password):
            session['legacy_member_email'] = email
            return redirect(url_for('legacy_portal'))
        else:
            error = 'Could not update password. Try again or contact support.'

    return render_template(
        'legacy_reset_password.html',
        email=email,
        token=token,
        token_valid=token_valid,
        error=error,
        success=None,
        reset_hours=PASSWORD_RESET_HOURS,
    )


@app.route('/members', methods=['GET', 'POST'])
@app.route('/legacy', methods=['GET', 'POST'])
def legacy_portal():
    next_url = request.args.get('next', '')
    member = get_logged_in_member()

    if request.method == 'POST':
        action = request.form.get('action')
        next_url = request.form.get('next') or request.args.get('next', '')

        if action == 'register':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not email or not password:
                error = 'Email and password are required.'
            elif password != confirm_password:
                error = 'Passwords do not match.'
            elif len(password) < 8:
                error = 'Password must be at least 8 characters.'
            elif get_legacy_member(email):
                error = 'An account with that email already exists.'
            else:
                with members_lock:
                    members = load_members()
                    members.append({
                        'email': email,
                        'password_hash': hash_password(password),
                        'saved_tickets': [],
                        'joined_at': datetime.now(timezone.utc).isoformat(),
                    })
                    save_members(members)
                session['legacy_member_email'] = email
                if next_url.startswith('/'):
                    return redirect(next_url)
                return redirect(url_for('legacy_portal'))
            return render_template(
                'legacy_portal.html',
                **portal_context(error=error, next_url=next_url, active_tab='register'),
            )

        if action == 'login':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            if verify_legacy_login(email, password):
                session['legacy_member_email'] = email
                if next_url.startswith('/'):
                    return redirect(next_url)
                return redirect(url_for('legacy_portal'))
            return render_template(
                'legacy_portal.html',
                **portal_context(
                    error='Invalid email or password.',
                    next_url=next_url,
                    active_tab='login',
                ),
            )

        if action == 'forgot_password':
            logged_in_member = get_logged_in_member()
            email = request.form.get('email', '').strip().lower()
            if logged_in_member:
                email = logged_in_member['email']
            sent = False
            member = get_legacy_member(email)
            if not member:
                print(f"Password reset skipped; no member account for {email}")
            else:
                token = set_password_reset_token(email)
                if not token:
                    print(f"Password reset token not saved for {email}")
                else:
                    reset_url = (
                        f"{get_public_base_url()}/reset-password?"
                        f"{urlencode({'email': email, 'token': token})}"
                    )
                    sent = deliver_password_reset_email(email, token, reset_url=reset_url)
                    print(f"Password reset delivery for {email}: sent={sent}")

            if logged_in_member:
                if sent:
                    success_msg = (
                        f'We sent a password reset link to {email}. '
                        'Check your inbox and spam folder.'
                    )
                else:
                    success_msg = (
                        'We could not send the reset email right now. '
                        'Please try again in a few minutes.'
                    )
                active_tab = 'login'
            else:
                success_msg = (
                    'If an account exists for that email, we sent a password reset link. '
                    'Check your inbox and spam folder.'
                )
                active_tab = 'forgot'

            return render_template(
                'legacy_portal.html',
                **portal_context(
                    success=success_msg,
                    next_url=next_url,
                    active_tab=active_tab,
                ),
            )

        if action == 'logout':
            session.pop('legacy_member_email', None)
            return redirect(url_for('legacy_portal'))

        if action == 'save_ticket' and member:
            ticket_id = request.form.get('ticket_id', '')
            record = get_ticket_record(ticket_id)
            if record:
                add_saved_ticket_for_member(member['email'], ticket_id)
                refreshed = get_legacy_member(member['email'])
                if refreshed and member_has_past_purchases(refreshed):
                    ensure_member_discount_code(refreshed)
            return redirect(url_for('legacy_portal'))

        if action == 'remove_ticket' and member:
            ticket_id = request.form.get('ticket_id', '')
            remove_saved_ticket_for_member(member['email'], ticket_id)
            return redirect(url_for('legacy_portal'))

    return render_template('legacy_portal.html', **portal_context(next_url=next_url))


@app.route('/admin')
def admin_dashboard():
    if not require_admin():
        return 'Unauthorized', 401

    tickets = sorted(load_tickets(), key=lambda t: t.get('purchased_at', ''), reverse=True)
    total_admissions = sum(ticket.get('quantity', 0) for ticket in tickets)
    return render_template(
        'admin.html',
        tickets=tickets,
        tickets_json=json.dumps(tickets, indent=2),
        total_admissions=total_admissions,
        key=request.args.get('key'),
    )


@app.route('/admin/tickets.csv')
def download_tickets_csv():
    if not require_admin():
        return 'Unauthorized', 401

    tickets = load_tickets()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'purchased_at', 'ticket_id', 'email', 'quantity', 'ticket_type', 'access',
        'legacy_discount', 'scanned_at', 'email_sent_at', 'verify_url',
    ])
    for ticket in tickets:
        writer.writerow([
            ticket.get('purchased_at', ''),
            ticket.get('ticket_id', ''),
            ticket.get('email', ''),
            ticket.get('quantity', ''),
            ticket.get('ticket_type', 'general'),
            ticket.get('access', ''),
            ticket.get('legacy_discount', False),
            ticket.get('scanned_at', ''),
            ticket.get('email_sent_at', ''),
            ticket.get('verify_url', ''),
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=thesection-tickets.csv'},
    )


@app.route('/admin/tickets.json')
def download_tickets_json():
    if not require_admin():
        return 'Unauthorized', 401

    return Response(
        json.dumps(load_tickets(), indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=thesection-tickets.json'},
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
