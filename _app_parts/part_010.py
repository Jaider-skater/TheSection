 {guest_word} admitted"
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
        if member_discount_eligible(logged_in) and not logged_in.get('discount_code'):
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
        'has_returning_guest_discount': member_has_returning_guest_discount(logged_in) if logged_in else False,
        'discount_eligible': member_discount_eligible(logged_in) if logged_in else False,
        'bundle_min': bundle_min,
        'bundle_discount_percent': int(bundle_discount * 100),
        'member_discount_percent': int(member_discount * 100),
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
        'next_url': next_url,
        'active_tab': active_tab,
        'show_scanner_link': is_scanner_admin_member(),
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
     