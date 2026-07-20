acy_member(email)
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
        })
    return rows


def invites_ready_to_send():
    ready = []
    for row in invite_list_for_admin():
        if row['status'] in ('pending', 'sent'):
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
    return True, None


def clear_returning_guest_discount_if_purchased(email):
    normalized = email.strip().lower()
    member = get_legacy_member(normalized)
    if not member or not member.get('returning_guest_discount'):
        return
    if not member_has_past_purchases(member):
        return
    with members_lock:
        members = load_members()
        for stored in members:
            if stored.get('email', '').strip().lower() == normalized:
                stored.pop('returning_guest_discount', None)
                save_members(members)
                break


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


def member_discount_eligible(member):
    if not member:
        return False
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


def active_member_discount_rate(quantity=1):
    """Percent rate (0–1) when member discount is applied.

    Returning-guest list members get the higher rate only for quantity 1
    so they can bring friends on multi-ticket orders at the normal member rate.
    """
    if not member_discount_active():
        return 0.0
    member = get_logged_in_member()
    quantity = max(1, int(quantity or 1))
    if member_has_returning_guest_discount(member) and quantity == 1:
        return returning_guest_discount if returning_guest_discount > 0 else member_discount
    return member_discount if member_discount > 0 else 0.0


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
    base_total = base * quantity
    quantity = max(1, int(quantity or 1))

    if not apply_member_discount:
        return calculate_bulk_total_cents(ticket_type, quantity)

    rate = active_member_discount_rate(quantity)
    if rate <= 0:
        return calculate_bulk_total_cents(ticket_type, quantity)

    # Single-ticket returning-guest rate does not stack with bulk (qty is always 1).
    if bulk_discount_applies(ticket_type, quantity):
        return int(base_total * (1 - bulk_discount_rate(ticket_type) - rate))

    return int(base_total * (1 - rate))


def calculate_unit_price(ticket_type, quantity, apply_member_discount=False):
    if quantity < 1:
        quantity = 1
    return calculate_total_cents(ticket_type, quantity, apply_member_discount) // quantity


def pricing_breakdown(ticket_type, quantity, apply_member_discount=False):
    quantity = max(1, int(quantity or 1))
    base = TICKET_TYPES[ticket_type]['price_cents']
    base_total_cents = base * quantity
    bulk_only_total = calculate_bulk_total_cents(ticket_type, quantity)
    rate = active_member_discount_rate(quantity) if apply_member_discount else 0.0
    total_cents = calculate_total_cents(ticket_type, quantity, apply_member_discount)
    unit_price = total_cents // quantity

    bulk_savings_active = bulk_only_total < base_total_cents
    member_requested = apply_member_discount and rate > 0
    stacked_discount_applied = (
        bulk_savings_active and member_requested and total_cents < bulk_only_total
    