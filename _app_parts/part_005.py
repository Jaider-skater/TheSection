    if 'full' in lists:
        for entry in load_full_mailing_list():
            email = (entry.get('email') or '').strip().lower()
            if email:
                emails.add(email)
    return sorted(emails)


def send_broadcast_email(subject, body, recipients):
    """Send plain/html broadcast to many recipients. Returns sent, failed lists."""
    subject = (subject or '').strip()
    body = (body or '').strip()
    sent = []
    failed = []
    if not subject or not body or not recipients:
        return sent, failed
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        + ''.join(f'<p>{line}</p>' if line.strip() else '<br>' for line in body.split('\n'))
        + '</div>'
    )
    with app.app_context():
        for email in recipients:
            try:
                msg = Message(
                    subject,
                    sender=mail_from_address(),
                    recipients=[email],
                )
                msg.body = body
                msg.html = html_body
                mail.send(msg)
                sent.append(email)
            except Exception as e:
                print(f'Broadcast email failed for {email}:', e)
                failed.append(email)
    return sent, failed


def clear_returning_guest_discount_if_purchased(email):
    """No-op: list members keep 20% on single tickets for life (multi-ticket stays at member rate)."""
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
    """Exclusive-list emails keep the lifetime single-ticket perk even if they signed up without the invite link."""
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


def active_member_discount_rate(quantity=1, require_active=True):
    """Percent rate (0–1) for the logged-in member at this quantity.

    Exclusive-list members keep returning_guest_discount for life:
    - quantity 1 → higher welcome rate (default 20%)
    - quantity 2+ → standard member rate (default 10%) for group/friend buys

    If require_active is False, returns the eligible rate even when the code is not applied.
    """
    member = get_logged_in_member()
    if member:
        member = ensure_returning_guest_flag_for_exclusive_member(member)
    if require_active and not member_discount_active():
        return 0.0
    if not member or not member_discount_eligible(member):
        return 0.0
    quantity = max(1, int(quantity or 1))
    if member_has_returning_guest_discount(member) and quantity == 1:
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


def calculate_total_cents(ticket_type, quantity, apply_member_discount=False):
    base = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['price_cents']
