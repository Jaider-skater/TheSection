ifest_bytes)
    if not signature:
        return None

    files['signature'] = signature

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


bootstrap_legacy_members()
_founding = bootstrap_staff_emails()
if _founding:
    add_emails_to_full_mailing_list(_founding, source='founding')
log_storage_state()


def extract_ticket_id_from_url(raw):
    for marker in ('/verify/t/', '/t/'):
        if marker in raw:
            ticket_id = raw.split(marker)[-1].split('?')[0].split('/')[0].strip()
            return normalize_ticket_id(ticket_id)
    return None


def load_scanner_settings():
    if not ensure_data_dir(scanner_settings_file):
        return {}
    if not os.path.exists(scanner_settings_file):
        return {}
    try:
        with open(scanner_settings_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load scanner settings ({scanner_settings_file}):', e)
        return {}


def save_scanner_settings(settings):
    if not ensure_data_dir(scanner_settings_file):
        return False
    try:
        with open(scanner_settings_file, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save scanner settings ({scanner_settings_file}):', e)
        return False


def parse_max_capacity(raw):
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_max_capacity():
    settings = load_scanner_settings()
    return parse_max_capacity(settings.get('max_capacity'))


def set_max_capacity(value):
    normalized = parse_max_capacity(value)
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
        return {'s