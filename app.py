from flask import Flask, render_template, request, jsonify
import stripe
import qrcode
from io import BytesIO
import base64
import uuid
from flask_mail import Mail, Message
import os
import threading
import json
import ast

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')

base_url = os.getenv('BASE_URL', 'http://10.0.0.199:5000')

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


def parse_scanned_ticket(raw):
    if not raw:
        return None

    raw = raw.strip()

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

    if raw.isalnum():
        return raw.upper()

    return None


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

        customer_email = None
        quantity = 1
        ticket_data = None
        ticket_id = str(uuid.uuid4())[:12].upper()

        if session.customer_details:
            customer_email = session.customer_details.email

        if session.line_items and session.line_items.data:
            quantity = session.line_items.data[0].quantity

        qr_payload = json.dumps({
            "ticket_id": ticket_id,
            "event": "The Section Oct 24",
            "quantity": quantity,
        }, separators=(',', ':'))

        # Generate QR — compact JSON scans much more reliably on phone screens
        qr = qrcode.QRCode(version=1, box_size=12, border=4)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        ticket_data = base64.b64encode(buffered.getvalue()).decode()

        if customer_email:
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

@app.route('/verify', methods=['GET', 'POST'])
def verify_ticket():
    if request.method == 'POST':
        ticket_data = request.form.get('ticket_data') or request.json.get('ticket_data') if request.is_json else None
        ticket_id = parse_scanned_ticket(ticket_data)
        if ticket_id:
            if ticket_id in used_tickets:
                return "❌ Ticket already used!"
            used_tickets.add(ticket_id)
            return "✅ Ticket Accepted! Welcome to The Section."
        return "Invalid ticket"

    return render_template('verify.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
