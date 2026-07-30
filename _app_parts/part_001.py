        '(not SECRET_KEY, and not the pk_live_ publishable key).'
    )

# Email Config (Gmail app password — set MAIL_* env vars on Render)
DEFAULT_MAIL_USERNAME = 'thesectionevents@gmail.com'
mail_username = (os.getenv('MAIL_USERNAME') or DEFAULT_MAIL_USERNAME).strip()
mail_sender = (os.getenv('MAIL_DEFAULT_SENDER') or mail_username).strip() or mail_username
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = mail_username
app.config['MAIL_PASSWORD'] = (os.getenv('MAIL_PASSWORD') or '').strip()
app.config['MAIL_DEFAULT_SENDER'] = mail_sender
app.config['MAIL_TIMEOUT'] = int(os.getenv('MAIL_TIMEOUT', '10'))
mail = Mail(app)


def ensure_data_dir(path):
    directory = os.path.dirname(path)
    if not directory:
        return True
    try:
        os.makedirs(directory, exist_ok=True)
        return True
    except OSError as e:
        print(f'Failed to create data directory ({directory}):', e)
        return False


def load_tickets():
    if not ensure_data_dir(tickets_file):
        return []
    if not os.path.exists(tickets_file):
        return []
    try:
        with open(tickets_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load tickets file ({tickets_file}):', e)
        return []


def save_tickets(tickets):
    if not ensure_data_dir(tickets_file):
        return False
    try:
        with open(tickets_file, 'w', encoding='utf-8') as f:
            json.dump(tickets, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save tickets file ({tickets_file}):', e)
        return False


def get_ticket_by_session(session_id):
    for ticket in load_tickets():
        if ticket.get('session_id') == session_id:
            return ticket
    return None


def record_ticket(session_id, ticket_id, email, quantity, ticket_type='general', legacy_discount=False):
    ticket_id = normalize_ticket_id(ticket_id)
    if not ticket_id:
        raise ValueError('Invalid ticket id')
    ticket_meta = TICKET_TYPES.get(ticket_type, TICKET_TYPES['general'])
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.get('session_id') == session_id:
                return ticket

        ticket = {
            'session_id': session_id,
            'ticket_id': ticket_id,
            'email': email,
            'quantity': quantity,
            'ticket_type': ticket_type,
            'access': ticket_meta.get('access'),
            'legacy_discount': legacy_discount,
            'purchased_at': datetime.now(timezone.utc).isoformat(),
            'scanned_at': None,
            'email_sent_at': None,
            'verify_url': f"{base_url}/verify/t/{ticket_id}",
        }
        tickets.append(ticket)
        save_tickets(tickets)
        return ticket


def hash_member_code(code):
    return hashlib.sha256(code.strip().upper().encode('utf-8')).hexdigest()


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, stored_hash):
    return check_password_hash(stored_hash, password)


def normalize_discount_code(code):
    if not code:
        return None
    normalized = str(code).strip().upper().replace(' ', '')
    return normalized if normalized.replace('-', '').isalnum() else None


def generate_discount_code(email):
    prefix = ''.join(c for c in email.split('@')[0].upper() if c.isalnum())[:4] or 'MEM'
    return f"{prefix}-{secrets.token_hex(2).upper()}"


def discount_code_taken(code, exclude_email=None):
    normalized = normalize_discount_code(code)
    if not normalized:
        return True
    for member in load_members():
        if exclude_email and member.get('email', '').lower() == exclude_email.strip().lower():
            continue
        if normalize_discount_code(member.get('discount_code', '')) == normalized:
            return True
    return False


def ensure_member_discount_code(member):
    if member.get('discount_code'):
        return member['discount_code']
    code = generate_discount_code(member.get('email', 'member'))
    while discount_code_taken(code, exclude_email=member.get('email')):
        code = generate_discount_code(member.get('email', 'member'))
    with members_lock:
        members = load_members()
        for stored in members:
            if stored.get('email', '').lower() == member.get('email', '').lower():
                stored['discount_code'] = code
                save_members(members)
                break
    member['discount_code'] = code
    return code


def load_members():
    if not ensure_data_dir(members_file):
        return []
    if not os.path.exists(members_file):
        return []
    try:
        with open(members_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else data.get('members', [])
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load members file ({members_file}):', e)
        return []


def save_members(members):
    if not ensure_data_dir(members_file):
        return False
    try:
        with open(members_file, 'w', encoding='utf-8') as f:
            json.dump(members, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save members file ({members_file}):', e)
        return False


def bootstrap_staff_emails():
    """Emails that get member-portal bootstrap accounts (comma-separated ok)."""
    emails = parse_staff_emails(
        os.getenv('LEGACY_BOOTSTRAP_EMAIL', ''),
        os.getenv('LEGACY_BOOTSTRAP_EMAILS', ''),
        os.getenv('VERIFY_LOGIN_EMAIL', ''),
        os.getenv('VERIFY_LOGIN_EMAILS', ''),
    )
    return emails


def bootstrap_legacy_members():
    bootstrap_password = (
        os.getenv('LEGACY_BOOTSTRAP_PASSWORD', '').strip()
        or os.getenv('LEGACY_BOOTSTRAP_CODE', '').strip()
        or verify_login_password
    )
