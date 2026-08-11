#!/usr/bin/env python3
"""
Configure Stripe webhook + Render environment variables for The Section.

Usage:
  1. Copy .env.example -> .deploy-secrets.env and fill STRIPE_SECRET_KEY + RENDER_API_KEY
  2. python3 scripts/setup_production.py

Safe to re-run: updates existing webhook / env vars when possible.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS_FILE = ROOT / ".deploy-secrets.env"
WEBHOOK_PATH = "/stripe/webhook"
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
]


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | list | None = None,
    form: dict[str, str] | None = None,
) -> tuple[int, object]:
    data = None
    req_headers = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return resp.status, payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"error": raw}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return e.code, payload


def require(cfg: dict[str, str], key: str) -> str:
    value = (cfg.get(key) or os.getenv(key) or "").strip()
    if not value:
        raise SystemExit(
            f"Missing {key}. Set it in {SECRETS_FILE.name} or the environment."
        )
    return value


def ensure_secret(cfg: dict[str, str], key: str, nbytes: int = 32) -> str:
    value = (cfg.get(key) or os.getenv(key) or "").strip()
    if value:
        return value
    generated = secrets.token_urlsafe(nbytes)
    cfg[key] = generated
    print(f"Generated {key}={mask(generated)}")
    return generated


def save_env_file(path: Path, cfg: dict[str, str], preserve_comments_from: Path | None = None):
    """Write key=value file, preserving unknown keys/order when possible."""
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    elif preserve_comments_from and preserve_comments_from.exists():
        existing_lines = preserve_comments_from.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    out: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in cfg:
            out.append(f"{key}={cfg[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in cfg.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def stripe_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Stripe-Version": "2024-06-20",
    }


def ensure_stripe_webhook(api_key: str, base_url: str) -> str:
    endpoint_url = base_url.rstrip("/") + WEBHOOK_PATH
    status, listed = http_json(
        "GET",
        "https://api.stripe.com/v1/webhook_endpoints?limit=100",
        headers=stripe_headers(api_key),
    )
    if status >= 400:
        raise SystemExit(f"Stripe list webhooks failed ({status}): {listed}")

    existing = None
    for item in (listed or {}).get("data", []):
        if item.get("url") == endpoint_url:
            existing = item
            break

    form = {
        "url": endpoint_url,
        "description": "The Section ticket fulfillment",
        "enabled_events[0]": WEBHOOK_EVENTS[0],
        "enabled_events[1]": WEBHOOK_EVENTS[1],
    }

    if existing:
        endpoint_id = existing["id"]
        print(f"Updating existing Stripe webhook {endpoint_id} -> {endpoint_url}")
        status, updated = http_json(
            "POST",
            f"https://api.stripe.com/v1/webhook_endpoints/{endpoint_id}",
            headers=stripe_headers(api_key),
            form=form,
        )
        if status >= 400:
            raise SystemExit(f"Stripe update webhook failed ({status}): {updated}")
        # secret is only returned on create; keep existing if we already stored it
        secret = updated.get("secret")
        if secret:
            print(f"Stripe webhook secret rotated: {mask(secret)}")
            return secret
        print(
            "Webhook updated. Secret not returned for existing endpoints — "
            "using STRIPE_WEBHOOK_SECRET from secrets file if present."
        )
        return ""

    print(f"Creating Stripe webhook -> {endpoint_url}")
    status, created = http_json(
        "POST",
        "https://api.stripe.com/v1/webhook_endpoints",
        headers=stripe_headers(api_key),
        form=form,
    )
    if status >= 400:
        raise SystemExit(f"Stripe create webhook failed ({status}): {created}")
    secret = created.get("secret") or ""
    if not secret:
        raise SystemExit(f"Stripe created webhook but no secret returned: {created}")
    print(f"Stripe webhook created: {created.get('id')} secret={mask(secret)}")
    return secret


def render_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def find_render_service(api_key: str, service_id: str, service_name: str) -> str:
    if service_id:
        return service_id
    status, payload = http_json(
        "GET",
        "https://api.render.com/v1/services?limit=50",
        headers=render_headers(api_key),
    )
    if status >= 400:
        raise SystemExit(f"Render list services failed ({status}): {payload}")

    matches = []
    for row in payload if isinstance(payload, list) else []:
        svc = row.get("service") if isinstance(row, dict) and "service" in row else row
        if not isinstance(svc, dict):
            continue
        name = (svc.get("name") or "").strip()
        sid = svc.get("id") or ""
        if service_name and name == service_name:
            matches.append(sid)
        elif not service_name:
            matches.append(sid)

    if not matches:
        raise SystemExit(
            f"No Render service named '{service_name}'. "
            "Set RENDER_SERVICE_ID in the secrets file."
        )
    if len(matches) > 1 and not service_name:
        raise SystemExit(
            f"Multiple Render services found; set RENDER_SERVICE_NAME or RENDER_SERVICE_ID. {matches}"
        )
    print(f"Using Render service {matches[0]}")
    return matches[0]


def upsert_render_env_var(api_key: str, service_id: str, key: str, value: str) -> None:
    status, payload = http_json(
        "PUT",
        f"https://api.render.com/v1/services/{service_id}/env-vars/{urllib.parse.quote(key, safe='')}",
        headers=render_headers(api_key),
        body={"value": value},
    )
    if status >= 400:
        raise SystemExit(f"Render set {key} failed ({status}): {payload}")
    print(f"Render env set: {key}={mask(value)}")


def trigger_render_deploy(api_key: str, service_id: str) -> None:
    status, payload = http_json(
        "POST",
        f"https://api.render.com/v1/services/{service_id}/deploys",
        headers=render_headers(api_key),
        body={"clearCache": "do_not_clear"},
    )
    if status >= 400:
        print(f"Warning: could not trigger deploy ({status}): {payload}")
        return
    deploy_id = payload.get("id") if isinstance(payload, dict) else None
    print(f"Triggered Render deploy{f' {deploy_id}' if deploy_id else ''}")


def main() -> int:
    cfg = load_env_file(SECRETS_FILE)
    # also allow process env to override
    for key in list(cfg.keys()) + [
        "STRIPE_SECRET_KEY",
        "RENDER_API_KEY",
        "RENDER_SERVICE_ID",
        "RENDER_SERVICE_NAME",
        "BASE_URL",
        "SECRET_KEY",
        "ADMIN_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "MAIL_USERNAME",
        "MAIL_PASSWORD",
        "MAIL_DEFAULT_SENDER",
        "VERIFY_LOGIN_EMAIL",
        "VERIFY_LOGIN_PASSWORD",
        "LEGACY_BOOTSTRAP_EMAIL",
        "LEGACY_BOOTSTRAP_PASSWORD",
    ]:
        env_val = os.getenv(key)
        if env_val:
            cfg[key] = env_val

    if not SECRETS_FILE.exists() and not cfg.get("STRIPE_SECRET_KEY"):
        example = ROOT / ".env.example"
        if example.exists() and not SECRETS_FILE.exists():
            SECRETS_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(SECRETS_FILE, 0o600)
        raise SystemExit(
            f"Created {SECRETS_FILE.name}. Fill in STRIPE_SECRET_KEY and RENDER_API_KEY, then re-run."
        )

    stripe_key = require(cfg, "STRIPE_SECRET_KEY")
    if not (
        stripe_key.startswith("sk_")
        or stripe_key.startswith("rk_")
    ):
        print(
            "WARNING: STRIPE_SECRET_KEY does not look like sk_/rk_. "
            "Restricted keys usually start with rk_test_ or rk_live_."
        )
    render_key = require(cfg, "RENDER_API_KEY")
    base_url = (cfg.get("BASE_URL") or "https://thesection.onrender.com").rstrip("/")
    cfg["BASE_URL"] = base_url

    secret_key = ensure_secret(cfg, "SECRET_KEY", 48)
    admin_key = ensure_secret(cfg, "ADMIN_KEY", 24)
    if not (cfg.get("VERIFY_LOGIN_PASSWORD") or "").strip():
        cfg["VERIFY_LOGIN_PASSWORD"] = secrets.token_urlsafe(16)
        print(f"Generated VERIFY_LOGIN_PASSWORD={mask(cfg['VERIFY_LOGIN_PASSWORD'])}")

    print("\n=== Stripe webhook ===")
    webhook_secret = ensure_stripe_webhook(stripe_key, base_url)
    if webhook_secret:
        cfg["STRIPE_WEBHOOK_SECRET"] = webhook_secret
    elif not (cfg.get("STRIPE_WEBHOOK_SECRET") or "").strip():
        print(
            "WARNING: No webhook secret available. "
            "If the endpoint already existed, roll the secret in Stripe Dashboard "
            "or delete the endpoint and re-run this script."
        )

    print("\n=== Render env vars ===")
    service_id = find_render_service(
        render_key,
        (cfg.get("RENDER_SERVICE_ID") or "").strip(),
        (cfg.get("RENDER_SERVICE_NAME") or "thesection").strip(),
    )
    cfg["RENDER_SERVICE_ID"] = service_id

    env_to_set = {
        "FLASK_ENV": "production",
        "BASE_URL": base_url,
        "SECRET_KEY": secret_key,
        "ADMIN_KEY": admin_key,
        "STRIPE_SECRET_KEY": stripe_key,
        "MAX_TICKET_QUANTITY": cfg.get("MAX_TICKET_QUANTITY") or "20",
        "MEMBERS_FILE": "/opt/render/project/src/data/legacy_members.json",
        "TICKETS_FILE": "/opt/render/project/src/data/tickets.json",
        "MAIL_SERVER": cfg.get("MAIL_SERVER") or "smtp.gmail.com",
        "MAIL_PORT": cfg.get("MAIL_PORT") or "587",
        "MAIL_USE_TLS": cfg.get("MAIL_USE_TLS") or "true",
    }
    if cfg.get("STRIPE_WEBHOOK_SECRET"):
        env_to_set["STRIPE_WEBHOOK_SECRET"] = cfg["STRIPE_WEBHOOK_SECRET"]

    optional_keys = [
        "MAIL_USERNAME",
        "MAIL_PASSWORD",
        "MAIL_DEFAULT_SENDER",
        "VERIFY_LOGIN_EMAIL",
        "VERIFY_LOGIN_PASSWORD",
        "LEGACY_BOOTSTRAP_EMAIL",
        "LEGACY_BOOTSTRAP_PASSWORD",
    ]
    for key in optional_keys:
        if (cfg.get(key) or "").strip():
            env_to_set[key] = cfg[key].strip()

    for key, value in env_to_set.items():
        upsert_render_env_var(render_key, service_id, key, value)

    save_env_file(SECRETS_FILE, cfg, preserve_comments_from=ROOT / ".env.example")
    print(f"\nSaved secrets locally to {SECRETS_FILE} (gitignored)")

    deploy = (os.getenv("TRIGGER_DEPLOY") or "1").strip().lower() not in ("0", "false", "no")
    if deploy:
        print("\n=== Deploy ===")
        trigger_render_deploy(render_key, service_id)
    else:
        print("\nSkipped deploy (TRIGGER_DEPLOY=0). Redeploy from the Render dashboard when ready.")

    print(
        "\nDone.\n"
        f"- Admin login: {base_url}/admin  (password = ADMIN_KEY)\n"
        f"- Webhook URL: {base_url}{WEBHOOK_PATH}\n"
        "- Rotate any old Stripe keys that were committed to git.\n"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
