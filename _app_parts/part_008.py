        return True
    # Staff email session can open ticket admin + mailing lists (no separate key needed).
    if is_staff_user():
        session['admin_authenticated'] = True
        return True
    key = request.args.get('key') or request.form.get('key') or ''
    if _admin_key_matches(key):
        session['admin_authenticated'] = True
        return True
    return False


def admin_key_for_templates():
    return (request.args.get('key') or request.form.get('key') or '').strip()


def admin_login_required(next_path=None):
    """Return admin login page when the request is not authorized."""
    if next_path is None:
        next_path = request.path or '/admin'
    if not next_path.startswith('/admin'):
        next_path = '/admin'
    provided_key = (request.args.get('key') or request.form.get('key') or '').strip()
    provided_email = (request.args.get('email') or request.form.get('email') or '').strip()
    error = None
    if provided_key or provided_email:
        error = 'Invalid credentials. Use your staff email/password or admin key.'
    return render_template(
        'admin_login.html',
        error=error,
        next_path=next_path,
    ), 401


def verify_auth_configured():
    return bool(staff_emails and verify_login_password)


def is_staff_email(email):
    normalized = (email or '').strip().lower()
    if not normalized or not staff_emails:
        return False
    return any(secure_equal(normalized, allowed) for allowed in staff_emails)


def is_scanner_admin_member():
    if not staff_emails:
        return False
    member = get_logged_in_member()
    if not member:
        return False
    member_email = (member.get('email') or '').strip().lower()
    return is_staff_email(member_email)


def verify_scanner_session_authenticated():
    if session.get('verify_authenticated') is not True:
        return False
    logged_email = (session.get('verify_login_email') or '').strip().lower()
    return is_staff_email(logged_email)


def verify_authenticated():
    if not verify_auth_configured():
        return False
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def verify_scanner_credentials(email, password):
    """Staff form: any VERIFY_LOGIN email + shared password, or that member's portal password."""
    if not verify_auth_configured():
        return False
    normalized_email = (email or '').strip().lower()
    password = (password or '').strip()
    if not normalized_email or not password:
        return False
    if not is_staff_email(normalized_email):
        return False
    if secure_equal(password, verify_login_password):
        return True
    # Same person often uses the member portal password; accept that too.
    return verify_legacy_login(normalized_email, password)


def mark_scanner_session_authenticated(email=None):
    session['verify_authenticated'] = True
    chosen = (email or '').strip().lower()
    if not is_staff_email(chosen):
        chosen = verify_login_email or (staff_emails[0] if staff_emails else '')
    session['verify_login_email'] = chosen


def protect_scanner_response():
    if not verify_auth_configured():
        message = 'Scanner login is not configured. Set VERIFY_LOGIN_EMAIL and VERIFY_LOGIN_PASSWORD.'
        if request.method == 'POST' or request.is_json:
            return jsonify({'error': message}), 503
        return render_template('verify_login.html', error=message), 503

    if verify_authenticated():
        # Member-portal staff access: pin a scanner flag so API fetches stay authorized.
        if is_scanner_admin_member() and not verify_scanner_session_authenticated():
            mark_scanner_session_authenticated()
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
