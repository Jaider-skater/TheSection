       sender=app.config['MAIL_DEFAULT_SENDER'],
                recipients=[customer_email],
            )
            access_line = f"Access: {access}\n" if access else ''
            msg.body = (
                f"You're in for The Section!\n\n"
                f"Ticket type: {type_label}\n"
                f"Ticket ID: {ticket_id}\n"
                f"Guests: {quantity}\n"
                f"{access_line}\n"
                f"Show the attached QR code at the door.\n"
                f"Or open this link on your phone to view your ticket:\n{view_url}\n"
            )
            msg.attach("ticket-qr.png", "image/png", base64.b64decode(ticket_data))
            mail.send(msg)
            print(f"Ticket email sent to {customer_email}")
            return True
        except Exception as e:
            print(f"Email failed for {customer_email}:", str(e))
            return False


def deliver_ticket_email(session_id, customer_email, ticket_id, quantity, ticket_data, ticket_type='general', access=None):
    if not customer_email:
        return False

    record = get_ticket_by_session(session_id)
    if record and record.get('email_sent_at'):
        return True

    if record:
        ticket_type = record.get('ticket_type', ticket_type)
        access = record.get('access', access)

    result = {'sent': False}

    def _send():
        result['sent'] = send_ticket_email(
            customer_email, ticket_id, quantity, ticket_data, ticket_type, access
        )
        if result['sent']:
            mark_email_sent(session_id)

    thread = threading.Thread(target=_send, daemon=False)
    thread.start()
    thread.join(timeout=app.config['MAIL_TIMEOUT'] + 2)
    return result['sent']


def build_password_reset_url(email, token, reset_url=None):
    if reset_url:
        return reset_url
    query = urlencode({'email': email, 'token': token})
    return f"{get_public_base_url()}/reset-password?{query}"


def mail_from_address():
    sender = app.config['MAIL_DEFAULT_SENDER']
    return ('The Section', sender) if sender else sender


def send_password_reset_email(customer_email, token, reset_url=None):
    reset_url = build_password_reset_url(customer_email, token, reset_url)
    hours_label = f'{PASSWORD_RESET_HOURS} hour{"s" if PASSWORD_RESET_HOURS != 1 else ""}'
    plain_body = (
        'You requested a password reset for your The Section member account.\n\n'
        f'Open this link to choose a new password (expires in {hours_label}):\n'
        f'{reset_url}\n\n'
        'If you did not request this, you can ignore this email.\n'
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        '<p>You requested a password reset for your member account.</p>'
        f'<p><a href="{reset_url}" style="display:inline-block;padding:12px 18px;'
        'background:#111;color:#fff;text-decoration:none;border-radius:10px;">'
        'Choose a new password</a></p>'
        f'<p style="color:#555;font-size:14px;">This link expires in {hours_label}.</p>'
        f'<p style="color:#555;font-size:14px;">If the button does not work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all;">{reset_url}</span></p>'
        '<p style="color:#555;font-size:14px;">If you did not request this, you can ignore this email.</p>'
        '</div>'
    )
    with app.app_context():
        try:
            msg = Message(
                'The Section — member password link',
                sender=mail_from_address(),
                recipients=[customer_email],
            )
            msg.body = plain_body
            msg.html = html_body
            mail.send(msg)
            print(f"Password reset email sent to {customer_email}")
            return True
        except Exception as e:
            print(f"Password reset email failed for {customer_email}:", str(e))
            return False


def deliver_password_reset_email(customer_email, token, reset_url=None):
    sent = send_password_reset_email(customer_email, token, reset_url)
    if not sent:
        print(f"Password reset email not confirmed for {customer_email}")
    return sent


def build_member_invite_url(email, token):
    query = urlencode({'email': email, 'token': token})
    return f"{get_public_base_url()}/legacy/join?{query}"


def send_member_invite_email(customer_email, token, invite_url=None):
    invite_url = invite_url or build_member_invite_url(customer_email, token)
    days_label = f'{INVITE_EXPIRY_DAYS} day{"s" if INVITE_EXPIRY_DAYS != 1 else ""}'
    welcome_pct = int(returning_guest_discount * 100)
    member_pct = int(member_discount * 100)
    plain_body = (
        "You've been to The Section before — welcome back!\n\n"
        f'Create your member account and get {welcome_pct}% off one ticket '
        f'(or {member_pct}% when you buy more than one):\n'
        f'{invite_url}\n\n'
        f'This link expires in {days_label}.\n'
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#111;max-width:560px;line-height:1.5;">'
        '<h2 style="margin:0 0 12px;">The Section</h2>'
        '<p>You\'ve been to The Section before — welcome back!</p>'
        f'<p>Create your member account to save tickets and get '
        f'<strong>{welcome_pct}% off one ticket</strong> — or '
        f'<strong>{member_pct}% off</strong> when you buy more than one for friends.</p>'
        f'<p><a href="{invite_url}" style="display:inline-block;padding:12px 18px;'
        'background:#111;color:#fff;text-decoration:none;border-radius:10px;">'
        'Set up your account</a></p>'
        f'<p style="color:#555;font-size:14px;">This link expires in {days_label}.</p>'
        f'<p style="color:#555;font-size:14px;">If the button does not work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all;">{invite_url}</span></p>'
        '</div>'
    )
    with app.app_context():
        try:
            msg = Message(
                'The Section — welcome back (member invite)',
                sender=mail_from_address(),
                recipients=[customer_email],
            )
            msg.body = plain_body
            msg.html = html_body
            mail.send(msg)
            print(f"Member invite email sent to {customer_email}")
            return True
        except Exception as e:
            print(f"Member invite email failed for {customer_email}:", str(e))
            return False


def deliver_member_invite_email(customer_email, token, invite_url=None):
    return send_member_invite_email(customer_email, token, invite_url=invite_url)


def send_pending_member_invites():
    sent = []
    failed = []
    skipped = []
    for email in invites_ready_to_send():
        if get_legacy_member(email):
            skipped.append(email)
            continue
        token = set_member_invite_token(email)
        if not token:
            failed.append(e