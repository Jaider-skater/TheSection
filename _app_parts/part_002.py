    bootstrap_emails = bootstrap_staff_emails()
    if not bootstrap_emails:
        return
    if not bootstrap_password:
        print(
            'Staff/bootstrap emails are set but LEGACY_BOOTSTRAP_PASSWORD '
            '(or VERIFY_LOGIN_PASSWORD) is missing; member accounts will not auto-recreate.'
        )
        return
    with members_lock:
        members = load_members()
        existing = {m.get('email', '').lower() for m in members}
        created = 0
        for bootstrap_email in bootstrap_emails:
            if bootstrap_email in existing:
                print(f'Bootstrap member already present: {bootstrap_email}')
                continue
            bootstrap_discount_code = normalize_discount_code(
                os.getenv('LEGACY_BOOTSTRAP_DISCOUNT_CODE', '')
            ) or generate_discount_code(bootstrap_email)
            while discount_code_taken(bootstrap_discount_code):
                bootstrap_discount_code = generate_discount_code(bootstrap_email)
            members.append({
                'email': bootstrap_email,
                'password_hash': hash_password(bootstrap_password),
                'discount_code': bootstrap_discount_code,
                'saved_tickets': [],
                'joined_at': datetime.now(timezone.utc).isoformat(),
            })
            existing.add(bootstrap_email)
            created += 1
            print(f'Bootstrap member created after deploy: {bootstrap_email}')
        if created:
            save_members(members)


def log_storage_state():
    members = load_members()
    print(
        'Storage state:',
        f'members_file={members_file}',
        f'exists={os.path.exists(members_file)}',
        f'member_count={len(members)}',
        f'tickets_file={tickets_file}',
        f'tickets_exists={os.path.exists(tickets_file)}',
    )


def get_legacy_member(email):
    if not email:
        return None
    normalized = email.strip().lower()
    for member in load_members():
        if member.get('email', '').lower() == normalized:
            return member
    return None


def verify_legacy_login(email, password):
    member = get_legacy_member(email)
    if not member:
        return False
    if member.get('password_hash'):
        return verify_password(password, member['password_hash'])
    if member.get('code_hash'):
        return member.get('code_hash') == hash_member_code(password)
    return False


PASSWORD_RESET_HOURS = int(os.getenv('PASSWORD_RESET_HOURS', '1'))


def hash_reset_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def set_password_reset_token(email):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=PASSWORD_RESET_HOURS)
    normalized = email.strip().lower()
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == normalized:
                member['password_reset_token'] = hash_reset_token(token)
                member['password_reset_expires'] = expires.isoformat()
                save_members(members)
                return token
    return None


def verify_password_reset_token(email, token):
    member = get_legacy_member(email)
    if not member or not token or not member.get('password_reset_token'):
        return False
    expires_raw = member.get('password_reset_expires')
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
    return member['password_reset_token'] == hash_reset_token(token)


def update_member_password(email, new_password):
    normalized = email.strip().lower()
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == normalized:
                member['password_hash'] = hash_password(new_password)
                member.pop('code_hash', None)
                member.pop('password_reset_token', None)
                member.pop('password_reset_expires', None)
                save_members(members)
                return True
    return False


def load_invites():
    if not ensure_data_dir(invites_file):
        return []
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
