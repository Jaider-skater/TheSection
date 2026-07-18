rn []
    if not os.path.exists(invites_file):
        return []
    try:
        with open(invites_file, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        print(f'Failed to load invites file ({invites_file}):', e)
        return []


def save_invites(invites):
    if not ensure_data_dir(invites_file):
        return False
    try:
        with open(invites_file, 'w', encoding='utf-8') as f:
            json.dump(invites, f, indent=2)
        return True
    except OSError as e:
        print(f'Failed to save invites file ({invites_file}):', e)
        return False


def normalize_email_list(raw):
    if not raw:
        return []
    normalized = []
    seen = set()
    for chunk in raw.replace(',', '\n').replace(';', '\n').split('\n'):
        email = chunk.strip().lower()
        if not email or '@' not in email:
            continue
        if email in seen:
            continue
        seen.add(email)
        normalized.append(email)
    return normalized


def get_member_invite(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    for invite in load_invites():
        if invite.get('email', '').strip().lower() == normalized:
            return invite
    return None


def add_emails_to_invite_list(emails):
    added = []
    skipped = []
    with invites_lock:
        invites = load_invites()
        existing = {i.get('email', '').strip().lower() for i in invites}
        for email in emails:
            if email in existing:
                skipped.append(email)
                continue
            invites.append({
                'email': email,
                'added_at': datetime.now(timezone.utc).isoformat(),
                'sent_at': None,
                'claimed_at': None,
                'invite_token': None,
                'invite_expires': None,
            })
            existing.add(email)
            added.append(email)
        save_invites(invites)
    return added, skipped


def remove_email_from_invite_list(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        updated = [i for i in invites if i.get('email', '').strip().lower() != normalized]
        if len(updated) == len(invites):
            return False
        save_invites(updated)
        return True


def set_member_invite_token(email):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['invite_token'] = hash_reset_token(token)
                invite['invite_expires'] = expires.isoformat()
                save_invites(invites)
                return token
    return None


def verify_member_invite_token(email, token):
    invite = get_member_invite(email)
    if not invite or not token or not invite.get('invite_token'):
        return False
    if invite.get('claimed_at'):
        return False
    expires_raw = invite.get('invite_expires')
    if not expires_raw:
        return False
    try:
        expires = datetime.fromisoformat(expires_raw.replace('Z', '+00:00'))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if datetime.now(timezone.utc) > expires:
        return False
    return invite['invite_token'] == hash_reset_token(token)


def mark_member_invite_claimed(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['claimed_at'] = datetime.now(timezone.utc).isoformat()
                invite.pop('invite_token', None)
                invite.pop('invite_expires', None)
                save_invites(invites)
                return True
    return False


def mark_member_invite_sent(email):
    normalized = email.strip().lower()
    with invites_lock:
        invites = load_invites()
        for invite in invites:
            if invite.get('email', '').strip().lower() == normalized:
                invite['sent_at'] = datetime.now(timezone.utc).isoformat()
                save_invites(invites)
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


def sync_member_tickets_from_email(member):
    email = member.get('email', '').strip().lower()
    if not email:
        return
    for ticket in load_tick