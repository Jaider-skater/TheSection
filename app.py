from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for, g, abort, send_from_directory, has_request_context
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import stripe
import qrcode
from io import BytesIO, StringIO
import base64
import secrets
from flask_mail import Mail, Message
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import os
import re
import html
import threading
from contextlib import contextmanager
import json
import ast
import csv
import hashlib
import zipfile
import subprocess
import tempfile
import time
try:
    import fcntl
except ImportError:  # Windows
    class fcntl:
        LOCK_SH = 1
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(_fd, _op):
            return None
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
from urllib.parse import urlencode

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')
# Trust Render/proxy HTTPS headers so secure cookies work correctly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

PRODUCTION_BASE_URL = 'https://thesection.onrender.com'
WEAK_DEFAULT_SECRETS = {
    'SECRET_KEY': 'thesection-legacy-portal-change-me',
    'ADMIN_KEY': 'section2024',
}


def is_production_env():
    if (os.getenv('FLASK_ENV') or '').strip().lower() == 'production':
        return True
    if (os.getenv('ENV') or '').strip().lower() == 'production':
        return True
    if (os.getenv('RENDER') or '').strip().lower() in ('1', 'true', 'yes'):
        return True
    base = (os.getenv('BASE_URL') or '').strip().lower()
    return base.startswith('https://') and 'localhost' not in base and '127.0.0.1' not in base


IS_PRODUCTION = is_production_env()


def require_env_secret(name, *, min_length=16, allow_dev_generate=False):
    value = (os.getenv(name) or '').strip()
    weak = WEAK_DEFAULT_SECRETS.get(name)
    if value and weak and value == weak:
        value = ''
    if value:
        if len(value) < min_length and IS_PRODUCTION:
            raise RuntimeError(f'{name} must be at least {min_length} characters in production')
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f'{name} must be set via environment in production')
    if allow_dev_generate:
        generated = secrets.token_hex(32)
        print(f'WARNING: {name} not set; using ephemeral dev secret for this process')
        return generated
    fallback = weak or secrets.token_hex(32)
    print(f'WARNING: {name} not set; using insecure local-dev fallback')
    return fallback


def get_public_base_url():
    configured = (os.getenv('BASE_URL') or '').strip().rstrip('/')
    if configured and 'localhost' not in configured and '127.0.0.1' not in configured:
        if not configured.startswith('http://10.') and not configured.startswith('http://192.168.'):
            return configured
    return PRODUCTION_BASE_URL


base_url = get_public_base_url()
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = require_env_secret('ADMIN_KEY', min_length=12, allow_dev_generate=False)
verify_login_email = os.getenv('VERIFY_LOGIN_EMAIL', '').strip().lower()
verify_login_emails = {
    part.strip().lower()
    for part in verify_login_email.replace(';', ',').split(',')
    if part.strip()
}
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
invites_file = os.getenv(
    'INVITES_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'member_invites.json'),
)
full_mailing_list_file = os.getenv(
    'FULL_MAILING_LIST_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'full_mailing_list.json'),
)
mailing_list_log_file = os.getenv(
    'MAILING_LIST_LOG_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'mailing_list_log.json'),
)
exclusive_holds_file = os.getenv(
    'EXCLUSIVE_HOLDS_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'exclusive_holds.json'),
)
costumes_file = os.getenv(
    'COSTUMES_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'costumes.json'),
)
events_file = os.getenv(
    'EVENTS_FILE',
    os.path.join(os.path.dirname(__file__), 'data', 'events.json'),
)
flyers_dir = os.getenv(
    'FLYERS_DIR',
    os.path.join(os.path.dirname(__file__), 'data', 'flyers'),
)
costume_photos_dir = os.getenv(
    'COSTUME_PHOTOS_DIR',
    os.path.join(os.path.dirname(__file__), 'data', 'costume_photos'),
)
INVITE_EXPIRY_DAYS = int(os.getenv('INVITE_EXPIRY_DAYS', '14'))
PROTECTED_MAILING_LIST_EMAILS = frozenset({
    'hallieworkshop@gmail.com',
    'thesectionevents@gmail.com',
})
APP_TIMEZONE = os.getenv('APP_TIMEZONE', 'America/Los_Angeles')
stripe_webhook_secret = (os.getenv('STRIPE_WEBHOOK_SECRET') or '').strip()


def parse_discount_value(raw, default=0.15):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value > 1:
        value = value / 100.0
    if value < 0:
        return default
    # Never allow 100%+ off (free or negative tickets) via a bad env value.
    return min(value, 0.90)


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
returning_guest_discount = parse_discount_value(
    os.getenv('RETURNING_GUEST_DISCOUNT', '0.20'),
    0.20,
)
app.secret_key = require_env_secret('SECRET_KEY', min_length=24, allow_dev_generate=True)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # Lax keeps the cookie on return from Stripe Checkout (top-level navigation / back).
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    # Permanent cookies so login survives leaving to Stripe and hitting Back.
    PERMANENT_SESSION_LIFETIME=timedelta(days=31),
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_NAME='thesection_session',
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)
MEMBER_LOGIN_COOKIE = 'thesection_member'
MEMBER_LOGIN_SALT = 'thesection-member-login'
MEMBER_LOGIN_MAX_AGE = int(timedelta(days=31).total_seconds())
SCANNER_LOGIN_COOKIE = 'thesection_scanner'
SCANNER_LOGIN_SALT = 'thesection-scanner-login'
SCANNER_LOGIN_MAX_AGE = MEMBER_LOGIN_MAX_AGE
tickets_lock = threading.Lock()
members_lock = threading.Lock()
scanner_settings_lock = threading.Lock()
events_lock = threading.Lock()
invites_lock = threading.Lock()
full_list_lock = threading.Lock()
mailing_list_log_lock = threading.Lock()
_mailing_send_lock = threading.Lock()
exclusive_holds_lock = threading.Lock()
costumes_lock = threading.Lock()
_presence_lock = threading.Lock()
_presence_seen = {}
PRESENCE_TTL_SECONDS = 90
PRESENCE_MAX_IDS = 5000
VISITOR_ID_RE = re.compile(r'^[a-f0-9]{16,64}$')
VISITOR_COOKIE = 'thesection_vid'


_broadcast_continue_thread = None
_broadcast_delivery_lock = threading.Lock()
_inflight_broadcasts = set()
_delivered_broadcasts = set()


def mailing_send_is_busy():
    thread = _broadcast_continue_thread
    return bool(thread is not None and thread.is_alive())


@contextmanager
def mailing_send_guard():
    """Only one broadcast/invite send at a time so a double-click cannot resend."""
    if mailing_send_is_busy():
        yield False
        return
    acquired = _mailing_send_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _mailing_send_lock.release()


def wait_for_broadcast_background(timeout=8):
    """Test helper: wait until a leftover broadcast thread finishes."""
    thread = _broadcast_continue_thread
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


_rate_limit_lock = threading.Lock()
_rate_limit_buckets = {}
EXCLUSIVE_HOLD_TTL = timedelta(minutes=45)
EMAIL_RE = re.compile(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', re.IGNORECASE)

HALLOWEEN_EVENT_SLUG = 'halloween-2026'
HALLOWEEN_EVENT_DATE = '2026-10-24'
# Local calendar date: purchases before this day are void at the door and omitted from Ticket Sales.
TICKET_VALIDITY_CUTOFF_DATE = os.getenv('TICKET_VALIDITY_CUTOFF', '2026-09-08').strip() or '2026-09-08'
TICKET_VALIDITY_RESET_TOKEN = 'void-before-2026-09-08-1'

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


def door_surcharge_cents():
    raw = (os.getenv('DOOR_SURCHARGE_CENTS') or '500').strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 500
    return value if value >= 0 else 500


def door_unit_price_cents(ticket_type):
    """Walk-up price: posted online rate plus the door surcharge."""
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    return base + door_surcharge_cents()

# Stripe — never hardcode keys; set STRIPE_SECRET_KEY in the environment
stripe.api_key = (os.getenv('STRIPE_SECRET_KEY') or os.getenv('STRIPE_API_KEY') or '').strip()
stripe_publishable_key = (
    os.getenv('STRIPE_PUBLISHABLE_KEY') or os.getenv('STRIPE_PUBLIC_KEY') or ''
).strip()
if not stripe.api_key:
    if IS_PRODUCTION:
        raise RuntimeError('STRIPE_SECRET_KEY must be set in production')
    print('WARNING: STRIPE_SECRET_KEY is not set; checkout will fail until configured')
if stripe.api_key and not stripe_publishable_key:
    print('WARNING: STRIPE_PUBLISHABLE_KEY is not set; door tap-to-pay will not load')

# Email Config (set MAIL_* env vars on Render)
mail_username = (os.getenv('MAIL_USERNAME') or '').strip()
mail_sender = (os.getenv('MAIL_DEFAULT_SENDER') or mail_username).strip() or mail_username
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = mail_username
app.config['MAIL_PASSWORD'] = (os.getenv('MAIL_PASSWORD') or '').strip()
app.config['MAIL_DEFAULT_SENDER'] = mail_sender
app.config['MAIL_TIMEOUT'] = int(os.getenv('MAIL_TIMEOUT', '10'))
mail = Mail(app)


def _prune_presence(now=None):
    now = time.time() if now is None else now
    cutoff = now - PRESENCE_TTL_SECONDS
    stale = [vid for vid, ts in _presence_seen.items() if ts < cutoff]
    for vid in stale:
        _presence_seen.pop(vid, None)
    if len(_presence_seen) > PRESENCE_MAX_IDS:
        newest = sorted(_presence_seen.items(), key=lambda item: item[1], reverse=True)[:PRESENCE_MAX_IDS]
        _presence_seen.clear()
        _presence_seen.update(newest)


def bump_public_viewer(visitor_id):
    vid = (visitor_id or '').strip().lower()
    if not VISITOR_ID_RE.fullmatch(vid):
        return False
    now = time.time()
    with _presence_lock:
        _presence_seen[vid] = now
        _prune_presence(now)
    return True


def count_public_viewers():
    now = time.time()
    with _presence_lock:
        _prune_presence(now)
        return sum(1 for ts in _presence_seen.values() if now - ts <= PRESENCE_TTL_SECONDS)


def client_ip():
    forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return forwarded or (request.remote_addr or 'unknown')


def rate_limit_allow(scope, limit, window_seconds):
    """Simple process-local rate limit. Returns True if allowed."""
    key = f'{scope}:{client_ip()}'
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_buckets.get(key, [])
        bucket = [ts for ts in bucket if now - ts < window_seconds]
        if len(bucket) >= limit:
            _rate_limit_buckets[key] = bucket
            return False
        bucket.append(now)
        _rate_limit_buckets[key] = bucket
        return True


def safe_next_url(raw, default='/'):
    """Only allow same-site relative paths; block //evil.com open redirects."""
    if not raw:
        return default
    candidate = str(raw).strip()
    if not candidate.startswith('/') or candidate.startswith('//'):
        return default
    if '\\' in candidate or '://' in candidate:
        return default
    if any(c in candidate for c in ('\r', '\n', '\0')):
        return default
    return candidate


def secure_equals(a, b):
    """Constant-time string compare that tolerates different lengths."""
    left = hashlib.sha256((a if a is not None else '').encode('utf-8')).digest()
    right = hashlib.sha256((b if b is not None else '').encode('utf-8')).digest()
    return secrets.compare_digest(left, right)


def regenerate_session():
    """Mitigate session fixation by clearing prior session data on login/logout."""
    preserved = {}
    for key in ('csrf_token',):
        if key in session:
            preserved[key] = session[key]
    session.clear()
    session.update(preserved)
    session.permanent = True
    session.modified = True
    ensure_csrf_token()


def touch_auth_session():
    """Keep auth cookies permanent so Stripe Checkout + Back does not drop login."""
    if (
        session.get('legacy_member_email')
        or session.get('admin_authenticated')
        or session.get('verify_authenticated')
        or session.get('checkout_intent')
    ):
        session.permanent = True
        session.modified = True


def mark_member_session(email):
    """Set member login on the current permanent session."""
    session['legacy_member_email'] = (email or '').strip().lower()
    session.permanent = True
    session.modified = True


def _member_login_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt=MEMBER_LOGIN_SALT)


def member_login_cookie_samesite():
    # None so the cookie is sent on the top-level return from Stripe Checkout.
    # Secure is required with None; local HTTP keeps Lax.
    return 'None' if IS_PRODUCTION else 'Lax'


def set_member_login_cookie(response, email):
    normalized = (email or '').strip().lower()
    if not normalized or not response:
        return response
    try:
        token = _member_login_serializer().dumps(normalized)
    except Exception as e:
        print('Failed to sign member login cookie:', e)
        return response
    response.set_cookie(
        MEMBER_LOGIN_COOKIE,
        token,
        max_age=MEMBER_LOGIN_MAX_AGE,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite=member_login_cookie_samesite(),
        path='/',
    )
    return response


def clear_member_login_cookie(response):
    if not response:
        return response
    response.delete_cookie(
        MEMBER_LOGIN_COOKIE,
        path='/',
        samesite=member_login_cookie_samesite(),
        secure=IS_PRODUCTION,
        httponly=True,
    )
    return response


def read_member_login_cookie():
    raw = request.cookies.get(MEMBER_LOGIN_COOKIE)
    if not raw:
        return None
    try:
        email = _member_login_serializer().loads(raw, max_age=MEMBER_LOGIN_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    email = (email or '').strip().lower()
    if email and get_legacy_member(email):
        return email
    return None


def restore_member_session_from_cookie():
    """Re-attach member login if Stripe/browser dropped the Flask session cookie."""
    if session.get('legacy_member_email'):
        return
    email = read_member_login_cookie()
    if email:
        mark_member_session(email)


def _scanner_login_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt=SCANNER_LOGIN_SALT)


def set_scanner_login_cookie(response, email):
    """SameSite=None cookie so Back / Apple Pay does not drop door staff login."""
    normalized = (email or '').strip().lower()
    if not normalized or not response or not is_staff_email(normalized):
        return response
    try:
        token = _scanner_login_serializer().dumps(normalized)
    except Exception as e:
        print('Failed to sign scanner login cookie:', e)
        return response
    response.set_cookie(
        SCANNER_LOGIN_COOKIE,
        token,
        max_age=SCANNER_LOGIN_MAX_AGE,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite=member_login_cookie_samesite(),
        path='/',
    )
    return response


def clear_scanner_login_cookie(response):
    if not response:
        return response
    response.delete_cookie(
        SCANNER_LOGIN_COOKIE,
        path='/',
        samesite=member_login_cookie_samesite(),
        secure=IS_PRODUCTION,
        httponly=True,
    )
    return response


def read_scanner_login_cookie():
    raw = request.cookies.get(SCANNER_LOGIN_COOKIE)
    if not raw:
        return None
    try:
        email = _scanner_login_serializer().loads(raw, max_age=SCANNER_LOGIN_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    email = (email or '').strip().lower()
    if email and is_staff_email(email):
        return email
    return None


def restore_scanner_session_from_cookie():
    """Re-attach door staff login if the Flask session cookie was dropped."""
    if verify_scanner_session_authenticated():
        return
    email = read_scanner_login_cookie()
    if email:
        mark_scanner_session_authenticated(email)
        session['admin_authenticated'] = True
        session.permanent = True
        session.modified = True


def ensure_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_hex(32)
        session['csrf_token'] = token
    return token


def validate_csrf_token():
    expected = session.get('csrf_token')
    provided = (
        request.form.get('csrf_token')
        or request.headers.get('X-CSRF-Token')
        or (request.get_json(silent=True) or {}).get('csrf_token')
    )
    if not expected or not provided:
        return False
    try:
        return secrets.compare_digest(str(expected), str(provided))
    except (TypeError, ValueError):
        return False


def csrf_failure_response():
    if request.is_json or request.path.startswith('/api/') or request.path == '/create-checkout-session':
        return jsonify({'error': 'Invalid or missing CSRF token'}), 400
    return 'Invalid or missing CSRF token', 400


class TicketSalesError(Exception):
    """Raised when a checkout would exceed the ticket sales cap."""

    def __init__(self, message, remaining=0):
        super().__init__(message)
        self.remaining = remaining


DEFAULT_MAX_ORDER_QUANTITY = 10


def per_order_quantity_cap():
    """Hard ceiling for one checkout so nobody can buy the whole house."""
    raw = os.getenv('MAX_ORDER_QUANTITY', str(DEFAULT_MAX_ORDER_QUANTITY))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_ORDER_QUANTITY
    return value if value > 0 else DEFAULT_MAX_ORDER_QUANTITY


def max_order_quantity(event_id=None):
    """Most tickets one checkout can buy: leftover inventory, max 10 per order."""
    per_order = per_order_quantity_cap()
    remaining = ticket_sales_remaining(event_id)
    if remaining is None:
        return per_order
    return max(0, min(per_order, remaining))


def clamp_quantity(raw, default=1, event_id=None):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    value = max(1, value)
    cap = max_order_quantity(event_id)
    if cap is None:
        return value
    if cap < 1:
        return 1
    return min(cap, value)


def is_valid_email(email):
    value = (email or '').strip()
    return bool(value) and len(value) <= 254 and EMAIL_RE.match(value) is not None


def new_ticket_id():
    return secrets.token_hex(16).upper()


def new_view_token():
    return secrets.token_urlsafe(24)


def public_error_message(exc=None, fallback='Something went wrong. Please try again.'):
    if exc is not None:
        print(f'Request error: {exc}')
    return fallback


def _locked_json_read(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r+', encoding='utf-8') as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                raw = f.read()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if not raw.strip():
            return default
        data = json.loads(raw)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load JSON ({path}):', e)
        return default


def _locked_json_write(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f'{path}.tmp.{os.getpid()}.{secrets.token_hex(4)}'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        # Windows cannot replace a file that still has an open handle, and
        # flock is a no-op there anyway. POSIX keeps an exclusive lock on the
        # destination during the replace so readers see a complete file.
        if os.name == 'nt':
            os.replace(tmp_path, path)
            return True
        flags = os.O_RDWR | os.O_CREAT
        fd = os.open(path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.replace(tmp_path, path)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return True
    except (OSError, TypeError, ValueError) as e:
        print(f'Failed to save JSON ({path}):', e)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


@app.before_request
def security_before_request():
    restore_member_session_from_cookie()
    restore_scanner_session_from_cookie()
    ensure_csrf_token()
    touch_auth_session()
    # CSRF for state-changing requests (except Stripe webhook)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.path in ('/stripe/webhook',):
            return None
        if request.endpoint in ('static',):
            return None
        if not validate_csrf_token():
            return csrf_failure_response()
    return None


@app.after_request
def security_after_request(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(), payment=*'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    response.headers['X-Permitted-Cross-Domain-Policies'] = 'none'
    if IS_PRODUCTION:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Expose CSRF token for JS clients
    if session.get('csrf_token'):
        response.headers['X-CSRF-Token'] = session['csrf_token']
    # Never cache authenticated HTML responses in shared caches
    if session.get('legacy_member_email') or session.get('admin_authenticated') or session.get('verify_authenticated'):
        response.headers['Cache-Control'] = 'private, no-store'
    member_email = (session.get('legacy_member_email') or '').strip().lower()
    if member_email:
        set_member_login_cookie(response, member_email)
    elif request.cookies.get(MEMBER_LOGIN_COOKIE):
        clear_member_login_cookie(response)
    scanner_email = (session.get('verify_login_email') or '').strip().lower()
    if session.get('verify_authenticated') and is_staff_email(scanner_email):
        set_scanner_login_cookie(response, scanner_email)
    elif request.cookies.get(SCANNER_LOGIN_COOKIE):
        clear_scanner_login_cookie(response)
    return response


@app.context_processor
def inject_security_template_globals():
    return {
        'csrf_token': ensure_csrf_token(),
        'csrf_field': f'<input type="hidden" name="csrf_token" value="{ensure_csrf_token()}">',
        'show_staff_nav': is_staff_user() or admin_authenticated(),
        'member_logged_in': bool(get_logged_in_member()),
        'costume_contest_open': is_costume_contest_open(),
    }


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
    data = _locked_json_read(tickets_file, [])
    return data if isinstance(data, list) else []


def save_tickets(tickets):
    if not ensure_data_dir(tickets_file):
        return False
    return _locked_json_write(tickets_file, tickets)


def ticket_quantity(ticket):
    if not isinstance(ticket, dict):
        return 0
    raw = ticket.get('quantity')
    if raw is None or raw == '':
        return 1
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def ticket_quantities_by_email(tickets=None):
    """Total admissions purchased per buyer email."""
    counts = {}
    for ticket in tickets if tickets is not None else load_tickets():
        if not isinstance(ticket, dict):
            continue
        email = (ticket.get('email') or '').strip().lower()
        if not email:
            continue
        counts[email] = counts.get(email, 0) + ticket_quantity(ticket)
    return counts


def get_ticket_by_session(session_id):
    for ticket in load_tickets():
        if ticket.get('session_id') == session_id:
            return ticket
    return None


def record_ticket(session_id, ticket_id, email, quantity, ticket_type='general', legacy_discount=False, view_token=None, event_id=None, exclusive_single_rate=False, door_sale=False):
    ticket_id = normalize_ticket_id(ticket_id)
    if not ticket_id:
        raise ValueError('Invalid ticket id')
    ticket_meta = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])
    view_token = view_token or new_view_token()
    stamped_event_id = (event_id or get_sales_event_id() or '').strip() or None
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') == session_id:
                if not ticket.get('view_token'):
                    ticket['view_token'] = view_token
                    if not save_tickets(tickets):
                        raise RuntimeError('Could not save ticket')
                return ticket

        ticket = {
            'session_id': session_id,
            'ticket_id': ticket_id,
            'email': email,
            'quantity': quantity,
            'ticket_type': ticket_type,
            'access': ticket_meta.get('access'),
            'legacy_discount': legacy_discount,
            'exclusive_single_rate': bool(exclusive_single_rate),
            'door_sale': bool(door_sale),
            'event_id': stamped_event_id,
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': None,
            'email_sent_at': None,
            'view_token': view_token,
            'verify_url': f"{base_url}/verify/t/{ticket_id}",
        }
        tickets.append(ticket)
        if not save_tickets(tickets):
            raise RuntimeError('Could not save ticket')
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
    data = _locked_json_read(members_file, [])
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get('members', [])
    return []


def save_members(members):
    if not ensure_data_dir(members_file):
        return False
    return _locked_json_write(members_file, members)


COSTUME_DISPLAY_NAME_MAX = 80
COSTUME_DESCRIPTION_MAX = 200
COSTUME_PHOTO_MAX_BYTES = 8 * 1024 * 1024
COSTUME_PHOTO_MAX_DIMENSION = 1200
COSTUME_PHOTO_JPEG_QUALITY = 82


COSTUME_MAX_RANKS = 3
COSTUME_RANK_POINTS = (3, 2, 1)  # Borda: 1st=3, 2nd=2, 3rd=1


def _empty_costumes_store():
    return {'entries': [], 'ballots': {}, 'contest_open': True}


def _migrate_costumes_store(store):
    """Normalize store shape. Legacy per-entry `votes` arrays are discarded;
    ranked-choice ballots live in top-level `ballots` (start fresh).
    """
    if isinstance(store, list):
        store = {'entries': store}
    if not isinstance(store, dict):
        return _empty_costumes_store()
    entries = store.get('entries')
    if not isinstance(entries, list):
        entries = []
    cleaned_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cleaned = dict(entry)
        cleaned.pop('votes', None)
        cleaned_entries.append(cleaned)
    raw_ballots = store.get('ballots')
    if not isinstance(raw_ballots, dict):
        raw_ballots = {}
    cleaned_ballots = {}
    for voter, ranks in raw_ballots.items():
        key = (voter or '').strip().lower()
        if not key or not isinstance(ranks, list):
            continue
        cleaned_ranks = []
        seen = set()
        for eid in ranks:
            eid = (eid or '').strip()
            if not eid or eid in seen:
                continue
            cleaned_ranks.append(eid)
            seen.add(eid)
            if len(cleaned_ranks) >= COSTUME_MAX_RANKS:
                break
        if cleaned_ranks:
            cleaned_ballots[key] = cleaned_ranks
    # Default True so existing live stores stay visible until an admin hides the contest.
    contest_open = store.get('contest_open')
    if not isinstance(contest_open, bool):
        contest_open = True
    return {
        'entries': cleaned_entries,
        'ballots': cleaned_ballots,
        'contest_open': contest_open,
    }


def load_costumes():
    if not ensure_data_dir(costumes_file):
        return _empty_costumes_store()
    data = _locked_json_read(costumes_file, _empty_costumes_store())
    return _migrate_costumes_store(data)


def save_costumes(store):
    if not ensure_data_dir(costumes_file):
        return False
    store = _migrate_costumes_store(store)
    return _locked_json_write(costumes_file, store)


def is_costume_contest_open(store=None):
    """Whether the public costume contest menu/page is visible."""
    store = store if store is not None else load_costumes()
    return bool(store.get('contest_open', True))


def set_costume_contest_open(open_flag):
    """Persist contest visibility. Returns True on success."""
    with costumes_lock:
        store = load_costumes()
        store['contest_open'] = bool(open_flag)
        return save_costumes(store)


def costume_borda_scores(store=None):
    """Map entry_id -> Borda score from ranked ballots (3/2/1)."""
    store = store if store is not None else load_costumes()
    scores = {}
    for ranks in (store.get('ballots') or {}).values():
        if not isinstance(ranks, list):
            continue
        for i, eid in enumerate(ranks[:COSTUME_MAX_RANKS]):
            eid = (eid or '').strip()
            if not eid:
                continue
            scores[eid] = scores.get(eid, 0) + COSTUME_RANK_POINTS[i]
    return scores


def get_costume_ballot(voter_email, store=None):
    """Return voter's ranked entry ids (up to 3), or []."""
    email = (voter_email or '').strip().lower()
    if not email:
        return []
    store = store if store is not None else load_costumes()
    ranks = (store.get('ballots') or {}).get(email) or []
    if not isinstance(ranks, list):
        return []
    return [str(eid).strip() for eid in ranks if (eid or '').strip()][:COSTUME_MAX_RANKS]


def costume_entry_score(entry_id, store=None):
    if not entry_id:
        return 0
    return int(costume_borda_scores(store).get(entry_id, 0))


def list_costume_entries_public(viewer_email=None):
    """Public-safe costume listings: no emails. Sorted by Borda score desc, then newest."""
    viewer = (viewer_email or '').strip().lower()
    store = load_costumes()
    entries = list(store.get('entries') or [])
    scores = costume_borda_scores(store)
    my_ballot = get_costume_ballot(viewer, store) if viewer else []
    my_rank_by_id = {eid: (i + 1) for i, eid in enumerate(my_ballot)}
    entries.sort(key=lambda e: (
        scores.get(e.get('id') or '', 0),
        e.get('updated_at') or e.get('created_at') or '',
    ), reverse=True)
    public = []
    for entry in entries:
        eid = entry.get('id') or ''
        owner = (entry.get('owner_email') or '').strip().lower()
        score = int(scores.get(eid, 0))
        public.append({
            'id': eid,
            'display_name': entry.get('display_name') or '',
            'costume': entry.get('costume') or '',
            'photo_url': costume_photo_url(entry.get('photo')),
            'score': score,
            'vote_count': score,  # alias for templates/tests that expect a count-like field
            'my_rank': my_rank_by_id.get(eid),
            'is_mine': bool(viewer and viewer == owner),
            'created_at': entry.get('created_at') or '',
            'updated_at': entry.get('updated_at') or '',
        })
    return public


def find_costume_entry_for_email(email):
    target = (email or '').strip().lower()
    if not target:
        return None
    for entry in (load_costumes().get('entries') or []):
        if (entry.get('owner_email') or '').strip().lower() == target:
            return entry
    return None


def submit_costume_entry(owner_email, display_name, costume):
    """Create or update the member's single costume entry. Returns (ok, error_or_entry)."""
    email = (owner_email or '').strip().lower()
    name = ' '.join((display_name or '').split())
    desc = ' '.join((costume or '').split())
    if not email:
        return False, 'You must be signed in.'
    if not name:
        return False, 'Enter a display name.'
    if not desc:
        return False, 'Describe your costume.'
    if len(name) > COSTUME_DISPLAY_NAME_MAX:
        return False, f'Display name must be {COSTUME_DISPLAY_NAME_MAX} characters or fewer.'
    if len(desc) > COSTUME_DESCRIPTION_MAX:
        return False, f'Costume description must be {COSTUME_DESCRIPTION_MAX} characters or fewer.'
    now = datetime.now(timezone.utc).isoformat()
    with costumes_lock:
        store = load_costumes()
        entries = store.setdefault('entries', [])
        existing = None
        for entry in entries:
            if (entry.get('owner_email') or '').strip().lower() == email:
                existing = entry
                break
        if existing:
            existing['display_name'] = name
            existing['costume'] = desc
            existing['updated_at'] = now
            existing.pop('votes', None)
            if not existing.get('id'):
                existing['id'] = secrets.token_hex(16)
            saved = existing
        else:
            saved = {
                'id': secrets.token_hex(16),
                'owner_email': email,
                'display_name': name,
                'costume': desc,
                'created_at': now,
                'updated_at': now,
            }
            entries.append(saved)
        if not save_costumes(store):
            return False, 'Could not save your costume. Try again.'
        return True, saved


def set_costume_ballot(voter_email, ranked_entry_ids):
    """Set a member's ranked-choice ballot (up to 3 distinct other entries).

    Partial ballots are OK (e.g. only 1st). Empty list clears the ballot.
    Returns (ok, error_message).
    """
    email = (voter_email or '').strip().lower()
    if not email:
        return False, 'You must be signed in to vote.'
    raw = ranked_entry_ids if isinstance(ranked_entry_ids, (list, tuple)) else []
    cleaned = []
    seen = set()
    for eid in raw:
        eid = (eid or '').strip()
        if not eid:
            continue
        if eid in seen:
            return False, 'Each costume can only appear once in your rankings.'
        cleaned.append(eid)
        seen.add(eid)
        if len(cleaned) > COSTUME_MAX_RANKS:
            return False, f'You can rank at most {COSTUME_MAX_RANKS} costumes.'
    with costumes_lock:
        store = load_costumes()
        entries = store.get('entries') or []
        by_id = {e.get('id'): e for e in entries if e.get('id')}
        for eid in cleaned:
            target = by_id.get(eid)
            if not target:
                return False, 'That costume entry was not found.'
            owner = (target.get('owner_email') or '').strip().lower()
            if owner == email:
                return False, 'You cannot vote for your own costume.'
        ballots = store.setdefault('ballots', {})
        if cleaned:
            ballots[email] = cleaned
        else:
            ballots.pop(email, None)
        if not save_costumes(store):
            return False, 'Could not save your rankings. Try again.'
        return True, None


def _purge_entry_from_ballots(store, entry_id):
    """Remove an entry id from every ballot (after delete). Mutates store."""
    eid = (entry_id or '').strip()
    if not eid:
        return
    ballots = store.get('ballots')
    if not isinstance(ballots, dict):
        return
    for voter, ranks in list(ballots.items()):
        if not isinstance(ranks, list):
            ballots.pop(voter, None)
            continue
        cleaned = [r for r in ranks if (r or '').strip() != eid]
        if cleaned:
            ballots[voter] = cleaned
        else:
            ballots.pop(voter, None)


def toggle_costume_vote(entry_id, voter_email):
    """Legacy helper removed; ranked ballots replace toggle votes."""
    return False, 'Costume voting now uses ranked choice (1st–3rd).', None


def costume_photo_is_safe_filename(filename):
    """Same basename rules as event flyers (no path traversal)."""
    return flyer_is_safe_filename(filename)


def costume_photo_url(filename):
    name = (filename or '').strip()
    if name and costume_photo_is_safe_filename(name):
        return f'/costume-photos/{name}'
    return None


def delete_costume_photo_file(filename):
    if not costume_photo_is_safe_filename(filename):
        return
    path = os.path.join(costume_photos_dir, filename)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def set_costume_entry_photo(entry_id, photo_filename):
    """Set or clear photo on an entry by id. Returns (ok, error_or_entry)."""
    target_id = (entry_id or '').strip()
    if not target_id:
        return False, 'Missing costume entry.'
    with costumes_lock:
        store = load_costumes()
        for entry in (store.get('entries') or []):
            if entry.get('id') == target_id:
                if photo_filename:
                    entry['photo'] = photo_filename
                else:
                    entry.pop('photo', None)
                entry['updated_at'] = datetime.now(timezone.utc).isoformat()
                if not save_costumes(store):
                    return False, 'Could not save photo. Try again.'
                return True, entry
        return False, 'That costume entry was not found.'


def delete_costume_entry(entry_id):
    """Admin moderation: remove a costume entry and its photo. Returns (ok, error_or_removed)."""
    target_id = (entry_id or '').strip()
    if not target_id:
        return False, 'Missing costume entry.'
    removed = None
    with costumes_lock:
        store = load_costumes()
        entries = store.get('entries') if isinstance(store.get('entries'), list) else []
        kept = []
        for entry in entries:
            if entry.get('id') == target_id and removed is None:
                removed = entry
                continue
            kept.append(entry)
        if removed is None:
            return False, 'That costume entry was not found.'
        store['entries'] = kept
        _purge_entry_from_ballots(store, target_id)
        if not save_costumes(store):
            return False, 'Could not remove that costume. Try again.'
    photo = (removed.get('photo') or '').strip()
    if photo:
        delete_costume_photo_file(photo)
    return True, removed


def delete_own_costume_entry(owner_email):
    """Member deletes their own costume entry (by session email). Returns (ok, error_or_removed)."""
    email = (owner_email or '').strip().lower()
    if not email:
        return False, 'You must be signed in.'
    removed = None
    with costumes_lock:
        store = load_costumes()
        entries = store.get('entries') if isinstance(store.get('entries'), list) else []
        kept = []
        for entry in entries:
            owner = (entry.get('owner_email') or '').strip().lower()
            if owner == email and removed is None:
                removed = entry
                continue
            kept.append(entry)
        if removed is None:
            return False, 'You do not have a costume entry.'
        store['entries'] = kept
        _purge_entry_from_ballots(store, removed.get('id'))
        if not save_costumes(store):
            return False, 'Could not delete your costume. Try again.'
    photo = (removed.get('photo') or '').strip()
    if photo:
        delete_costume_photo_file(photo)
    return True, removed


def apply_costume_photo_upload_or_remove(entry, upload=None, remove_photo=False):
    """Apply optional photo upload or removal to an existing entry.

    Shared by /costumes and the member portal so validation stays in sync.
    Returns (ok, error_message_or_None).
    """
    if not entry or not entry.get('id'):
        return False, 'Missing costume entry.'
    has_upload = bool(upload and getattr(upload, 'filename', None))
    if has_upload:
        try:
            filename = save_costume_photo(entry.get('id'), upload)
        except ValueError as exc:
            return False, str(exc)
        if filename:
            old_photo = (entry.get('photo') or '').strip()
            photo_ok, photo_result = set_costume_entry_photo(entry.get('id'), filename)
            if not photo_ok:
                return False, photo_result or 'Could not save photo.'
            if old_photo and old_photo != filename:
                delete_costume_photo_file(old_photo)
        return True, None
    if remove_photo and entry.get('photo'):
        old_photo = entry.get('photo')
        photo_ok, photo_result = set_costume_entry_photo(entry.get('id'), None)
        if not photo_ok:
            return False, photo_result or 'Could not remove photo.'
        delete_costume_photo_file(old_photo)
    return True, None


def submit_costume_entry_with_photo(
    owner_email, display_name, costume, upload=None, remove_photo=False,
):
    """Create/update costume text then apply photo changes. Returns (ok, error_or_entry)."""
    ok, result = submit_costume_entry(owner_email, display_name, costume)
    if not ok:
        return False, result
    entry = result
    photo_ok, photo_error = apply_costume_photo_upload_or_remove(
        entry, upload=upload, remove_photo=remove_photo,
    )
    if not photo_ok:
        return False, photo_error
    refreshed = find_costume_entry_for_email(owner_email) or entry
    return True, refreshed


def save_costume_photo(entry_id, file_storage):
    """Validate, resize, and compress a costume photo to JPEG on disk.

    Accepts jpeg/png/webp/gif. Animated GIFs use the first frame only.
    Max upload 8MB; longest side capped at 1200px; saved as JPEG quality 82.
    """
    from PIL import Image

    if not file_storage or not getattr(file_storage, 'filename', None):
        return None
    raw = file_storage.read()
    if not raw:
        return None
    if len(raw) > COSTUME_PHOTO_MAX_BYTES:
        raise ValueError('Photo must be 8MB or smaller.')
    if not detect_image_extension(raw):
        raise ValueError('Photo must be a JPG, PNG, WEBP, or GIF image.')

    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError('Could not read that image. Try another file.') from exc

    # Animated / multi-frame: keep first frame only.
    try:
        if getattr(img, 'n_frames', 1) > 1:
            img.seek(0)
            img = img.copy()
    except Exception:
        pass

    if img.mode in ('RGBA', 'LA'):
        background = Image.new('RGB', img.size, (24, 24, 27))
        alpha = img.split()[-1]
        background.paste(img.convert('RGBA'), mask=alpha)
        img = background
    elif img.mode == 'P':
        img = img.convert('RGBA')
        background = Image.new('RGB', img.size, (24, 24, 27))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    width, height = img.size
    longest = max(width, height)
    if longest > COSTUME_PHOTO_MAX_DIMENSION:
        scale = COSTUME_PHOTO_MAX_DIMENSION / float(longest)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
        img = img.resize(new_size, resample)

    if not ensure_data_dir(os.path.join(costume_photos_dir, 'placeholder')):
        raise ValueError('Could not save photo.')

    safe_id = secure_filename(str(entry_id or '')) or secrets.token_hex(8)
    filename = f'{safe_id}_{secrets.token_hex(6)}.jpg'
    if not costume_photo_is_safe_filename(filename):
        raise ValueError('Could not save photo.')
    path = os.path.join(costume_photos_dir, filename)

    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=COSTUME_PHOTO_JPEG_QUALITY, optimize=True)
    with open(path, 'wb') as handle:
        handle.write(buffer.getvalue())
    return filename



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
        try:
            return secrets.compare_digest(member.get('code_hash'), hash_member_code(password))
        except (TypeError, ValueError):
            return False
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
    try:
        return secure_equals(member.get('password_reset_token'), hash_reset_token(token))
    except (TypeError, ValueError):
        return False


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
    data = _locked_json_read(invites_file, [])
    return data if isinstance(data, list) else []


def save_invites(invites):
    if not ensure_data_dir(invites_file):
        return False
    return _locked_json_write(invites_file, invites)


def normalize_email_list(raw, max_emails=500):
    if not raw:
        return []
    normalized = []
    seen = set()
    for chunk in raw.replace(',', '\n').replace(';', '\n').split('\n'):
        email = chunk.strip().lower()
        if not is_valid_email(email):
            continue
        if email in seen:
            continue
        seen.add(email)
        normalized.append(email)
        if len(normalized) >= max_emails:
            break
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
    for email in added:
        grant_exclusive_member_features(email)
    return added, skipped


def is_protected_mailing_list_email(email):
    """These addresses stay on mailing lists and cannot be removed or renamed."""
    return (email or '').strip().lower() in PROTECTED_MAILING_LIST_EMAILS


def posted_mailing_list_emails():
    """Emails from a single field or a multi-select checkbox list."""
    chunks = [value for value in request.form.getlist('emails') if value]
    single = request.form.get('email')
    if single:
        chunks.append(single)
    return normalize_email_list('\n'.join(str(chunk) for chunk in chunks))


def remove_emails_from_invite_list(emails):
    """Remove exclusive-list addresses. Skips protected. Returns (removed, skipped_protected)."""
    skipped_protected = []
    wanted = []
    seen = set()
    for email in emails:
        normalized = (email or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if is_protected_mailing_list_email(normalized):
            skipped_protected.append(normalized)
        else:
            wanted.append(normalized)
    removed = []
    removed_records = []
    if wanted:
        wanted_set = set(wanted)
        with invites_lock:
            invites = load_invites()
            remaining = []
            for invite in invites:
                email = (invite.get('email') or '').strip().lower()
                if email in wanted_set:
                    removed.append(email)
                    removed_records.append(dict(invite))
                else:
                    remaining.append(invite)
            if removed:
                save_invites(remaining)
    if removed_records:
        log_mailing_list_removal('exclusive', removed_records)
    return removed, skipped_protected


def remove_email_from_invite_list(email):
    removed, _skipped = remove_emails_from_invite_list([email])
    return bool(removed)


def clear_exclusive_member_features(email):
    """Remove exclusive-list perks from a member account (keep login + tickets).

    Clears returning_guest_discount. If they have no purchase history, also
    clears discount_code so they no longer get member pricing.
    """
    normalized = (email or '').strip().lower()
    if not normalized:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').strip().lower() != normalized:
                continue
            changed = False
            if 'returning_guest_discount' in member:
                member.pop('returning_guest_discount', None)
                changed = True
            # No past purchases + exclusive removed → lose discount eligibility.
            has_purchases = False
            for ticket in load_tickets():
                if ticket.get('email', '').lower() == normalized:
                    has_purchases = True
                    break
            if not has_purchases:
                for ticket_id in member.get('saved_tickets') or []:
                    if get_ticket_record(ticket_id):
                        has_purchases = True
                        break
            if not has_purchases and member.get('discount_code'):
                member.pop('discount_code', None)
                changed = True
            if changed:
                save_members(members)
            return changed
    return False


def grant_exclusive_member_features(email):
    """If a member account exists for this email, attach exclusive 20% one-per-event perk."""
    normalized = (email or '').strip().lower()
    if not normalized:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').strip().lower() != normalized:
                continue
            member['returning_guest_discount'] = True
            if not member.get('discount_code'):
                code = generate_discount_code(normalized)
                while discount_code_taken(code, exclude_email=normalized):
                    code = generate_discount_code(normalized)
                member['discount_code'] = code
            save_members(members)
            return True
    return False


def update_email_on_invite_list(old_email, new_email):
    """Rename an exclusive-list address. Returns (ok, error_message)."""
    old = (old_email or '').strip().lower()
    new = (new_email or '').strip().lower()
    if not old or not is_valid_email(new):
        return False, 'Enter a valid new email address.'
    if old == new:
        return True, None
    if is_protected_mailing_list_email(old):
        return False, f'{old} is a protected address and cannot be changed.'
    with invites_lock:
        invites = load_invites()
        target = None
        for invite in invites:
            email = invite.get('email', '').strip().lower()
            if email == new:
                return False, f'{new} is already on the exclusive list.'
            if email == old:
                target = invite
        if not target:
            return False, 'That exclusive-list email was not found.'
        target['email'] = new
        # Force a fresh invite link if address changed before claim.
        if not target.get('claimed_at'):
            target['invite_token'] = None
            target['invite_expires'] = None
            target['sent_at'] = None
        save_invites(invites)
    # Old address loses exclusive perks; new address gains them if they have an account.
    clear_exclusive_member_features(old)
    grant_exclusive_member_features(new)
    return True, None


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
    invite = get_member_invite(email)
    if not invite or not token or not invite.get('invite_token'):
        return False
    if invite.get('claimed_at'):
        return False
    expires_raw = invite.get('invite_expires')
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
    return secure_equals(invite.get('invite_token'), hash_reset_token(token))


def mark_member_invite_claimed(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['claimed_at'] = datetime.now(timezone.utc).isoformat()
                invite.pop('invite_token', None)
                invite.pop('invite_expires', None)
                save_invites(invites)
                return True
    return False


def mark_member_invite_sent(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['sent_at'] = datetime.now(timezone.utc).isoformat()
                save_invites(invites)
                return True
    return False


def invite_list_for_admin():
    rows = []
    ticket_counts = ticket_quantities_by_email()
    invites = [invite for invite in load_invites() if isinstance(invite, dict)]
    for invite in sorted(invites, key=lambda i: i.get('added_at') or '', reverse=True):
        email = (invite.get('email') or '').strip().lower()
        if not email:
            continue
        member = get_legacy_member(email)
        status = 'pending'
        if member:
            status = 'account_exists'
        elif invite.get('claimed_at'):
            status = 'claimed'
        elif invite.get('sent_at'):
            status = 'sent'
        rows.append({
            'email': email,
            'added_at': invite.get('added_at'),
            'sent_at': invite.get('sent_at'),
            'claimed_at': invite.get('claimed_at'),
            'status': status,
            'protected': is_protected_mailing_list_email(email),
            'tickets_purchased': ticket_counts.get(email, 0),
        })
    return rows


def count_signups_and_invites():
    """Exclusive-list people who signed up vs total invites.

    Signup = claimed invite or an account already exists for that email.
    """
    member_emails = {
        (member.get('email') or '').strip().lower()
        for member in load_members()
        if isinstance(member, dict)
    }
    member_emails.discard('')
    signups = 0
    invites = 0
    for invite in load_invites():
        if not isinstance(invite, dict):
            continue
        email = (invite.get('email') or '').strip().lower()
        if not email:
            continue
        invites += 1
        if invite.get('claimed_at') or email in member_emails:
            signups += 1
    return signups, invites


def invites_ready_to_send():
    already = emails_already_sent_invites()
    ready = []
    for row in invite_list_for_admin():
        if row['status'] != 'pending':
            continue
        if row['email'] in already:
            continue
        ready.append(row['email'])
    return ready


def create_member_from_invite(email, password):
    normalized = email.strip().lower()
    if get_legacy_member(normalized):
        return False, 'An account with that email already exists.'
    discount_code = generate_discount_code(normalized)
    while discount_code_taken(discount_code):
        discount_code = generate_discount_code(normalized)
    with members_lock:
        members = load_members()
        members.append({
            'email': normalized,
            'password_hash': hash_password(password),
            'saved_tickets': [],
            'discount_code': discount_code,
            'returning_guest_discount': True,
            'joined_at': datetime.now(timezone.utc).isoformat(),
        })
        save_members(members)
    mark_member_invite_claimed(normalized)
    # Exclusive list only — do not put returning-guest accounts on the full list.
    return True, None


# --- Full mailing list (signups + founding + manual; no exclusive 20% perk) ---


def load_full_mailing_list():
    if not ensure_data_dir(full_mailing_list_file):
        return []
    data = _locked_json_read(full_mailing_list_file, [])
    return data if isinstance(data, list) else []


def save_full_mailing_list(entries):
    if not ensure_data_dir(full_mailing_list_file):
        return False
    return _locked_json_write(full_mailing_list_file, entries)


def is_on_exclusive_invite_list(email):
    return get_member_invite(email) is not None


def add_emails_to_full_mailing_list(emails, source='manual'):
    """Add emails to the general list. Skips exclusive invite-list addresses."""
    added = []
    skipped = []
    with full_list_lock:
        entries = load_full_mailing_list()
        existing = {e.get('email', '').strip().lower() for e in entries}
        for email in emails:
            normalized = (email or '').strip().lower()
            if not normalized or '@' not in normalized:
                continue
            if is_on_exclusive_invite_list(normalized):
                skipped.append(normalized)
                continue
            if normalized in existing:
                skipped.append(normalized)
                continue
            entries.append({
                'email': normalized,
                'added_at': datetime.now(timezone.utc).isoformat(),
                'source': source,
            })
            existing.add(normalized)
            added.append(normalized)
        save_full_mailing_list(entries)
    return added, skipped


def remove_emails_from_full_mailing_list(emails):
    """Remove full-list addresses. Skips protected. Returns (removed, skipped_protected)."""
    skipped_protected = []
    wanted = []
    seen = set()
    for email in emails:
        normalized = (email or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if is_protected_mailing_list_email(normalized):
            skipped_protected.append(normalized)
        else:
            wanted.append(normalized)
    removed = []
    removed_records = []
    if wanted:
        wanted_set = set(wanted)
        with full_list_lock:
            entries = load_full_mailing_list()
            remaining = []
            for entry in entries:
                email = (entry.get('email') or '').strip().lower()
                if email in wanted_set:
                    removed.append(email)
                    removed_records.append(dict(entry))
                else:
                    remaining.append(entry)
            if removed:
                save_full_mailing_list(remaining)
    if removed_records:
        log_mailing_list_removal('full', removed_records)
    return removed, skipped_protected


def remove_email_from_full_mailing_list(email):
    removed, _skipped = remove_emails_from_full_mailing_list([email])
    return bool(removed)


MAILING_LIST_LOG_MAX = 2000


def load_mailing_list_log():
    if not ensure_data_dir(mailing_list_log_file):
        return []
    data = _locked_json_read(mailing_list_log_file, [])
    return data if isinstance(data, list) else []


def save_mailing_list_log(entries):
    if not ensure_data_dir(mailing_list_log_file):
        return False
    return _locked_json_write(mailing_list_log_file, entries)


def append_mailing_list_log(entry):
    if not entry:
        return False
    return append_mailing_list_log_entries([entry])


def append_mailing_list_log_entries(new_entries):
    if not new_entries:
        return False
    try:
        with mailing_list_log_lock:
            entries = load_mailing_list_log()
            entries.extend(new_entries)
            if len(entries) > MAILING_LIST_LOG_MAX:
                entries = entries[-MAILING_LIST_LOG_MAX:]
            return save_mailing_list_log(entries)
    except Exception as e:
        print('Failed to append mailing list log:', e)
        return False


def log_mailing_list_removal(list_kind, records):
    """One log row per deleted address, with the time it was removed."""
    now = datetime.now(timezone.utc).isoformat()
    new_entries = []
    for record in records or []:
        email = (record.get('email') or '').strip().lower()
        if not email:
            continue
        new_entries.append({
            'id': secrets.token_hex(8),
            'action': 'remove',
            'list': list_kind,
            'at': now,
            'emails': [email],
            'records': [record],
        })
    return append_mailing_list_log_entries(new_entries)


def mailing_message_fingerprint(kind, subject, body=''):
    payload = f'{(kind or "").strip().lower()}\n{(subject or "").strip()}\n{body or ""}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _broadcast_delivery_key(fingerprint, email):
    return (fingerprint, (email or '').strip().lower())


def mark_broadcast_delivered(fingerprint, email):
    """Remember a successful send in-process so a retry cannot duplicate it."""
    key = _broadcast_delivery_key(fingerprint, email)
    if not key[1]:
        return
    with _broadcast_delivery_lock:
        _delivered_broadcasts.add(key)
        _inflight_broadcasts.discard(key)


def claim_broadcast_emails(fingerprint, emails):
    """Claim addresses that are not delivered and not already in this send."""
    claimed = []
    with _broadcast_delivery_lock:
        for email in emails or []:
            key = _broadcast_delivery_key(fingerprint, email)
            if not key[1]:
                continue
            if key in _delivered_broadcasts or key in _inflight_broadcasts:
                continue
            _inflight_broadcasts.add(key)
            claimed.append(key[1])
    return claimed


def release_broadcast_emails(fingerprint, emails):
    with _broadcast_delivery_lock:
        for email in emails or []:
            _inflight_broadcasts.discard(_broadcast_delivery_key(fingerprint, email))


def emails_already_sent_message(kind, subject, body=''):
    """Addresses that already got this exact message successfully."""
    fingerprint = mailing_message_fingerprint(kind, subject, body)
    subject = (subject or '').strip()
    found = set()
    for entry in load_mailing_list_log():
        if not isinstance(entry, dict):
            continue
        if entry.get('action') != 'send' or entry.get('status') != 'sent':
            continue
        if (entry.get('kind') or '') != kind:
            continue
        entry_fp = entry.get('fingerprint') or ''
        if entry_fp:
            if entry_fp != fingerprint:
                continue
        elif (entry.get('subject') or '') != subject:
            continue
        for email in entry.get('emails') or []:
            normalized = (email or '').strip().lower()
            if normalized:
                found.add(normalized)
    with _broadcast_delivery_lock:
        for delivered_fp, email in _delivered_broadcasts:
            if delivered_fp == fingerprint and email:
                found.add(email)
    return found


def emails_sent_for_fingerprint(fingerprint):
    """Addresses logged as successfully sent for this stored send fingerprint."""
    wanted = (fingerprint or '').strip()
    if not wanted or not re.fullmatch(r'[a-f0-9]{8,32}', wanted):
        return set()
    found = set()
    for entry in load_mailing_list_log():
        if not isinstance(entry, dict):
            continue
        if entry.get('action') != 'send' or entry.get('status') != 'sent':
            continue
        if (entry.get('kind') or 'broadcast') != 'broadcast':
            continue
        if (entry.get('fingerprint') or '') != wanted:
            continue
        found.update(_log_entry_emails(entry))
    return found


def emails_already_sent_invites():
    found = set()
    for entry in load_mailing_list_log():
        if not isinstance(entry, dict):
            continue
        if entry.get('action') != 'send':
            continue
        if entry.get('kind') != 'invite':
            continue
        if entry.get('status') != 'sent':
            continue
        for email in entry.get('emails') or []:
            normalized = (email or '').strip().lower()
            if normalized:
                found.add(normalized)
    return found


def log_broadcast_message_snapshot(
    subject, body, lists, fingerprint, *, kind='broadcast', action='message',
):
    """Store one copy of the email itself so it can be resent to anyone who missed it."""
    subject = (subject or '').replace('\r', ' ').replace('\n', ' ').strip()
    if len(subject) > 200:
        subject = subject[:200]
    body = (body or '').strip()
    if len(body) > 20000:
        body = body[:20000]
    fingerprint = (fingerprint or '').strip()
    kind = (kind or 'broadcast').strip().lower()
    action = (action or 'message').strip().lower()
    if not subject or not body or not fingerprint:
        return False
    lists = sorted({
        (item or '').strip().lower()
        for item in (lists or [])
        if (item or '').strip().lower() in ('exclusive', 'full')
    })
    try:
        with mailing_list_log_lock:
            entries = load_mailing_list_log()
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and entry.get('action') == action
                    and entry.get('fingerprint') == fingerprint
                ):
                    return True
            entries.append({
                'id': secrets.token_hex(8),
                'action': action,
                'kind': kind,
                'subject': subject,
                'body': body,
                'fingerprint': fingerprint,
                'lists': lists,
                'at': datetime.now(timezone.utc).isoformat(),
            })
            if len(entries) > MAILING_LIST_LOG_MAX:
                entries = entries[-MAILING_LIST_LOG_MAX:]
            return save_mailing_list_log(entries)
    except Exception as e:
        print('Failed to save broadcast message snapshot:', e)
        return False


def get_broadcast_message(message_id):
    wanted = (message_id or '').strip()
    if not wanted or not re.fullmatch(r'[a-f0-9]{8,32}', wanted):
        return None
    for entry in load_mailing_list_log():
        if not isinstance(entry, dict):
            continue
        if entry.get('id') != wanted:
            continue
        if entry.get('action') != 'message' or entry.get('kind') != 'broadcast':
            continue
        return entry
    return None


def _log_entry_emails(entry):
    raw = entry.get('emails') or []
    if isinstance(raw, str):
        raw = [raw]
    return [
        (email or '').strip().lower()
        for email in raw
        if (email or '').strip()
    ]


def _preview_addresses(emails, limit=40):
    items = sorted(emails)
    return items[:limit], max(0, len(items) - limit)


def broadcast_messages_for_admin():
    """Saved emails plus older subject-only sends, with who got them."""
    try:
        log_entries = load_mailing_list_log()
    except Exception as e:
        print('Failed to load mailing list log:', e)
        return []
    snapshots = {}
    snapshot_order = []
    grouped_sends = {}
    for entry in log_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get('action') == 'message' and entry.get('kind') == 'broadcast':
            fp = (entry.get('fingerprint') or entry.get('id') or '').strip()
            if fp and fp not in snapshots:
                snapshots[fp] = entry
                snapshot_order.append(fp)
            continue
        if entry.get('action') != 'send' or (entry.get('kind') or 'broadcast') != 'broadcast':
            continue
        fp = (entry.get('fingerprint') or '').strip()
        subject = (entry.get('subject') or '').strip()
        key = fp or f'subject:{(subject or "").lower()}'
        bucket = grouped_sends.setdefault(key, {
            'subject': subject,
            'fingerprint': fp,
            'sent': set(),
            'failed': set(),
            'at': entry.get('at'),
        })
        if subject and not bucket['subject']:
            bucket['subject'] = subject
        if entry.get('at') and (not bucket['at'] or str(entry.get('at')) > str(bucket['at'])):
            bucket['at'] = entry.get('at')
        emails = set(_log_entry_emails(entry))
        if entry.get('status') == 'failed':
            bucket['failed'].update(emails - bucket['sent'])
        else:
            bucket['sent'].update(emails)
            bucket['failed'].difference_update(emails)

    rows = []
    used_keys = set()
    for fp in reversed(snapshot_order):
        entry = snapshots[fp]
        subject = (entry.get('subject') or '').strip()
        body = (entry.get('body') or '').strip()
        lists = {
            (item or '').strip().lower()
            for item in (entry.get('lists') or [])
            if (item or '').strip().lower() in ('exclusive', 'full')
        }
        if not lists:
            lists = {'exclusive', 'full'}
        recipients = set(resolve_broadcast_recipients(lists))
        already = emails_already_sent_message('broadcast', subject, body)
        remaining = sorted(email for email in recipients if email not in already)
        send_info = grouped_sends.get(fp) or grouped_sends.get(f'subject:{subject.lower()}')
        sent_emails = set((send_info or {}).get('sent') or []) | (already & recipients)
        failed_emails = set((send_info or {}).get('failed') or []) - sent_emails
        sent_preview, sent_more = _preview_addresses(sent_emails)
        failed_preview, failed_more = _preview_addresses(failed_emails)
        remaining_preview, remaining_more = _preview_addresses(remaining)
        rows.append({
            'id': entry.get('id') or '',
            'has_body': True,
            'fingerprint': fp,
            'subject': subject,
            'body': body,
            'at': entry.get('at') or (send_info or {}).get('at'),
            'lists': sorted(lists),
            'sent_count': len(sent_emails),
            'sent': sent_preview,
            'sent_more': sent_more,
            'failed_count': len(failed_emails),
            'failed': failed_preview,
            'failed_more': failed_more,
            'remaining_count': len(remaining),
            'remaining': remaining_preview,
            'remaining_more': remaining_more,
        })
        used_keys.add(fp)
        if subject:
            used_keys.add(f'subject:{subject.lower()}')

    leftover_keys = [key for key in grouped_sends if key not in used_keys]
    leftover_keys.sort(key=lambda key: str(grouped_sends[key].get('at') or ''), reverse=True)
    for key in leftover_keys:
        bucket = grouped_sends[key]
        sent_emails = bucket['sent']
        failed_emails = bucket['failed'] - sent_emails
        sent_preview, sent_more = _preview_addresses(sent_emails)
        failed_preview, failed_more = _preview_addresses(failed_emails)
        rows.append({
            'id': '',
            'has_body': False,
            'fingerprint': bucket['fingerprint'],
            'subject': bucket['subject'],
            'body': '',
            'at': bucket['at'],
            'lists': [],
            'sent_count': len(sent_emails),
            'sent': sent_preview,
            'sent_more': sent_more,
            'failed_count': len(failed_emails),
            'failed': failed_preview,
            'failed_more': failed_more,
            'remaining_count': 0,
            'remaining': [],
            'remaining_more': 0,
        })
    return rows


def test_messages_for_admin():
    """Saved test sends (Hallie + events only)."""
    try:
        log_entries = load_mailing_list_log()
    except Exception as e:
        print('Failed to load mailing list log:', e)
        return []
    snapshots = []
    grouped = {}
    for entry in log_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get('action') == 'test_message':
            snapshots.append(entry)
            continue
        if entry.get('action') != 'send' or entry.get('kind') != 'test':
            continue
        fp = (entry.get('fingerprint') or '').strip() or (entry.get('id') or '')
        bucket = grouped.setdefault(fp, {
            'subject': (entry.get('subject') or '').strip(),
            'sent': set(),
            'failed': set(),
            'at': entry.get('at'),
        })
        if entry.get('at') and (not bucket['at'] or str(entry.get('at')) > str(bucket['at'])):
            bucket['at'] = entry.get('at')
        emails = set(_log_entry_emails(entry))
        if entry.get('status') == 'failed':
            bucket['failed'].update(emails - bucket['sent'])
        else:
            bucket['sent'].update(emails)
            bucket['failed'].difference_update(emails)
    rows = []
    used = set()
    for entry in reversed(snapshots):
        fp = (entry.get('fingerprint') or '').strip()
        send_info = grouped.get(fp) or {}
        sent_emails = sorted(send_info.get('sent') or [])
        failed_emails = sorted(send_info.get('failed') or [])
        rows.append({
            'id': entry.get('id') or '',
            'subject': (entry.get('subject') or '').strip(),
            'body': (entry.get('body') or '').strip(),
            'at': entry.get('at') or send_info.get('at'),
            'sent': sent_emails,
            'sent_count': len(sent_emails),
            'failed': failed_emails,
            'failed_count': len(failed_emails),
        })
        if fp:
            used.add(fp)
    leftover = [fp for fp in grouped if fp not in used]
    leftover.sort(key=lambda key: str(grouped[key].get('at') or ''), reverse=True)
    for fp in leftover:
        bucket = grouped[fp]
        sent_emails = sorted(bucket['sent'])
        failed_emails = sorted(bucket['failed'] - bucket['sent'])
        rows.append({
            'id': '',
            'subject': bucket['subject'],
            'body': '',
            'at': bucket['at'],
            'sent': sent_emails,
            'sent_count': len(sent_emails),
            'failed': failed_emails,
            'failed_count': len(failed_emails),
        })
    return rows


def log_mailing_list_send(kind, subject, emails, status='sent', fingerprint=''):
    """One log row per recipient for a broadcast or invite send."""
    now = datetime.now(timezone.utc).isoformat()
    subject = (subject or '').replace('\r', ' ').replace('\n', ' ').strip()
    if len(subject) > 200:
        subject = subject[:200]
    kind = (kind or 'broadcast').strip().lower()
    status = 'failed' if status == 'failed' else 'sent'
    new_entries = []
    for email in emails or []:
        normalized = (email or '').strip().lower()
        if not normalized:
            continue
        entry = {
            'id': secrets.token_hex(8),
            'action': 'send',
            'kind': kind,
            'subject': subject,
            'status': status,
            'at': now,
            'emails': [normalized],
        }
        if fingerprint:
            entry['fingerprint'] = fingerprint
        new_entries.append(entry)
    return append_mailing_list_log_entries(new_entries)


def mailing_list_log_for_admin(action='remove'):
    rows = []
    try:
        log_entries = load_mailing_list_log()
    except Exception as e:
        print('Failed to load mailing list log:', e)
        return rows
    for entry in reversed(log_entries):
        if not isinstance(entry, dict):
            continue
        entry_action = entry.get('action') or 'remove'
        if action and entry_action != action:
            continue
        raw_emails = entry.get('emails') or []
        if isinstance(raw_emails, str):
            raw_emails = [raw_emails]
        emails = [
            (email or '').strip().lower()
            for email in raw_emails
            if (email or '').strip()
        ]
        if not emails:
            continue
        rows.append({
            'id': entry.get('id') or '',
            'action': entry_action,
            'list': entry.get('list') or '',
            'kind': entry.get('kind') or '',
            'subject': entry.get('subject') or '',
            'status': entry.get('status') or '',
            'at': entry.get('at'),
            'emails': emails,
            'restored': bool(entry.get('restored_at')),
            'restored_at': entry.get('restored_at'),
        })
    return rows


def restore_exclusive_invite_records(records):
    """Put exclusive addresses back without sending a new invite email."""
    added = []
    skipped = []
    with invites_lock:
        invites = load_invites()
        existing = {i.get('email', '').strip().lower() for i in invites}
        for record in records or []:
            email = (record.get('email') or '').strip().lower()
            if not email or not is_valid_email(email):
                continue
            if email in existing:
                skipped.append(email)
                continue
            invites.append({
                'email': email,
                'added_at': record.get('added_at') or datetime.now(timezone.utc).isoformat(),
                'sent_at': record.get('sent_at'),
                'claimed_at': record.get('claimed_at'),
                'invite_token': record.get('invite_token'),
                'invite_expires': record.get('invite_expires'),
            })
            existing.add(email)
            added.append(email)
        if added:
            save_invites(invites)
    for email in added:
        grant_exclusive_member_features(email)
    return added, skipped


def restore_mailing_list_removal(entry_id):
    """Put removed addresses back on the list they were taken from.

    Never sends an invite email. Exclusive restores reuse the original
    invite record (including sent_at) so they are not treated as new.
    """
    wanted_id = (entry_id or '').strip()
    if not wanted_id or not re.fullmatch(r'[a-f0-9]{8,32}', wanted_id):
        return False, 'That backup entry was not found.'
    with mailing_list_log_lock:
        entries = load_mailing_list_log()
        target = None
        for entry in entries:
            if entry.get('id') == wanted_id:
                target = entry
                break
        if not target:
            return False, 'That backup entry was not found.'
        if (target.get('action') or 'remove') != 'remove':
            return False, 'Only removals can be restored.'
        if target.get('restored_at'):
            return False, 'That removal was already restored.'
        list_kind = target.get('list')
        emails = [
            (email or '').strip().lower()
            for email in (target.get('emails') or [])
            if (email or '').strip()
        ]
        records = [dict(record) for record in (target.get('records') or [])]
    if not emails:
        return False, 'That backup entry has no addresses to restore.'
    if not records:
        records = [{'email': email} for email in emails]
    if list_kind == 'exclusive':
        added, skipped = restore_exclusive_invite_records(records)
        list_label = 'exclusive list'
    elif list_kind == 'full':
        added, skipped = add_emails_to_full_mailing_list(emails, source='restore')
        list_label = 'full list'
    else:
        return False, 'Unknown list in that backup entry.'
    with mailing_list_log_lock:
        entries = load_mailing_list_log()
        for entry in entries:
            if entry.get('id') == wanted_id:
                entry['restored_at'] = datetime.now(timezone.utc).isoformat()
                entry['restored_count'] = len(added)
                break
        save_mailing_list_log(entries)
    parts = []
    if added:
        parts.append(f'Restored {len(added)} to the {list_label}.')
    if skipped:
        parts.append(f'{len(skipped)} already present.')
    return True, ' '.join(parts) or 'Nothing to restore.'


def update_email_on_full_mailing_list(old_email, new_email):
    """Rename a full-list address. Returns (ok, error_message)."""
    old = (old_email or '').strip().lower()
    new = (new_email or '').strip().lower()
    if not old or not is_valid_email(new):
        return False, 'Enter a valid new email address.'
    if old == new:
        return True, None
    if is_protected_mailing_list_email(old):
        return False, f'{old} is a protected address and cannot be changed.'
    if is_on_exclusive_invite_list(new):
        return False, f'{new} is on the exclusive list and cannot be added here.'
    with full_list_lock:
        entries = load_full_mailing_list()
        target = None
        for entry in entries:
            email = entry.get('email', '').strip().lower()
            if email == new:
                return False, f'{new} is already on the full list.'
            if email == old:
                target = entry
        if not target:
            return False, 'That full-list email was not found.'
        target['email'] = new
        save_full_mailing_list(entries)
    return True, None


def full_mailing_list_for_admin():
    rows = []
    ticket_counts = ticket_quantities_by_email()
    entries = [entry for entry in load_full_mailing_list() if isinstance(entry, dict)]
    for entry in sorted(entries, key=lambda e: e.get('added_at') or '', reverse=True):
        email = (entry.get('email') or '').strip().lower()
        if not email:
            continue
        member = get_legacy_member(email)
        rows.append({
            'email': email,
            'added_at': entry.get('added_at'),
            'source': entry.get('source') or 'manual',
            'has_account': bool(member),
            'protected': is_protected_mailing_list_email(email),
            'tickets_purchased': ticket_counts.get(email, 0),
        })
    return rows


def sync_members_into_full_mailing_list():
    """Pull non-exclusive members (including founding) into the full list."""
    emails = []
    for member in load_members():
        email = (member.get('email') or '').strip().lower()
        if not email:
            continue
        if member.get('returning_guest_discount'):
            continue
        if is_on_exclusive_invite_list(email):
            continue
        emails.append(email)
    return add_emails_to_full_mailing_list(emails, source='member')


def subscribe_signup_to_full_list(email):
    """Public self-signup → full list only if not on exclusive invite list."""
    normalized = (email or '').strip().lower()
    if not normalized:
        return
    if is_on_exclusive_invite_list(normalized):
        return
    add_emails_to_full_mailing_list([normalized], source='signup')


def resolve_broadcast_recipients(lists):
    """lists is a set like {'exclusive', 'full'}."""
    emails = set()
    if 'exclusive' in lists:
        for invite in load_invites():
            email = (invite.get('email') or '').strip().lower()
            if email:
                emails.add(email)
    if 'full' in lists:
        for entry in load_full_mailing_list():
            email = (entry.get('email') or '').strip().lower()
            if email:
                emails.add(email)
    return sorted(emails)


def mail_is_configured():
    sender = (app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME') or '').strip()
    password = (app.config.get('MAIL_PASSWORD') or '').strip()
    server = (app.config.get('MAIL_SERVER') or '').strip()
    return bool(sender and password and server)


def _broadcast_html_body(body):
    return (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        + ''.join(
            f'<p>{html.escape(line)}</p>' if line.strip() else '<br>'
            for line in body.split('\n')
        )
        + '</div>'
    )


def _send_broadcast_messages(
    subject, body, html_body, to_send, fingerprint, deadline=None,
    kind='broadcast', skip_dedupe=False,
):
    """Send to to_send. Returns (sent, failed, leftover). leftover was not attempted."""
    sent = []
    failed = []
    leftover = []
    connection_cm = None
    connection = None
    if to_send and not app.config.get('TESTING'):
        try:
            connection_cm = mail.connect()
            connection = connection_cm.__enter__()
        except Exception as e:
            print('Broadcast could not open a shared SMTP connection:', e)
            connection_cm = None
            connection = None
    try:
        for email in to_send:
            if deadline is not None and time.monotonic() >= deadline:
                leftover = [
                    remaining for remaining in to_send
                    if remaining not in sent and remaining not in failed
                ]
                print(
                    f'Broadcast paused to avoid request timeout; '
                    f'{len(leftover)} still queued'
                )
                break
            if not skip_dedupe:
                with _broadcast_delivery_lock:
                    already_delivered = (
                        _broadcast_delivery_key(fingerprint, email) in _delivered_broadcasts
                    )
                if already_delivered:
                    sent.append(email)
                    continue
            try:
                msg = Message(
                    subject,
                    sender=mail_from_address(),
                    recipients=[email],
                )
                msg.body = body
                msg.html = html_body
                if connection is not None:
                    connection.send(msg)
                else:
                    mail.send(msg)
                if not skip_dedupe:
                    mark_broadcast_delivered(fingerprint, email)
                sent.append(email)
                log_mailing_list_send(
                    kind, subject, [email], status='sent', fingerprint=fingerprint
                )
            except Exception as e:
                print(f'Broadcast email failed for {email}:', e)
                if not skip_dedupe:
                    release_broadcast_emails(fingerprint, [email])
                failed.append(email)
                log_mailing_list_send(
                    kind, subject, [email], status='failed', fingerprint=fingerprint
                )
    finally:
        if connection_cm is not None:
            try:
                connection_cm.__exit__(None, None, None)
            except Exception as e:
                print('Broadcast SMTP connection close failed:', e)
    return sent, failed, leftover


def _continue_broadcast_in_background(subject, body, html_body, recipients, fingerprint):
    global _broadcast_continue_thread

    def _run():
        with app.app_context():
            try:
                _send_broadcast_messages(
                    subject, body, html_body, recipients, fingerprint, deadline=None
                )
            except Exception as e:
                print('Background broadcast continuation failed:', e)
            finally:
                release_broadcast_emails(fingerprint, recipients)

    thread = threading.Thread(target=_run, name='broadcast-continue', daemon=True)
    _broadcast_continue_thread = thread
    thread.start()
    return thread


def send_broadcast_email(
    subject, body, recipients, continue_in_background=True, request_budget=None,
    lists=None,
):
    """Send plain/html broadcast to many recipients.

    Returns sent, failed, skipped, pending.
    pending were not attempted yet (request-time budget); they continue in a
    background thread unless continue_in_background is False.
    The same address never receives the same subject+body more than once.
    """
    subject = (subject or '').strip()
    body = (body or '').strip()
    sent = []
    failed = []
    skipped = []
    pending = []
    if not subject or not body or not recipients:
        return sent, failed, skipped, pending
    if any(c in subject for c in '\r\n'):
        return sent, failed, skipped, pending
    fingerprint = mailing_message_fingerprint('broadcast', subject, body)
    log_broadcast_message_snapshot(subject, body, lists, fingerprint)
    if not app.config.get('TESTING') and not mail_is_configured():
        print('Broadcast skipped: mail is not configured')
        return sent, list(recipients), skipped, pending
    already = emails_already_sent_message('broadcast', subject, body)
    unique = []
    seen = set()
    for email in recipients:
        normalized = (email or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in already:
            skipped.append(normalized)
        else:
            unique.append(normalized)
    to_send = claim_broadcast_emails(fingerprint, unique)
    claimed = set(to_send)
    for email in unique:
        if email not in claimed:
            skipped.append(email)
    if not to_send:
        return sent, failed, skipped, pending
    html_body = _broadcast_html_body(body)
    deadline = None
    if request_budget is None and continue_in_background:
        try:
            request_budget = float(os.getenv('BROADCAST_REQUEST_BUDGET', '20'))
        except (TypeError, ValueError):
            request_budget = 20.0
    if request_budget is not None and request_budget > 0:
        deadline = time.monotonic() + request_budget
    with app.app_context():
        sent, failed, leftover = _send_broadcast_messages(
            subject, body, html_body, to_send, fingerprint, deadline=deadline
        )
    pending = leftover
    if leftover and continue_in_background:
        print(
            f'Continuing broadcast to {len(leftover)} remaining addresses '
            f'in the background'
        )
        _continue_broadcast_in_background(
            subject, body, html_body, leftover, fingerprint
        )
    elif leftover:
        release_broadcast_emails(fingerprint, leftover)
    return sent, failed, skipped, pending


def test_mail_recipients():
    """Only these addresses may receive a test send."""
    return sorted(PROTECTED_MAILING_LIST_EMAILS)


def send_test_broadcast_email(subject, body):
    """Send a test copy to Hallie and events only. Never uses the mailing lists."""
    subject = (subject or '').strip()
    body = (body or '').strip()
    sent, failed, skipped, pending = [], [], [], []
    if not subject or not body or any(c in subject for c in '\r\n'):
        return sent, failed, skipped, pending
    if not subject.upper().startswith('[TEST]'):
        subject = f'[TEST] {subject}'
    recipients = [
        email for email in test_mail_recipients()
        if is_valid_email(email)
    ]
    if not recipients:
        return sent, failed, skipped, pending
    if not app.config.get('TESTING') and not mail_is_configured():
        print('Test broadcast skipped: mail is not configured')
        return sent, list(recipients), skipped, pending
    fingerprint = mailing_message_fingerprint('test', subject, body) + secrets.token_hex(4)
    log_broadcast_message_snapshot(
        subject, body, [], fingerprint, kind='test', action='test_message',
    )
    html_body = _broadcast_html_body(body)
    with app.app_context():
        sent, failed, leftover = _send_broadcast_messages(
            subject, body, html_body, recipients, fingerprint,
            deadline=None, kind='test', skip_dedupe=True,
        )
    return sent, failed, skipped, leftover



def clear_returning_guest_discount_if_purchased(email):
    """No-op: exclusive members keep 20% on one single ticket per event."""
    return


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


def member_has_returning_guest_discount(member):
    return bool(member and member.get('returning_guest_discount'))


def ensure_returning_guest_flag_for_exclusive_member(member):
    """Exclusive-list emails keep the one-per-event single-ticket perk even if they signed up without the invite link."""
    if not member:
        return member
    if member.get('returning_guest_discount'):
        return member
    email = (member.get('email') or '').strip().lower()
    if not email or not is_on_exclusive_invite_list(email):
        return member
    with members_lock:
        members = load_members()
        for stored in members:
            if stored.get('email', '').strip().lower() == email:
                stored['returning_guest_discount'] = True
                if not stored.get('discount_code'):
                    code = generate_discount_code(email)
                    while discount_code_taken(code, exclude_email=email):
                        code = generate_discount_code(email)
                    stored['discount_code'] = code
                save_members(members)
                member = stored
                break
    return member


def member_discount_eligible(member):
    if not member:
        return False
    member = ensure_returning_guest_flag_for_exclusive_member(member)
    return member_has_past_purchases(member) or member_has_returning_guest_discount(member)


def member_discount_active():
    if not is_legacy_member_logged_in():
        return False
    member = get_logged_in_member()
    return member_discount_eligible(member)


def resolve_member_discount_application(requested):
    if not requested:
        return False
    return member_discount_active()


def exclusive_hold_key(email, event_id):
    return f'{(email or "").strip().lower()}|{(event_id or "").strip()}'


def load_exclusive_holds():
    if not ensure_data_dir(exclusive_holds_file):
        return {}
    data = _locked_json_read(exclusive_holds_file, {})
    return data if isinstance(data, dict) else {}


def save_exclusive_holds(holds):
    if not ensure_data_dir(exclusive_holds_file):
        return False
    return _locked_json_write(exclusive_holds_file, holds)


def prune_exclusive_holds(holds):
    now = datetime.now(timezone.utc)
    changed = False
    for key, hold in list((holds or {}).items()):
        expires_raw = (hold or {}).get('expires_at')
        expired = True
        if expires_raw:
            try:
                expires = datetime.fromisoformat(str(expires_raw).replace('Z', '+00:00'))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                expired = now >= expires
            except ValueError:
                expired = True
        if expired:
            holds.pop(key, None)
            changed = True
    return changed


def exclusive_hold_active(email, event_id):
    email = (email or '').strip().lower()
    event_id = (event_id or '').strip()
    if not email or not event_id:
        return False
    with exclusive_holds_lock:
        holds = load_exclusive_holds()
        if prune_exclusive_holds(holds):
            save_exclusive_holds(holds)
        return exclusive_hold_key(email, event_id) in holds


def reserve_exclusive_single_rate(email, event_id):
    """Atomically claim the 20% single-ticket perk for this email + event."""
    email = (email or '').strip().lower()
    event_id = (event_id or '').strip()
    if not email or not event_id:
        return False
    now = datetime.now(timezone.utc)
    with exclusive_holds_lock:
        holds = load_exclusive_holds()
        prune_exclusive_holds(holds)
        key = exclusive_hold_key(email, event_id)
        if key in holds:
            return False
        member = get_legacy_member(email)
        if exclusive_single_rate_used_for_event(member, event_id):
            return False
        holds[key] = {
            'email': email,
            'event_id': event_id,
            'created_at': now.isoformat(),
            'expires_at': (now + EXCLUSIVE_HOLD_TTL).isoformat(),
            'checkout_session_id': None,
        }
        save_exclusive_holds(holds)
        return True


def bind_exclusive_hold(email, event_id, checkout_session_id):
    email = (email or '').strip().lower()
    event_id = (event_id or '').strip()
    if not email or not event_id:
        return False
    with exclusive_holds_lock:
        holds = load_exclusive_holds()
        prune_exclusive_holds(holds)
        hold = holds.get(exclusive_hold_key(email, event_id))
        if not hold:
            return False
        hold['checkout_session_id'] = checkout_session_id
        save_exclusive_holds(holds)
        return True


def release_exclusive_hold(email, event_id):
    email = (email or '').strip().lower()
    event_id = (event_id or '').strip()
    if not email or not event_id:
        return False
    with exclusive_holds_lock:
        holds = load_exclusive_holds()
        prune_exclusive_holds(holds)
        if holds.pop(exclusive_hold_key(email, event_id), None) is None:
            if prune_exclusive_holds(holds):
                save_exclusive_holds(holds)
            return False
        save_exclusive_holds(holds)
        return True


def exclusive_single_rate_used_for_event(member, event_id):
    """True when this exclusive member already used the 20% single-ticket rate on this event."""
    if not member:
        return False
    email = (member.get('email') or '').strip().lower()
    target = (event_id or '').strip()
    if not email or not target:
        return False
    for ticket in load_tickets():
        if (ticket.get('email') or '').strip().lower() != email:
            continue
        if not ticket_belongs_to_event(ticket, target):
            continue
        if ticket.get('exclusive_single_rate'):
            return True
        # Older singles bought with a discount, before this flag existed.
        if int(ticket.get('quantity') or 1) == 1 and ticket.get('legacy_discount'):
            return True
    return False


def exclusive_single_rate_available(member, event_id=None, quantity=1, honor_holds=True):
    if not member_has_returning_guest_discount(member):
        return False
    if max(1, int(quantity or 1)) != 1:
        return False
    target = (event_id or '').strip() or get_sales_event_id()
    if not target:
        return False
    if exclusive_single_rate_used_for_event(member, target):
        return False
    if honor_holds:
        email = (member.get('email') or '').strip().lower()
        if exclusive_hold_active(email, target):
            return False
    return True


def active_member_discount_rate(quantity=1, require_active=True, event_id=None, exclusive_reserved=None):
    """Percent rate (0–1) for the logged-in member at this quantity.

    Exclusive-list members get 20% off one single ticket per event.
    After that, or for 2+ tickets, they use the standard member rate (10%).

    If require_active is False, returns the eligible rate even when the code is not applied.
    exclusive_reserved=True forces the 20% single rate after this checkout claimed it.
    """
    member = get_logged_in_member()
    if member:
        member = ensure_returning_guest_flag_for_exclusive_member(member)
    if require_active and not member_discount_active():
        return 0.0
    if not member or not member_discount_eligible(member):
        return 0.0
    quantity = max(1, int(quantity or 1))
    exclusive_ok = exclusive_reserved if exclusive_reserved is not None else exclusive_single_rate_available(
        member, event_id, quantity
    )
    if exclusive_ok:
        rate = returning_guest_discount if returning_guest_discount > 0 else member_discount
        return rate if rate > 0 else 0.20
    rate = member_discount if member_discount > 0 else 0.0
    return rate if rate > 0 else 0.10




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
    logged_in_email = ''
    if has_request_context():
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


def calculate_total_cents(ticket_type, quantity, apply_member_discount=False, event_id=None, exclusive_reserved=None):
    """Price with optional member/exclusive discount.

    Exclusive members: 20% off one single ticket per event.
    Two or more tickets, or a later single after that perk is used, use the
    normal member rate (default 10%) and can stack with bulk.
    """
    quantity = max(1, int(quantity or 1))
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
    base_total = base * quantity

    if not apply_member_discount:
        return calculate_bulk_total_cents(ticket_type, quantity)

    rate = active_member_discount_rate(
        quantity, event_id=event_id, exclusive_reserved=exclusive_reserved
    )
    if rate <= 0:
        return calculate_bulk_total_cents(ticket_type, quantity)

    # Single-ticket exclusive rate does not use bulk (qty is 1).
    if bulk_discount_applies(ticket_type, quantity):
        return int(base_total * (1 - bulk_discount_rate(ticket_type) - rate))

    return int(base_total * (1 - rate))


def calculate_unit_price(ticket_type, quantity, apply_member_discount=False, event_id=None, exclusive_reserved=None):
    if quantity < 1:
        quantity = 1
    return calculate_total_cents(
        ticket_type, quantity, apply_member_discount, event_id=event_id,
        exclusive_reserved=exclusive_reserved,
    ) // quantity


def pricing_breakdown(ticket_type, quantity, apply_member_discount=False, event_id=None, exclusive_reserved=None):
    quantity = max(1, int(quantity or 1))
    base = TICKET_TYPES[ticket_type]['price_cents']
    base_total_cents = base * quantity
    bulk_only_total = calculate_bulk_total_cents(ticket_type, quantity)
    eligible_rate = active_member_discount_rate(
        quantity, require_active=False, event_id=event_id, exclusive_reserved=exclusive_reserved
    )
    rate = eligible_rate if apply_member_discount else 0.0
    total_cents = calculate_total_cents(
        ticket_type, quantity, apply_member_discount, event_id=event_id,
        exclusive_reserved=exclusive_reserved,
    )
    unit_price = total_cents // quantity

    bulk_savings_active = bulk_only_total < base_total_cents
    member_requested = apply_member_discount and rate > 0
    stacked_discount_applied = (
        bulk_savings_active and member_requested and total_cents < bulk_only_total
    )

    member_only_total = (
        int(base_total_cents * (1 - rate))
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
        combined_discount_percent = int((bulk_discount_rate(ticket_type) + rate) * 100)

    member = get_logged_in_member()
    is_returning = bool(member and member_has_returning_guest_discount(member))
    if exclusive_reserved:
        exclusive_single_available = True
    else:
        exclusive_single_available = exclusive_single_rate_available(member, event_id, quantity)
    exclusive_single_applied = bool(
        apply_member_discount and exclusive_single_available and quantity == 1 and rate >= 0.20
    )
    # Percent shown for the code at this quantity (20% unused exclusive single, else 10%).
    display_member_percent = int(round(rate * 100)) if rate > 0 else int(member_discount * 100)
    if not apply_member_discount and eligible_rate > 0:
        display_member_percent = int(round(eligible_rate * 100))

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
        'member_discount_percent': display_member_percent,
        'returning_guest_discount': is_returning,
        'returning_guest_single_ticket_rate': exclusive_single_applied,
        'exclusive_single_available': exclusive_single_available,
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


def ticket_result_meta(record, admission_as=None):
    ticket_type = record.get('ticket_type', 'general')
    admitted = admission_as or record.get('admission_as')
    # Door display: how they entered this scan (VIP ticket may enter as GA when VIP full).
    is_vip_entry = (admitted or ticket_type) == 'vip'
    access = record.get('access') or TICKET_TYPES.get(ticket_type, {}).get('access')
    return {
        'ticket_type': ticket_type,
        'access': access if is_vip_entry else None,
        'is_vip': is_vip_entry,
        'admission_as': admitted or ('vip' if ticket_type == 'vip' else 'ga'),
        'ticket_is_vip': ticket_type == 'vip',
        'vip_overflow_note': None,
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


def mark_ticket_scanned(ticket_id, admission_as=None):
    """Record door entry. admission_as is 'vip' or 'ga'.

    VIP tickets admitted as GA (VIP area full) do not set vip_redeemed_at so the
    ticket can still be treated as VIP after counts reset if you re-issue policy.
    """
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return False
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if normalize_ticket_id(ticket.get('ticket_id')) == normalized:
                if ticket.get('scanned_at'):
                    return False
                ticket_type = ticket.get('ticket_type', 'general')
                entry = admission_as or ('vip' if ticket_type == 'vip' else 'ga')
                if entry not in ('vip', 'ga'):
                    entry = 'ga'
                now_iso = datetime.now(timezone.utc).isoformat()
                ticket['scanned_at'] = now_iso
                ticket['admission_as'] = entry
                if entry == 'vip':
                    ticket['vip_redeemed_at'] = now_iso
                if not save_tickets(tickets):
                    ticket['scanned_at'] = None
                    ticket.pop('admission_as', None)
                    if entry == 'vip':
                        ticket.pop('vip_redeemed_at', None)
                    return False
                return True
    return False


def get_counting_epoch():
    settings = load_scanner_settings()
    return parse_iso_datetime(settings.get('counting_epoch'))


def get_sales_epoch():
    settings = load_scanner_settings()
    return parse_iso_datetime(settings.get('sales_epoch'))


def new_event_id():
    return secrets.token_hex(8)


def get_current_event_id():
    settings = load_scanner_settings()
    event_id = (settings.get('current_event_id') or '').strip()
    return event_id or None


def ensure_current_event_id():
    existing = get_door_event_id()
    if existing and get_event(existing):
        return existing
    seeded = ensure_halloween_event()
    if seeded:
        set_door_event_id(seeded['id'])
        return seeded['id']
    return existing


def event_looks_like_halloween(event):
    if not event:
        return False
    event_id = (event.get('id') or '').strip().lower()
    name = (event.get('name') or '').strip().lower()
    date_value = (event.get('date') or '')[:10]
    if event_id == HALLOWEEN_EVENT_SLUG:
        return True
    if name == 'halloween' or 'halloween' in name:
        return True
    return date_value == HALLOWEEN_EVENT_DATE


def resolved_ticket_event_id(record):
    """Night this ticket is valid for.

    Catalog stamps win. Untagged / unknown stamps are the original Halloween sale.
    """
    if not record:
        return None
    stamped = (record.get('event_id') or '').strip()
    if stamped and get_event(stamped):
        return stamped
    halloween = find_halloween_event()
    return (halloween.get('id') if halloween else HALLOWEEN_EVENT_SLUG)


def ticket_belongs_to_event(record, event_id):
    """True only when this ticket was sold for the door event being scanned."""
    if not record:
        return False
    target_id = (event_id or '').strip()
    if not target_id:
        return False
    return resolved_ticket_event_id(record) == target_id


def ticket_belongs_to_current_event(record):
    """True when this ticket was sold for the door/scanner event."""
    return ticket_belongs_to_event(record, get_door_event_id())


def ticket_counts_for_current_period(scanned_at):
    """Whether a scan should count toward the live GA/VIP/total boards."""
    scanned = parse_iso_datetime(scanned_at)
    if not scanned:
        return False
    counting_epoch = get_counting_epoch()
    if counting_epoch is None:
        return True
    return scanned >= counting_epoch


def ticket_validity_cutoff():
    """First instant (app timezone) at which a purchase is still valid."""
    raw = TICKET_VALIDITY_CUTOFF_DATE
    try:
        year, month, day = (int(part) for part in raw.split('-')[:3])
    except Exception:
        year, month, day = 2026, 9, 8
    try:
        tz = get_display_timezone()
    except Exception:
        try:
            tz = ZoneInfo(APP_TIMEZONE)
        except Exception:
            tz = timezone.utc
    return datetime(year, month, day, tzinfo=tz)


def ticket_is_valid_purchase(ticket):
    """False for test/old sales from before the validity cutoff."""
    purchased = parse_iso_datetime((ticket or {}).get('purchased_at'))
    if not purchased:
        return False
    cutoff = ticket_validity_cutoff()
    if purchased.tzinfo is None:
        purchased = purchased.replace(tzinfo=timezone.utc)
    return purchased >= cutoff.astimezone(timezone.utc)


def ticket_counts_for_current_sales_period(purchased_at):
    """Whether a purchase counts toward the live ticket-sales cap."""
    if not ticket_is_valid_purchase({'purchased_at': purchased_at}):
        return False
    epoch = get_sales_epoch()
    if epoch is None:
        return True
    purchased = parse_iso_datetime(purchased_at)
    if not purchased:
        return False
    return purchased >= epoch


def get_reset_history():
    settings = load_scanner_settings()
    history = settings.get('reset_history', [])
    if not isinstance(history, list):
        return []
    # Ensure each entry has a stable id for delete buttons.
    changed = False
    for entry in history:
        if isinstance(entry, dict) and not entry.get('id'):
            entry['id'] = entry.get('reset_at') or secrets.token_hex(8)
            changed = True
    if changed:
        with scanner_settings_lock:
            settings = load_scanner_settings()
            settings['reset_history'] = history[-50:]  # bound growth
            save_scanner_settings(settings)
    return history


def delete_reset_history_entry(entry_id):
    """Remove one reset history row by id (or legacy reset_at string)."""
    target = (entry_id or '').strip()
    if not target:
        return False
    with scanner_settings_lock:
        settings = load_scanner_settings()
        history = settings.get('reset_history', [])
        if not isinstance(history, list):
            return False
        updated = []
        removed = False
        for entry in history:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get('id') or entry.get('reset_at') or '')
            if not removed and eid == target:
                removed = True
                continue
            updated.append(entry)
        if not removed:
            return False
        settings['reset_history'] = updated
        save_scanner_settings(settings)
        return True


def reset_admission_counts():
    """Zero live counts for a new period WITHOUT making old tickets reusable.

    Scanned tickets keep scanned_at forever (void). Counts only include scans
    at/after counting_epoch. Each reset is logged for the door team.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    counts = compute_admission_counts()

    with scanner_settings_lock:
        settings = load_scanner_settings()
        history = settings.get('reset_history', [])
        if not isinstance(history, list):
            history = []
        history.append({
            'id': secrets.token_hex(8),
            'reset_at': now_iso,
            'ga': counts['ga'],
            'vip': counts['vip'],
            'total': counts['total'],
            'free_girls': counts.get('free_girls', 0),
        })
        # Keep last 50 resets
        settings['reset_history'] = history[-50:]
        settings['counting_epoch'] = now_iso
        # First-hour comps follow the same counting period as scanned tickets.
        settings['free_girls_entries'] = []
        save_scanner_settings(settings)

    return {
        'reset_at': now_iso,
        'ga': counts['ga'],
        'vip': counts['vip'],
        'total': counts['total'],
        'free_girls': counts.get('free_girls', 0),
        'cleared': 0,  # tickets stay void; not cleared
    }


def reset_ticket_sales():
    """Deprecated: create and feature a new event in Admin → Events instead."""
    sales = compute_ticket_sales_counts(get_door_event_id())
    return {
        'reset_at': datetime.now(timezone.utc).isoformat(),
        'sold': sales['sold'],
        'ga': sales['ga'],
        'vip': sales['vip'],
        'event_id': get_door_event_id(),
        'error': 'Create a new event in Admin to start the next sale.',
    }


# Back-compat alias used by older call sites
def reset_all_ticket_scans():
    result = reset_admission_counts()
    return result.get('cleared', 0)


def admin_authenticated():
    return session.get('admin_authenticated') is True


def is_staff_email(email):
    """Staff emails come from VERIFY_LOGIN_EMAIL (comma-separated)."""
    normalized = (email or '').strip().lower()
    if not normalized or not verify_login_emails:
        return False
    return any(secure_equals(normalized, allowed) for allowed in verify_login_emails)


def is_scanner_admin_member():
    """True when a staff email is signed into the member portal."""
    member = get_logged_in_member()
    if not member:
        return False
    return is_staff_email(member.get('email'))


def verify_scanner_session_authenticated():
    if session.get('verify_authenticated') is not True:
        return False
    return is_staff_email(session.get('verify_login_email'))


def is_staff_user():
    """Staff via member portal login or dedicated scanner/admin session."""
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def mark_scanner_session_authenticated(email=None):
    session['verify_authenticated'] = True
    chosen = (email or '').strip().lower()
    if not is_staff_email(chosen):
        chosen = next(iter(sorted(verify_login_emails)), '')
    session['verify_login_email'] = chosen
    session.permanent = True
    session.modified = True


def require_admin():
    """Admin dashboard access: explicit admin session, or staff member/scanner session."""
    if admin_authenticated():
        return True
    if is_staff_user():
        session['admin_authenticated'] = True
        return True
    return False


def grant_session_ticket_access(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return
    access = dict(session.get('ticket_access') or {})
    access[normalized] = True
    # Bound cookie growth
    if len(access) > 30:
        # keep most recent keys only
        access = dict(list(access.items())[-30:])
    session['ticket_access'] = access
    touch_auth_session()


def ensure_ticket_view_token(record):
    if not record:
        return None
    token = record.get('view_token')
    if token:
        return token
    token = new_view_token()
    ticket_id = normalize_ticket_id(record.get('ticket_id'))
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if normalize_ticket_id(ticket.get('ticket_id')) == ticket_id:
                ticket['view_token'] = token
                save_tickets(tickets)
                record['view_token'] = token
                break
    return token


def can_view_ticket(record, provided_token=None):
    if not record:
        return False
    if admin_authenticated() or verify_authenticated():
        return True
    expected = record.get('view_token')
    if provided_token and expected:
        try:
            if secure_equals(str(provided_token), str(expected)):
                return True
        except (TypeError, ValueError):
            pass
    ticket_id = normalize_ticket_id(record.get('ticket_id'))
    if ticket_id and (session.get('ticket_access') or {}).get(ticket_id):
        return True
    member = get_logged_in_member()
    if member:
        member_email = (member.get('email') or '').strip().lower()
        ticket_email = (record.get('email') or '').strip().lower()
        if member_email and ticket_email and secure_equals(member_email, ticket_email):
            return True
        saved = member.get('saved_tickets') or []
        if ticket_id and ticket_id in [normalize_ticket_id(t) for t in saved]:
            return True
    return False


def checkout_session_is_paid(checkout_session):
    if not checkout_session:
        return False
    payment_status = (getattr(checkout_session, 'payment_status', None) or '').lower()
    status = (getattr(checkout_session, 'status', None) or '').lower()
    if payment_status == 'paid':
        return True
    # Some API shapes expose dicts
    if isinstance(checkout_session, dict):
        payment_status = (checkout_session.get('payment_status') or '').lower()
        status = (checkout_session.get('status') or '').lower()
        if payment_status == 'paid':
            return True
        return status == 'complete' and payment_status in ('paid', 'no_payment_required')
    return status == 'complete' and payment_status in ('paid', 'no_payment_required')


def fulfill_paid_checkout(checkout_session):
    """Idempotently create ticket records for a paid Stripe Checkout session."""
    if not checkout_session_is_paid(checkout_session):
        raise ValueError('Checkout session is not paid')
    if isinstance(checkout_session, dict):
        session_id = checkout_session.get('id')
        metadata = checkout_session.get('metadata') or {}
        customer_details = checkout_session.get('customer_details') or {}
        stripe_email = customer_details.get('email')
        line_items = checkout_session.get('line_items') or {}
        line_data = line_items.get('data') if isinstance(line_items, dict) else None
        quantity = 1
        if line_data:
            quantity = int(line_data[0].get('quantity') or 1)
    else:
        session_id = checkout_session.id
        metadata = checkout_session.metadata or {}
        stripe_email = None
        if checkout_session.customer_details:
            stripe_email = checkout_session.customer_details.email
        quantity = 1
        line_items = getattr(checkout_session, 'line_items', None)
        if line_items and getattr(line_items, 'data', None):
            quantity = int(line_items.data[0].quantity or 1)

    existing = get_ticket_by_session(session_id)
    if existing:
        return existing

    try:
        quantity = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        quantity = 1
    ticket_id = new_ticket_id()
    ticket_type = metadata.get('ticket_type', 'general')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    legacy_discount = metadata.get('legacy_discount') == 'true'
    delivery_email = ticket_recipient_email(stripe_email, metadata)
    view_token = new_view_token()

    ticket = record_ticket(
        session_id, ticket_id, delivery_email, quantity,
        ticket_type=ticket_type, legacy_discount=legacy_discount,
        view_token=view_token,
        event_id=metadata.get('event_id') or get_sales_event_id(),
        exclusive_single_rate=metadata.get('exclusive_single_rate') == 'true',
        door_sale=metadata.get('door_sale') == 'true',
    )
    if delivery_email:
        release_exclusive_hold(delivery_email, ticket.get('event_id'))
        purchased_member = get_legacy_member(delivery_email)
        if purchased_member:
            add_saved_ticket_for_member(delivery_email, ticket.get('ticket_id', ticket_id))
            refreshed = get_legacy_member(delivery_email)
            if refreshed and member_has_past_purchases(refreshed):
                ensure_member_discount_code(refreshed)
    return ticket


def payment_intent_is_paid(payment_intent):
    if not payment_intent:
        return False
    status = getattr(payment_intent, 'status', None)
    if status is None and isinstance(payment_intent, dict):
        status = payment_intent.get('status')
    return (status or '').lower() == 'succeeded'


def payment_intent_email(payment_intent, metadata=None):
    metadata = metadata or {}
    if isinstance(payment_intent, dict):
        email = (payment_intent.get('receipt_email') or '').strip().lower()
        charges = ((payment_intent.get('charges') or {}).get('data') or [])
        if not email and charges:
            email = ((charges[0].get('billing_details') or {}).get('email') or '').strip().lower()
    else:
        email = (getattr(payment_intent, 'receipt_email', None) or '').strip().lower()
        charges = getattr(payment_intent, 'charges', None)
        data = getattr(charges, 'data', None) if charges else None
        if not email and data:
            details = getattr(data[0], 'billing_details', None)
            email = (getattr(details, 'email', None) or '').strip().lower()
    return ticket_recipient_email(email, metadata)


def payment_intent_quantity(payment_intent, metadata=None):
    metadata = metadata or {}
    try:
        qty = int(metadata.get('quantity') or 1)
    except (TypeError, ValueError):
        qty = 1
    if qty >= 1:
        return qty
    return 1


def admit_paid_door_ticket(ticket):
    """Walk-up is already at the door: count them in after payment."""
    if not ticket:
        return ticket, None
    if ticket.get('scanned_at'):
        return ticket, None
    ticket_id = ticket.get('ticket_id')
    quantity = int(ticket.get('quantity') or 1)
    ticket_type = ticket.get('ticket_type', 'general')
    admission_as = 'vip' if ticket_type == 'vip' else 'ga'
    vip_note = None
    if ticket_type == 'vip':
        vip_left = vip_capacity_remaining()
        if vip_left is not None and quantity > vip_left:
            admission_as = 'ga'
            vip_note = 'VIP area full — admitted as GA.'
    mark_ticket_scanned(ticket_id, admission_as=admission_as)
    return get_ticket_record(ticket_id) or ticket, vip_note


def fulfill_paid_payment_intent(payment_intent):
    """Idempotently create (and door-admit) a ticket from a succeeded PaymentIntent."""
    if not payment_intent_is_paid(payment_intent):
        raise ValueError('Payment is not complete')
    if isinstance(payment_intent, dict):
        intent_id = payment_intent.get('id')
        metadata = payment_intent.get('metadata') or {}
    else:
        intent_id = payment_intent.id
        metadata = payment_intent.metadata or {}
    existing = get_ticket_by_session(intent_id)
    if existing:
        if metadata.get('door_sale') == 'true' and not existing.get('scanned_at'):
            existing, _note = admit_paid_door_ticket(existing)
        return existing

    ticket_type = metadata.get('ticket_type', 'general')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    quantity = payment_intent_quantity(payment_intent, metadata)
    ticket = record_ticket(
        intent_id, new_ticket_id(), payment_intent_email(payment_intent, metadata), quantity,
        ticket_type=ticket_type,
        legacy_discount=False,
        event_id=metadata.get('event_id') or get_door_event_id() or get_sales_event_id(),
        exclusive_single_rate=False,
        door_sale=metadata.get('door_sale') == 'true',
    )
    if metadata.get('door_sale') == 'true':
        ticket, _note = admit_paid_door_ticket(ticket)
    return ticket


def _stripe_get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def payment_intent_amount_cents(payment_intent):
    try:
        return int(_stripe_get(payment_intent, 'amount') or 0)
    except (TypeError, ValueError):
        return 0


def payment_is_card_present(payment_intent):
    """True for Stripe app / Terminal Tap to Pay (card held to the phone or reader)."""
    types = list(_stripe_get(payment_intent, 'payment_method_types') or [])
    if 'card_present' in types:
        return True
    charges = []
    raw_charges = _stripe_get(payment_intent, 'charges')
    if isinstance(raw_charges, dict):
        charges.extend(raw_charges.get('data') or [])
    elif raw_charges is not None:
        charges.extend(getattr(raw_charges, 'data', None) or [])
    latest = _stripe_get(payment_intent, 'latest_charge')
    if isinstance(latest, dict):
        charges.append(latest)
    for charge in charges:
        details = _stripe_get(charge, 'payment_method_details')
        detail_type = _stripe_get(details, 'type') if details is not None else None
        if detail_type in ('card_present', 'interac_present'):
            return True
    return False


def door_ticket_type_from_in_person_amount(amount_cents):
    """Stripe app charges a single amount: $15 GA or $30 VIP."""
    if amount_cents == door_unit_price_cents('vip'):
        return 'vip'
    if amount_cents == door_unit_price_cents('general'):
        return 'general'
    return None


def fulfill_in_person_door_payment(payment_intent):
    """Admit a Stripe-app / Tap to Pay charge on the live door count.

    Only card-present payments of door GA ($15) or VIP ($30). Online checkouts
    are card-not-present and stay on the existing Checkout webhook.
    """
    if not payment_intent_is_paid(payment_intent):
        return None
    intent_id = _stripe_get(payment_intent, 'id')
    if not intent_id:
        return None
    existing = get_ticket_by_session(intent_id)
    if existing:
        if existing.get('door_sale') and not existing.get('scanned_at'):
            existing, _note = admit_paid_door_ticket(existing)
        return existing

    metadata = _stripe_get(payment_intent, 'metadata') or {}
    if isinstance(metadata, dict) and metadata.get('door_sale') == 'true':
        return fulfill_paid_payment_intent(payment_intent)

    if not payment_is_card_present(payment_intent):
        return None
    ticket_type = door_ticket_type_from_in_person_amount(
        payment_intent_amount_cents(payment_intent)
    )
    if not ticket_type:
        print(
            f'In-person payment {intent_id} amount '
            f'{payment_intent_amount_cents(payment_intent)}c is not door GA/VIP'
        )
        return None

    email = payment_intent_email(
        payment_intent, metadata if isinstance(metadata, dict) else {}
    )
    ticket = record_ticket(
        intent_id,
        new_ticket_id(),
        email,
        1,
        ticket_type=ticket_type,
        event_id=get_door_event_id() or get_sales_event_id(),
        door_sale=True,
    )
    ticket, _note = admit_paid_door_ticket(ticket)
    return ticket


def verify_auth_configured():
    return bool(verify_login_emails and verify_login_password)


def verify_authenticated():
    if not verify_auth_configured():
        return False
    # Staff member portal login counts as scanner-authorized.
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def verify_scanner_credentials(email, password):
    """Staff form: VERIFY_LOGIN email + shared password, or that member's portal password."""
    if not verify_auth_configured():
        return False
    candidate = (email or '').strip().lower()
    password = (password or '').strip()
    if not candidate or not password:
        return False
    if not is_staff_email(candidate):
        return False
    if secure_equals(password, verify_login_password):
        return True
    # Same person often uses the member portal password.
    return verify_legacy_login(candidate, password)


def protect_scanner_response():
    if not verify_auth_configured():
        message = 'Scanner login is not configured. Set VERIFY_LOGIN_EMAIL and VERIFY_LOGIN_PASSWORD.'
        if request.method == 'POST' or request.is_json:
            return jsonify({'error': message}), 503
        return render_template('verify_login.html', error=message), 503

    if verify_authenticated():
        if is_scanner_admin_member() and not verify_scanner_session_authenticated():
            mark_scanner_session_authenticated()
        return None

    if request.method == 'POST' or request.is_json:
        return jsonify({'error': 'Unauthorized'}), 401

    next_url = request.full_path if request.query_string else request.path
    if next_url.endswith('?'):
        next_url = next_url[:-1]
    return redirect(url_for('verify_login', next=safe_next_url(next_url, '/verify')))


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


def ticket_display_url(ticket_id, view_token=None):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return None
    if not view_token:
        record = get_ticket_record(normalized)
        view_token = ensure_ticket_view_token(record) if record else None
    if view_token:
        return f"{base_url}/t/{normalized}?{urlencode({'k': view_token})}"
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
                'value': (
                    (ticket_event_record(get_ticket_record(ticket_id)) or {}).get('name')
                    or 'The Section'
                ),
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
_founding = [e for e in verify_login_emails]
if _founding:
    add_emails_to_full_mailing_list(_founding, source='founding')
log_storage_state()
# Event catalog is seeded after helper functions are defined (see seed_default_event call below).


def extract_ticket_id_from_url(raw):
    for marker in ('/verify/t/', '/t/'):
        if marker in raw:
            ticket_id = raw.split(marker)[-1].split('?')[0].split('/')[0].strip()
            return normalize_ticket_id(ticket_id)
    return None


def load_scanner_settings():
    if not ensure_data_dir(scanner_settings_file):
        return {}
    data = _locked_json_read(scanner_settings_file, {})
    return data if isinstance(data, dict) else {}


def save_scanner_settings(settings):
    if not ensure_data_dir(scanner_settings_file):
        return False
    return _locked_json_write(scanner_settings_file, settings)


def load_events():
    if not ensure_data_dir(events_file):
        return []
    data = _locked_json_read(events_file, [])
    if not isinstance(data, list):
        return []
    return [normalize_event(item) for item in data if isinstance(item, dict)]


def save_events(events):
    if not ensure_data_dir(events_file):
        return False
    cleaned = [normalize_event(item) for item in events if isinstance(item, dict)]
    return _locked_json_write(events_file, cleaned)


def parse_event_date(raw):
    value = (raw or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def coerce_sales_open(value, default=True):
    """Treat common falsey form/JSON values as coming-soon, not on sale."""
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('0', 'false', 'no', 'off', 'closed', 'teaser', 'coming-soon', 'coming soon'):
            return False
        if normalized in ('1', 'true', 'yes', 'on', 'open'):
            return True
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def event_is_sales_open(event):
    if not event:
        return False
    return coerce_sales_open(event.get('sales_open'), default=True)


def resolve_event_teaser(data, sales_open):
    """Old events with no teaser field were coming-soon whenever they were not on sale."""
    if isinstance(data, dict) and 'teaser' in data:
        return coerce_sales_open(data.get('teaser'), default=False)
    return not sales_open


def event_is_teaser(event):
    if not event:
        return False
    return coerce_sales_open(event.get('teaser'), default=False)


def _day_ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return f'{day}{suffix}'


def format_clock_label(raw):
    value = (raw or '').strip()
    if not value:
        return ''
    for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I:%M%p', '%I:%M %P'):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime('%I:%M %p').lstrip('0')
        except ValueError:
            continue
    return value


def format_event_headline(date_str, headline=None):
    custom = (headline or '').strip()
    if custom:
        return custom
    day = parse_event_date(date_str)
    if not day:
        return ''
    return f"{day.strftime('%B')} {_day_ordinal(day.day)}"


def format_event_date_line(date_str):
    day = parse_event_date(date_str)
    if not day:
        return ''
    return f"{day.strftime('%A')}, {day.strftime('%B')} {_day_ordinal(day.day)}"


def format_event_time_line(start, end):
    start_label = format_clock_label(start)
    end_label = format_clock_label(end)
    if start_label and end_label:
        return f'{start_label} – {end_label}'
    return start_label or end_label or ''


def ticket_price_line():
    ga = TICKET_TYPES['general']['price_cents'] // 100
    vip = TICKET_TYPES['vip']['price_cents'] // 100
    ga_pct = int(round(bundle_discount * 100))
    vip_pct = int(round(vip_bulk_discount * 100))
    return (
        f'GA ${ga} · VIP ${vip} · {ga_pct}% off at {bundle_min}+ GA '
        f'or {vip_bundle_min}+ VIP'
    )


def flyer_is_safe_filename(filename):
    name = os.path.basename((filename or '').strip())
    if not name or name != filename:
        return False
    if '..' in name or '/' in name or '\\' in name:
        return False
    return re.fullmatch(r'[A-Za-z0-9._-]+', name) is not None


def detect_image_extension(data):
    if not data or len(data) < 12:
        return None
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    return None


def event_flyer_url(event):
    if not event:
        return None
    filename = (event.get('flyer_filename') or '').strip()
    if filename and flyer_is_safe_filename(filename):
        return f'/media/flyers/{filename}'
    static_name = (event.get('flyer_static') or '').strip().lstrip('/')
    if static_name:
        return f'/static/{static_name}'
    return None


def normalize_event(raw):
    data = raw if isinstance(raw, dict) else {}
    event_id = (data.get('id') or '').strip() or new_event_id()
    date_value = ''
    parsed_date = parse_event_date(data.get('date'))
    if parsed_date:
        date_value = parsed_date.isoformat()
    ticket_cap = parse_max_capacity(data.get('ticket_cap'))
    vip_cap = parse_max_capacity(data.get('vip_cap'))
    sales_open = coerce_sales_open(data.get('sales_open'), default=True)
    teaser = resolve_event_teaser(data, sales_open)
    return {
        'id': event_id,
        'name': (data.get('name') or '').strip() or 'Untitled event',
        'headline': (data.get('headline') or '').strip(),
        'date': date_value,
        'time_start': (data.get('time_start') or '').strip(),
        'time_end': (data.get('time_end') or '').strip(),
        'venue': (data.get('venue') or '').strip(),
        'description': (data.get('description') or '').strip(),
        'details': (data.get('details') or '').strip(),
        'flyer_filename': (data.get('flyer_filename') or '').strip(),
        'flyer_static': (data.get('flyer_static') or '').strip(),
        'ticket_cap': ticket_cap,
        'vip_cap': vip_cap,
        'sales_open': sales_open,
        'teaser': teaser,
        'created_at': data.get('created_at') or datetime.now(timezone.utc).isoformat(),
        'updated_at': data.get('updated_at') or data.get('created_at') or datetime.now(timezone.utc).isoformat(),
    }


def event_display(event):
    if not event:
        return None
    date_line = format_event_date_line(event.get('date'))
    headline = format_event_headline(event.get('date'), event.get('headline')) or event.get('name')
    sold = compute_ticket_sales_counts(event.get('id'))['sold']
    cap = parse_max_capacity(event.get('ticket_cap'))
    remaining = None if not cap else max(0, cap - sold)
    sold_out = bool(cap and remaining == 0)
    sales_open = event_is_sales_open(event)
    teaser = event_is_teaser(event)
    return {
        **event,
        'headline_display': headline,
        'date_display': date_line,
        'time_display': format_event_time_line(event.get('time_start'), event.get('time_end')),
        'price_display': ticket_price_line(),
        'flyer_url': event_flyer_url(event),
        'is_featured': event.get('id') == get_featured_event_id(),
        'is_door': event.get('id') == get_door_event_id(),
        'tickets_sold': sold,
        'tickets_remaining': remaining,
        'sold_out': sold_out,
        'sales_open': sales_open,
        'teaser': teaser,
        'can_buy': sales_open and not sold_out,
    }


def event_sort_key(event):
    return (event.get('date') or '9999-99-99', (event.get('name') or '').lower())


def today_iso():
    return datetime.now(get_display_timezone()).date().isoformat()


def event_date_iso(event):
    return ((event or {}).get('date') or '')[:10]


def event_is_upcoming(event, today=None):
    today = today or today_iso()
    date_value = event_date_iso(event)
    if not date_value:
        return True
    return date_value >= today


def next_event_sort_key(event, today=None):
    """Soonest upcoming dated night first. Past nights lose to future ones."""
    today = today or today_iso()
    date_value = event_date_iso(event)
    name = ((event or {}).get('name') or '').lower()
    if date_value and date_value >= today:
        return (0, date_value, name)
    if not date_value:
        return (1, '9999-99-99', name)
    return (2, date_value, name)


def list_on_sale_events():
    events = [event_display(event) for event in load_events() if event_is_sales_open(event)]
    events.sort(key=event_sort_key)
    return events


def list_teaser_events():
    """Coming-soon events for the homepage, shown under NEXT EVENT."""
    today = today_iso()
    teasers = []
    for event in load_events():
        if event_is_sales_open(event) or not event_is_teaser(event):
            continue
        date_value = (event.get('date') or '')[:10]
        if date_value and date_value < today:
            continue
        teasers.append(event_display(event))
    teasers.sort(key=event_sort_key)
    return teasers


def pick_next_event(on_sale_events=None):
    """The single homepage NEXT EVENT: soonest upcoming on-sale night.

    A Halloween in October beats a Christmas / NYE in December, even if
    Christmas was marked featured or created first.
    """
    on_sale = list(on_sale_events if on_sale_events is not None else list_on_sale_events())
    if not on_sale:
        return None
    return sorted(on_sale, key=next_event_sort_key)[0]


def get_event(event_id):
    target = (event_id or '').strip()
    if not target:
        return None
    for event in load_events():
        if event.get('id') == target:
            return event
    return None


def get_featured_event_id():
    settings = load_scanner_settings()
    return (settings.get('featured_event_id') or '').strip() or None


def get_door_event_id():
    settings = load_scanner_settings()
    door = (settings.get('current_event_id') or '').strip()
    if door:
        return door
    return get_featured_event_id()


def get_sales_event_id():
    next_event = pick_next_event()
    if next_event and next_event.get('id'):
        return next_event['id']
    return get_door_event_id()


def get_featured_event():
    return get_event(get_featured_event_id())


def get_door_event():
    return get_event(get_door_event_id())


def get_sales_event():
    return get_event(get_sales_event_id())


def set_featured_event_id(event_id):
    normalized = (event_id or '').strip() or None
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized:
            settings['featured_event_id'] = normalized
        else:
            settings.pop('featured_event_id', None)
        save_scanner_settings(settings)
    return normalized


def set_door_event_id(event_id):
    normalized = (event_id or '').strip() or None
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized:
            settings['current_event_id'] = normalized
        else:
            settings.pop('current_event_id', None)
        save_scanner_settings(settings)
    return normalized


def upsert_event(payload, event_id=None):
    now_iso = datetime.now(timezone.utc).isoformat()
    with events_lock:
        events = load_events()
        existing = None
        target = (event_id or payload.get('id') or '').strip()
        if target:
            for event in events:
                if event.get('id') == target:
                    existing = event
                    break
        if existing:
            merged = {**existing, **payload, 'id': existing['id'], 'updated_at': now_iso}
            updated = normalize_event(merged)
            events = [updated if event.get('id') == updated['id'] else event for event in events]
        else:
            created = normalize_event({**payload, 'id': target or new_event_id(), 'created_at': now_iso, 'updated_at': now_iso})
            events.append(created)
            updated = created
        save_events(events)
        return updated


def delete_event(event_id):
    target = (event_id or '').strip()
    if not target:
        return False
    with events_lock:
        events = load_events()
        remaining = [event for event in events if event.get('id') != target]
        if len(remaining) == len(events):
            return False
        save_events(remaining)
    settings = load_scanner_settings()
    if settings.get('featured_event_id') == target:
        next_id = remaining[0]['id'] if remaining else None
        set_featured_event_id(next_id)
    if settings.get('current_event_id') == target:
        next_id = remaining[0]['id'] if remaining else None
        set_door_event_id(next_id)
    return True


def save_event_flyer(event_id, file_storage):
    if not file_storage or not getattr(file_storage, 'filename', None):
        return None
    raw = file_storage.read()
    if not raw:
        return None
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Flyer must be 8MB or smaller.')
    ext = detect_image_extension(raw)
    if not ext:
        raise ValueError('Flyer must be a JPG, PNG, WEBP, or GIF image.')
    if not ensure_data_dir(os.path.join(flyers_dir, 'placeholder')):
        raise ValueError('Could not save flyer.')
    filename = f'{secure_filename(event_id) or new_event_id()}_{secrets.token_hex(6)}{ext}'
    path = os.path.join(flyers_dir, filename)
    with open(path, 'wb') as handle:
        handle.write(raw)
    return filename


def delete_event_flyer_file(filename):
    if not flyer_is_safe_filename(filename):
        return
    path = os.path.join(flyers_dir, filename)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def find_halloween_event():
    events = load_events()
    for event in events:
        if (event.get('id') or '').strip() == HALLOWEEN_EVENT_SLUG:
            return event
    for event in events:
        if event_looks_like_halloween(event):
            return event
    return None


def adopt_legacy_tickets_for_event(event_id):
    """Stamp pre-catalog tickets onto this event so they keep working at the door."""
    target = (event_id or '').strip()
    if not target:
        return 0
    catalog_ids = {event.get('id') for event in load_events() if event.get('id')}
    adopted = 0
    with tickets_lock:
        tickets = load_tickets()
        changed = False
        for ticket in tickets:
            stamped = (ticket.get('event_id') or '').strip()
            if stamped == target:
                continue
            if stamped and stamped in catalog_ids:
                continue
            ticket['event_id'] = target
            changed = True
            adopted += 1
        if changed:
            save_tickets(tickets)
    if adopted:
        print(f'Adopted {adopted} previously sold ticket(s) onto event {target}')
    return adopted


def ensure_halloween_event():
    """Keep the Halloween event and attach every older sale to it."""
    existing = find_halloween_event()
    if not existing:
        with events_lock:
            existing = find_halloween_event()
            if not existing:
                seeded = normalize_event({
                    'id': HALLOWEEN_EVENT_SLUG,
                    'name': 'Halloween',
                    'headline': 'October 24th',
                    'date': HALLOWEEN_EVENT_DATE,
                    'time_start': '22:00',
                    'time_end': '02:00',
                    'venue': 'The Gem, Idaho Falls',
                    'description': '',
                    'details': '21+ • Limited Capacity • +$5 at door',
                    'flyer_static': 'images/TheSectionHalloweenFlyer.JPG',
                    'ticket_cap': get_legacy_max_capacity(),
                    'vip_cap': get_legacy_max_vip_capacity(),
                    'sales_open': True,
                })
                events = load_events()
                events.append(seeded)
                save_events(events)
                existing = seeded

    # Orphan door/homepage settings (old anonymous IDs) should point at Halloween
    # so previously sold tickets are the ones being scanned tonight.
    if not get_event(get_featured_event_id() or ''):
        set_featured_event_id(existing['id'])
    if not get_event(get_current_event_id() or ''):
        set_door_event_id(existing['id'])

    adopt_legacy_tickets_for_event(existing['id'])
    return existing


def seed_default_event():
    return ensure_halloween_event()


def ticket_event_record(record):
    """Catalog event this ticket is for; Halloween if it predates named events."""
    if not record:
        return None
    stamped = (record.get('event_id') or '').strip()
    event = get_event(stamped) if stamped else None
    if event:
        return event
    return find_halloween_event()


def parse_max_capacity(raw):
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_legacy_max_capacity():
    settings = load_scanner_settings()
    return parse_max_capacity(settings.get('max_capacity'))


def get_legacy_max_vip_capacity():
    settings = load_scanner_settings()
    return parse_max_capacity(settings.get('max_vip_capacity'))


def event_ticket_cap(event):
    if event:
        cap = parse_max_capacity(event.get('ticket_cap'))
        if cap is not None:
            return cap
    return get_legacy_max_capacity()


def event_vip_cap(event):
    if event:
        cap = parse_max_capacity(event.get('vip_cap'))
        if cap is not None:
            return cap
    return get_legacy_max_vip_capacity()


def get_max_capacity():
    return event_ticket_cap(get_door_event())


def get_sales_ticket_cap():
    return event_ticket_cap(get_sales_event())


def set_max_capacity(value):
    normalized = parse_max_capacity(value)
    door = get_door_event()
    if door:
        upsert_event({**door, 'ticket_cap': normalized}, event_id=door['id'])
        return normalized
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized is None:
            settings.pop('max_capacity', None)
        else:
            settings['max_capacity'] = normalized
        save_scanner_settings(settings)
    return normalized


def get_max_vip_capacity():
    return event_vip_cap(get_door_event())


def set_max_vip_capacity(value):
    normalized = parse_max_capacity(value)
    door = get_door_event()
    if door:
        upsert_event({**door, 'vip_cap': normalized}, event_id=door['id'])
        return normalized
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized is None:
            settings.pop('max_vip_capacity', None)
        else:
            settings['max_vip_capacity'] = normalized
        save_scanner_settings(settings)
    return normalized


SCAN_RESET_TOKEN = 'unused-after-door-event-fix-1'
SALES_RESET_TOKEN = 'sales-counter-zero-1'


def apply_one_time_unused_ticket_reset():
    """Clear every door scan once so wrongly-accepted tickets can be used tonight."""
    settings = load_scanner_settings()
    if settings.get('scan_reset_applied') == SCAN_RESET_TOKEN:
        return 0
    halloween = find_halloween_event()
    halloween_id = halloween.get('id') if halloween else HALLOWEEN_EVENT_SLUG
    catalog_ids = {event.get('id') for event in load_events() if event.get('id')}
    cleared = 0
    with tickets_lock:
        tickets = load_tickets()
        changed = False
        for ticket in tickets:
            if ticket.get('scanned_at') or ticket.get('admission_as') or ticket.get('vip_redeemed_at'):
                ticket['scanned_at'] = None
                ticket.pop('admission_as', None)
                ticket.pop('vip_redeemed_at', None)
                changed = True
                cleared += 1
            stamped = (ticket.get('event_id') or '').strip()
            if not stamped or stamped not in catalog_ids:
                ticket['event_id'] = halloween_id
                changed = True
        if changed:
            save_tickets(tickets)
    with scanner_settings_lock:
        settings = load_scanner_settings()
        settings['scan_reset_applied'] = SCAN_RESET_TOKEN
        save_scanner_settings(settings)
    if cleared:
        print(f'Cleared {cleared} door scan(s); tickets are unused again')
    return cleared


def apply_one_time_sales_counter_reset():
    """Zero the sold counter without deleting tickets. New sales still count."""
    settings = load_scanner_settings()
    if settings.get('sales_reset_applied') == SALES_RESET_TOKEN:
        return False
    with scanner_settings_lock:
        settings = load_scanner_settings()
        settings['sales_epoch'] = datetime.now(timezone.utc).isoformat()
        settings['sales_reset_applied'] = SALES_RESET_TOKEN
        save_scanner_settings(settings)
    print('Ticket sold counter reset to 0')
    return True


def apply_one_time_pre_cutoff_ticket_void():
    """Drop pre-cutoff test sales from sold counts. Door also rejects them."""
    settings = load_scanner_settings()
    if settings.get('validity_reset_applied') == TICKET_VALIDITY_RESET_TOKEN:
        return False
    cutoff_dt = ticket_validity_cutoff().astimezone(timezone.utc)
    cutoff_iso = cutoff_dt.isoformat()
    with scanner_settings_lock:
        settings = load_scanner_settings()
        existing = None
        raw_epoch = settings.get('sales_epoch')
        if raw_epoch:
            try:
                existing = datetime.fromisoformat(str(raw_epoch).replace('Z', '+00:00'))
                if existing.tzinfo is None:
                    existing = existing.replace(tzinfo=timezone.utc)
            except ValueError:
                existing = None
        if existing is None or existing < cutoff_dt:
            settings['sales_epoch'] = cutoff_iso
        settings['ticket_validity_epoch'] = cutoff_iso
        settings['validity_reset_applied'] = TICKET_VALIDITY_RESET_TOKEN
        save_scanner_settings(settings)
    print(f'Tickets purchased before {TICKET_VALIDITY_CUTOFF_DATE} are void')
    return True


try:
    seed_default_event()
    apply_one_time_unused_ticket_reset()
    apply_one_time_sales_counter_reset()
    apply_one_time_pre_cutoff_ticket_void()
except Exception as exc:
    print('Event seed skipped:', exc)


def admission_entry_type(ticket):
    """How this ticket counted for the door: vip or ga."""
    admitted = ticket.get('admission_as')
    if admitted in ('vip', 'ga'):
        return admitted
    return 'vip' if ticket.get('ticket_type') == 'vip' else 'ga'


def normalize_free_girls_entries(raw):
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        event_id = (entry.get('event_id') or '').strip()
        if not event_id:
            continue
        try:
            qty = int(entry.get('quantity') or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            continue
        cleaned.append({
            'id': entry.get('id') or secrets.token_hex(8),
            'at': entry.get('at'),
            'event_id': event_id,
            'quantity': qty,
        })
    return cleaned


def compute_free_girls_count(event_id=None):
    """Complimentary first-hour guests for the door event in the current period."""
    target = (event_id or get_door_event_id() or '').strip()
    if not target:
        return 0
    settings = load_scanner_settings()
    total = 0
    for entry in normalize_free_girls_entries(settings.get('free_girls_entries')):
        if entry['event_id'] != target:
            continue
        if not ticket_counts_for_current_period(entry.get('at')):
            continue
        total += entry['quantity']
    return total


def add_free_girls(delta=1, event_id=None):
    """Admit or undo complimentary first-hour guests on the live door count.

    Positive delta adds that many guests. Negative delta undoes recent adds.
    Returns admission totals, or None if tonight's event is not set / save failed.
    """
    target = (event_id or get_door_event_id() or '').strip()
    if not target or not get_event(target):
        return None
    try:
        change = int(delta)
    except (TypeError, ValueError):
        change = 1
    if change == 0:
        return get_admission_totals()
    change = max(-20, min(20, change))

    with scanner_settings_lock:
        settings = load_scanner_settings()
        entries = normalize_free_girls_entries(settings.get('free_girls_entries'))
        if change > 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            for _ in range(change):
                entries.append({
                    'id': secrets.token_hex(8),
                    'at': now_iso,
                    'event_id': target,
                    'quantity': 1,
                })
        else:
            remaining = abs(change)
            kept = []
            for entry in reversed(entries):
                if (
                    remaining
                    and entry['event_id'] == target
                    and ticket_counts_for_current_period(entry.get('at'))
                ):
                    remaining -= 1
                    continue
                kept.append(entry)
            entries = list(reversed(kept))
        pruned = [
            entry for entry in entries
            if ticket_counts_for_current_period(entry.get('at'))
        ]
        settings['free_girls_entries'] = pruned[-2000:]
        if not save_scanner_settings(settings):
            return None
    return get_admission_totals()


def compute_admission_counts(event_id=None):
    """Live door counts for the night being scanned (not every event)."""
    target = (event_id or get_door_event_id() or '').strip()
    ga = 0
    vip = 0
    if not target:
        return {'ga': 0, 'vip': 0, 'total': 0, 'free_girls': 0}
    for ticket in load_tickets():
        scanned_at = ticket.get('scanned_at')
        if not scanned_at or not ticket_counts_for_current_period(scanned_at):
            continue
        if not ticket_belongs_to_event(ticket, target):
            continue
        qty = int(ticket.get('quantity') or 1)
        if admission_entry_type(ticket) == 'vip':
            vip += qty
        else:
            ga += qty
    free_girls = compute_free_girls_count(target)
    ga += free_girls
    return {'ga': ga, 'vip': vip, 'total': ga + vip, 'free_girls': free_girls}


def compute_ticket_sales_counts(event_id=None):
    """Tickets sold for an event (quantity, not orders)."""
    target = (event_id or get_sales_event_id() or '').strip() or None
    ga = 0
    vip = 0
    for ticket in load_tickets():
        if target:
            if not ticket_belongs_to_event(ticket, target):
                continue
        elif not ticket_belongs_to_current_event(ticket):
            continue
        if not ticket_counts_for_current_sales_period(ticket.get('purchased_at')):
            continue
        qty = int(ticket.get('quantity') or 1)
        if ticket.get('ticket_type') == 'vip':
            vip += qty
        else:
            ga += qty
    return {'ga': ga, 'vip': vip, 'sold': ga + vip}


def ticket_sales_remaining(event_id=None):
    event = get_event(event_id) if event_id else get_sales_event()
    max_capacity = event_ticket_cap(event)
    if not max_capacity:
        return None
    counts = compute_ticket_sales_counts(event.get('id') if event else event_id)
    return max(0, max_capacity - counts['sold'])


def get_ticket_availability(event_id=None):
    event = get_event(event_id) if event_id else get_sales_event()
    max_capacity = event_ticket_cap(event)
    sold = compute_ticket_sales_counts(event.get('id') if event else None)['sold']
    remaining = None if not max_capacity else max(0, max_capacity - sold)
    sales_open = event_is_sales_open(event) if event else False
    sold_out = bool(max_capacity and remaining == 0)
    return {
        'max_capacity': max_capacity,
        'sold': sold if max_capacity else None,
        'remaining': remaining,
        'sold_out': sold_out,
        'sales_open': sales_open,
        'event_id': event.get('id') if event else None,
        'event_name': event.get('name') if event else None,
        'event_date_display': format_event_date_line((event or {}).get('date')) if event else None,
        'can_buy': bool(event and sales_open and not sold_out),
        'max_quantity': 0 if sold_out else max_order_quantity(event.get('id') if event else None),
    }


def ensure_ticket_sales_available(quantity, event_id=None):
    """Reject checkout when the chosen event is not for sale or is at cap."""
    event = get_event(event_id) if event_id else get_sales_event()
    if not event:
        raise TicketSalesError('No event is on sale right now.', remaining=0)
    if not event_is_sales_open(event):
        raise TicketSalesError('Tickets are not on sale for this event yet.', remaining=0)
    remaining = ticket_sales_remaining(event.get('id'))
    if remaining is None:
        return remaining
    try:
        quantity = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        quantity = 1
    if remaining <= 0:
        raise TicketSalesError('Tickets are sold out.', remaining=0)
    if quantity > remaining:
        noun = 'ticket' if remaining == 1 else 'tickets'
        raise TicketSalesError(f'Only {remaining} {noun} left.', remaining=remaining)
    return remaining


def vip_capacity_remaining():
    max_vip = get_max_vip_capacity()
    if not max_vip:
        return None
    counts = compute_admission_counts()
    return max(0, max_vip - counts['vip'])


def get_admission_totals():
    counts = compute_admission_counts()
    door_event = get_door_event()
    door_event_id = door_event.get('id') if door_event else get_door_event_id()
    sales = compute_ticket_sales_counts(door_event_id)
    max_capacity = event_ticket_cap(door_event)
    max_vip_capacity = event_vip_cap(door_event)
    tickets_sold = sales['sold']
    tickets_remaining = None
    sold_out = False
    if max_capacity:
        tickets_remaining = max(0, max_capacity - tickets_sold)
        sold_out = tickets_sold >= max_capacity
    vip_capacity_reached = bool(max_vip_capacity and counts['vip'] >= max_vip_capacity)
    vip_spots_remaining = None
    if max_vip_capacity:
        vip_spots_remaining = max(0, max_vip_capacity - counts['vip'])
    settings = load_scanner_settings()
    event_options = []
    for event in sorted(load_events(), key=lambda item: item.get('date') or '', reverse=True):
        event_options.append({
            'id': event.get('id'),
            'name': event.get('name'),
            'date': event.get('date'),
            'headline': format_event_headline(event.get('date'), event.get('headline')),
        })
    return {
        **counts,
        'max_capacity': max_capacity,
        'capacity_reached': sold_out,
        'spots_remaining': tickets_remaining,
        'tickets_sold': tickets_sold,
        'tickets_remaining': tickets_remaining,
        'sold_out': sold_out,
        'max_vip_capacity': max_vip_capacity,
        'vip_capacity_reached': vip_capacity_reached,
        'vip_spots_remaining': vip_spots_remaining,
        'reset_history': get_reset_history(),
        'counting_epoch': settings.get('counting_epoch'),
        'sales_epoch': settings.get('sales_epoch'),
        'current_event_id': door_event_id,
        'featured_event_id': get_featured_event_id(),
        'door_event_id': door_event_id,
        'door_event_name': door_event.get('name') if door_event else None,
        'events': event_options,
        'door_surcharge_cents': door_surcharge_cents(),
        'door_ga_cents': door_unit_price_cents('general'),
        'door_vip_cents': door_unit_price_cents('vip'),
    }


def check_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return {
            'status': 'invalid', 'ticket_id': ticket_id or None, 'quantity': 0,
            'ticket_type': None, 'access': None, 'is_vip': False,
        }

    record = get_ticket_record(normalized)
    if not record:
        return {
            'status': 'invalid', 'ticket_id': normalized, 'quantity': 0,
            'ticket_type': None, 'access': None, 'is_vip': False,
        }

    quantity = int(record.get('quantity') or 1)
    display_id = record.get('ticket_id', normalized)
    ticket_type = record.get('ticket_type', 'general')
    meta = ticket_result_meta(record)
    door_id = get_door_event_id()
    door_event = get_event(door_id) if door_id else None
    ticket_event = ticket_event_record(record)
    meta['door_event_name'] = (door_event or {}).get('name') if door_event else None
    meta['ticket_event_name'] = (ticket_event or {}).get('name') if ticket_event else None

    if not door_id or not door_event:
        return {
            'status': 'wrong_event',
            'ticket_id': display_id,
            'quantity': quantity,
            **meta,
            'detail': 'Pick tonight’s event on the scanner first.',
        }

    if not ticket_belongs_to_event(record, door_id):
        return {'status': 'wrong_event', 'ticket_id': display_id, 'quantity': quantity, **meta}

    if not ticket_is_valid_purchase(record):
        return {
            'status': 'void',
            'ticket_id': display_id,
            'quantity': quantity,
            **meta,
            'detail': 'This ticket is from an earlier sale and is not valid.',
        }

    if record.get('scanned_at'):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    # VIP tickets: if VIP area is full, still admit but as GA (like before).
    admission_as = 'vip' if ticket_type == 'vip' else 'ga'
    vip_note = None
    if ticket_type == 'vip':
        vip_left = vip_capacity_remaining()
        if vip_left is not None and quantity > vip_left:
            admission_as = 'ga'
            vip_note = 'VIP area full — admitted as GA.'

    if not mark_ticket_scanned(normalized, admission_as=admission_as):
        refreshed = get_ticket_record(normalized) or record
        if refreshed.get('scanned_at'):
            return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}
        return {
            'status': 'error',
            'ticket_id': display_id,
            'quantity': quantity,
            **meta,
            'detail': 'Could not record this scan. Try again.',
        }

    record = get_ticket_record(normalized) or record
    meta = ticket_result_meta(record, admission_as=admission_as)
    result = {
        'status': 'accepted',
        'ticket_id': display_id,
        'quantity': quantity,
        **meta,
    }
    if vip_note:
        result['vip_overflow_note'] = vip_note
    return result


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
                ticket.pop('email_sending_at', None)
                save_tickets(tickets)
                return


def claim_ticket_email_delivery(session_id):
    """True if this caller should send the ticket email (at most once)."""
    if not session_id:
        return True
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') != session_id:
                continue
            if ticket.get('email_sent_at') or ticket.get('email_sending_at'):
                return False
            ticket['email_sending_at'] = datetime.now(timezone.utc).isoformat()
            save_tickets(tickets)
            return True
        return True


def clear_ticket_email_sending(session_id):
    if not session_id:
        return
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') == session_id and not ticket.get('email_sent_at'):
                ticket.pop('email_sending_at', None)
                save_tickets(tickets)
                return


def send_ticket_email(customer_email, ticket_id, quantity, ticket_data, ticket_type='general', access=None):
    view_url = ticket_display_url(ticket_id)
    type_label = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['name']
    record = get_ticket_record(ticket_id)
    event = ticket_event_record(record) if record else get_sales_event()
    event_name = (event or {}).get('name') or 'The Section'
    event_when = format_event_date_line((event or {}).get('date')) if event else ''
    event_line = f"Event: {event_name}" + (f" — {event_when}" if event_when else '')
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
                f"{event_line}\n"
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
    if not claim_ticket_email_delivery(session_id):
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
        else:
            clear_ticket_email_sending(session_id)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    thread.join(timeout=app.config['MAIL_TIMEOUT'] + 2)
    if not result['sent']:
        clear_ticket_email_sending(session_id)
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


_display_tz = None


def get_display_timezone():
    global _display_tz
    if _display_tz is None:
        for key in (APP_TIMEZONE, 'America/Los_Angeles'):
            try:
                _display_tz = ZoneInfo(key)
                break
            except Exception:
                _display_tz = None
        if _display_tz is None:
            _display_tz = timezone.utc
    return _display_tz


def display_timezone_label():
    return datetime.now(get_display_timezone()).strftime('%Z')


def format_display_datetime(iso_raw, date_only=False):
    dt = parse_iso_datetime(iso_raw)
    if not dt:
        return '—'
    local = dt.astimezone(get_display_timezone())
    if date_only:
        return local.strftime('%Y-%m-%d')
    return local.strftime('%Y-%m-%d %H:%M')


@app.template_filter('local_time')
def local_time_filter(iso_raw):
    return format_display_datetime(iso_raw)


@app.template_filter('local_date')
def local_date_filter(iso_raw):
    return format_display_datetime(iso_raw, date_only=True)


def build_member_invite_url(email, token):
    query = urlencode({'email': email, 'token': token})
    return f"{get_public_base_url()}/legacy/join?{query}"


def send_member_invite_email(customer_email, token, invite_url=None):
    normalized = (customer_email or '').strip().lower()
    if normalized and normalized in emails_already_sent_invites():
        print(f"Member invite already sent to {customer_email}; skipping duplicate")
        return True
    invite_url = invite_url or build_member_invite_url(customer_email, token)
    days_label = f'{INVITE_EXPIRY_DAYS} day{"s" if INVITE_EXPIRY_DAYS != 1 else ""}'
    welcome_pct = int(returning_guest_discount * 100)
    member_pct = int(member_discount * 100)
    plain_body = (
        "You've been to The Section before — welcome back!\n\n"
        f'Create your member account for {welcome_pct}% off one single ticket per event '
        f'(or {member_pct}% when you buy more than one):\n'
        f'{invite_url}\n\n'
        f'This link expires in {days_label}.\n'
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        '<p>You\'ve been to The Section before — welcome back!</p>'
        f'<p>Create your member account to save tickets and get '
        f'<strong>{welcome_pct}% off one single ticket per event</strong> — or '
        f'<strong>{member_pct}% off</strong> when you buy more than one for friends.</p>'
        f'<p><a href="{invite_url}" style="display:inline-block;padding:12px 18px;'
        'background:#111;color:#fff;text-decoration:none;border-radius:10px;">'
        'Set up your account</a></p>'
        f'<p style="color:#555;font-size:14px;">This link expires in {days_label}.</p>'
        f'<p style="color:#555;font-size:14px;">If the button does not work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all;">{invite_url}</span></p>'
        '</div>'
    )
    if not app.config.get('TESTING') and not mail_is_configured():
        print(f"Member invite email skipped for {customer_email}: mail is not configured")
        log_mailing_list_send(
            'invite',
            'The Section — welcome back (member invite)',
            [customer_email],
            status='failed',
        )
        return False
    with app.app_context():
        try:
            msg = Message(
                'The Section — welcome back (member invite)',
                sender=mail_from_address(),
                recipients=[customer_email],
            )
            msg.body = plain_body
            msg.html = html_body
            mail.send(msg)
            print(f"Member invite email sent to {customer_email}")
            log_mailing_list_send(
                'invite',
                'The Section — welcome back (member invite)',
                [customer_email],
                status='sent',
            )
            return True
        except Exception as e:
            print(f"Member invite email failed for {customer_email}:", str(e))
            log_mailing_list_send(
                'invite',
                'The Section — welcome back (member invite)',
                [customer_email],
                status='failed',
            )
            return False


def deliver_member_invite_email(customer_email, token, invite_url=None):
    return send_member_invite_email(customer_email, token, invite_url=invite_url)


def send_pending_member_invites():
    sent = []
    failed = []
    skipped = []
    already = emails_already_sent_invites()
    for email in invites_ready_to_send():
        if get_legacy_member(email) or email in already:
            skipped.append(email)
            continue
        token = set_member_invite_token(email)
        if not token:
            failed.append(email)
            continue
        invite_url = build_member_invite_url(email, token)
        if deliver_member_invite_email(email, token, invite_url=invite_url):
            mark_member_invite_sent(email)
            already.add(email)
            sent.append(email)
        else:
            failed.append(email)
    return {'sent': sent, 'failed': failed, 'skipped': skipped}


@app.route('/.well-known/apple-developer-merchantid-domain-association')
def apple_pay_domain_association():
    """Apple fetches this to allow Apple Pay on www.thesectionevents.com."""
    return send_from_directory(
        app.static_folder,
        'apple-developer-merchantid-domain-association',
        mimetype='text/plain',
    )


@app.route('/')
def home():
    on_sale_events = list_on_sale_events()
    teaser_events = list_teaser_events()
    next_event = pick_next_event(on_sale_events)
    more_on_sale_events = [
        event for event in on_sale_events
        if not next_event or event.get('id') != next_event.get('id')
    ]
    return render_template(
        'home.html',
        show_scanner_link=is_scanner_admin_member(),
        ticket_availability=get_ticket_availability(next_event.get('id') if next_event else None),
        featured_event=next_event,
        next_event=next_event,
        on_sale_events=on_sale_events,
        more_on_sale_events=more_on_sale_events,
        teaser_events=teaser_events,
    )


@app.route('/api/viewing')
def public_viewing_heartbeat():
    """Homepage heartbeat. Never returns the live count (admin-only)."""
    try:
        if not rate_limit_allow('viewing', 30, 60):
            return jsonify({'ok': True})
        vid = (request.cookies.get(VISITOR_COOKIE) or '').strip().lower()
        if not VISITOR_ID_RE.fullmatch(vid):
            vid = secrets.token_hex(16)
        if not (admin_authenticated() or is_staff_user()):
            bump_public_viewer(vid)
        resp = jsonify({'ok': True})
        resp.set_cookie(
            VISITOR_COOKIE,
            vid,
            max_age=int(timedelta(days=31).total_seconds()),
            httponly=True,
            secure=IS_PRODUCTION,
            samesite='Lax',
            path='/',
        )
        return resp
    except Exception as e:
        print('Viewing heartbeat failed:', e)
        return jsonify({'ok': True})


@app.route('/admin/viewing.json')
def admin_viewing_count():
    if not require_admin():
        return jsonify({'error': 'auth'}), 401
    try:
        viewing = count_public_viewers()
    except Exception as e:
        print('Viewing count failed:', e)
        viewing = 0
    return jsonify({'viewing': int(viewing)})


@app.route('/api/member-status')
def member_status():
    member = get_logged_in_member()
    discount_code = None
    discount_eligible = False
    event_id = (request.args.get('event_id') or '').strip() or get_sales_event_id()
    if member:
        member = ensure_returning_guest_flag_for_exclusive_member(member)
        discount_eligible = member_discount_eligible(member)
        if discount_eligible:
            discount_code = member.get('discount_code') or ensure_member_discount_code(member)
    return jsonify({
        'logged_in': bool(member),
        'email': session.get('legacy_member_email'),
        'discount_code': discount_code,
        'member_discount_eligible': discount_eligible,
        'returning_guest_discount': member_has_returning_guest_discount(member) if member else False,
        'exclusive_single_available': exclusive_single_rate_available(member, event_id, 1) if member else False,
        'member_discount_percent': int(member_discount * 100) if member_discount > 0 else 10,
        'returning_guest_discount_percent': int(returning_guest_discount * 100) if returning_guest_discount > 0 else 20,
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
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    apply_member = resolve_member_discount_application(
        request.args.get('apply_member_discount', '').lower() in ('1', 'true', 'yes')
    )
    event_id = resolve_checkout_event_id(request.args.get('event_id'))
    quantity = clamp_quantity(request.args.get('quantity', 1), event_id=event_id)
    return jsonify(pricing_breakdown(ticket_type, quantity, apply_member, event_id=event_id))


@app.route('/api/ticket-availability')
def ticket_availability():
    return jsonify(get_ticket_availability(request.args.get('event_id')))


def resolve_checkout_event_id(raw):
    event_id = (raw or '').strip()
    if event_id and get_event(event_id):
        return event_id
    return get_sales_event_id()


def build_checkout_session(quantity, ticket_type, apply_member_discount=False, event_id=None):
    if not stripe.api_key:
        raise RuntimeError('Stripe is not configured')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    sales_event_id = resolve_checkout_event_id(event_id)
    quantity = clamp_quantity(quantity, event_id=sales_event_id)
    ensure_ticket_sales_available(quantity, sales_event_id)
    sales_event = get_event(sales_event_id) if sales_event_id else get_sales_event()
    sales_event_id = sales_event.get('id') if sales_event else sales_event_id

    legacy_member = is_legacy_member_logged_in()
    apply_member = resolve_member_discount_application(apply_member_discount)
    member = get_logged_in_member()
    member_email = (member.get('email') or '').strip().lower() if member else ''
    exclusive_reserved = False
    if (
        apply_member
        and member
        and exclusive_single_rate_available(member, sales_event_id, quantity)
    ):
        exclusive_reserved = reserve_exclusive_single_rate(member_email, sales_event_id)
    try:
        breakdown = pricing_breakdown(
            ticket_type, quantity, apply_member, event_id=sales_event_id,
            exclusive_reserved=exclusive_reserved if exclusive_reserved else None,
        )
        checkout_session = _create_stripe_checkout_session(
            quantity=quantity,
            ticket_type=ticket_type,
            ticket_meta=TICKET_TYPES[ticket_type],
            breakdown=breakdown,
            sales_event=sales_event,
            sales_event_id=sales_event_id,
            legacy_member=legacy_member,
            member_email=member_email,
            exclusive_reserved=exclusive_reserved,
        )
    except Exception:
        if exclusive_reserved:
            release_exclusive_hold(member_email, sales_event_id)
        raise
    if exclusive_reserved:
        bind_exclusive_hold(member_email, sales_event_id, checkout_session.id)
    return checkout_session


def _create_stripe_checkout_session(
    quantity, ticket_type, ticket_meta, breakdown, sales_event, sales_event_id,
    legacy_member, member_email, exclusive_reserved=False,
):
    unit_price = breakdown['unit_price_cents']
    ticket_meta = TICKET_TYPES[ticket_type]
    if sales_event:
        event_bits = [
            sales_event.get('name'),
            format_event_date_line(sales_event.get('date')) or format_event_headline(sales_event.get('date'), sales_event.get('headline')),
            sales_event.get('venue'),
        ]
        description = ' • '.join(bit for bit in event_bits if bit) or ticket_meta['description']
    else:
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

    print(f"Creating {ticket_type} session for {quantity} tickets @ {unit_price}c")

    checkout_kwargs = {
        'payment_method_types': ['card'],
        'line_items': [{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': (
                        f"The Section - {ticket_meta['name']}"
                        + (f" ({sales_event.get('name')})" if sales_event and sales_event.get('name') else '')
                    ),
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
            'event_id': sales_event_id or '',
            'exclusive_single_rate': 'true' if breakdown.get('returning_guest_single_ticket_rate') else 'false',
        },
        'success_url': f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}",
        'cancel_url': f"{base_url}/",
    }
    if member_email:
        checkout_kwargs['customer_email'] = member_email
    # Exclusive 20% sessions expire with the hold so a second 20% cannot be opened later
    # and the first unpaid session paid after the hold lapses.
    if exclusive_reserved:
        checkout_kwargs['expires_at'] = int((datetime.now(timezone.utc) + EXCLUSIVE_HOLD_TTL).timestamp())

    return stripe.checkout.Session.create(**checkout_kwargs)


def door_stripe_error_message(exc):
    text = str(exc or '')
    lowered = text.lower()
    if 'restricted' in lowered or 'required permissions' in lowered:
        return (
            'Stripe restricted key cannot create this charge. '
            'Door sales need Checkout Sessions: Write (already used for tickets), '
            'or switch STRIPE_SECRET_KEY to a full sk_live_ key.'
        )
    return public_error_message(exc, 'Could not start door checkout. Please try again.')


def build_door_checkout_session(quantity, ticket_type, event_id=None, embedded=True):
    """Walk-up Stripe Checkout: posted price plus the door surcharge, no member rates."""
    if not stripe.api_key:
        raise RuntimeError('Stripe is not configured')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    door_event_id = (event_id or get_door_event_id() or '').strip()
    door_event = get_event(door_event_id) if door_event_id else None
    if not door_event:
        raise TicketSalesError("Pick tonight’s event first.", remaining=0)
    quantity = clamp_quantity(quantity, event_id=door_event_id)
    ensure_ticket_sales_available(quantity, door_event_id)
    unit_price = door_unit_price_cents(ticket_type)
    ticket_meta = TICKET_TYPES[ticket_type]
    event_bits = [
        door_event.get('name'),
        format_event_date_line(door_event.get('date')) or format_event_headline(
            door_event.get('date'), door_event.get('headline')
        ),
        door_event.get('venue'),
        f"+${door_surcharge_cents() // 100} at the door",
    ]
    description = ' • '.join(bit for bit in event_bits if bit)
    name = (
        f"The Section - {ticket_meta['name']} (door)"
        + (f" ({door_event.get('name')})" if door_event.get('name') else '')
    )
    print(f"Creating door {ticket_type} session for {quantity} tickets @ {unit_price}c")
    checkout_kwargs = {
        'line_items': [{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': name,
                    'description': description,
                },
                'unit_amount': unit_price,
            },
            'quantity': quantity,
        }],
        'mode': 'payment',
        'metadata': {
            'ticket_type': ticket_type,
            'legacy_member': 'false',
            'legacy_discount': 'false',
            'member_email': '',
            'event_id': door_event_id,
            'exclusive_single_rate': 'false',
            'door_sale': 'true',
            'quantity': str(quantity),
        },
    }
    if embedded:
        # Stays on the scanner. Uses Checkout Sessions (allowed on the live restricted key).
        checkout_kwargs['ui_mode'] = 'embedded'
        checkout_kwargs['redirect_on_completion'] = 'never'
    else:
        checkout_kwargs['payment_method_types'] = ['card']
        checkout_kwargs['success_url'] = (
            f"{base_url}/verify/door-paid?session_id={{CHECKOUT_SESSION_ID}}"
        )
        checkout_kwargs['cancel_url'] = f"{base_url}/verify"
    return stripe.checkout.Session.create(**checkout_kwargs)


def build_door_payment_intent(quantity, ticket_type, event_id=None):
    """On-page tap-to-pay PaymentIntent: posted price plus the door surcharge."""
    if not stripe.api_key:
        raise RuntimeError('Stripe is not configured')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    door_event_id = (event_id or get_door_event_id() or '').strip()
    door_event = get_event(door_event_id) if door_event_id else None
    if not door_event:
        raise TicketSalesError("Pick tonight’s event first.", remaining=0)
    quantity = clamp_quantity(quantity, event_id=door_event_id)
    ensure_ticket_sales_available(quantity, door_event_id)
    unit_price = door_unit_price_cents(ticket_type)
    amount = unit_price * quantity
    ticket_meta = TICKET_TYPES[ticket_type]
    event_name = door_event.get('name') or 'The Section'
    return stripe.PaymentIntent.create(
        amount=amount,
        currency='usd',
        automatic_payment_methods={'enabled': True},
        description=f"{event_name} door {ticket_meta['name']} × {quantity}",
        metadata={
            'ticket_type': ticket_type,
            'event_id': door_event_id,
            'quantity': str(quantity),
            'door_sale': 'true',
            'legacy_member': 'false',
            'exclusive_single_rate': 'false',
        },
    )


@app.route('/api/checkout-intent', methods=['GET', 'POST', 'DELETE'])
def checkout_intent():
    if request.method == 'POST':
        data = request.get_json() or {}
        ticket_type = data.get('ticket_type', 'general')
        if ticket_type not in TICKET_TYPES:
            ticket_type = 'general'
        event_id = resolve_checkout_event_id(data.get('event_id'))
        session['checkout_intent'] = {
            'quantity': clamp_quantity(data.get('quantity', 1), event_id=event_id),
            'ticket_type': ticket_type,
            'apply_member_discount': bool(data.get('apply_member_discount')),
            'event_id': event_id,
        }
        touch_auth_session()
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
            event_id=intent.get('event_id'),
        )
        return redirect(checkout_session.url)
    except TicketSalesError:
        event_id = (intent or {}).get('event_id') or ''
        return redirect('/?open_tickets=1' + (f'&event_id={event_id}' if event_id else ''))
    except Exception as e:
        print("Error resuming checkout:", str(e))
        return redirect('/?open_tickets=1')


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if not is_legacy_member_logged_in():
        return jsonify({'error': 'Sign in to your member account before purchasing tickets.'}), 401
    if not rate_limit_allow('checkout', 10, 60):
        return jsonify({'error': 'Too many checkout attempts. Please wait a moment.'}), 429

    try:
        data = request.get_json() or {}
        ticket_type = data.get('ticket_type', 'general')
        apply_member_discount = bool(data.get('apply_member_discount'))
        event_id = resolve_checkout_event_id(data.get('event_id'))
        quantity = clamp_quantity(data.get('quantity', 1), event_id=event_id)
        # Re-stamp permanent session so login survives Stripe + browser Back.
        member = get_logged_in_member()
        if member:
            mark_member_session(member.get('email'))
        checkout_session = build_checkout_session(
            quantity, ticket_type, apply_member_discount=apply_member_discount,
            event_id=event_id,
        )
        print("Session created successfully:", checkout_session.url)
        return jsonify({'url': checkout_session.url})
    except TicketSalesError as e:
        event_id = resolve_checkout_event_id((request.get_json() or {}).get('event_id'))
        availability = get_ticket_availability(event_id)
        return jsonify({
            'error': str(e),
            'remaining': e.remaining,
            'sold_out': e.remaining <= 0,
            **availability,
        }), 409
    except Exception as e:
        return jsonify({'error': public_error_message(e, 'Could not start checkout. Please try again.')}), 500

@app.route('/success')
def success():
    session_id = request.args.get('session_id')
    print("Success page called with session_id:", session_id)

    if not session_id:
        return render_template('success.html', error="Missing session ID")
    if not rate_limit_allow('success', 30, 60):
        return render_template('success.html', error="Too many requests. Please try again shortly.")

    try:
        if not stripe.api_key:
            return render_template('success.html', error="Payment system is not configured.")

        checkout_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])
        if not checkout_session_is_paid(checkout_session):
            return render_template(
                'success.html',
                error="Payment is not complete yet. If you were charged, refresh this page in a moment.",
            )

        ticket = fulfill_paid_checkout(checkout_session)
        session_email = (session.get('legacy_member_email') or '').strip().lower()
        if session_email:
            mark_member_session(session_email)
        ticket_id = ticket['ticket_id']
        quantity = ticket['quantity']
        ticket_type = ticket.get('ticket_type', 'general')
        access = ticket.get('access') or TICKET_TYPES.get(ticket_type, {}).get('access')
        delivery_email = ticket.get('email')
        grant_session_ticket_access(ticket_id)

        ticket_data = build_qr_image(ticket_id)
        email_sent = deliver_ticket_email(
            session_id, delivery_email, ticket_id, quantity, ticket_data, ticket_type, access
        )
        view_token = ensure_ticket_view_token(ticket)

        return render_template(
            'success.html',
            email=delivery_email,
            email_sent=email_sent,
            ticket_data=ticket_data,
            ticket_id=ticket_id,
            quantity=quantity,
            ticket_type=ticket_type,
            access=access,
            wallet_enabled=wallet_enabled,
            view_token=view_token,
            ticket_view_url=ticket_display_url(ticket_id, view_token),
        )

    except Exception as e:
        return render_template('success.html', error=public_error_message(e))


@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    if not stripe_webhook_secret:
        print('Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured')
        return jsonify({'error': 'Webhook not configured'}), 503
    if not stripe.api_key:
        return jsonify({'error': 'Stripe not configured'}), 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, stripe_webhook_secret)
    except ValueError as e:
        print('Stripe webhook invalid payload:', e)
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        print('Stripe webhook bad signature:', e)
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event.get('type') if isinstance(event, dict) else event['type']
    data_object = event['data']['object'] if isinstance(event, dict) else event.data.object

    if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        try:
            session_id = data_object.get('id') if isinstance(data_object, dict) else data_object.id
            checkout_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])
            if checkout_session_is_paid(checkout_session):
                ticket = fulfill_paid_checkout(checkout_session)
                # Best-effort email delivery from webhook path
                ticket_id = ticket['ticket_id']
                ticket_data = build_qr_image(ticket_id)
                deliver_ticket_email(
                    session_id,
                    ticket.get('email'),
                    ticket_id,
                    ticket.get('quantity', 1),
                    ticket_data,
                    ticket.get('ticket_type', 'general'),
                    ticket.get('access'),
                )
        except Exception as e:
            print('Stripe webhook fulfill error:', e)
            return jsonify({'error': 'Fulfillment failed'}), 500

    if event_type == 'payment_intent.succeeded':
        try:
            intent_id = data_object.get('id') if isinstance(data_object, dict) else data_object.id
            payment_intent = data_object
            try:
                fetched = stripe.PaymentIntent.retrieve(intent_id, expand=['latest_charge'])
                if fetched:
                    payment_intent = fetched
            except Exception as refresh_error:
                print('Using webhook PaymentIntent payload:', refresh_error)
            ticket = fulfill_in_person_door_payment(payment_intent)
            if ticket:
                ticket_id = ticket['ticket_id']
                ticket_data = build_qr_image(ticket_id)
                deliver_ticket_email(
                    intent_id,
                    ticket.get('email'),
                    ticket_id,
                    ticket.get('quantity', 1),
                    ticket_data,
                    ticket.get('ticket_type', 'general'),
                    ticket.get('access'),
                )
        except Exception as e:
            print('Stripe webhook door payment fulfill error:', e)
            return jsonify({'error': 'Fulfillment failed'}), 500

    return jsonify({'received': True})

@app.route('/wallet/<ticket_id>.pkpass')
def download_wallet_pass(ticket_id):
    if not wallet_enabled:
        return (
            'Apple Wallet is not configured yet. Screenshot your ticket or download the QR code instead.',
            503,
        )
    if not rate_limit_allow('ticket_view', 60, 60):
        return 'Too many requests', 429

    record = get_ticket_record(ticket_id)
    if not record:
        return 'Ticket not found', 404
    if not can_view_ticket(record, request.args.get('k')):
        return 'Unauthorized', 401

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
    if not rate_limit_allow('ticket_view', 60, 60):
        return render_template('ticket_view.html', error='Too many requests. Try again shortly.'), 429
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return render_template('ticket_view.html', error='Invalid ticket'), 404
    record = get_ticket_record(normalized)
    if not record:
        return render_template('ticket_view.html', error='Ticket not found'), 404
    if not can_view_ticket(record, request.args.get('k')):
        return render_template(
            'ticket_view.html',
            error='This ticket link is private. Open the link from your confirmation email, or sign in with the purchasing account.',
        ), 401
    grant_session_ticket_access(normalized)
    meta = ticket_result_meta(record)
    view_token = ensure_ticket_view_token(record)
    return render_template(
        'ticket_view.html',
        ticket={
            'ticket_id': record.get('ticket_id', normalized),
            'quantity': int(record.get('quantity') or 1),
            'scanned': bool(record.get('scanned_at')),
            'current_event': ticket_belongs_to_current_event(record),
            'event_name': (ticket_event_record(record) or {}).get('name') or '',
            **meta,
        },
        ticket_data=build_qr_image(normalized),
        view_token=view_token,
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
    result = reset_admission_counts()
    totals = get_admission_totals()
    return jsonify({**result, **totals})


@app.route('/api/door-payment-intent', methods=['POST'])
def door_payment_intent():
    guard = protect_scanner_response()
    if guard:
        return guard
    if not stripe.api_key:
        return jsonify({'error': 'Payment system is not configured.'}), 503
    if not stripe_publishable_key:
        return jsonify({'error': 'Tap to pay is not configured. Set STRIPE_PUBLISHABLE_KEY.'}), 503
    if not rate_limit_allow('door_checkout', 30, 60):
        return jsonify({'error': 'Too many checkout attempts. Please wait a moment.'}), 429
    data = request.get_json(silent=True) or {}
    ticket_type = data.get('ticket_type', 'general')
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    event_id = data.get('event_id') or get_door_event_id()
    qty = clamp_quantity(data.get('quantity', 1), event_id=event_id)
    amount = door_unit_price_cents(ticket_type) * qty
    try:
        checkout = build_door_checkout_session(
            qty, ticket_type, event_id=event_id, embedded=True,
        )
        client_secret = checkout.get('client_secret') if isinstance(checkout, dict) else checkout.client_secret
        session_id = checkout.get('id') if isinstance(checkout, dict) else checkout.id
        if not client_secret:
            raise RuntimeError('Embedded checkout did not return a client secret')
        return jsonify({
            'client_secret': client_secret,
            'session_id': session_id,
            'amount': amount,
            'quantity': qty,
            'ticket_type': ticket_type,
            'publishable_key': stripe_publishable_key,
            'label': f"The Section door {TICKET_TYPES[ticket_type]['name']}",
        })
    except TicketSalesError as e:
        availability = get_ticket_availability(get_door_event_id())
        return jsonify({
            'error': str(e),
            'remaining': e.remaining,
            'sold_out': e.remaining <= 0,
            **availability,
        }), 409
    except Exception as e:
        print('Embedded door checkout failed, trying hosted checkout:', e)
        try:
            checkout = build_door_checkout_session(
                qty, ticket_type, event_id=event_id, embedded=False,
            )
            url = checkout.get('url') if isinstance(checkout, dict) else checkout.url
            if url:
                return jsonify({
                    'url': url,
                    'session_id': checkout.get('id') if isinstance(checkout, dict) else checkout.id,
                    'amount': amount,
                    'quantity': qty,
                    'ticket_type': ticket_type,
                })
        except Exception as hosted_error:
            e = hosted_error
        return jsonify({'error': door_stripe_error_message(e)}), 500


@app.route('/api/door-payment-complete', methods=['POST'])
def door_payment_complete():
    guard = protect_scanner_response()
    if guard:
        return guard
    if not stripe.api_key:
        return jsonify({'error': 'Payment system is not configured.'}), 503
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    intent_id = (data.get('payment_intent_id') or '').strip()
    if not session_id and not intent_id:
        return jsonify({'error': 'Missing payment.'}), 400
    try:
        if session_id:
            checkout_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])
            ticket = fulfill_paid_checkout(checkout_session)
            deliver_id = session_id
        else:
            payment_intent = stripe.PaymentIntent.retrieve(intent_id)
            ticket = fulfill_paid_payment_intent(payment_intent)
            deliver_id = intent_id
        ticket, vip_note = admit_paid_door_ticket(ticket)
        ticket_id = ticket.get('ticket_id')
        quantity = int(ticket.get('quantity') or 1)
        ticket_data = build_qr_image(ticket_id)
        deliver_ticket_email(
            deliver_id,
            ticket.get('email'),
            ticket_id,
            quantity,
            ticket_data,
            ticket.get('ticket_type', 'general'),
            ticket.get('access'),
        )
        meta = ticket_result_meta(ticket, admission_as=ticket.get('admission_as'))
        if vip_note:
            meta['vip_overflow_note'] = vip_note
        return jsonify({
            'status': 'accepted',
            'ticket_id': ticket_id,
            'quantity': quantity,
            'admission_totals': get_admission_totals(),
            **meta,
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 402
    except Exception as e:
        return jsonify({'error': public_error_message(e, 'Could not record this door sale. Try again.')}), 500


@app.route('/verify/door-paid')
def verify_door_paid():
    guard = protect_scanner_response()
    if guard:
        return guard
    session_id = (request.args.get('session_id') or '').strip()
    intent_id = (request.args.get('payment_intent') or '').strip()
    if not session_id and not intent_id:
        return redirect(url_for('verify_ticket'))
    if not stripe.api_key:
        return render_template(
            'verify_result.html',
            status='error',
            detail='Payment system is not configured.',
            admission_totals=get_admission_totals(),
            ticket_id=None,
            quantity=0,
            is_vip=False,
        )
    try:
        if intent_id:
            payment_intent = stripe.PaymentIntent.retrieve(intent_id)
            ticket = fulfill_paid_payment_intent(payment_intent)
            ticket, vip_note = admit_paid_door_ticket(ticket)
            deliver_id = intent_id
        else:
            checkout_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])
            if not checkout_session_is_paid(checkout_session):
                return render_template(
                    'verify_result.html',
                    status='error',
                    detail='Payment is not complete yet. If you were charged, refresh this page.',
                    admission_totals=get_admission_totals(),
                    ticket_id=None,
                    quantity=0,
                    is_vip=False,
                )
            ticket = fulfill_paid_checkout(checkout_session)
            ticket, vip_note = admit_paid_door_ticket(ticket)
            deliver_id = session_id
        ticket_id = ticket.get('ticket_id')
        quantity = int(ticket.get('quantity') or 1)
        ticket_data = build_qr_image(ticket_id)
        deliver_ticket_email(
            deliver_id,
            ticket.get('email'),
            ticket_id,
            quantity,
            ticket_data,
            ticket.get('ticket_type', 'general'),
            ticket.get('access'),
        )
        meta = ticket_result_meta(ticket, admission_as=ticket.get('admission_as'))
        if vip_note:
            meta['vip_overflow_note'] = vip_note
        return render_template(
            'verify_result.html',
            status='accepted',
            admission_totals=get_admission_totals(),
            ticket_id=ticket_id,
            quantity=quantity,
            **meta,
        )
    except Exception as e:
        return render_template(
            'verify_result.html',
            status='error',
            detail=public_error_message(e, 'Could not record this door sale. Try again.'),
            admission_totals=get_admission_totals(),
            ticket_id=None,
            quantity=0,
            is_vip=False,
        )


@app.route('/api/admission-totals/free-girl', methods=['POST'])
def add_free_girl_route():
    guard = protect_scanner_response()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    totals = add_free_girls(data.get('delta', 1))
    if totals is None:
        door_id = get_door_event_id()
        if not door_id or not get_event(door_id):
            return jsonify({'error': "Pick tonight’s event first."}), 400
        return jsonify({'error': 'Could not save. Try again.'}), 500
    return jsonify(totals)


@app.route('/api/admission-totals/reset-history', methods=['DELETE'])
def delete_admission_reset_history():
    guard = protect_scanner_response()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    entry_id = data.get('id') or request.args.get('id') or ''
    if not delete_reset_history_entry(entry_id):
        return jsonify({'error': 'Reset history entry not found'}), 404
    return jsonify(get_admission_totals())


@app.route('/api/ticket-sales/reset', methods=['POST'])
def reset_ticket_sales_route():
    guard = protect_scanner_response()
    if guard:
        return guard
    result = reset_ticket_sales()
    totals = get_admission_totals()
    return jsonify({**result, **totals})


@app.route('/api/scanner-settings', methods=['GET', 'POST'])
def scanner_settings():
    guard = protect_scanner_response()
    if guard:
        return guard

    if request.method == 'POST':
        data = request.get_json() or {}
        if 'door_event_id' in data or 'current_event_id' in data:
            chosen = data.get('door_event_id', data.get('current_event_id'))
            if chosen and get_event(chosen):
                set_door_event_id(chosen)
        totals = get_admission_totals()
        return jsonify(totals)

    return jsonify({
        'max_capacity': get_max_capacity(),
        'max_vip_capacity': get_max_vip_capacity(),
        **get_admission_totals(),
    })


@app.route('/verify/login', methods=['GET', 'POST'])
def verify_login():
    if request.method == 'POST':
        if not verify_auth_configured():
            return render_template(
                'verify_login.html',
                error='Scanner login is not configured. Set VERIFY_LOGIN_EMAIL and VERIFY_LOGIN_PASSWORD.',
            ), 503
        if not rate_limit_allow('scanner_login', 10, 300):
            return render_template(
                'verify_login.html',
                error='Too many login attempts. Please wait a few minutes.',
                next_url=safe_next_url(request.form.get('next'), ''),
            ), 429

        email = request.form.get('email') or ''
        password = request.form.get('password') or ''
        if verify_scanner_credentials(email, password):
            regenerate_session()
            mark_scanner_session_authenticated(email)
            if get_legacy_member((email or '').strip().lower()):
                mark_member_session(email)
            session['admin_authenticated'] = True
            next_url = safe_next_url(request.form.get('next'), url_for('verify_ticket'))
            return redirect(next_url)

        return render_template(
            'verify_login.html',
            error='Invalid email or password',
            next_url=safe_next_url(request.form.get('next'), ''),
        )

    if verify_authenticated():
        return redirect(url_for('verify_ticket'))

    next_url = safe_next_url(request.args.get('next'), '')
    return render_template('verify_login.html', next_url=next_url)


@app.route('/verify/logout', methods=['POST'])
def verify_logout():
    session.pop('verify_authenticated', None)
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
        payload = request.get_json(silent=True) if request.is_json else None
        ticket_data = request.form.get('ticket_data') or (payload or {}).get('ticket_data')
        ticket_id = parse_scanned_ticket(ticket_data)
        if not ticket_id:
            if request.is_json:
                return jsonify({
                    'status': 'invalid',
                    'ticket_id': None,
                    'quantity': 0,
                    'admission_totals': get_admission_totals(),
                })
            return "Invalid ticket"

        result = check_ticket(ticket_id)
        if request.is_json:
            return jsonify({**result, 'admission_totals': get_admission_totals()})
        if result['status'] == 'accepted':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            type_label = 'VIP' if result.get('is_vip') else 'GA'
            msg = f"✅ {type_label} — {qty} {guest_word} admitted"
            if result.get('vip_overflow_note'):
                msg += f" ({result['vip_overflow_note']})"
            return msg
        if result['status'] == 'used':
            qty = result['quantity']
            guest_word = 'guest' if qty == 1 else 'guests'
            return f"❌ Already used ({qty} {guest_word})"
        if result['status'] == 'sold_out':
            return '🎉 Ticket cap reached — congrats on selling this place out!'
        if result['status'] == 'wrong_event':
            ticket_night = result.get('ticket_event_name') or 'another event'
            door_night = result.get('door_event_name') or 'tonight'
            detail = result.get('detail') or f'This ticket is for {ticket_night}. Tonight is {door_night}.'
            return f'❌ Wrong event — {detail}'
        if result['status'] == 'error':
            return f"❌ {result.get('detail') or 'Could not record this scan. Try again.'}"
        if result['status'] == 'void':
            return f"❌ {result.get('detail') or 'This ticket is no longer valid.'}"
        return "Invalid ticket"

    return render_template(
        'verify.html',
        admission_totals=get_admission_totals(),
        stripe_publishable_key=stripe_publishable_key,
    )


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
                    'current_event': ticket_belongs_to_current_event(record),
                    'event_name': (ticket_event_record(record) or {}).get('name') or '',
                    'view_url': ticket_display_url(ticket_id, ensure_ticket_view_token(record)),
                })
    if logged_in:
        logged_in = ensure_returning_guest_flag_for_exclusive_member(logged_in) or logged_in
        # refresh after possible flag write
        logged_in = get_logged_in_member() or logged_in
    discount_eligible = member_discount_eligible(logged_in) if logged_in else False
    has_returning = member_has_returning_guest_discount(logged_in) if logged_in else False
    my_costume_entry = None
    my_costume_photo_url = None
    my_costume_score = 0
    if logged_in:
        my_costume_entry = find_costume_entry_for_email(logged_in.get('email'))
        if my_costume_entry:
            my_costume_photo_url = costume_photo_url(my_costume_entry.get('photo'))
            my_costume_score = costume_entry_score(my_costume_entry.get('id'))
    return {
        'error': error,
        'success': success,
        'member': logged_in,
        'saved_ticket_details': saved_ticket_details or [],
        'tickets_purchased': (
            ticket_quantities_by_email().get((logged_in.get('email') or '').strip().lower(), 0)
            if logged_in else 0
        ),
        'has_past_purchases': member_has_past_purchases(logged_in) if logged_in else False,
        'has_returning_guest_discount': has_returning,
        'discount_eligible': discount_eligible,
        'bundle_min': bundle_min,
        'bundle_discount_percent': int(bundle_discount * 100),
        'member_discount_percent': int(member_discount * 100),
        'returning_guest_discount_percent': int(returning_guest_discount * 100),
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
        'next_url': next_url,
        'active_tab': active_tab,
        'show_scanner_link': is_scanner_admin_member(),
        'my_costume_entry': my_costume_entry,
        'my_costume_photo_url': my_costume_photo_url,
        'my_costume_score': my_costume_score,
        'display_name_max': COSTUME_DISPLAY_NAME_MAX,
        'costume_max': COSTUME_DESCRIPTION_MAX,
        'show_costume_entry': is_costume_contest_open() or require_admin(),
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
        if not rate_limit_allow('password_reset', 10, 300):
            error = 'Too many attempts. Please wait a few minutes.'
            return render_template(
                'legacy_reset_password.html',
                email=email,
                token=token,
                token_valid=token_valid,
                error=error,
                success=None,
                reset_hours=PASSWORD_RESET_HOURS,
            ), 429
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not token_valid:
            error = 'This reset link is invalid or has expired. Request a new one from the member portal.'
        elif new_password != confirm_password:
            error = 'Passwords do not match.'
        elif len(new_password) < 8:
            error = 'Password must be at least 8 characters.'
        elif update_member_password(email, new_password):
            regenerate_session()
            mark_member_session(email)
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
    next_url = safe_next_url(request.args.get('next'), '')
    member = get_logged_in_member()

    if request.method == 'POST':
        action = request.form.get('action')
        next_url = safe_next_url(request.form.get('next') or request.args.get('next'), '')

        if action == 'register':
            if not rate_limit_allow('member_register', 8, 600):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(error='Too many attempts. Please wait a few minutes.', next_url=next_url, active_tab='register'),
                ), 429
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not email or not password:
                error = 'Email and password are required.'
            elif not is_valid_email(email):
                error = 'Enter a valid email address.'
            elif password != confirm_password:
                error = 'Passwords do not match.'
            elif len(password) < 8:
                error = 'Password must be at least 8 characters.'
            elif is_on_exclusive_invite_list(email):
                # Exclusive 20% accounts can only be created via the signed invite link.
                error = (
                    'That email has an exclusive invite. '
                    'Use the link we emailed you to create your account.'
                )
            elif get_legacy_member(email):
                # Avoid confirming account existence with a distinct message
                error = 'Could not create account. Try signing in or use a different email.'
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
                subscribe_signup_to_full_list(email)
                regenerate_session()
                mark_member_session(email)
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('legacy_portal'))
            return render_template(
                'legacy_portal.html',
                **portal_context(error=error, next_url=next_url, active_tab='register'),
            )

        if action == 'login':
            if not rate_limit_allow('member_login', 15, 300):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error='Too many login attempts. Please wait a few minutes.',
                        next_url=next_url,
                        active_tab='login',
                    ),
                ), 429
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            if verify_legacy_login(email, password):
                regenerate_session()
                mark_member_session(email)
                member = get_legacy_member(email)
                if member:
                    ensure_returning_guest_flag_for_exclusive_member(member)
                if is_staff_email(email):
                    session['admin_authenticated'] = True
                    mark_scanner_session_authenticated(email)
                if next_url:
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
            if not rate_limit_allow('forgot_password', 5, 600):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        success='If an account exists for that email, we sent a password reset link. Check your inbox and spam folder.',
                        next_url=next_url,
                        active_tab='forgot',
                    ),
                )
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

            # Always generic for anonymous users
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
            session.pop('admin_authenticated', None)
            session.pop('verify_authenticated', None)
            session.pop('verify_login_email', None)
            regenerate_session()
            # Prefer returning home when logout started from the site menu.
            next_url = safe_next_url(request.form.get('next') or request.args.get('next'), '')
            if next_url:
                return redirect(next_url)
            return redirect(url_for('legacy_portal'))

        if action == 'save_ticket' and member:
            # Ticket saving is automatic for purchases; manual attach only if email matches
            ticket_id = request.form.get('ticket_id', '')
            record = get_ticket_record(ticket_id)
            member_email = (member.get('email') or '').strip().lower()
            ticket_email = (record.get('email') or '').strip().lower() if record else ''
            if record and member_email and ticket_email and secure_equals(member_email, ticket_email):
                add_saved_ticket_for_member(member['email'], ticket_id)
                refreshed = get_legacy_member(member['email'])
                if refreshed and member_has_past_purchases(refreshed):
                    ensure_member_discount_code(refreshed)
            return redirect(url_for('legacy_portal'))

        if action == 'remove_ticket' and member:
            ticket_id = request.form.get('ticket_id', '')
            remove_saved_ticket_for_member(member['email'], ticket_id)
            return redirect(url_for('legacy_portal'))

        if action == 'costume_submit':
            if not member:
                return redirect(url_for('legacy_portal'))
            if not is_costume_contest_open() and not require_admin():
                return redirect(url_for('legacy_portal'))
            if not rate_limit_allow('costume_submit', 20, 300):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error='Too many attempts. Please wait a few minutes.',
                        next_url=next_url,
                    ),
                ), 429
            upload = request.files.get('photo')
            has_upload = bool(upload and getattr(upload, 'filename', None))
            remove_photo = (request.form.get('remove_photo') or '').strip().lower() in (
                '1', 'on', 'true', 'yes',
            )
            if has_upload and not rate_limit_allow('costume_photo_upload', 10, 300):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error='Too many photo uploads. Please wait a few minutes.',
                        next_url=next_url,
                    ),
                ), 429
            ok, result = submit_costume_entry_with_photo(
                member.get('email'),
                request.form.get('display_name', ''),
                request.form.get('costume', ''),
                upload=upload if has_upload else None,
                remove_photo=remove_photo,
            )
            if not ok:
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error=result or 'Could not save your costume.',
                        next_url=next_url,
                    ),
                )
            return redirect(url_for('legacy_portal', costume_saved=1))

        if action == 'costume_delete':
            if not member:
                return redirect(url_for('legacy_portal'))
            if not is_costume_contest_open() and not require_admin():
                return redirect(url_for('legacy_portal'))
            if not rate_limit_allow('costume_delete_own', 20, 300):
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error='Too many attempts. Please wait a few minutes.',
                        next_url=next_url,
                    ),
                ), 429
            ok, result = delete_own_costume_entry(member.get('email'))
            if not ok:
                return render_template(
                    'legacy_portal.html',
                    **portal_context(
                        error=result or 'Could not delete your costume.',
                        next_url=next_url,
                    ),
                )
            return redirect(url_for('legacy_portal', costume_deleted=1))

    success = None
    if request.args.get('costume_saved') == '1':
        success = 'Costume saved. Good luck!'
    elif request.args.get('costume_deleted') == '1':
        success = 'Costume entry deleted.'
    return render_template(
        'legacy_portal.html',
        **portal_context(next_url=next_url, success=success),
    )


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if require_admin():
        return redirect(url_for('admin_dashboard'))

    error = None
    if request.method == 'POST':
        if not rate_limit_allow('admin_login', 8, 300):
            return render_template('admin_login.html', error='Too many attempts. Please wait.'), 429
        key = (request.form.get('key') or request.form.get('password') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = (request.form.get('password') or '').strip()

        # Admin key alone (password field used for key when email blank)
        if key and secure_equals(key, admin_key):
            regenerate_session()
            session['admin_authenticated'] = True
            return redirect(url_for('admin_dashboard'))

        # Staff email + shared scanner password or member portal password
        if email and password and verify_scanner_credentials(email, password):
            regenerate_session()
            session['admin_authenticated'] = True
            mark_scanner_session_authenticated(email)
            if get_legacy_member(email):
                mark_member_session(email)
            return redirect(url_for('admin_dashboard'))

        error = 'Invalid credentials. Use staff email/password or admin key.'

    return render_template('admin_login.html', error=error)


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_authenticated', None)
    regenerate_session()
    return redirect(url_for('admin_login'))


@app.route('/logout', methods=['POST'])
def site_logout():
    session.pop('legacy_member_email', None)
    session.pop('admin_authenticated', None)
    session.pop('verify_authenticated', None)
    session.pop('verify_login_email', None)
    regenerate_session()
    next_url = safe_next_url(request.form.get('next') or request.args.get('next'), '/')
    return redirect(next_url or '/')


def event_payload_from_form(form, existing=None):
    payload = {
        'name': form.get('name'),
        'headline': form.get('headline'),
        'date': form.get('date'),
        'time_start': form.get('time_start'),
        'time_end': form.get('time_end'),
        'venue': form.get('venue'),
        'description': form.get('description'),
        'details': form.get('details'),
        'ticket_cap': form.get('ticket_cap'),
        'vip_cap': form.get('vip_cap'),
        'sales_open': coerce_sales_open(form.get('sales_open'), default=False),
        'teaser': coerce_sales_open(form.get('teaser'), default=False),
    }
    if existing:
        payload['id'] = existing.get('id')
        payload['flyer_filename'] = existing.get('flyer_filename')
        payload['flyer_static'] = existing.get('flyer_static')
        payload['created_at'] = existing.get('created_at')
    return payload


@app.route('/media/flyers/<filename>')
def serve_event_flyer(filename):
    if not flyer_is_safe_filename(filename):
        abort(404)
    return send_from_directory(flyers_dir, filename)


@app.route('/costume-photos/<filename>')
def serve_costume_photo(filename):
    if not costume_photo_is_safe_filename(filename):
        abort(404)
    return send_from_directory(costume_photos_dir, filename)


@app.route('/admin/events', methods=['GET', 'POST'])
def admin_events():
    if not require_admin():
        return redirect(url_for('admin_login'))

    error = None
    success = None
    if request.method == 'POST':
        action = request.form.get('action')
        event_id = (request.form.get('event_id') or '').strip()
        if action == 'door' and event_id and get_event(event_id):
            set_door_event_id(event_id)
            success = 'Door scanner will check tickets for this event.'
        elif action == 'listing' and event_id and get_event(event_id):
            event = get_event(event_id)
            sales_open = coerce_sales_open(request.form.get('sales_open'), default=False)
            teaser = coerce_sales_open(request.form.get('teaser'), default=False)
            upsert_event({**event, 'sales_open': sales_open, 'teaser': teaser}, event_id=event_id)
            name = event.get('name') or 'Event'
            if sales_open:
                success = f'{name} is on sale.'
                if not get_featured_event_id():
                    set_featured_event_id(event_id)
            elif teaser:
                success = f'{name} is a teaser.'
            else:
                success = f'{name} is saved for later.'
        elif action == 'delete' and event_id:
            event = get_event(event_id)
            if not event:
                error = 'Event not found.'
            else:
                sold = compute_ticket_sales_counts(event_id)['sold']
                if sold:
                    error = f'Cannot delete — {sold} ticket{"s" if sold != 1 else ""} already sold for this event.'
                else:
                    if event.get('flyer_filename'):
                        delete_event_flyer_file(event['flyer_filename'])
                    delete_event(event_id)
                    success = 'Event deleted.'
        else:
            error = 'Unknown action.'

    events = [event_display(event) for event in load_events()]
    events.sort(key=lambda item: item.get('date') or '', reverse=True)
    return render_template(
        'events.html',
        events=events,
        featured_event_id=get_featured_event_id(),
        door_event_id=get_door_event_id(),
        error=error,
        success=success,
    )


@app.route('/admin/events/new', methods=['GET', 'POST'])
def admin_event_new():
    if not require_admin():
        return redirect(url_for('admin_login'))
    return admin_event_form()


@app.route('/admin/events/<event_id>', methods=['GET', 'POST'])
def admin_event_edit(event_id):
    if not require_admin():
        return redirect(url_for('admin_login'))
    event = get_event(event_id)
    if not event:
        return redirect(url_for('admin_events'))
    return admin_event_form(event)


def admin_event_form(existing=None):
    error = None
    if request.method == 'POST':
        payload = event_payload_from_form(request.form, existing)
        if not (payload.get('name') or '').strip():
            error = 'Give the event a name.'
        else:
            try:
                saved = upsert_event(payload, event_id=(existing or {}).get('id'))
                upload = request.files.get('flyer')
                if upload and upload.filename:
                    filename = save_event_flyer(saved['id'], upload)
                    if filename:
                        old = saved.get('flyer_filename')
                        saved = upsert_event({**saved, 'flyer_filename': filename, 'flyer_static': ''}, event_id=saved['id'])
                        if old and old != filename:
                            delete_event_flyer_file(old)
                if saved.get('sales_open') and not get_featured_event_id():
                    set_featured_event_id(saved['id'])
                if not get_door_event_id():
                    set_door_event_id(saved['id'])
                return redirect(url_for('admin_events'))
            except ValueError as exc:
                error = str(exc)
            except Exception as exc:
                print('Event save failed:', exc)
                error = 'Could not save this event. Try again.'

    event = existing or {
        'name': '',
        'headline': '',
        'date': '',
        'time_start': '22:00',
        'time_end': '02:00',
        'venue': 'The Gem, Idaho Falls',
        'description': '',
        'details': '21+ • Limited Capacity • +$5 at door',
        'ticket_cap': '',
        'vip_cap': '',
        'sales_open': True,
        'teaser': False,
        'flyer_url': None,
    }
    if existing:
        event = event_display(existing)
    return render_template(
        'event_form.html',
        event=event,
        is_new=existing is None,
        error=error,
        featured_event_id=get_featured_event_id(),
        door_event_id=get_door_event_id(),
    )


@app.route('/admin')
def admin_dashboard():
    if not require_admin():
        return redirect(url_for('admin_login'))

    # Never include view_token secrets in admin JSON dump for clipboard sharing
    tickets = sorted(
        [ticket for ticket in load_tickets() if ticket_is_valid_purchase(ticket)],
        key=lambda t: t.get('purchased_at', ''),
        reverse=True,
    )
    safe_tickets = []
    for ticket in tickets:
        safe = {k: v for k, v in ticket.items() if k != 'view_token'}
        safe['event_name'] = (ticket_event_record(ticket) or {}).get('name') or ''
        safe_tickets.append(safe)
    total_admissions = sum(ticket_quantity(ticket) for ticket in tickets)
    ticket_counts = ticket_quantities_by_email(tickets)
    unique_buyers = len(ticket_counts)
    for ticket in safe_tickets:
        email = (ticket.get('email') or '').strip().lower()
        ticket['tickets_purchased'] = ticket_counts.get(email, 0)
    try:
        signup_count, invite_count = count_signups_and_invites()
    except Exception as e:
        print('Signup/invite count failed:', e)
        signup_count, invite_count = 0, 0
    contest_flash = None
    if request.args.get('contest') == 'shown':
        contest_flash = 'Costume contest is now visible in the menu.'
    elif request.args.get('contest') == 'hidden':
        contest_flash = 'Costume contest is now hidden from the public menu.'
    return render_template(
        'admin.html',
        tickets=safe_tickets,
        tickets_json=json.dumps(safe_tickets, indent=2),
        total_admissions=total_admissions,
        unique_buyers=unique_buyers,
        signup_count=signup_count,
        invite_count=invite_count,
        costume_contest_open=is_costume_contest_open(),
        contest_flash=contest_flash,
    )


@app.route('/admin/costume-contest', methods=['POST'])
def admin_costume_contest_toggle():
    if not require_admin():
        return redirect(url_for('admin_login'))
    currently_open = is_costume_contest_open()
    new_open = not currently_open
    contest_status = 'error'
    if set_costume_contest_open(new_open):
        contest_status = 'shown' if new_open else 'hidden'

    next_url = safe_next_url(request.form.get('next'), '')
    path_only = next_url.split('?', 1)[0].rstrip('/') if next_url else ''
    if path_only in ('', '/admin'):
        return redirect(url_for('admin_dashboard', contest=contest_status))
    if next_url and path_only != '/admin/costume-contest':
        return redirect(next_url)
    return redirect(url_for('admin_dashboard', contest=contest_status))


@app.route('/admin/tickets.csv')
def download_tickets_csv():
    if not require_admin():
        return redirect(url_for('admin_login'))

    tickets = load_tickets()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'purchased_at', 'ticket_id', 'email', 'quantity', 'ticket_type', 'access',
        'event_id', 'legacy_discount', 'scanned_at', 'email_sent_at', 'verify_url',
    ])
    for ticket in tickets:
        writer.writerow([
            ticket.get('purchased_at', ''),
            ticket.get('ticket_id', ''),
            ticket.get('email', ''),
            ticket.get('quantity', ''),
            ticket.get('ticket_type', 'general'),
            ticket.get('access', ''),
            ticket.get('event_id', ''),
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
        return redirect(url_for('admin_login'))

    tickets = load_tickets()
    safe_tickets = [{k: v for k, v in t.items() if k != 'view_token'} for t in tickets]
    return Response(
        json.dumps(safe_tickets, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=thesection-tickets.json'},
    )



@app.route('/admin/mailing-list', methods=['GET', 'POST'])
def admin_mailing_list():
    if not require_admin():
        return redirect(url_for('admin_login'))

    error = None
    success = None

    if request.method == 'POST':
        try:
            error, success = _admin_mailing_list_post()
        except Exception as e:
            error = public_error_message(
                e, 'Could not complete that mailing-list action. Please try again.'
            )
            success = None

    try:
        invites = invite_list_for_admin()
        ready_count = len(invites_ready_to_send())
        blocked_count = sum(1 for row in invites if row['status'] == 'account_exists')
        signup_count = sum(1 for row in invites if row['status'] in ('claimed', 'account_exists'))
        invite_count = len(invites)
        full_list = full_mailing_list_for_admin()
        backup_log = mailing_list_log_for_admin('remove')
        send_log = mailing_list_log_for_admin('send')
        message_log = broadcast_messages_for_admin()
        test_log = test_messages_for_admin()
    except Exception as e:
        error = public_error_message(
            e, 'Could not load mailing lists. Please try again.'
        )
        invites, ready_count, blocked_count = [], 0, 0
        signup_count, invite_count = 0, 0
        full_list, backup_log, send_log, message_log, test_log = [], [], [], [], []
    return render_template(
        'mailing_list.html',
        invites=invites,
        ready_count=ready_count,
        blocked_count=blocked_count,
        signup_count=signup_count,
        invite_count=invite_count,
        full_list=full_list,
        full_list_count=len(full_list),
        backup_log=backup_log,
        backup_log_count=len(backup_log),
        send_log=send_log,
        send_log_count=len(send_log),
        message_log=message_log,
        message_log_count=len(message_log),
        test_log=test_log,
        test_log_count=len(test_log),
        key='',
        error=error,
        success=success,
        member_discount_percent=int(member_discount * 100),
        returning_guest_discount_percent=int(returning_guest_discount * 100),
        invite_days=INVITE_EXPIRY_DAYS,
        timezone_label=display_timezone_label(),
        test_mail_recipients=test_mail_recipients(),
    )


def _admin_mailing_list_post():
    error = None
    success = None
    action = request.form.get('action')
    if action == 'add_emails':
        emails = normalize_email_list(request.form.get('emails', ''))
        if not emails:
            error = 'Add at least one valid email address.'
        else:
            added, skipped = add_emails_to_invite_list(emails)
            parts = []
            if added:
                parts.append(f'Added {len(added)} exclusive email{"s" if len(added) != 1 else ""}.')
            if skipped:
                parts.append(f'{len(skipped)} already on exclusive list.')
            success = ' '.join(parts) or 'No new emails added.'
    elif action in ('remove_email', 'remove_emails'):
        emails = posted_mailing_list_emails()
        if not emails:
            error = 'Select at least one email to remove.'
        else:
            removed, skipped = remove_emails_from_invite_list(emails)
            for email in removed:
                clear_exclusive_member_features(email)
            parts = []
            if len(removed) == 1:
                parts.append(
                    f'Removed {removed[0]} from exclusive list and cleared exclusive member perks '
                    f'(account/tickets kept if they exist).'
                )
            elif removed:
                parts.append(
                    f'Removed {len(removed)} emails from exclusive list and cleared exclusive member perks '
                    f'(accounts/tickets kept if they exist).'
                )
            if skipped:
                parts.append(
                    f'{len(skipped)} locked address{"es" if len(skipped) != 1 else ""} skipped.'
                )
            if removed:
                success = ' '.join(parts)
            else:
                error = ' '.join(parts) or 'Could not remove that email.'
    elif action == 'edit_email':
        old_email = (request.form.get('email') or '').strip().lower()
        new_email = (request.form.get('new_email') or '').strip().lower()
        ok, err = update_email_on_invite_list(old_email, new_email)
        if ok:
            success = (
                f'Updated exclusive email {old_email} → {new_email}.'
                if old_email != new_email else 'No change.'
            )
        else:
            error = err or 'Could not update that email.'
    elif action == 'send_invites':
        with mailing_send_guard() as got_lock:
            if not got_lock:
                error = 'A send is already in progress. Wait for it to finish.'
            elif not rate_limit_allow('send_invites', 5, 3600):
                error = 'Invite send limit reached (5 per hour). Wait before sending again.'
            else:
                result = send_pending_member_invites()
                sent_count = len(result['sent'])
                failed_count = len(result['failed'])
                skipped_count = len(result.get('skipped') or [])
                if sent_count:
                    success = f'Sent {sent_count} invite email{"s" if sent_count != 1 else ""}.'
                    if failed_count:
                        success += f' {failed_count} failed to send.'
                    if skipped_count:
                        success += f' {skipped_count} already sent.'
                elif failed_count:
                    error = f'Could not send invites ({failed_count} failed). Check mail settings.'
                else:
                    success = 'No pending invites to send.'
    elif action == 'add_full_emails':
        emails = normalize_email_list(request.form.get('emails', ''))
        if not emails:
            error = 'Add at least one valid email address for the full list.'
        else:
            added, skipped = add_emails_to_full_mailing_list(emails, source='manual')
            parts = []
            if added:
                parts.append(f'Added {len(added)} to full list.')
            if skipped:
                parts.append(
                    f'{len(skipped)} skipped (already on full list or exclusive list).'
                )
            success = ' '.join(parts) or 'No new emails added to full list.'
    elif action in ('remove_full_email', 'remove_full_emails'):
        emails = posted_mailing_list_emails()
        if not emails:
            error = 'Select at least one email to remove.'
        else:
            removed, skipped = remove_emails_from_full_mailing_list(emails)
            parts = []
            if len(removed) == 1:
                parts.append(f'Removed {removed[0]} from full list.')
            elif removed:
                parts.append(f'Removed {len(removed)} emails from full list.')
            if skipped:
                parts.append(
                    f'{len(skipped)} locked address{"es" if len(skipped) != 1 else ""} skipped.'
                )
            if removed:
                success = ' '.join(parts)
            else:
                error = ' '.join(parts) or 'Could not remove that email from the full list.'
    elif action == 'edit_full_email':
        old_email = (request.form.get('email') or '').strip().lower()
        new_email = (request.form.get('new_email') or '').strip().lower()
        ok, err = update_email_on_full_mailing_list(old_email, new_email)
        if ok:
            success = (
                f'Updated full-list email to {new_email}.'
                if old_email != new_email else 'No change.'
            )
        else:
            error = err or 'Could not update that email.'
    elif action == 'restore_log':
        ok, msg = restore_mailing_list_removal(request.form.get('log_id'))
        if ok:
            success = msg
        else:
            error = msg or 'Could not restore that backup entry.'
    elif action == 'sync_full_list':
        added, skipped = sync_members_into_full_mailing_list()
        success = (
            f'Synced members into full list: {len(added)} added, '
            f'{len(skipped)} already present or exclusive.'
        )
    elif action == 'send_test_broadcast':
        with mailing_send_guard() as got_lock:
            if not got_lock:
                error = 'A send is already in progress. Wait for it to finish.'
            elif not rate_limit_allow('test_broadcast', 20, 3600):
                error = 'Test send limit reached (20 per hour). Wait before sending again.'
            else:
                subject = (request.form.get('subject') or '').strip()
                body = (request.form.get('body') or '').strip()
                if not subject or not body:
                    error = 'Subject and message body are required.'
                elif any(c in subject for c in '\r\n'):
                    error = 'Subject cannot contain line breaks.'
                elif len(subject) > 200:
                    error = 'Subject is too long (max 200 characters).'
                elif len(body) > 20000:
                    error = 'Message is too long (max 20,000 characters).'
                else:
                    recipients = test_mail_recipients()
                    sent, failed, skipped, pending = send_test_broadcast_email(subject, body)
                    unexpected = [
                        email for email in (sent + failed + skipped + pending)
                        if email not in PROTECTED_MAILING_LIST_EMAILS
                    ]
                    if unexpected:
                        error = 'Test send aborted: unexpected recipient.'
                    else:
                        parts = []
                        if sent:
                            parts.append(
                                f'Test sent to {", ".join(sent)}.'
                            )
                        if failed:
                            parts.append(f'{len(failed)} test send(s) failed.')
                        if sent:
                            success = ' '.join(parts)
                        elif failed:
                            error = (
                                f'Test send failed for {", ".join(failed)}. Check mail settings.'
                            )
                        else:
                            error = 'Test send did not go out.'
    elif action == 'send_broadcast':
        with mailing_send_guard() as got_lock:
            if not got_lock:
                error = 'A send is already in progress. Wait for it to finish.'
            else:
                subject = (request.form.get('subject') or '').strip()
                body = (request.form.get('body') or '').strip()
                lists = set()
                if request.form.get('list_exclusive'):
                    lists.add('exclusive')
                if request.form.get('list_full'):
                    lists.add('full')
                if not lists:
                    error = 'Select at least one mailing list to send to.'
                elif not subject or not body:
                    error = 'Subject and message body are required.'
                elif any(c in subject for c in '\r\n'):
                    error = 'Subject cannot contain line breaks.'
                elif len(subject) > 200:
                    error = 'Subject is too long (max 200 characters).'
                elif len(body) > 20000:
                    error = 'Message is too long (max 20,000 characters).'
                else:
                    recipients = resolve_broadcast_recipients(lists)
                    skip_fingerprint = (request.form.get('skip_fingerprint') or '').strip()
                    earlier_sent = emails_sent_for_fingerprint(skip_fingerprint)
                    if earlier_sent:
                        recipients = [
                            email for email in recipients if email not in earlier_sent
                        ]
                    max_recipients = int(os.getenv('BROADCAST_MAX_RECIPIENTS', '2000'))
                    already_sent = emails_already_sent_message('broadcast', subject, body)
                    if not recipients:
                        error = (
                            'Everyone on those lists already got the earlier send you loaded.'
                            if earlier_sent
                            else 'No recipients on the selected list(s).'
                        )
                    elif len(recipients) > max_recipients:
                        error = (
                            f'Too many recipients ({len(recipients)}). '
                            f'Max is {max_recipients}. Split the send or raise BROADCAST_MAX_RECIPIENTS.'
                        )
                    elif not already_sent and not rate_limit_allow('broadcast_email', 3, 3600):
                        error = 'Broadcast limit reached (3 per hour). Wait before sending again.'
                    else:
                        sent, failed, skipped, pending = send_broadcast_email(
                            subject, body, recipients, lists=lists
                        )
                        parts = []
                        if sent:
                            parts.append(
                                f'Sent broadcast to {len(sent)} address{"es" if len(sent) != 1 else ""}.'
                            )
                        if earlier_sent:
                            parts.append(
                                f'Skipped {len(earlier_sent)} from the earlier send.'
                            )
                        if skipped:
                            parts.append(
                                f'Skipped {len(skipped)} already sent this message.'
                            )
                        if pending:
                            parts.append(
                                f'Continuing {len(pending)} in the background. '
                                f'Refresh the send log in a minute — already-sent addresses will not get a duplicate.'
                            )
                        if failed:
                            parts.append(f'{len(failed)} failed.')
                        if sent or skipped or pending:
                            success = ' '.join(parts)
                        elif failed:
                            error = f'All {len(failed)} sends failed. Check mail settings.'
                        else:
                            error = 'Nothing was sent.'
    elif action == 'retry_broadcast':
        with mailing_send_guard() as got_lock:
            if not got_lock:
                error = 'A send is already in progress. Wait for it to finish.'
            else:
                message = get_broadcast_message(request.form.get('message_id'))
                if not message:
                    error = 'That saved email was not found.'
                else:
                    subject = (message.get('subject') or '').strip()
                    body = (message.get('body') or '').strip()
                    lists = {
                        (item or '').strip().lower()
                        for item in (message.get('lists') or [])
                        if (item or '').strip().lower() in ('exclusive', 'full')
                    }
                    if not lists:
                        lists = {'exclusive', 'full'}
                    if not subject or not body:
                        error = 'That saved email has no subject or body to send.'
                    else:
                        recipients = resolve_broadcast_recipients(lists)
                        already_sent = emails_already_sent_message(
                            'broadcast', subject, body
                        )
                        remaining = [
                            email for email in recipients if email not in already_sent
                        ]
                        if not remaining:
                            success = 'Everyone on those lists already got this email.'
                        else:
                            sent, failed, skipped, pending = send_broadcast_email(
                                subject, body, remaining, lists=lists
                            )
                            parts = []
                            if sent:
                                parts.append(
                                    f'Sent to {len(sent)} address{"es" if len(sent) != 1 else ""} who missed it.'
                                )
                            if skipped:
                                parts.append(
                                    f'Skipped {len(skipped)} already sent this message.'
                                )
                            if pending:
                                parts.append(
                                    f'Continuing {len(pending)} in the background.'
                                )
                            if failed:
                                parts.append(f'{len(failed)} failed.')
                            if sent or skipped or pending:
                                success = ' '.join(parts)
                            elif failed:
                                error = f'All {len(failed)} sends failed. Check mail settings.'
                            else:
                                error = 'Nothing was sent.'
    return error, success


@app.route('/admin/mailing-list/log.json')
def admin_mailing_list_log_download():
    if not require_admin():
        return redirect(url_for('admin_login'))
    return Response(
        json.dumps(load_mailing_list_log(), indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=mailing-list-backup-log.json'},
    )


@app.route('/legacy/join', methods=['GET', 'POST'])
def legacy_member_invite_signup():
    email = (
        request.form.get('email', '').strip().lower()
        or request.args.get('email', '').strip().lower()
    )
    token = request.form.get('token', '') or request.args.get('token', '')
    error = None
    if not email or not token:
        return render_template(
            'legacy_invite_signup.html',
            email=email,
            token=token,
            token_valid=False,
            error='This invite link is incomplete. Use the link from your email.',
            invite_days=INVITE_EXPIRY_DAYS,
            member_discount_percent=int(member_discount * 100),
            returning_guest_discount_percent=int(returning_guest_discount * 100),
        )
    token_valid = verify_member_invite_token(email, token)
    if request.method == 'POST':
        if not rate_limit_allow('invite_signup', 10, 300):
            error = 'Too many attempts. Please wait a few minutes.'
        else:
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not token_valid:
                error = 'This invite link is invalid or has expired.'
            elif new_password != confirm_password:
                error = 'Passwords do not match.'
            elif len(new_password) < 8:
                error = 'Password must be at least 8 characters.'
            else:
                ok, create_error = create_member_from_invite(email, new_password)
                if ok:
                    regenerate_session()
                    mark_member_session(email)
                    return redirect(url_for('legacy_portal'))
                error = create_error or 'Could not create account.'
    return render_template(
        'legacy_invite_signup.html',
        email=email,
        token=token,
        token_valid=token_valid,
        error=error,
        invite_days=INVITE_EXPIRY_DAYS,
        member_discount_percent=int(member_discount * 100),
        returning_guest_discount_percent=int(returning_guest_discount * 100),
    )



@app.route('/costumes', methods=['GET', 'POST'])
def costumes():
    """Costume voting: members enter a costume and rank favorites (1st–3rd)."""
    member = get_logged_in_member()
    is_admin = require_admin()
    error = None
    success = None
    status = 200

    if not is_costume_contest_open() and not is_admin:
        return redirect(url_for('home'))

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()
        if action == 'admin_remove':
            if not is_admin:
                abort(403)
            if not rate_limit_allow('costume_admin_remove', 30, 300):
                error = 'Too many removals. Please wait a few minutes.'
                status = 429
            else:
                ok, result = delete_costume_entry(request.form.get('entry_id', ''))
                if not ok:
                    error = result or 'Could not remove that costume.'
                else:
                    return redirect(url_for('costumes', removed=1))
        elif not member:
            return redirect(url_for('legacy_portal', next='/costumes'))
        elif action == 'submit':
            if not rate_limit_allow('costume_submit', 20, 300):
                error = 'Too many attempts. Please wait a few minutes.'
                status = 429
            else:
                upload = request.files.get('photo')
                has_upload = bool(upload and getattr(upload, 'filename', None))
                remove_photo = (request.form.get('remove_photo') or '').strip().lower() in (
                    '1', 'on', 'true', 'yes',
                )
                if has_upload and not rate_limit_allow('costume_photo_upload', 10, 300):
                    error = 'Too many photo uploads. Please wait a few minutes.'
                    status = 429
                else:
                    ok, result = submit_costume_entry_with_photo(
                        member.get('email'),
                        request.form.get('display_name', ''),
                        request.form.get('costume', ''),
                        upload=upload if has_upload else None,
                        remove_photo=remove_photo,
                    )
                    if ok:
                        return redirect(url_for('costumes', saved=1))
                    error = result or 'Could not save your costume.'
        elif action in ('rank', 'ballot', 'vote', 'unvote'):
            # vote/unvote aliases kept so old forms fail softly into ranked save UX
            if not rate_limit_allow('costume_vote', 60, 60):
                error = 'Too many votes too quickly. Slow down a second.'
                status = 429
            else:
                ranks = [
                    request.form.get('rank_1', ''),
                    request.form.get('rank_2', ''),
                    request.form.get('rank_3', ''),
                ]
                ok, vote_error = set_costume_ballot(member.get('email'), ranks)
                if not ok:
                    error = vote_error or 'Could not update your rankings.'
                else:
                    return redirect(url_for('costumes', ranked=1))
        else:
            error = 'Unknown action.'

    if request.args.get('saved') == '1' and not error:
        success = 'Costume saved. Good luck!'
    elif request.args.get('removed') == '1' and not error:
        success = 'Costume entry removed.'
    elif request.args.get('ranked') == '1' and not error:
        success = 'Rankings saved.'

    viewer_email = (member or {}).get('email') if member else None
    entries = list_costume_entries_public(viewer_email)
    my_entry = find_costume_entry_for_email(viewer_email) if viewer_email else None
    my_photo_url = costume_photo_url((my_entry or {}).get('photo')) if my_entry else None
    my_ballot_ids = get_costume_ballot(viewer_email) if viewer_email else []
    entry_by_id = {e['id']: e for e in entries if e.get('id')}
    my_ballot = [entry_by_id[eid] for eid in my_ballot_ids if eid in entry_by_id]
    rankable_entries = [e for e in entries if not e.get('is_mine')]
    return render_template(
        'costumes.html',
        entries=entries,
        member=member,
        is_admin=is_admin,
        my_entry=my_entry,
        my_photo_url=my_photo_url,
        my_ballot=my_ballot,
        my_ballot_ids=my_ballot_ids,
        rankable_entries=rankable_entries,
        error=error,
        success=success,
        display_name_max=COSTUME_DISPLAY_NAME_MAX,
        costume_max=COSTUME_DESCRIPTION_MAX,
    ), status



if __name__ == '__main__':
    # Never enable debug in production
    app.run(host='127.0.0.1', port=5000, debug=False)
