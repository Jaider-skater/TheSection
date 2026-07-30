:
                if ticket.get('email', '').lower() == normalized:
                    has_purchases = True
                    break
            if not has_purchases:
                for ticket_id in member.get('saved_tickets') or []:
                    if get_ticket_record(ticket_id):
                        has_purchases = True
                        break
            if not has_purchases and member.get('discount_code'):
                member.pop('discount_code', None)
                changed = True
            if changed:
                save_members(members)
            return changed
    return False


def grant_exclusive_member_features(email):
    """If a member account exists for this email, attach exclusive lifetime perk."""
    normalized = (email or '').strip().lower()
    if not normalized:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').strip().lower() != normalized:
                continue
            member['returning_guest_discount'] = True
            if not member.get('discount_code'):
                code = generate_discount_code(normalized)
                while discount_code_taken(code, exclude_email=normalized):
                    code = generate_discount_code(normalized)
                member['discount_code'] = code
            save_members(members)
            return True
    return False


def update_email_on_invite_list(old_email, new_email):
    """Rename an exclusive-list address. Returns (ok, error_message)."""
    old = (old_email or '').strip().lower()
    new = (new_email or '').strip().lower()
    if not old or not new or '@' not in new:
        return False, 'Enter a valid new email address.'
    if old == new:
        return True, None
    with invites_lock:
        invites = load_invites()
        target = None
        for invite in invites:
            email = invite.get('email', '').strip().lower()
            if email == new:
                return False, f'{new} is already on the exclusive list.'
            if email == old:
                target = invite
        if not target:
            return False, 'That exclusive-list email was not found.'
        target['email'] = new
        # Force a fresh invite link if address changed before claim.
        if not target.get('claimed_at'):
            target['invite_token'] = None
            target['invite_expires'] = None
            target['sent_at'] = None
        save_invites(invites)
    # Old address loses exclusive perks; new address gains them if they have an account.
    clear_exclusive_member_features(old)
    grant_exclusive_member_features(new)
    return True, None


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
      