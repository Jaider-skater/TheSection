member(delivery_email, ticket_id)
                    clear_returning_guest_discount_if_purchased(delivery_email)
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
            mark_scanner_session_authenticated()
            # Keep member portal in sync so Door Scanner stays open after portal login.
            if get_legacy_member(verify_login_email):
                session['legacy_member_email'] = verify_login_email
            next_url = (request.form.get('next') or '').strip()
            if not next_url or not next_url.startswith('/'):
                next_url = url_for('verify_ticket')
            return redirect(next_url)

        return render_template(
            'verify_login.html',
            error='Invalid email or password. Use VERIFY_LOGIN_EMAIL plus VERIFY_LOGIN_PASSWORD, or that member account password.',
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
    # Auto-logout when leaving the scanner page: only clear the scanner flag.
    # Keep the member portal session so staff who signed in via /legacy stay signed in.
    auto_leave = request.headers.get('X-Scanner-Logout') == '1'
    if not auto_leave:
        # Explicit "Sign out" on the scanner: also leave the staff member portal account.
        member_email = (session.get('legacy_member_email') or '').strip().lower()
        if verify_login_email and member_email and secure_equal(member_email, verify_login_email):
            session.pop('legacy_member_email', None)
    if request.is_json or auto_leave:
        return jsonify({'ok': True})
    return redirect(url_for('home'))


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
            return f"✅ {type_label} — {qty}