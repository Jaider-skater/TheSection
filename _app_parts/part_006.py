 ticket_id,
        }],
        'eventTicket': {
            'primaryFields': [{
                'key': 'event',
                'label': 'EVENT',
                'value': 'The Section',
            }],
            'secondaryFields': [
                {
                    'key': 'guests',
                    'label': 'GUESTS',
                    'value': guest_label,
                },
                {
                    'key': 'ticket',
                    'label': 'TICKET',
                    'value': ticket_id,
                },
            ],
            'backFields': [{
                'key': 'verify',
                'label': 'VERIFY',
                'value': verify_url,
            }],
        },
    }

    icon_png = make_pass_icon_png()
    files = {
        'pass.json': json.dumps(pass_json, indent=2).encode('utf-8'),
        'icon.png': icon_png,
        'icon@2x.png': icon_png,
        'logo.png': icon_png,
        'logo@2x.png': icon_png,
    }
    manifest = {
        name: hashlib.sha1(data).hexdigest()
        for name, data in files.items()
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode('utf-8')
    files['manifest.json'] = manifest_bytes

    signature = sign_wallet_manifest(manifest_bytes)
    if not signature:
        return None

    files['signature'] = signature

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


bootstrap_legacy_members()
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


def compute_admission_counts():
    ga = 0
    vip = 0
    for ticket in load_tickets():
        scanned_at = ticket.get('scanned_at')
        if not scanned_at or not ticket_counts_for_current_period(scanned_at):
            continue
        qty = int(ticket.get('quantity') or 1)
        if ticket.get('ticket_type') == 'vip':
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


def get_admission_totals():
    counts = compute_admission_counts()
    max_capacity = get_max_capacity()
    capacity_reached = bool(max_capacity and counts['total'] >= max_capacity)
    spots_remaining = None
    if max_capacity:
        spots_remaining = max(0, max_capacity - counts['total'])
    return {
        **counts,
        'max_capacity': max_capacity,
        'capacity_reached': capacity_reached,
        'spots_remaining': spots_remaining,
        'reset_history': get_reset_history(),
    }


def check_ticket(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return {'status': 'invalid', 'ticket_id': ticket_id or None, 'quantity': 0, 'ticket_type': None, 'access': None, 'is_vip': False}

    record = get_ticket_record(normalized)
    if not record:
        return {'status': 'invalid', 'ticket_id': normalized, 'quantity': 0, 'ticket_type': None, 'access': None, 'is_vip': False}

    quantity = int(record.get('quantity') or 1)
    display_id = record.get('ticket_id', normalized)
    meta = ticket_result_meta(record)

    if record.get('scanned_at'):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    remaining = admission_capacity_remaining()
    if remaining is not None and quantity > remaining:
        return {'status': 'sold_out', 'ticket_id': display_id, 'quantity': quantity, **meta}

    if not mark_ticket_scanned(normalized):
        return {'status': 'used', 'ticket_id': display_id, 'quantity': quantity, **meta}

    return {'status': 'accepted', 'ticket_id': display_id, 'quantity': quantity, **meta}


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
         