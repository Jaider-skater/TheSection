                ticket_type = ticket.get('ticket_type', 'general')
                entry = admission_as or ('vip' if ticket_type == 'vip' else 'general')
                if entry == 'general':
                    entry = 'ga'
                if entry not in ('vip', 'ga'):
                    entry = 'ga'

                # Already fully used as VIP, or GA ticket already used ever.
                if ticket.get('vip_redeemed_at'):
                    return False
                if ticket_type != 'vip' and ticket.get('scanned_at'):
                    return False
                # Same counting period already admitted.
                if ticket_counts_for_current_period(ticket.get('scanned_at')):
                    return False

                now_iso = datetime.now(timezone.utc).isoformat()
                ticket['scanned_at'] = now_iso
                ticket['admission_as'] = entry
                if entry == 'vip' or ticket_type != 'vip':
                    # Full VIP redeem, or any GA ticket → permanent for that privilege.
                    if entry == 'vip':
                        ticket['vip_redeemed_at'] = now_iso
                    # GA tickets stay void forever via scanned_at (no period re-use).
                # VIP + admission_as ga: leave vip_redeemed_at unset for later VIP use.
                save_tickets(tickets)
                return True
    return False


def parse_iso_datetime(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


_display_tz = None


def get_display_timezone():
    global _display_tz
    if _display_tz is None:
        try:
            _display_tz = ZoneInfo(APP_TIMEZONE)
        except Exception:
            _display_tz = ZoneInfo('America/Los_Angeles')
    return _display_tz


def display_timezone_label():
    return datetime.now(get_display_timezone()).strftime('%Z')


def format_display_datetime(iso_raw, date_only=False):
    dt = parse_iso_datetime(iso_raw)
    if not dt:
        return '—'
    local = dt.astimezone(get_display_timezone())
    if date_only:
        return local.strftime('%Y-%m-%d')
    return local.strftime('%Y-%m-%d %H:%M')


@app.template_filter('local_time')
def local_time_filter(iso_raw):
    return format_display_datetime(iso_raw)


@app.template_filter('local_date')
def local_date_filter(iso_raw):
    return format_display_datetime(iso_raw, date_only=True)


def get_counting_epoch():
    settings = load_scanner_settings()
    return parse_iso_datetime(settings.get('counting_epoch'))


def get_reset_history():
    settings = load_scanner_settings()
    history = settings.get('reset_history', [])
    if not isinstance(history, list):
        return []
    # Ensure each entry has a stable id for delete buttons.
    changed = False
    for entry in history:
        if isinstance(entry, dict) and not entry.get('id'):
            entry['id'] = entry.get('reset_at') or secrets.token_hex(8)
            changed = True
    if changed:
        with scanner_settings_lock:
            settings = load_scanner_settings()
            settings['reset_history'] = history
            save_scanner_settings(settings)
    return history


def delete_reset_history_entry(entry_id):
    """Remove one reset history row by id (or legacy reset_at string)."""
    target = (entry_id or '').strip()
    if not target:
        return False
    with scanner_settings_lock:
        settings = load_scanner_settings()
        history = settings.get('reset_history', [])
        if not isinstance(history, list):
            return False
        updated = []
        removed = False
        for entry in history:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get('id') or entry.get('reset_at') or '')
            if not removed and eid == target:
                removed = True
                continue
            updated.append(entry)
        if not removed:
            return False
        settings['reset_history'] = updated
        save_scanner_settings(settings)
        return True


def ticket_counts_for_current_period(scanned_at):
    scanned = parse_iso_datetime(scanned_at)
    if not scanned:
        return False
    counting_epoch = get_counting_epoch()
    if counting_epoch is None:
        return True
    return scanned >= counting_epoch


def reset_admission_counts():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    counts = compute_admission_counts()

    with scanner_settings_lock:
        settings = load_scanner_settings()
        history = settings.get('reset_history', [])
        if not isinstance(history, list):
            history = []
        history.append({
            'id': secrets.token_hex(8),
            'reset_at': now_iso,
            'ga': counts['ga'],
            'vip': counts['vip'],
            'total': counts['total'],
        })
        settings['reset_history'] = history
        settings['counting_epoch'] = now_iso
        save_scanner_settings(settings)

    return {
        'reset_at': now_iso,
        'ga': counts['ga'],
        'vip': counts['vip'],
        'total': counts['total'],
    }


def _admin_key_matches(provided):
    key = (provided or '').strip()
    expected = (admin_key or '').strip()
    if not key or not expected:
        return False
    try:
        return secrets.compare_digest(key, expected)
    except (TypeError, ValueError):
        return key == expected


def is_staff_user():
    """Staff email (VERIFY_LOGIN_*) via member portal or scanner session."""
    return is_scanner_admin_member() or verify_scanner_session_authenticated()


def require_admin():
    if session.get('admin_authenticated') is True:
