   error = None

    if not email or not token:
        return render_template(
            'legacy_invite_signup.html',
            email='',
            token='',
            token_valid=False,
            error='This invite link is incomplete. Use the link from your email.',
            invite_days=INVITE_EXPIRY_DAYS,
            member_discount_percent=int(member_discount * 100),
            returning_guest_discount_percent=int(returning_guest_discount * 100),
        )

    token_valid = verify_member_invite_token(email, token)
    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not token_valid:
            error = 'This invite link is invalid or has expired.'
        elif new_password != confirm_password:
            error = 'Passwords do not match.'
        elif len(new_password) < 8:
            error = 'Password must be at least 8 characters.'
        else:
            ok, create_error = create_member_from_invite(email, new_password)
            if ok:
                session['legacy_member_email'] = email
                return redirect('/?open_tickets=1')
            error = create_error or 'Could not create your account. Try again or contact support.'

    return render_template(
        'legacy_invite_signup.html',
        email=email,
        token=token,
        token_valid=token_valid,
        error=error,
        invite_days=INVITE_EXPIRY_DAYS,
        member_discount_percent=int(member_discount * 100),
        returning_guest_discount_percent=int(returning_guest_discount * 100),
    )


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    next_path = request.values.get('next') or '/admin'
    if not next_path.startswith('/admin') or next_path.startswith('//'):
        next_path = '/admin'

    if request.method == 'POST':
        key = (request.form.get('key') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = (request.form.get('password') or '').strip()

        if key and _admin_key_matches(key):
            session['admin_authenticated'] = True
            sep = '&' if '?' in next_path else '?'
            return redirect(f'{next_path}{sep}key={key}')

        if email and password and verify_scanner_credentials(email, password):
            session['admin_authenticated'] = True
            mark_scanner_session_authenticated()
            if get_legacy_member(verify_login_email):
                session['legacy_member_email'] = verify_login_email
            return redirect(next_path)

        return render_template(
            'admin_login.html',
            error='Invalid credentials. Use staff email/password (VERIFY_LOGIN_*) or admin key.',
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
               