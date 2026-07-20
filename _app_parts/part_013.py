  parts = []
                if added:
                    parts.append(f'Added {len(added)} exclusive email{"s" if len(added) != 1 else ""}.')
                if skipped:
                    parts.append(f'{len(skipped)} already on exclusive list.')
                success = ' '.join(parts) or 'No new emails added.'
        elif action == 'remove_email':
            email = (request.form.get('email') or '').strip().lower()
            if email and remove_email_from_invite_list(email):
                success = f'Removed {email} from exclusive list.'
            else:
                error = 'Could not remove that email.'
        elif action == 'send_invites':
            result = send_pending_member_invites()
            sent_count = len(result['sent'])
            failed_count = len(result['failed'])
            if sent_count:
                success = f'Sent {sent_count} invite email{"s" if sent_count != 1 else ""}.'
                if failed_count:
                    success += f' {failed_count} failed to send.'
            elif failed_count:
                error = f'Could not send invites ({failed_count} failed). Check mail settings.'
            else:
                success = 'No pending invites to send.'
        elif action == 'add_full_emails':
            emails = normalize_email_list(request.form.get('emails', ''))
            if not emails:
                error = 'Add at least one valid email address for the full list.'
            else:
                added, skipped = add_emails_to_full_mailing_list(emails, source='manual')
                parts = []
                if added:
                    parts.append(f'Added {len(added)} to full list.')
                if skipped:
                    parts.append(
                        f'{len(skipped)} skipped (already on full list or exclusive list).'
                    )
                success = ' '.join(parts) or 'No new emails added to full list.'
        elif action == 'remove_full_email':
            email = (request.form.get('email') or '').strip().lower()
            if email and remove_email_from_full_mailing_list(email):
                success = f'Removed {email} from full list.'
            else:
                error = 'Could not remove that email from the full list.'
        elif action == 'sync_full_list':
            added, skipped = sync_members_into_full_mailing_list()
            success = (
                f'Synced members into full list: {len(added)} added, '
                f'{len(skipped)} already present or exclusive.'
            )
        elif action == 'send_broadcast':
            subject = (request.form.get('subject') or '').strip()
            body = (request.form.get('body') or '').strip()
            lists = set()
            if request.form.get('list_exclusive'):
                lists.add('exclusive')
            if request.form.get('list_full'):
                lists.add('full')
            if not lists:
                error = 'Select at least one mailing list to send to.'
            elif not subject or not body:
                error = 'Subject and message body are required.'
            else:
                recipients = resolve_broadcast_recipients(lists)
                if not recipients:
                    error = 'No recipients on the selected list(s).'
                else:
                    sent, failed = send_broadcast_email(subject, body, recipients)
                    if sent:
                        success = f'Sent broadcast to {len(sent)} address{"es" if len(sent) != 1 else ""}.'
                        if failed:
                            success += f' {len(failed)} failed.'
                    elif failed:
                        error = f'All {len(failed)} sends failed. Check mail settings.'
                    else:
                        error = 'Nothing was sent.'

    invites = invite_list_for_admin()
    ready_count = len(invites_ready_to_send())
    blocked_count = sum(1 for row in invites if row['status'] == 'account_exists')
    full_list = full_mailing_list_for_admin()
    return render_template(
        'mailing_list.html',
        invites=invites,
        ready_count=ready_count,
        blocked_count=blocked_count,
        full_list=full_list,
        full_list_count=len(full_list),
        key=key,
        error=error,
        success=success,
        member_discount_percent=int(member_discount * 100),
        returning_guest_discount_percent=int(returning_guest_discount * 100),
        invite_days=INVITE_EXPIRY_DAYS,
        timezone_label=display_timezone_label(),
    )


@app.route('/admin')
def admin_dashboard():
    if not require_admin():
        return admin_login_required('/admin')

    tickets = sorted(load_tickets(), key=lambda t: t.get('purchased_at', ''), reverse=True)
    total_admissions = sum(ticket.get('quantity', 0) for ticket in tickets)
    return render_template(
        'admin.html',
        tickets=tickets,
        tickets_json=json.dumps(tickets, indent=2),
        total_admissions=total_admissions,
        key=admin_key_for_templates(),
        timezone_label=display_timezone_label(),
    )


@app.route('/admin/tickets.csv')
def download_tickets_csv():
    if not require_admin():
        return admin_login_required('/admin')

    tickets = load_tickets()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'purchased_at', 'ticket_id', 'email', 'quantity', 'ticket_type', 'access',
        'legacy_discount', 'scanned_at', 'email_sent_at', 'verify_url',
    ])
    for ticket in tickets:
        writer.writerow([
            ticket.get('purchased_at', ''),
            ticket.get('ticket_id', ''),
            ticket.get('email', ''),
            ticket.get('quantity', ''),
            ticket.get('ticket_type', 'general'),
            ticket.get('access', ''),
            ticket.get('legacy_discount', False),
            ticket.get('scanned_at', ''),
            ticket.get('email_sent_at', ''),
            ticket.get('verify_url', ''),
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=thesection-tickets.csv'},
    )


@app.route('/admin/tickets.json')
def download_tickets_json():
    if not require_admin():
        return admin_login_required('/admin')

    return Response(
        json.dumps(load_tickets(), indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=thesection-tickets.json'},
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
