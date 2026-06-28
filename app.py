from flask import Flask, render_template, request, jsonify, Response
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
from datetime import datetime, timezone

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')

base_url = os.getenv('BASE_URL', 'http://10.0.0.199:5000')
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = os.getenv('ADMIN_KEY', 'section2024')
tickets_lock = threading.Lock()

# Stripe
stripe.api_key = "sk_test_51TmPE8GVxxcKcZp9PetRAcpNnLTlSqR3Xfa9h1SZyrtgGdzD09M2WC3QNCOfGhaSQJR0vmrSUYI8WGmHovmSy29u00JF8TStpp"   # Your "The Section" key

# Email Config (Gmail example)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'jaideharkness99@gmail.com'      # ← CHANGE
app.config['MAIL_PASSWORD'] = 'bjfanolwhzkjieyz'         # ← CHANGE
app.config['MAIL_TIMEOUT'] = 10
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


def record_ticket(session_id, ticket_id, email, quantity):
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
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': None,
            'verify_url': f"{base_url}/verify/t/{ticket_id}",
        }
        tickets.append(ticket)
        save_tickets(tickets)
        return ticket


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


def build_qr_image(ticket_id):
    qr_payload = f"{base_url}/verify/t/{ticket_id}"
    qr = qrcode.QRCode(version=1, box_size=12, border=4)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()


init_used_tickets()


def extract_ticket_id_from_url(raw):
    for marker in ('/verify/t/', '/t/'):
        if marker in raw:
            ticket_id = raw.split(marker)[-1].split('?')[0].split('/')[0].strip()
            return normalize_ticket_id(ticket_id)
    return None


def check_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return {'status': 'invalid', 'ticket_id': ticket_id or None, 'quantity': 0}

    record = get_ticket_record(normalized)
    if not record:
        return {'status': 'invalid', 'ticket_id': normalized, 'quantity': 0}

    quantity = int(record.get('quantity') or 1)
    display_id = record.get('ticket_id', normalized)

    if normalized in used_tickets or record.get('scanned_at'):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity}

    used_tickets.add(normalized)
    mark_ticket_scanned(normalized)
    return {'status': 'accepted', 'ticket_id': display_id, 'quantity': quantity}


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


def send_ticket_email(customer_email, ticket_id, quantity, ticket_data):
    with app.app_context():
        try:
            msg = Message("Your The Section Tickets 🎟️",
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[customer_email])
            msg.body = f"Ticket ID: {ticket_id}\nQuantity: {quantity}\n\nShow this QR at the door."
            msg.attach("ticket-qr.png", "image/png", base64.b64decode(ticket_data))
            mail.send(msg)
        except Exception as e:
            print("Email failed (non-fatal):", str(e))

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 1))
        print(f"Creating session for {quantity} tickets")

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'The Section - General Admission',
                        'description': 'October 24th • 10PM - 2AM @ The Gem',
                    },
                    'unit_amount': 1000,
                },
                'quantity': quantity,
            }],
            mode='payment',
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
        session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])

        existing_ticket = get_ticket_by_session(session_id)
        if existing_ticket:
            ticket_id = existing_ticket['ticket_id']
            quantity = existing_ticket['quantity']
            customer_email = existing_ticket.get('email')
        else:
            customer_email = None
            quantity = 1
            ticket_id = uuid.uuid4().hex[:12].upper()

            if session.customer_details:
                customer_email = session.customer_details.email

            if session.line_items and session.line_items.data:
                quantity = session.line_items.data[0].quantity

            record_ticket(session_id, ticket_id, customer_email, quantity)

        ticket_data = build_qr_image(ticket_id)

        if customer_email and not existing_ticket:
            threading.Thread(
                target=send_ticket_email,
                args=(customer_email, ticket_id, quantity, ticket_data),
                daemon=True,
            ).start()

        return render_template('success.html',
                               email=customer_email,
                               ticket_data=ticket_data,
                               ticket_id=ticket_id,
                               quantity=quantity)

    except Exception as e:
        print("SUCCESS ROUTE CRASH:", str(e))
        return render_template('success.html', error=str(e))

@app.route('/t/<ticket_id>')
def show_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return render_template('success.html', error="Invalid ticket"), 404
    return render_template('ticket.html', ticket_id=normalized)


@app.route('/verify/t/<ticket_id>')
def verify_ticket_native(ticket_id):
    result = check_ticket(ticket_id)
    return render_template('verify_result.html', **result)


@app.route('/verify', methods=['GET', 'POST'])
def verify_ticket():
    if request.method == 'POST':
        ticket_data = request.form.get('ticket_data') or request.json.get('ticket_data') if request.is_json else None
        ticket_id = parse_scanned_ticket(ticket_data)
        if not ticket_id:
            return "Invalid ticket"

        result = check_ticket(ticket_id)
        if result['status'] == 'accepted':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            return f"✅ {qty} {guest_word} admitted — Welcome to The Section!"
        if result['status'] == 'used':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            return f"❌ Already used ({qty} {guest_word})"
        return "Invalid ticket"

    return render_template('verify.html')


@app.route('/admin')
def admin_dashboard():
    if not require_admin():
        return 'Unauthorized', 401

    tickets = sorted(load_tickets(), key=lambda t: t.get('purchased_at', ''), reverse=True)
    total_admissions = sum(ticket.get('quantity', 0) for ticket in tickets)
    return render_template(
        'admin.html',
        tickets=tickets,
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
    writer.writerow(['purchased_at', 'ticket_id', 'email', 'quantity', 'scanned_at', 'verify_url'])
    for ticket in tickets:
        writer.writerow([
            ticket.get('purchased_at', ''),
            ticket.get('ticket_id', ''),
            ticket.get('email', ''),
            ticket.get('quantity', ''),
            ticket.get('scanned_at', ''),
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
