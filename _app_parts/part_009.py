        'teamIdentifier': wallet_team_id,
        'organizationName': 'The Section',
        'description': 'The Section Ticket',
        'serialNumber': normalize_ticket_id(ticket_id),
        'foregroundColor': 'rgb(255, 255, 255)',
        'backgroundColor': 'rgb(24, 24, 27)',
        'labelColor': 'rgb(161, 161, 170)',
        'barcodes': [{
            'format': 'PKBarcodeFormatQR',
            'message': verify_url,
            'messageEncoding': 'iso-8859-1',
            'altText': ticket_id,
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
