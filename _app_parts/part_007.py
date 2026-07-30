t_display_datetime(iso_raw)


@app.template_filter('local_date')
def local_date_filter(iso_raw):
    return format_display_datetime(iso_raw, date_only=True)


def get_counting_epoch():
    settings = load_scanner_settings()
    return parse_iso_datetime(settings.get('counting_epoch'))


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
            settings['reset_history'] = history
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
            'id': secrets.token_hex(8),
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


def _admin_key_matches(provided):
    key = (provided or '').strip()
    expected = (admin_key or '').strip()
    if not key or not expected:
        return False
    try:
        return secrets.compare_digest(key, expected)
    except (TypeError, ValueError):
        return key == expected


def is_staff_user():
    """Staff email (VERIFY_LOGIN_*) via member portal or scanner session."""
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def require_admin():
    if session.get('admin_authenticated') is True:
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
        # Member-portal staff access: pin a scanner flag so API f