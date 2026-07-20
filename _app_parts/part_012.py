cated'] = True
            # Keep ?key= for bookmarkable links and download URLs that still expect it.
            sep = '&' if '?' in next_path else '?'
            return redirect(f'{next_path}{sep}key={key}')
        return render_template(
            'admin_login.html',
            error='Invalid admin key. Try again.',
            next_path=next_path,
        ), 401

    if require_admin():
        return redirect(next_path)
    return render_template('admin_login.html', error=None, next_path=next_path)


@app.route('/admin/logout', methods=['POST', 'GET'])
def admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/mailing-list', methods=['GET', 'POST'])
def admin_mailing_list():
    if not require_admin():
        return admin_login_required('/admin/mailing-list')

    key = admin_key_for_templates()
    error = None
    success = None

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_emails':
            emails = normalize_email_list(request.form.get('emails', ''))
            if not emails:
                error = 'Add at least one valid email address.'
            else:
                added, skipped = add_emails_to_invite_list(emails)
                parts = []
                if added:
                    parts.append(f'Added {len(added)} email{"s" if len(added) != 1 else ""}.')
                if skipped:
                    parts.append(f'{len(skipped)} already on the list.')
                success = ' '.join(parts) or 'No new emails added.'
        elif action == 'remove_email':
            email = (request.form.get('email') or '').strip().lower()
            if email and remove_email_from_invite_list(email):
                success = f'Removed {email} from the list.'
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

    invites = invite_list_for_admin()
    ready_count = len(invites_ready_to_send())
    blocked_count = sum(1 for row in invites if row['status'] == 'account_exists')
    return render_template(
        'mailing_list.html',
        invites=invites,
        ready_count=ready_count,
        blocked_count=blocked_count,
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
