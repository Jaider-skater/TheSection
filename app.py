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
vip_bundle_min = int(os.getenv('VIP_BUNDLE_MIN', '5'))
vip_bundle_total_cents = int(os.getenv('VIP_BUNDLE_TOTAL_CENTS', '10000'))
vip_additional_discount = parse_discount_value(os.getenv('VIP_ADDITIONAL_DISCOUNT', '0.20'))
member_discount = parse_discount_value(os.getenv('MEMBER_DISCOUNT', '0.10'))
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
