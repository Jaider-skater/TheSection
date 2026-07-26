  '<p>You\'ve been to The Section before — welcome back!</p>'
        f'<p>Create your member account to save tickets and get '
        f'<strong>{welcome_pct}% off any one-ticket order for life</strong> — or '
        f'<strong>{member_pct}% off</strong> when you buy more than one for friends.</p>'
        f'<p><a href="{invite_url}" style="display:inline-block;padding:12px 18px;'
        'background:#111;color:#fff;text-decoration:none;border-radius:10px;">'
        'Set up your account</a></p>'
        f'<p style="color:#555;font-size:14px;">This link expires in {days_label}.</p>'
        f'<p style="color:#555;font-size:14px;">If the button does not work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all;">{invite_url}</span></p>'
        '</div>'
    )
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
            return True
        except Exception as e:
            print(f"Member invite email failed for {customer_email}:", str(e))
            return False


def deliver_member_invite_email(customer_email, token, invite_url=None):
    return send_member_invite_email(customer_email, token, invite_url=invite_url)


def send_pending_member_invites():
    sent = []
    failed = []
    skipped = []
    for email in invites_ready_to_send():
        if get_legacy_member(email):
            skipped.append(email)
            continue
        token = set_member_invite_token(email)
        if not token:
            failed.append(email)
            continue
        invite_url = build_member_invite_url(email, token)
        if deliver_member_invite_email(email, token, invite_url=invite_url):
            mark_member_invite_sent(email)
            sent.append(email)
        else:
            failed.append(email)
    return {'sent': sent, 'failed': failed, 'skipped': skipped}


@app.route('/')
def home():
    return render_template('home.html', show_scanner_link=is_scanner_admin_member())


@app.route('/api/member-status')
def member_status():
    member = get_logged_in_member()
    discount_code = None
    discount_eligible = False
    if member:
        member = ensure_returning_guest_flag_for_exclusive_member(member)
        discount_eligible = member_discount_eligible(member)
        if discount_eligible:
            discount_code = member.get('discount_code') or ensure_member_discount_code(member)
            member = get_logged_in_member() or member
    return jsonify({
        'logged_in': bool(member),
        'email': session.get('legacy_member_email'),
        'discount_code': discount_code,
        'member_discount_eligible': discount_eligible,
        'returning_guest_discount': member_has_returning_guest_discount(member) if member else False,
        'member_discount_percent': int(member_discount * 100) if member_discount > 0 else 10,
        'returning_guest_discount_percent': (
            int(returning_guest_discount * 100) if returning_guest_discount > 0 else 20
        ),
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

    r