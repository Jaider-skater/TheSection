value)
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized is None:
            settings.pop('max_capacity', None)
        else:
            settings['max_capacity'] = normalized
        save_scanner_settings(settings)
    return normalized


def get_max_vip_capacity():
    settings = load_scanner_settings()
    return parse_max_capacity(settings.get('max_vip_capacity'))


def set_max_vip_capacity(value):
    normalized = parse_max_capacity(value)
    with scanner_settings_lock:
        settings = load_scanner_settings()
        if normalized is None:
            settings.pop('max_vip_capacity', None)
        else:
            settings['max_vip_capacity'] = normalized
        save_scanner_settings(settings)
    return normalized


def admission_entry_type(ticket):
    """How this ticket counted for the door: vip or ga."""
    admitted = ticket.get('admission_as')
    if admitted in ('vip', 'ga'):
        return admitted
    return 'vip' if ticket.get('ticket_type') == 'vip' else 'ga'


def compute_admission_counts():
    ga = 0
    vip = 0
    for ticket in load_tickets():
        scanned_at = ticket.get('scanned_at')
        if not scanned_at or not ticket_counts_for_current_period(scanned_at):
            continue
        qty = int(ticket.get('quantity') or 1)
        if admission_entry_type(ticket) == 'vip':
            vip += qty
        else:
            ga += qty
    return {'ga': ga, 'vip': vip, 'total': ga + vip}


def admission_capacity_remaining():
    max_capacity = get_max_capacity()
    if not max_capacity:
        return None
    counts = compute_admission_counts()
    return max(0, max_capacity - counts['total'])


def vip_capacity_remaining():
    max_vip = get_max_vip_capacity()
    if not max_vip:
        return None
    counts = compute_admission_counts()
    return max(0, max_vip - counts['vip'])


def ticket_already_used(record):
    """Whether this ticket cannot be scanned again right now."""
    if not record:
        return True
    ticket_type = record.get('ticket_type', 'general')
    # VIP fully redeemed as VIP → never again.
    if record.get('vip_redeemed_at'):
        return True
    # Non-VIP: any prior scan voids forever.
    if ticket_type != 'vip' and record.get('scanned_at'):
        return True
    # Any ticket already admitted in the current counting period.
    if ticket_counts_for_current_period(record.get('scanned_at')):
        return True
    return False


def get_admission_totals():
    counts = compute_admission_counts()
    max_capacity = get_max_capacity()
    max_vip_capacity = get_max_vip_capacity()
    capacity_reached = bool(max_capacity and counts['total'] >= max_capacity)
    vip_capacity_reached = bool(max_vip_capacity and counts['vip'] >= max_vip_capacity)
    spots_remaining = None
    if max_capacity:
        spots_remaining = max(0, max_capacity - counts['total'])
    vip_spots_remaining = None
    if max_vip_capacity:
        vip_spots_remaining = max(0, max_vip_capacity - counts['vip'])
    return {
        **counts,
        'max_capacity': max_capacity,
        'capacity_reached': capacity_reached,
        'spots_remaining': spots_remaining,
        'max_vip_capacity': max_vip_capacity,
        'vip_capacity_reached': vip_capacity_reached,
        'vip_spots_remaining': vip_spots_remaining,
        'reset_history': get_reset_history(),
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

    if ticket_already_used(record):
        meta = ticket_result_meta(record)
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    remaining = admission_capacity_remaining()
    if remaining is not None and quantity > remaining:
        meta = ticket_result_meta(record)
        return {'status': 'sold_out', 'ticket_id': display_id, 'quantity': quantity, **meta}

    # Decide VIP vs GA entry for VIP tickets when VIP area is at capacity.
    admission_as = 'vip' if ticket_type == 'vip' else 'ga'
    vip_note = None
    if ticket_type == 'vip':
        vip_left = vip_capacity_remaining()
        if vip_left is not None and quantity > vip_left:
            admission_as = 'ga'
            vip_note = 'VIP area full — admitted as GA. VIP still valid for another event.'

    if not mark_ticket_scanned(normalized, admission_as=admission_as):
        meta = ticket_result_meta(record)
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    # Reload for vip_redeemed / admission_as on response meta.
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
                save_tickets(tickets)
                return


def send_ticket_email(customer_email, ticket_id, quantity, ticket_data, ticket_type='general', access=None):
    view_url = ticket_display_url(ticket_id)
    type_label = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])['name']
    with app.app_context():
        try:
            msg = Message(
                "Your The Section Tickets",
                sender=app.config['MAIL_DEFAULT_SENDER'],
                recipients=[customer_email],
            )
            access_line = f"Access: {access}\n" if access else ''
            msg.body = (
