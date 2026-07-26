al
    )

    member_only_total = (
        int(base_total_cents * (1 - rate))
        if member_requested
        else None
    )

    bundle_discount_applied = bulk_savings_active and not stacked_discount_applied
    vip_bundle_applied = bundle_discount_applied and ticket_type == 'vip'
    member_discount_applied = (
        member_requested
        and not stacked_discount_applied
        and not bulk_savings_active
        and member_only_total is not None
        and total_cents == member_only_total
    )

    combined_discount_percent = None
    if stacked_discount_applied and bulk_discount_applies(ticket_type, quantity):
        combined_discount_percent = int(
            (bulk_discount_rate(ticket_type) + rate) * 100
        )

    bulk_min = vip_bundle_min if ticket_type == 'vip' else bundle_min
    bulk_percent = int(bulk_discount_rate(ticket_type) * 100)
    member = get_logged_in_member()
    if member:
        member = ensure_returning_guest_flag_for_exclusive_member(member)
    is_returning = member_has_returning_guest_discount(member)
    applied_pct = int(round(rate * 100)) if rate > 0 else 0
    eligible_pct = int(round(eligible_rate * 100)) if eligible_rate > 0 else 0
    # Welcome rate only when returning guest buys exactly one ticket.
    returning_single_ticket_rate = bool(is_returning and quantity == 1 and rate > 0)

    return {
        'ticket_type': ticket_type,
        'quantity': quantity,
        'unit_price_cents': unit_price,
        'total_cents': total_cents,
        'base_total_cents': base_total_cents,
        'base_unit_price_cents': base,
        'vip_bundle_applied': vip_bundle_applied,
        'bundle_discount_applied': bundle_discount_applied,
        'member_discount_applied': member_discount_applied,
        'stacked_discount_applied': stacked_discount_applied,
        'combined_discount_percent': combined_discount_percent,
        'legacy_discount_applied': total_cents < base_total_cents,
        'bundle_min': bulk_min,
        'bundle_discount_percent': bulk_percent,
        # Standard ongoing member rate (multi-ticket).
        'member_discount_percent': int(member_discount * 100) if member_discount > 0 else 10,
        # Rate used when code is ON for this cart.
        'applied_member_discount_percent': applied_pct,
        # Rate that would apply if they tap the code (for UI copy).
        'eligible_member_discount_percent': eligible_pct,
        'returning_guest_discount': is_returning,
        'returning_guest_discount_percent': (
            int(returning_guest_discount * 100) if returning_guest_discount > 0 else 20
        ),
        'returning_single_ticket_rate': returning_single_ticket_rate,
        'vip_bundle_min': vip_bundle_min,
        'vip_bulk_discount_percent': int(vip_bulk_discount * 100),
    }


def add_saved_ticket_for_member(email, ticket_id):
    normalized_id = normalize_ticket_id(ticket_id)
    if not normalized_id:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == email.strip().lower():
                saved = member.setdefault('saved_tickets', [])
                if normalized_id not in saved:
                    saved.append(normalized_id)
                    save_members(members)
                return True
    return False


def remove_saved_ticket_for_member(email, ticket_id):
    normalized_id = normalize_ticket_id(ticket_id)
    if not normalized_id:
        return False
    with members_lock:
        members = load_members()
        for member in members:
            if member.get('email', '').lower() == email.strip().lower():
                saved = member.get('saved_tickets', [])
                if normalized_id in saved:
                    saved.remove(normalized_id)
                    save_members(members)
                return True
    return False


def ticket_result_meta(record, admission_as=None):
    ticket_type = record.get('ticket_type', 'general')
    access = record.get('access') or TICKET_TYPES.get(ticket_type, {}).get('access')
    admitted = admission_as or record.get('admission_as')
    # Door display: how they entered this scan (VIP ticket may enter as GA when VIP full).
    is_vip_entry = (admitted or ticket_type) == 'vip'
    return {
        'ticket_type': ticket_type,
        'access': access if is_vip_entry else None,
        'is_vip': is_vip_entry,
        'ticket_is_vip': ticket_type == 'vip',
        'admission_as': admitted or ticket_type,
        'vip_redeemed': bool(record.get('vip_redeemed_at')),
        'vip_deferred': bool(
            ticket_type == 'vip'
            and admitted == 'ga'
            and not record.get('vip_redeemed_at')
        ),
    }


def normalize_ticket_id(ticket_id):
    if not ticket_id:
        return None
    normalized = str(ticket_id).strip().upper().replace('-', '')
    return normalized if normalized.isalnum() else None


def get_ticket_record(ticket_id):
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return None
    for ticket in load_tickets():
        stored = normalize_ticket_id(ticket.get('ticket_id'))
        if stored == normalized:
            return ticket
    return None


def mark_ticket_scanned(ticket_id, admission_as=None):
    """Record door entry. admission_as is 'vip' or 'ga'.

    VIP tickets admitted as GA (VIP area full) do not set vip_redeemed_at so the
    ticket can still be used as VIP at a later event after counts reset.
    """
    normalized = normalize_ticket_id(ticket_id)
    if not normalized:
        return False
    with tickets_lock:
        tickets = load_tickets()
        for ticket in tickets:
            if normalize_ticket_id(ticket.get('ticket_id')) == normalized:
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
                    # GA tickets stay void forever via scanned_a