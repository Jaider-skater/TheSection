,
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
    quantity = max(1, int(request.args.get('quantity', 1)))
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    apply_member = resolve_member_discount_application(
        request.args.get('apply_member_discount', '').lower() in ('1', 'true', 'yes')
    )
    return jsonify(pricing_breakdown(ticket_type, quantity, apply_member))


def build_checkout_session(quantity, ticket_type, apply_member_discount=False):
    if ticket_type not in TICKET_TYPES:
        ticket_type = 'general'
    quantity = max(1, int(quantity))

    legacy_member = is_legacy_member_logged_in()
    apply_member = resolve_member_discount_application(apply_member_discount)
    breakdown = pricing_breakdown(ticket_type, quantity, apply_member)
    unit_price = breakdown['unit_price_cents']
    ticket_meta = TICKET_TYPES[ticket_type]
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
        applied_pct = breakdown.get('applied_member_discount_percent') or breakdown.get(
            'member_discount_percent', 0
        )
        label = 'welcome' if breakdown.get('returning_single_ticket_rate') else 'member'
        if code:
            description += f' · {applied_pct}% {label} code {code}'
        else:
            description += f' · {applied_pct}% {label} discount'
    elif breakdown['bundle_discount_applied']:
        bulk_min = breakdown['bundle_min']
        description += f' · {breakdown["bundle_discount_percent"]}% bulk discount ({bulk_min}+ tickets)'

    member = get_logged_in_member()
    member_email = (member.get('email') or '').strip().lower() if member else ''

    print(f"Creating {ticket_type} session for {quantity} tickets @ {unit_price}c")

    checkout_kwargs = {
        'payment_method_types': ['card'],
        'line_items': [{
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': f"The Section - {ticket_meta['name']}",
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
        },
        'success_url': f"{base_url}/success?session_id={{CHECKOUT_SESSION_ID}}",
        'cancel_url': f"{base_url}/",
    }
    if member_email:
        checkout_kwargs['customer_email'] = member_email

    return stripe.checkout.Session.create(**checkout_kwargs)


@app.route('/api/checkout-intent', methods=['GET', 'POST', 'DELETE'])
def checkout_intent():
    if request.method == 'POST':
        data = request.get_json() or {}
        ticket_type = data.get('ticket_type', 'general')
        if ticket_type not in TICKET_TYPES:
            ticket_type = 'general'
        session['checkout_intent'] = {
            'quantity': max(1, int(data.get('quantity', 1))),
            'ticket_type': ticket_type,
            'apply_member_discount': bool(data.get('apply_member_discount')),
        }
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
        )
        return redirect(checkout_session.url)
    except Exception as e:
        print("Error resuming checkout:", str(e))
        return redirect('/?open_tickets=1')


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if not is_legacy_member_logged_in():
        return jsonify({'error': 'Sign in to your member account before purchasing tickets.'}), 401

    try:
        data = request.get_json()
        quantity = max(1, int(data.get('quantity', 1)))
        ticket_type = data.get('ticket_type', 'general')
        apply_member_discount = bool(data.get('apply_member_discount'))
        checkout_session = build_checkout_session(
            quantity, ticket_type, apply_member_discount=apply_member_discount,
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
