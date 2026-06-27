from flask import Flask, render_template, request, jsonify
import stripe
import qrcode
from io import BytesIO
import base64
import uuid
from flask_mail import Mail, Message   # ← Add this
import os

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
mail = Mail(app)

# In-memory used tickets
used_tickets = set()

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
            success_url="https://thesection.onrender.com/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://thesection.onrender.com/",
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
        return render_template('success.html', error="No session ID")

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])

        customer_email = session.customer_details.email if session.customer_details else None
        quantity = session.line_items.data[0].quantity if session.line_items and session.line_items.data else 1

        ticket_id = str(uuid.uuid4())[:12].upper()

        ticket_info = {"ticket_id": ticket_id, "event": "The Section Oct 24", "quantity": quantity}

        # QR Code
        qr = qrcode.QRCode(version=2, box_size=12, border=6)
        qr.add_data(str(ticket_info))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        ticket_data = base64.b64encode(buffered.getvalue()).decode()

        # Email (try but don't crash if fails)
        if customer_email:
            try:
                msg = Message("Your The Section Tickets",
                              sender=app.config['MAIL_USERNAME'],
                              recipients=[customer_email])
                msg.body = f"Ticket ID: {ticket_id}\nQuantity: {quantity}"
                msg.attach("ticket.png", "image/png", base64.b64decode(ticket_data))
                mail.send(msg)
            except:
                print("Email sending failed")

        return render_template('success.html',
                               email=customer_email,
                               ticket_data=ticket_data,
                               ticket_id=ticket_id,
                               quantity=quantity)

    except Exception as e:
        print("Success error:", str(e))
        return f"Error: {str(e)}", 500
@app.route('/verify', methods=['GET', 'POST'])
def verify_ticket():
    if request.method == 'POST':
        ticket_data = request.form.get('ticket_data') or request.json.get('ticket_data') if request.is_json else None
        if ticket_data:
            if ticket_data in used_tickets:
                return "❌ Ticket already used!"
            else:
                used_tickets.add(ticket_data)
                return "✅ Ticket Accepted! Welcome to The Section."
        return "Invalid ticket"

    return render_template('verify.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)