from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
import stripe
import qrcode
from io import BytesIO, StringIO
import base64
import uuid
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
from datetime import datetime, timezone

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')

base_url = os.getenv('BASE_URL', 'http://10.0.0.199:5000')
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = os.getenv('ADMIN_KEY', 'section2024')
wallet_team_id = os.getenv('WALLET_TEAM_ID', '')
wallet_pass_type_id = os.getenv('WALLET_PASS_TYPE_ID', 'pass.com.thesection.ticket')
wallet_cert_path = os.getenv('WALLET_CERT_PATH', '')
wallet_key_path = os.getenv('WALLET_KEY_PATH', '')
wallet_wwdr_path = os.getenv('WALLET_WWDR_PATH', '')
wallet_enabled = all([wallet_team_id, wallet_cert_path, wallet_key_path, wallet_wwdr_path])
members_file = os.getenv('MEMBERS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'legacy_members.json'))
def parse_discount_value(raw, default=0.15):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value > 1:
        return value / 100.0
    return value


bundle_min = int(os.getenv('BUNDLE_MIN') or os.getenv('LEGACY_BUNDLE_MIN', '4'))
bundle_discount = parse_discount_value(
    os.getenv('BUNDLE_DISCOUNT') or os.getenv('LEGACY_BUNDLE_DISCOUNT', '0.25'),
)
vip_discount = parse_discount_value(os.getenv('VIP_DISCOUNT', '0.10'))
verify_login_email = os.getenv('VERIFY_LOGIN_EMAIL', '').strip().lower()
verify_login_password = os.getenv('VERIFY_LOGIN_PASSWORD', '')
app.secret_key = os.getenv('SECRET_KEY', 'thesection-legacy-portal-change-me')
tickets_lock = threading.Lock()
members_lock = threading.Lock()

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
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'jaideharkness99@gmail.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'bjfanolwhzkjieyz')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])
app.config['MAIL_TIMEOUT'] = int(os.getenv('MAIL_TIMEOUT', '10'))
mail = Mail(app)

# In-memory used tickets
used_tickets = set()


def load_tickets():
    os.makedirs(os.path.dirname(tickets_file), exist_ok=True)
    if not os.path.exists(tickets_file):
        return []
    with open(tickets_file, encoding='utf-8') as f:
        return json.load(f)


def save_tickets(tickets):
    os.makedirs(os.path.dirname(tickets_file), exist_ok=True)
    with open(tickets_file, 'w', encoding='utf-8') as f:
        json.dump(tickets, f, indent=2)


def get_ticket_by_session(session_id):
    for ticket in load_tickets():
        if ticket.get('session_id') == session_id:
            return ticket
    return None


def record_ticket(session_id, ticket_id, email, quantity, ticket_type='general', legacy_discount=False):
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
            'view_url': f"{base_url}/ticket/{ticket_id}",
        }
        tickets.append(ticket)
        save_tickets(tickets)
        return ticket


def hash_member_code(code):
    return hashlib.sha256(code.strip().upper().encode('utf-8')).hexdigest()


def load_members():
    os.makedirs(os.path.dirname(members_file), exist_ok=True)
    if not os.path.exists(members_file):
        return []
    with open(members_file, encoding='utf-8') as f:
        data = json.load(f)
        return data if isinstance(data, list) else data.get('members', [])


def save_members(members):
    os.makedirs(os.path.dirname(members_file), exist_ok=True)
    with open(members_file, 'w', encoding='utf-8') as f:
        json.dump(members, f, indent=2)


def bootstrap_legacy_members():
    bootstrap_email = os.getenv('LEGACY_BOOTSTRAP_EMAIL', '').strip().lower()
    bootstrap_code = os.getenv('LEGACY_BOOTSTRAP_CODE', '').strip()
    if not bootstrap_email or not bootstrap_code:
        return
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == bootstrap_email:
                return
        members.append({
            'email': bootstrap_email,
            'code_hash': hash_member_code(bootstrap_code),
            'saved_tickets': [],
            'joined_at': datetime.now(timezone.utc).isoformat(),
        })
        save_members(members)


def get_legacy_member(email):
    if not email:
        return None
    normalized = email.strip().lower()
    for member in load_members():
        if member.get('email', '').lower() == normalized:
            return member
    return None


def verify_legacy_login(email, code):
    member = get_legacy_member(email)
    if not member:
        return False
    return member.get('code_hash') == hash_member_code(code)


def is_legacy_member_logged_in():
    email = session.get('legacy_member_email')
    return bool(email and get_legacy_member(email))


def get_logged_in_member():
    email = session.get('legacy_member_email')
    if not email:
        return None
    return get_legacy_member(email)


def bundle_discount_applies(quantity):
    return quantity >= bundle_min


def calculate_unit_price(ticket_type, quantity):
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    if not bundle_discount_applies(quantity):
        return base
    if ticket_type == 'vip' and vip_discount > 0:
        return int(base * (1 - vip_discount))
    if ticket_type == 'general':
        return int(base * (1 - bundle_discount))
    return base


def pricing_breakdown(ticket_type, quantity):
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    unit_price = calculate_unit_price(ticket_type, quantity)
    at_bundle_min = bundle_discount_applies(quantity)
    vip_applied = ticket_type == 'vip' and at_bundle_min and unit_price < base
    bundle_applied = ticket_type == 'general' and at_bundle_min and unit_price < base
    return {
        'base_unit_price_cents': base,
        'unit_price_cents': unit_price,
        'vip_discount_applied': vip_applied,
        'bundle_discount_applied': bundle_applied,
        'vip_discount_percent': int(vip_discount * 100),
        'bundle_discount_percent': int(bundle_discount * 100),
        'bundle_min': bundle_min,
    }


def verify_auth_enabled():
    return bool(verify_login_email and verify_login_password)


def is_verify_authenticated():
    return session.get('verify_authenticated') is True


def ensure_verify_access():
    if not verify_auth_enabled() or is_verify_authenticated():
        return None
    return redirect(url_for('verify_login', next=request.path))


def lookup_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return None
    record = get_ticket_record(normalized)
    if not record:
        return None
    quantity = int(record.get('quantity') or 1)
    display_id = record.get('ticket_id', normalized)
    meta = ticket_result_meta(record)
    scanned = bool(normalized in used_tickets or record.get('scanned_at'))
    return {
        'ticket_id': display_id,
        'quantity': quantity,
        'scanned': scanned,
        'email': record.get('email'),
        'purchased_at': record.get('purchased_at'),
        **meta,
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
        return
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if normalize_ticket_id(ticket.get('ticket_id')) == normalized:
                ticket['scanned_at'] = datetime.now(timezone.utc).isoformat()
                save_tickets(tickets)
                return


def init_used_tickets():
    for ticket in load_tickets():
        if ticket.get('scanned_at'):
            normalized = normalize_ticket_id(ticket.get('ticket_id'))
            if normalized:
                used_tickets.add(normalized)


def require_admin():
    return request.args.get('key') == admin_key


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


init_used_tickets()
bootstrap_legacy_members()


def extract_ticket_id_from_url(raw):
    for marker in ('/verify/t/', '/t/'):
        if marker in raw:
            ticket_id = raw.split(marker)[-1].split('?')[0].split('/')[0].strip()
            return normalize_ticket_id(ticket_id)
    return None


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

    if normalized in used_tickets or record.get('scanned_at'):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    used_tickets.add(normalized)
    mark_ticket_scanned(normalized)
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
            return str(data['ticket_id']).upper()
    except json.JSONDecodeError:
        pass

    try:
        data = ast.literal_eval(raw)
        if isinstance(data, dict) and data.get('ticket_id'):
            return str(data['ticket_id']).upper()
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
    verify_url = f"{base_url}/verify/t/{ticket_id}"
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
                f"Or open this link on your phone:\n{verify_url}\n"
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

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/api/member-status')
def member_status():
    legacy_member = is_legacy_member_logged_in()
    return jsonify({
        'logged_in': legacy_member,
        'email': session.get('legacy_member_email'),
        'bundle_min': bundle_min,
        'bundle_discount_percent': int(bundle_discount * 100),
        'vip_discount_percent': int(vip_discount * 100),
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
    breakdown = pricing_breakdown(ticket_type, quantity)
    unit_price = breakdown['unit_price_cents']
    base_price = breakdown['base_unit_price_cents']
    discount_applied = unit_price < base_price
    return jsonify({
        'ticket_type': ticket_type,
        'quantity': quantity,
        'unit_price_cents': unit_price,
        'total_cents': unit_price * quantity,
        'base_total_cents': base_price * quantity,
        'base_unit_price_cents': base_price,
        'bundle_discount_applied': breakdown['bundle_discount_applied'],
        'vip_discount_applied': breakdown['vip_discount_applied'],
        'legacy_discount_applied': discount_applied,
        'bundle_min': breakdown['bundle_min'],
        'bundle_discount_percent': breakdown['bundle_discount_percent'],
        'vip_discount_percent': breakdown['vip_discount_percent'],
    })


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.get_json()
        quantity = max(1, int(data.get('quantity', 1)))
        ticket_type = data.get('ticket_type', 'general')
        if ticket_type not in TICKET_TYPES:
            ticket_type = 'general'

        legacy_member = is_legacy_member_logged_in()
        breakdown = pricing_breakdown(ticket_type, quantity)
        unit_price = breakdown['unit_price_cents']
        ticket_meta = TICKET_TYPES[ticket_type]
        description = ticket_meta['description']
        if breakdown['vip_discount_applied']:
            description += f' · {breakdown["vip_discount_percent"]}% VIP discount'
        elif breakdown['bundle_discount_applied']:
            description += f' · {breakdown["bundle_discount_percent"]}% bundle discount ({quantity}+ tickets)'

        print(f"Creating {ticket_type} session for {quantity} tickets @ {unit_price}c")

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
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
            mode='payment',
            metadata={
                'ticket_type': ticket_type,
                'legacy_member': 'true' if legacy_member else 'false',
                'legacy_discount': 'true' if bundle_discount_applies(quantity) else 'false',
            },
            success_url=f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/",
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

        existing_ticket = get_ticket_by_session(session_id)
        if existing_ticket:
            ticket_id = existing_ticket['ticket_id']
            quantity = existing_ticket['quantity']
            customer_email = existing_ticket.get('email')
            ticket_type = existing_ticket.get('ticket_type', 'general')
            access = existing_ticket.get('access')
        else:
            customer_email = None
            quantity = 1
            ticket_id = uuid.uuid4().hex[:12].upper()
            metadata = checkout_session.metadata or {}
            ticket_type = metadata.get('ticket_type', 'general')
            if ticket_type not in TICKET_TYPES:
                ticket_type = 'general'
            legacy_discount = metadata.get('legacy_discount') == 'true'

            if checkout_session.customer_details:
                customer_email = checkout_session.customer_details.email

            if checkout_session.line_items and checkout_session.line_items.data:
                quantity = checkout_session.line_items.data[0].quantity

            record_ticket(
                session_id, ticket_id, customer_email, quantity,
                ticket_type=ticket_type, legacy_discount=legacy_discount,
            )
            access = TICKET_TYPES[ticket_type].get('access')

            member_email = session.get('legacy_member_email')
            if member_email:
                add_saved_ticket_for_member(member_email, ticket_id)

        ticket_data = build_qr_image(ticket_id)

        email_sent = deliver_ticket_email(
            session_id, customer_email, ticket_id, quantity, ticket_data, ticket_type, access
        )

        return render_template('success.html',
                               email=customer_email,
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
    return redirect(url_for('view_ticket', ticket_id=ticket_id))


@app.route('/ticket/<ticket_id>')
def view_ticket(ticket_id):
    ticket = lookup_ticket(ticket_id)
    if not ticket:
        return render_template('ticket_view.html', error='Ticket not found'), 404
    ticket_data = build_qr_image(ticket['ticket_id'])
    return render_template('ticket_view.html', ticket=ticket, ticket_data=ticket_data)


@app.route('/verify/login', methods=['GET', 'POST'])
def verify_login():
    if not verify_auth_enabled():
        return redirect(url_for('verify_ticket'))
    if is_verify_authenticated():
        return redirect(request.args.get('next') or url_for('verify_ticket'))

    error = None
    next_url = request.args.get('next', '')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        next_url = request.form.get('next') or next_url
        if email == verify_login_email and password == verify_login_password:
            session['verify_authenticated'] = True
            return redirect(next_url or url_for('verify_ticket'))
        error = 'Invalid email or password.'

    return render_template('verify_login.html', error=error, next_url=next_url)


@app.route('/verify/logout')
def verify_logout():
    session.pop('verify_authenticated', None)
    return redirect(url_for('verify_login'))


@app.route('/verify/t/<ticket_id>')
def verify_ticket_native(ticket_id):
    redirect_resp = ensure_verify_access()
    if redirect_resp:
        return redirect_resp
    result = check_ticket(ticket_id)
    return render_template('verify_result.html', **result)


@app.route('/verify', methods=['GET', 'POST'])
def verify_ticket():
    if request.method == 'POST':
        if verify_auth_enabled() and not is_verify_authenticated():
            return jsonify({'error': 'Unauthorized'}), 401
        ticket_data = request.form.get('ticket_data') or request.json.get('ticket_data') if request.is_json else None
        ticket_id = parse_scanned_ticket(ticket_data)
        if not ticket_id:
            return "Invalid ticket"

        result = check_ticket(ticket_id)
        if request.is_json:
            return jsonify(result)
        if result['status'] == 'accepted':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            type_label = 'VIP' if result.get('is_vip') else 'GA'
            return f"✅ {type_label} — {qty} {guest_word} admitted"
        if result['status'] == 'used':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            return f"❌ Already used ({qty} {guest_word})"
        return "Invalid ticket"

    redirect_resp = ensure_verify_access()
    if redirect_resp:
        return redirect_resp
    return render_template('verify.html', verify_auth_enabled=verify_auth_enabled())


@app.route('/legacy', methods=['GET', 'POST'])
def legacy_portal():
    member = get_logged_in_member()
    saved_ticket_details = []
    if member:
        for ticket_id in member.get('saved_tickets', []):
            record = get_ticket_record(ticket_id)
            saved_ticket_details.append({
                'ticket_id': ticket_id,
                'record': record,
            })

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'login':
            email = request.form.get('email', '').strip().lower()
            code = request.form.get('code', '').strip()
            if verify_legacy_login(email, code):
                session['legacy_member_email'] = email
                return redirect(url_for('legacy_portal'))
            return render_template(
                'legacy_portal.html',
                error='Invalid email or member code.',
                member=None,
                saved_ticket_details=[],
                bundle_min=bundle_min,
                bundle_discount_percent=int(bundle_discount * 100),
            )
        if action == 'logout':
            session.pop('legacy_member_email', None)
            return redirect(url_for('legacy_portal'))
        if action == 'save_ticket' and member:
            ticket_id = request.form.get('ticket_id', '')
            record = get_ticket_record(ticket_id)
            if record:
                add_saved_ticket_for_member(member['email'], ticket_id)
            return redirect(url_for('legacy_portal'))
        if action == 'remove_ticket' and member:
            ticket_id = request.form.get('ticket_id', '')
            remove_saved_ticket_for_member(member['email'], ticket_id)
            return redirect(url_for('legacy_portal'))

    return render_template(
        'legacy_portal.html',
        member=get_logged_in_member(),
        saved_ticket_details=saved_ticket_details if member else [],
        bundle_min=bundle_min,
        bundle_discount_percent=int(bundle_discount * 100),
        ticket_types=TICKET_TYPES,
    )


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
