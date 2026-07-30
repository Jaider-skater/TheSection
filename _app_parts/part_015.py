east 8 characters.'
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
            staff_email = email.strip().lower()
            mark_scanner_session_authenticated(staff_email)
            if get_legacy_member(staff_email):
                session['legacy_member_email'] = staff_email
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
                clear_exclusive_member_features(email)
                success = (
                    f'Removed {email} from exclusive list and cleared exclusive member perks '
                    f'(account/tickets kept if they exist).'
                )
            else:
                error = 'Could not remove that email.'
        elif action == 'edit_email':
            old_email = (request.form.get('email') or '').strip().lower()
            new_email = (request.form.get('new_email') or '').strip().lower()
            ok, err = update_email_on_invite_list(old_email, new_email)
            if ok:
                if old_email != new_email:
                    success = (
                        f'Updated exclusive email {old_email} → {new_email}. '
                        f'Exclusive perks moved off the old address.'
                    )
                else:
                    success = 'No change.'
            else:
                error = err or 'Could not update that email.'
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
        elif action == 'edit_full_email':
            old_email = (request.form.get('email') or '').strip().lower()
            new_email = (request.form.get('new_email') or '').strip().lower()
            ok, err = update_email_on_full_mailing_list(old_email, new_email)
            if ok:
                success = (
                    f'Updated full-list email to {new_email}.'
                    if old_email != new_email
                    else 'No change.'
                )
            else:
                error = err or 'Could not update that email.'
        elif action == 'sync_full_list':
            added, skipped = sync_members_into_full_mailing_list()
            success = (
                f'Synced members into full list: {len(added)} added, '
                f'{len(skipped)} already present or exclusive.'
            )
        elif action == 'send_broadcast':
            subject = (request.form.get('subject') or '').strip()
            body = (request.form.get('body') or '').