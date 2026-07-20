es)
                return True
    return False


def invite_list_for_admin():
    rows = []
    for invite in sorted(load_invites(), key=lambda i: i.get('added_at', ''), reverse=True):
        email = invite.get('email', '').strip().lower()
        member = get_legacy_member(email)
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
    # Exclusive list only — do not put returning-guest accounts on the full list.
    return True, None


# --- Full mailing list (signups + founding + manual; no exclusive 20% perk) ---


def load_full_mailing_list():
    if not ensure_data_dir(full_mailing_list_file):
        return []
    if not os.path.exists(full_mailing_list_file):
        return []
    try:
        with open(full_mailing_list_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load full mailing list ({full_mailing_list_file}):', e)
        return []


def save_full_mailing_list(entries):
    if not ensure_data_dir(full_mailing_list_file):
        return False
    try:
        with open(full_mailing_list_file, 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save full mailing list ({full_mailing_list_file}):', e)
        return False


def is_on_exclusive_invite_list(email):
    return get_member_invite(email) is not None


def add_emails_to_full_mailing_list(emails, source='manual'):
    """Add emails to the general list. Skips exclusive invite-list addresses."""
    added = []
    skipped = []
    with full_list_lock:
        entries = load_full_mailing_list()
        existing = {e.get('email', '').strip().lower() for e in entries}
        for email in emails:
            normalized = (email or '').strip().lower()
            if not normalized or '@' not in normalized:
                continue
            if is_on_exclusive_invite_list(normalized):
                skipped.append(normalized)
                continue
            if normalized in existing:
                skipped.append(normalized)
                continue
            entries.append({
                'email': normalized,
                'added_at': datetime.now(timezone.utc).isoformat(),
                'source': source,
            })
            existing.add(normalized)
            added.append(normalized)
        save_full_mailing_list(entries)
    return added, skipped


def remove_email_from_full_mailing_list(email):
    normalized = email.strip().lower()
    with full_list_lock:
        entries = load_full_mailing_list()
        updated = [e for e in entries if e.get('email', '').strip().lower() != normalized]
        if len(updated) == len(entries):
            return False
        save_full_mailing_list(updated)
        return True


def full_mailing_list_for_admin():
    rows = []
    for entry in sorted(load_full_mailing_list(), key=lambda e: e.get('added_at', ''), reverse=True):
        email = entry.get('email', '').strip().lower()
        member = get_legacy_member(email)
        rows.append({
            'email': email,
            'added_at': entry.get('added_at'),
            'source': entry.get('source') or 'manual',
            'has_account': bool(member),
        })
    return rows


def sync_members_into_full_mailing_list():
    """Pull non-exclusive members (including founding) into the full list."""
    emails = []
    for member in load_members():
        email = (member.get('email') or '').strip().lower()
        if not email:
            continue
        if member.get('returning_guest_discount'):
            continue
        if is_on_exclusive_invite_list(email):
            continue
        emails.append(email)
    return add_emails_to_full_mailing_list(emails, source='member')


def subscribe_signup_to_full_list(email):
    """Public self-signup → full list only if not on exclusive invite list."""
    normalized = (email or '').strip().lower()
    if not normalized:
        return
    if is_on_exclusive_invite_list(normalized):
        return
    add_emails_to_full_mailing_list([normalized], source='signup')


def resolve_broadcast_recipients(lists):
    """lists is a set like {'exclusive', 'full'}."""
    emails = set()
    if 'exclusive' in lists:
        for invite in load_invites():
            email = (invite.get('email') or '').strip().lower()
            if email:
                emails.add(email)
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
             