ly_member_discount=apply_member_discount,
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

        metadata = checkout_session.metadata or {}
        stripe_email = None
        if checkout_session.customer_details:
            stripe_email = checkout_session.customer_details.email

        existing_ticket = get_ticket_by_session(session_id)
        if existing_ticket:
            ticket_id = existing_ticket['ticket_id']
            quantity = existing_ticket['quantity']
            ticket_type = existing_ticket.get('ticket_type', 'general')
            access = existing_ticket.get('access')
            delivery_email = ticket_recipient_email(existing_ticket.get('email'), metadata)
        else:
            quantity = 1
            ticket_id = uuid.uuid4().hex[:12].upper()
            ticket_type = metadata.get('ticket_type', 'general')
            if ticket_type not in TICKET_TYPES:
                ticket_type = 'general'
            legacy_discount = metadata.get('legacy_discount') == 'true'

            if checkout_session.line_items and checkout_session.line_items.data:
                quantity = checkout_session.line_items.data[0].quantity

            delivery_email = ticket_recipient_email(stripe_email, metadata)

            record_ticket(
                session_id, ticket_id, delivery_email, quantity,
                ticket_type=ticket_type, legacy_discount=legacy_discount,
            )
            access = TICKET_TYPES[ticket_type].get('access')

            if delivery_email:
                purchased_member = get_legacy_member(delivery_email)
                if purchased_member:
                    add_saved_ticket_for_member(delivery_email, ticket_id)
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
            error='Invalid email or password. Use VERIFY_LOGIN_EMAIL plus VERIF