#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import mimetypes
import re
import sys
import time
from datetime import datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from cedarhq.config import BASE_DIR, config
from cedarhq.db import migrate, transaction, utcnow
from cedarhq.security import hash_token, random_token, sign_value, unsign_value, validate_password_strength
from cedarhq.services import (
    OPS_ROLES,
    STATE_OPTIONS,
    calculate_cost,
    cents,
    create_checkout_and_order,
    create_email_token,
    create_session,
    create_user,
    destroy_session,
    ensure_reference_data,
    get_document_for_user,
    get_dashboard_context,
    get_latest_order_for_user,
    get_onboarding,
    get_or_create_google_sandbox_user,
    get_order,
    get_timeline,
    get_user_by_session,
    list_compliance,
    list_documents,
    list_orders_for_ops,
    new_id,
    ops_transition_order,
    process_reminders,
    reset_password,
    save_onboarding,
    seed_demo,
    send_auth_email,
    verify_email,
    authenticate_user,
)
from cedarhq.workspaces import (
    BOOKKEEPING_CATEGORIES,
    TAX_TYPES,
    advance_monthly_close,
    analytics_context,
    ask_assistant,
    assistant_context,
    bookkeeping_context,
    connect_sandbox_commerce,
    connect_sandbox_finance,
    create_tax_filing,
    decide_assistant_action,
    save_tax_questionnaire,
    tax_action,
    taxes_context,
    update_transaction,
)


SESSION_COOKIE = "cedarhq_session"
GUEST_CSRF_COOKIE = "cedarhq_guest_csrf"
RATE_LIMITS: dict[str, list[float]] = {}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def checked(actual, expected) -> str:
    return " checked" if str(actual or "") == str(expected) else ""


def selected(actual, expected) -> str:
    return " selected" if str(actual or "") == str(expected) else ""


def status_label(value: str) -> str:
    return value.replace("_", " ").title()


def entity_label(value: str | None) -> str:
    return "C-Corp" if value == "c_corp" else "LLC"


def bool_badge(value: int | bool, true_label: str = "Included", false_label: str = "Not included") -> str:
    return f"<span class='badge {'ok' if value else 'muted'}'>{true_label if value else false_label}</span>"


def date_label(value: str | None) -> str:
    if not value:
        return "Not available"
    try:
        return datetime.fromisoformat(value).strftime("%b %d, %Y")
    except ValueError:
        return str(value)


def timestamp_label(value: str | None) -> str:
    if not value:
        return "Not available"
    try:
        return datetime.fromisoformat(value).strftime("%b %d, %Y at %H:%M UTC")
    except ValueError:
        return str(value)


def user_initials(name: str | None) -> str:
    parts = [part for part in (name or "User").split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "U"


class CedarHandler(BaseHTTPRequestHandler):
    server_version = "CedarHQ/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        self.parsed = urlparse(self.path)
        self.path_only = self.parsed.path.rstrip("/") or "/"
        self.query = parse_qs(self.parsed.query)
        self.extra_headers: list[tuple[str, str]] = []
        self.cookies = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        self.user = None
        self.session = None
        try:
            if self.path_only.startswith("/static/"):
                self.serve_static()
                return
            with transaction() as conn:
                self.conn = conn
                ensure_reference_data(conn)
                token = self.cookie_value(SESSION_COOKIE)
                self.user, self.session = get_user_by_session(conn, token)
                if method == "GET":
                    self.route_get()
                else:
                    self.route_post()
        except BrokenPipeError:
            return
        except Exception as exc:
            self.send_error_page(500, "Something went wrong", str(exc))

    def route_get(self):
        path = self.path_only
        if path == "/":
            return self.home()
        if path == "/signup":
            return self.signup_page()
        if path == "/login":
            return self.login_page()
        if path == "/logout":
            return self.redirect("/")
        if path == "/forgot-password":
            return self.forgot_password_page()
        if path == "/reset-password":
            return self.reset_password_page()
        if path == "/verify-email":
            return self.verify_email_route()
        if path == "/auth/google":
            return self.google_page()
        if path == "/app":
            return self.app_dashboard()
        if path == "/app/onboarding":
            return self.onboarding_page()
        if path == "/app/documents":
            return self.documents_page()
        if path == "/app/compliance":
            return self.compliance_page()
        if path == "/app/billing":
            return self.billing_page()
        if path == "/app/support":
            return self.support_page()
        if path == "/app/assistant":
            return self.assistant_page()
        if path == "/app/bookkeeping":
            return self.bookkeeping_page()
        if path == "/app/taxes":
            return self.taxes_page()
        if path == "/app/analytics":
            return self.analytics_page()
        if path == "/api/bookkeeping/export.csv":
            return self.bookkeeping_export()
        if path == "/api/analytics/export.csv":
            return self.analytics_export()
        if path == "/admin":
            return self.redirect("/ops")
        if path == "/ops":
            return self.redirect("/ops/orders")
        if path == "/ops/orders":
            return self.ops_orders_page()
        if path == "/ops/compliance":
            return self.ops_compliance_page()
        if path == "/ops/audit":
            return self.ops_audit_page()
        if path == "/ops/taxes":
            return self.ops_taxes_page()
        order_match = re.fullmatch(r"/app/orders/([^/]+)", path)
        if order_match:
            return self.order_page(unquote(order_match.group(1)))
        ops_order_match = re.fullmatch(r"/ops/orders/([^/]+)", path)
        if ops_order_match:
            return self.ops_order_page(unquote(ops_order_match.group(1)))
        doc_match = re.fullmatch(r"/api/documents/([^/]+)/download", path)
        if doc_match:
            return self.download_document(unquote(doc_match.group(1)))
        evidence_match = re.fullmatch(r"/api/evidence/([^/]+)/download", path)
        if evidence_match:
            return self.download_evidence(unquote(evidence_match.group(1)))
        self.send_error_page(404, "Not found", "The requested page does not exist.")

    def route_post(self):
        path = self.path_only
        if self._rate_limited(path):
            return self.send_error_page(429, "Too many attempts", "Wait a minute and try again.")
        if path == "/signup":
            return self.signup_post()
        if path == "/login":
            return self.login_post()
        if path == "/logout":
            return self.logout_post()
        if path == "/forgot-password":
            return self.forgot_password_post()
        if path == "/reset-password":
            return self.reset_password_post()
        if path == "/auth/google":
            return self.google_post()
        if path == "/api/onboarding/save":
            return self.onboarding_save_post()
        if path == "/api/checkout/sandbox":
            return self.checkout_post()
        if path == "/app/support":
            return self.support_post()
        if path == "/api/bookkeeping/connect-sandbox":
            return self.bookkeeping_connect_post()
        if path == "/api/taxes/start":
            return self.tax_start_post()
        if path == "/api/commerce/connect-sandbox":
            return self.commerce_connect_post()
        if path == "/api/assistant/message":
            return self.assistant_message_post()
        if path == "/api/jobs/reminders":
            return self.jobs_reminders_post()
        transaction_match = re.fullmatch(r"/api/bookkeeping/transactions/([^/]+)", path)
        if transaction_match:
            return self.bookkeeping_transaction_post(unquote(transaction_match.group(1)))
        close_match = re.fullmatch(r"/api/bookkeeping/closes/([^/]+)", path)
        if close_match:
            return self.bookkeeping_close_post(unquote(close_match.group(1)))
        tax_save_match = re.fullmatch(r"/api/taxes/([^/]+)/save", path)
        if tax_save_match:
            return self.tax_save_post(unquote(tax_save_match.group(1)))
        tax_action_match = re.fullmatch(r"/api/taxes/([^/]+)/action", path)
        if tax_action_match:
            return self.tax_action_post(unquote(tax_action_match.group(1)))
        assistant_action_match = re.fullmatch(r"/api/assistant/actions/([^/]+)", path)
        if assistant_action_match:
            return self.assistant_action_post(unquote(assistant_action_match.group(1)))
        ops_tax_action_match = re.fullmatch(r"/api/ops/taxes/([^/]+)/action", path)
        if ops_tax_action_match:
            return self.ops_tax_action_post(unquote(ops_tax_action_match.group(1)))
        ops_transition = re.fullmatch(r"/api/ops/orders/([^/]+)/transition", path)
        if ops_transition:
            return self.ops_transition_post(unquote(ops_transition.group(1)))
        self.send_error_page(404, "Not found", "The requested action does not exist.")

    def serve_static(self):
        relative = self.path_only.removeprefix("/static/").strip("/")
        target = (BASE_DIR / "static" / relative).resolve()
        if not str(target).startswith(str((BASE_DIR / "static").resolve())) or not target.is_file():
            return self.send_error_page(404, "Not found", "Static asset not found.")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def form_data(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}

    def json_data(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        parsed = json.loads(raw)
        return {str(key): str(value) for key, value in parsed.items()}

    def cookie_value(self, name: str) -> str | None:
        morsel = self.cookies.get(name)
        return morsel.value if morsel else None

    def cookie_header(self, name: str, value: str, max_age: int | None = None, http_only: bool = True) -> str:
        parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
        if http_only:
            parts.append("HttpOnly")
        if config.secure_cookies:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        return "; ".join(parts)

    def public_csrf(self) -> str:
        cached = getattr(self, "_guest_csrf", None)
        if cached:
            return cached
        signed = self.cookie_value(GUEST_CSRF_COOKIE)
        value = unsign_value(signed) if signed else None
        if not value:
            value = random_token(24)
            self.extra_headers.append((self.cookie_header(GUEST_CSRF_COOKIE, sign_value(value), max_age=86400), ""))
        self._guest_csrf = value
        return value

    def csrf_token(self) -> str:
        if self.session:
            return self.session["csrf_token"]
        return self.public_csrf()

    def verify_csrf(self, data: dict[str, str]) -> bool:
        provided = data.get("csrf_token") or self.headers.get("X-CSRF-Token") or ""
        if self.session:
            return provided == self.session["csrf_token"]
        signed = self.cookie_value(GUEST_CSRF_COOKIE)
        expected = unsign_value(signed) if signed else None
        return bool(expected and provided == expected)

    def set_session_cookie(self, token: str) -> None:
        self.extra_headers.append((self.cookie_header(SESSION_COOKIE, token, max_age=7 * 24 * 3600), ""))

    def clear_session_cookie(self) -> None:
        self.extra_headers.append((self.cookie_header(SESSION_COOKIE, "", max_age=0), ""))

    def _rate_limited(self, key: str, limit: int = 30, window_seconds: int = 60) -> bool:
        remote = self.client_address[0] if self.client_address else "local"
        bucket = f"{remote}:{key}"
        now = time.time()
        events = [ts for ts in RATE_LIMITS.get(bucket, []) if now - ts < window_seconds]
        events.append(now)
        RATE_LIMITS[bucket] = events
        return len(events) > limit

    def require_auth(self) -> bool:
        if self.user:
            return True
        self.redirect(f"/login?next={quote(self.path)}")
        return False

    def require_ops(self) -> bool:
        if not self.require_auth():
            return False
        if self.user["role"] not in OPS_ROLES:
            self.send_error_page(403, "Access denied", "This workspace requires a staff, accountant, or admin role.")
            return False
        return True

    def send_html(self, title: str, body: str, status: int = 200):
        page = self.layout(title, body)
        encoded = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'self'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        for header, value in self.extra_headers:
            if value == "":
                self.send_header("Set-Cookie", header)
            else:
                self.send_header(header, value)
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, payload: dict, status: int = 200):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        for header, value in self.extra_headers:
            if value == "":
                self.send_header("Set-Cookie", header)
            else:
                self.send_header(header, value)
        self.end_headers()
        self.wfile.write(encoded)

    def send_csv(self, filename: str, rows: list[list[str]]) -> None:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        encoded = output.getvalue().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        for header, value in self.extra_headers:
            if value == "":
                self.send_header("Set-Cookie", header)
            else:
                self.send_header(header, value)
        self.end_headers()

    def send_error_page(self, status: int, title: str, message: str):
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Status {status}</span>
          <h1>{esc(title)}</h1>
          <p>{esc(message)}</p>
          <a class="button" href="/">Go home</a>
        </section>
        """
        self.send_html(title, body, status=status)

    def layout(self, title: str, body: str) -> str:
        csrf = esc(self.csrf_token())
        notice = esc((self.query.get("notice") or [""])[0])
        notice_html = f"<div class='notice'>{notice}</div>" if notice else ""
        verification = self.verification_banner()
        if self.user:
            page_content = f"""
  <div class="app-shell">
    {self.sidebar()}
    <div class="app-stage">
      <header class="app-topbar">
        <div>
          <span class="topbar-kicker">CedarHQ workspace</span>
          <strong>{esc(title)}</strong>
        </div>
        <span class="badge warning">Sandbox services</span>
      </header>
      <main class="page app-page">
        {notice_html}
        {verification}
        {body}
      </main>
    </div>
  </div>"""
        else:
            page_content = f"""
  <header class="topbar">
    <a class="brand" href="/">
      <img src="/static/brand-mark.svg" width="34" height="34" alt="">
      <span>CedarHQ</span>
    </a>
    {self.nav()}
  </header>
  <main class="page">
    {notice_html}
    {verification}
    {body}
  </main>"""
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="{csrf}">
  <title>{esc(title)} - CedarHQ</title>
  <link rel="stylesheet" href="/static/app.css">
  <script defer src="/static/app.js"></script>
</head>
<body>
  {page_content}
</body>
</html>"""

    def nav(self) -> str:
        if not self.user:
            return """
            <nav class="nav">
              <a href="/login">Log in</a>
              <a class="button small" href="/signup">Start</a>
            </nav>
            """
        ops_link = "<a href='/ops/orders'>Operations</a>" if self.user["role"] in OPS_ROLES else ""
        return f"""
        <nav class="nav">
          <a href="/app">Workspace</a>
          <a href="/app/documents">Documents</a>
          <a href="/app/compliance">Compliance</a>
          {ops_link}
          <form method="post" action="/logout" class="inline-form">
            <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
            <button class="link-button" type="submit">Log out</button>
          </form>
        </nav>
        """

    def sidebar_link(self, href: str, label: str, short: str) -> str:
        current = self.path_only
        active = current == href or (href != "/app" and current.startswith(href + "/"))
        return f"<a class='sidebar-link{' active' if active else ''}' href='{esc(href)}'><span>{esc(short)}</span>{esc(label)}</a>"

    def sidebar(self) -> str:
        is_ops = self.user["role"] in OPS_ROLES and self.path_only.startswith("/ops")
        latest = get_latest_order_for_user(self.conn, self.user["id"]) if not is_ops else None
        company_name = "Operations console" if is_ops else (
            (latest["name_choice_1"] or latest["legal_name"]) if latest else "New company"
        )
        if is_ops:
            links = f"""
              <span class="sidebar-label">Operations</span>
              {self.sidebar_link('/ops/orders', 'Formation queue', 'FQ')}
              {self.sidebar_link('/ops/compliance', 'Compliance risk', 'CR')}
              {self.sidebar_link('/ops/taxes', 'Tax queue', 'TX')}
              {self.sidebar_link('/ops/audit', 'Audit log', 'AL')}
            """
        else:
            formation_href = f"/app/orders/{latest['id']}" if latest else "/app/onboarding"
            links = f"""
              <span class="sidebar-label">Workspace</span>
              {self.sidebar_link('/app', 'Overview', 'OV')}
              {self.sidebar_link(formation_href, 'Formation', 'FM')}
              {self.sidebar_link('/app/documents', 'Documents', 'DC')}
              {self.sidebar_link('/app/compliance', 'Compliance', 'CP')}
              <span class="sidebar-label">Products</span>
              {self.sidebar_link('/app/assistant', 'AI assistant', 'AI')}
              {self.sidebar_link('/app/bookkeeping', 'Bookkeeping', 'BK')}
              {self.sidebar_link('/app/taxes', 'Taxes', 'TX')}
              {self.sidebar_link('/app/analytics', 'Analytics', 'AN')}
              <span class="sidebar-label">Manage</span>
              {self.sidebar_link('/app/billing', 'Plans & billing', 'BL')}
              {self.sidebar_link('/app/support', 'Support', 'SP')}
            """
        return f"""
        <aside class="sidebar">
          <a class="brand sidebar-brand" href="/">
            <img src="/static/brand-mark.svg" width="32" height="32" alt="">
            <span>CedarHQ</span>
          </a>
          <div class="company-switcher">
            <span class="company-avatar">{esc(company_name[:1].upper())}</span>
            <span><small>{'Staff workspace' if is_ops else 'Current company'}</small><strong>{esc(company_name)}</strong></span>
          </div>
          <nav class="sidebar-nav">{links}</nav>
          <div class="sidebar-user">
            <span class="user-avatar">{esc(user_initials(self.user['name']))}</span>
            <span><strong>{esc(self.user['name'] or 'Workspace user')}</strong><small>{esc(status_label(self.user['role']))}</small></span>
            <form method="post" action="/logout" class="inline-form">
              <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
              <button class="icon-logout" type="submit" title="Log out" aria-label="Log out">&#8594;</button>
            </form>
          </div>
        </aside>
        """

    def verification_banner(self) -> str:
        if not self.user or self.user["email_verified"]:
            return ""
        outbox = self.conn.execute(
            """
            SELECT body FROM outbox_emails
            WHERE to_email = ? AND subject LIKE '%Verify%'
            ORDER BY created_at DESC LIMIT 1
            """,
            (self.user["email"],),
        ).fetchone()
        link = ""
        if outbox and config.demo_mode:
            match = re.search(r"(https?://\S+)", outbox["body"])
            if match:
                parsed = urlparse(match.group(1))
                local_path = parsed.path + ("?" + parsed.query if parsed.query else "")
                link = f"<a href='{esc(local_path)}'>Verify now</a>"
        return f"""
        <div class="notice warning">
          Verify your email before checkout. {link}
        </div>
        """

    def home(self):
        if self.user:
            return self.redirect("/ops/orders" if self.user["role"] in OPS_ROLES else "/app")
        body = """
        <section class="hero-grid">
          <div class="hero-copy">
            <span class="eyebrow">Formation and back office, evidence first</span>
            <h1>CedarHQ</h1>
            <p>Launch a US company through plain-language workflows, transparent cost review, operations review, secure documents, and compliance tracking.</p>
            <div class="actions">
              <a class="button" href="/signup">Create account</a>
              <a class="button secondary" href="/login">Log in</a>
            </div>
          </div>
          <div class="workflow-preview" aria-label="Workflow preview">
            <div><strong>1</strong><span>Entity quiz</span></div>
            <div><strong>2</strong><span>State and cost review</span></div>
            <div><strong>3</strong><span>Sandbox checkout</span></div>
            <div><strong>4</strong><span>Evidence-backed timeline</span></div>
          </div>
        </section>
        <section class="grid three">
          <article class="card"><span class="icon">ID</span><h2>Founder-ready</h2><p>Save progress, resume later, and see what CedarHQ needs next.</p></article>
          <article class="card"><span class="icon">EV</span><h2>Evidence-backed</h2><p>Completed steps require receipts, timestamps, responsible parties, and downloadable evidence.</p></article>
          <article class="card"><span class="icon">OP</span><h2>Operations queue</h2><p>Staff review, block, and advance orders through auditable actions.</p></article>
        </section>
        """
        self.send_html("Founder operations platform", body)

    def signup_page(self, error: str = ""):
        token = self.public_csrf()
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Create founder account</span>
          <h1>Start a company workspace</h1>
          {self.error_box(error)}
          <form method="post" action="/signup" class="stack">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <label>Full name<input required name="name" autocomplete="name"></label>
            <label>Email<input required type="email" name="email" autocomplete="email"></label>
            <label>Password<input required type="password" name="password" autocomplete="new-password" minlength="10"></label>
            <button class="button" type="submit">Create account</button>
          </form>
          <div class="divider">or</div>
          <form method="post" action="/auth/google">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <button class="button secondary full" type="submit">Continue with sandbox Google</button>
          </form>
          <p class="muted small-text">Sandbox Google creates a verified local user. Production requires Google OAuth credentials.</p>
        </section>
        """
        self.send_html("Sign up", body)

    def signup_post(self):
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.signup_page("Security token expired. Reload and try again.")
        try:
            user = create_user(self.conn, data.get("email", ""), data.get("password", ""), data.get("name", ""), role="founder")
            token = create_email_token(self.conn, user["id"], "verify_email")
            send_auth_email(self.conn, user, "verify_email", token, config.base_url)
            session_token = create_session(self.conn, user["id"], self.headers.get("User-Agent"), hash_token(self.client_address[0]))
            self.set_session_cookie(session_token)
            self.redirect("/app/onboarding?notice=Account created. Verify your email before checkout.")
        except Exception as exc:
            self.signup_page(str(exc))

    def login_page(self, error: str = ""):
        token = self.public_csrf()
        next_url = esc((self.query.get("next") or ["/app"])[0])
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Secure access</span>
          <h1>Log in</h1>
          {self.error_box(error)}
          <form method="post" action="/login" class="stack">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <input type="hidden" name="next" value="{next_url}">
            <label>Email<input required type="email" name="email" autocomplete="email"></label>
            <label>Password<input required type="password" name="password" autocomplete="current-password"></label>
            <button class="button" type="submit">Log in</button>
          </form>
          <div class="actions compact">
            <a href="/forgot-password">Reset password</a>
            <a href="/signup">Create account</a>
          </div>
        </section>
        """
        self.send_html("Log in", body)

    def login_post(self):
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.login_page("Security token expired. Reload and try again.")
        user = authenticate_user(self.conn, data.get("email", ""), data.get("password", ""))
        if not user:
            return self.login_page("Email or password is incorrect.")
        token = create_session(self.conn, user["id"], self.headers.get("User-Agent"), hash_token(self.client_address[0]))
        self.set_session_cookie(token)
        next_url = data.get("next") or ("/ops/orders" if user["role"] in OPS_ROLES else "/app")
        if not next_url.startswith("/"):
            next_url = "/app"
        self.redirect(next_url)

    def logout_post(self):
        data = self.form_data()
        if self.user and not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        destroy_session(self.conn, self.cookie_value(SESSION_COOKIE))
        self.clear_session_cookie()
        self.redirect("/")

    def forgot_password_page(self, message: str = "", error: str = ""):
        token = self.public_csrf()
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Account recovery</span>
          <h1>Reset password</h1>
          {self.error_box(error)}
          {self.message_box(message)}
          <form method="post" action="/forgot-password" class="stack">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <label>Email<input required type="email" name="email"></label>
            <button class="button" type="submit">Send reset link</button>
          </form>
        </section>
        """
        self.send_html("Forgot password", body)

    def forgot_password_post(self):
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.forgot_password_page(error="Security token expired. Reload and try again.")
        user = self.conn.execute("SELECT * FROM users WHERE email = ?", (data.get("email", "").strip().lower(),)).fetchone()
        if user:
            token = create_email_token(self.conn, user["id"], "password_reset")
            send_auth_email(self.conn, user, "password_reset", token, config.base_url)
        self.forgot_password_page("If the email exists, a reset link has been written to the local outbox.")

    def reset_password_page(self, error: str = ""):
        token = self.public_csrf()
        reset_token = esc((self.query.get("token") or [""])[0])
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Choose new password</span>
          <h1>Reset password</h1>
          {self.error_box(error)}
          <form method="post" action="/reset-password" class="stack">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <input type="hidden" name="token" value="{reset_token}">
            <label>New password<input required type="password" minlength="10" name="password" autocomplete="new-password"></label>
            <button class="button" type="submit">Update password</button>
          </form>
        </section>
        """
        self.send_html("Reset password", body)

    def reset_password_post(self):
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.reset_password_page("Security token expired. Reload and try again.")
        try:
            user = reset_password(self.conn, data.get("token", ""), data.get("password", ""))
            if not user:
                return self.reset_password_page("Reset link is invalid or expired.")
            token = create_session(self.conn, user["id"], self.headers.get("User-Agent"), hash_token(self.client_address[0]))
            self.set_session_cookie(token)
            self.redirect("/app?notice=Password updated.")
        except Exception as exc:
            self.reset_password_page(str(exc))

    def verify_email_route(self):
        token = (self.query.get("token") or [""])[0]
        user = verify_email(self.conn, token)
        if not user:
            return self.send_error_page(400, "Invalid link", "The verification link is invalid or expired.")
        session_token = create_session(self.conn, user["id"], self.headers.get("User-Agent"), hash_token(self.client_address[0]))
        self.set_session_cookie(session_token)
        self.redirect("/app/onboarding?notice=Email verified.")

    def google_page(self):
        token = self.public_csrf()
        body = f"""
        <section class="panel narrow">
          <span class="eyebrow">Sandbox OAuth</span>
          <h1>Continue with Google</h1>
          <p>This local build uses a sandbox Google adapter. Production requires a Google OAuth client and verified redirect URI.</p>
          <form method="post" action="/auth/google">
            <input type="hidden" name="csrf_token" value="{esc(token)}">
            <button class="button" type="submit">Continue with sandbox Google</button>
          </form>
        </section>
        """
        self.send_html("Google sign-in", body)

    def google_post(self):
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.google_page()
        user = get_or_create_google_sandbox_user(self.conn)
        token = create_session(self.conn, user["id"], self.headers.get("User-Agent"), hash_token(self.client_address[0]))
        self.set_session_cookie(token)
        self.redirect("/app/onboarding?notice=Sandbox Google sign-in completed.")

    def app_dashboard(self):
        if not self.require_auth():
            return
        ctx = get_dashboard_context(self.conn, self.user["id"])
        if not ctx:
            body = """
            <section class="dashboard-welcome empty-workspace">
              <div>
                <span class="eyebrow">Founder workspace</span>
                <h1>Build your company record</h1>
                <p>Complete the guided formation intake to create a trackable order, document vault, and compliance calendar.</p>
              </div>
              <a class="button" href="/app/onboarding">Start company formation</a>
            </section>
            <section class="empty-checklist" aria-label="Formation checklist">
              <span><strong>1</strong> Entity and state</span>
              <span><strong>2</strong> Founder information</span>
              <span><strong>3</strong> Cost review</span>
              <span><strong>4</strong> Sandbox checkout</span>
            </section>
            """
            return self.send_html("Overview", body)

        order = ctx["order"]
        company = ctx["company"]
        founder = ctx["founder"]
        plan = ctx["plan"]
        payment = ctx["payment"]
        next_step = ctx["next_step"]
        company_name = company["legal_name"] or company["name_choice_1"] or "Company formation"
        address = ", ".join(
            part for part in [company["address_line1"], company["city"], company["region"], company["postal_code"], company["country"]] if part
        ) or "Not provided"
        state_approved = next(
            (step for step in ctx["timeline"] if step["step_key"] == "state_approved" and step["status"] == "completed" and step["evidence_id"]),
            None,
        )
        ein_received = next(
            (step for step in ctx["timeline"] if step["step_key"] == "ein_received" and step["status"] == "completed" and step["evidence_id"]),
            None,
        )

        milestone_html = []
        for step in ctx["timeline"]:
            verified = step["status"] == "completed" and step["completed_at"] and step["receipt_id"] and step["evidence_id"]
            if verified:
                state = "complete"
                state_text = date_label(step["completed_at"])
            elif step["status"] == "blocked":
                state = "blocked"
                state_text = "Action needed"
            elif next_step and step["id"] == next_step["id"]:
                state = "current"
                state_text = "Current step"
            else:
                state = "pending"
                state_text = "Pending"
            milestone_html.append(
                f"<div class='formation-milestone {state}'><span class='milestone-dot'></span><strong>{esc(step['label'])}</strong><small>{esc(state_text)}</small></div>"
            )

        action_html = []
        if order["status"] == "blocked":
            action_html.append(
                f"<a class='action-row urgent' href='/app/orders/{esc(order['id'])}'><span>!</span><div><strong>Formation is blocked</strong><small>{esc(order['blocked_reason'] or 'Additional information is required.')}</small></div></a>"
            )
        for item in ctx["attention_items"][:3]:
            action_html.append(
                f"<a class='action-row' href='/app/compliance'><span>!</span><div><strong>{esc(item['title'])}</strong><small>Due {esc(date_label(item['due_date']))} &middot; {esc(status_label(item['status']))}</small></div></a>"
            )
        if next_step and order["status"] != "blocked":
            waiting_on_user = "founder" in next_step["responsible_party"].lower()
            action_html.append(
                f"<a class='action-row' href='/app/orders/{esc(order['id'])}'><span>{'Y' if waiting_on_user else 'C'}</span><div><strong>{esc(next_step['label'])}</strong><small>{'Your input may be required' if waiting_on_user else 'CedarHQ operations is responsible'} &middot; {esc(next_step['responsible_party'])}</small></div></a>"
            )
        if not action_html:
            action_html.append("<div class='action-row calm'><span>OK</span><div><strong>No immediate action</strong><small>We will surface verified tasks here as they arise.</small></div></div>")

        compliance_html = []
        for item in ctx["compliance"][:4]:
            compliance_html.append(f"""
              <a class="deadline-row" href="/app/compliance">
                <span class="deadline-date"><strong>{esc(item['due_date'][8:10])}</strong><small>{esc(date_label(item['due_date']).split()[0])}</small></span>
                <span><strong>{esc(item['title'])}</strong><small>{esc(item['responsible_party'])}</small></span>
                <span class="badge {esc(item['status'])}">{esc(status_label(item['status']))}</span>
              </a>
            """)

        document_html = []
        for document in ctx["documents"][:4]:
            document_html.append(f"""
              <div class="recent-document">
                <span class="document-type">{esc(document['category'][:2].upper())}</span>
                <span><strong>{esc(document['title'])}</strong><small>Version {document['current_version']} &middot; {esc(date_label(document['updated_at']))}</small></span>
                <a href="/api/documents/{esc(document['id'])}/download" aria-label="Download {esc(document['title'])}" title="Download">&#8595;</a>
              </div>
            """)
        if not document_html:
            document_html.append("<p class='muted'>No documents have been generated yet.</p>")

        def service_row(name: str, included: bool, detail: str) -> str:
            state = "included" if included else "not-included"
            label = "Included" if included else "Not included"
            return f"<div class='service-row'><span class='service-indicator {state}'></span><span><strong>{esc(name)}</strong><small>{detail}</small></span><em>{label}</em></div>"

        service_html = "".join(
            [
                service_row("Registered agent", bool(plan["registered_agent_included"]), "Sandbox setup pending" if not state_approved else "Sandbox coverage recorded"),
                service_row("Virtual mailroom", bool(plan["mailroom_included"]), "Address selection follows approval"),
                service_row("Bookkeeping", bool(plan["bookkeeping_included"]), "No financial account connected"),
                service_row("Tax preparation", bool(plan["tax_included"]), "Questionnaire not started"),
            ]
        )
        try:
            renewal_date = date_label((datetime.fromisoformat(order["created_at"]) + timedelta(days=365)).isoformat())
        except ValueError:
            renewal_date = "Not available"
        first_name = (self.user["name"] or "Founder").split()[0]
        support_open = (ctx["support"]["open_count"] or 0) if ctx["support"] else 0

        body = f"""
        <section class="dashboard-welcome">
          <div>
            <span class="eyebrow">Founder overview</span>
            <h1>Good day, {esc(first_name)}</h1>
            <p>{esc(company_name)} &middot; {esc(entity_label(order['entity_type']))} &middot; {esc(STATE_OPTIONS.get(order['state_code'], {}).get('name', order['state_code']))}</p>
          </div>
          <div class="header-actions">
            <span class="badge {esc(order['status'])}">{esc(status_label(order['status']))}</span>
            <a class="button secondary" href="/app/orders/{esc(order['id'])}">View formation</a>
          </div>
        </section>

        <section class="dashboard-grid">
          <article class="dashboard-card formation-card span-8">
            <div class="section-heading">
              <div><span class="section-kicker">FORMATION</span><h2>Company setup progress</h2></div>
              <strong class="progress-number">{ctx['progress_percent']}%</strong>
            </div>
            <div class="progress-track" aria-label="Formation {ctx['progress_percent']} percent complete"><span style="width:{ctx['progress_percent']}%"></span></div>
            <div class="formation-milestones">{''.join(milestone_html)}</div>
            <div class="card-footer"><span>{len(ctx['completed_steps'])} of {len(ctx['timeline'])} evidence-backed steps complete</span><a href="/app/orders/{esc(order['id'])}">Full timeline</a></div>
          </article>

          <article class="dashboard-card span-4">
            <div class="section-heading"><div><span class="section-kicker">PRIORITIES</span><h2>Next actions</h2></div><span class="count-pill">{len(action_html)}</span></div>
            <div class="action-list">{''.join(action_html)}</div>
          </article>

          <article class="dashboard-card span-4 company-record">
            <div class="section-heading"><div><span class="section-kicker">COMPANY</span><h2>Company record</h2></div></div>
            <dl class="record-list">
              <div><dt>Legal name</dt><dd>{esc(company_name)}</dd></div>
              <div><dt>Entity</dt><dd>{esc(entity_label(order['entity_type']))}</dd></div>
              <div><dt>Jurisdiction</dt><dd>{esc(order['state_code'])}</dd></div>
              <div><dt>State approval</dt><dd>{esc(date_label(state_approved['completed_at']) if state_approved else 'Pending')}</dd></div>
              <div><dt>EIN</dt><dd>{'Received with evidence' if ein_received else 'Not yet received'}</dd></div>
              <div><dt>Founder</dt><dd>{esc(founder['full_name'] if founder else self.user['name'])}</dd></div>
              <div><dt>Ownership</dt><dd>{esc(str(founder['ownership_percent']) + '%' if founder else 'Not recorded')}</dd></div>
              <div><dt>Address</dt><dd>{esc(address)}</dd></div>
            </dl>
          </article>

          <article class="dashboard-card span-4">
            <div class="section-heading"><div><span class="section-kicker">SERVICES</span><h2>Your coverage</h2></div></div>
            <div class="service-list">{service_html}</div>
            <div class="partner-note"><strong>Banking</strong><span>Not connected. Any future application is decided by the banking partner.</span></div>
          </article>

          <article class="dashboard-card span-4 billing-summary">
            <div class="section-heading"><div><span class="section-kicker">PLAN & BILLING</span><h2>{esc(plan['name'])}</h2></div><span class="badge {esc(payment['status'] if payment else 'pending')}">{esc(status_label(payment['status']) if payment else 'Pending')}</span></div>
            <div class="billing-total"><span>First-year total</span><strong>{esc(cents(order['total_first_year_cents']))}</strong></div>
            <dl class="record-list compact">
              <div><dt>Service fee</dt><dd>{esc(cents(order['service_fee_cents']))}</dd></div>
              <div><dt>State fee estimate</dt><dd>{esc(cents(order['state_fee_cents']))}</dd></div>
              <div><dt>Renewal estimate</dt><dd>{esc(cents(order['total_renewal_cents']))}</dd></div>
              <div><dt>Renewal date</dt><dd>{esc(renewal_date)}</dd></div>
            </dl>
            <p class="disclosure">Sandbox payment only. Variable government taxes, postage, partner fees, and cure costs are excluded.</p>
            <a href="/app/billing">Billing details</a>
          </article>

          <article class="dashboard-card span-8">
            <div class="section-heading"><div><span class="section-kicker">COMPLIANCE</span><h2>Upcoming obligations</h2></div><a href="/app/compliance">View calendar</a></div>
            <div class="deadline-list">{''.join(compliance_html)}</div>
          </article>

          <article class="dashboard-card span-4">
            <div class="section-heading"><div><span class="section-kicker">DOCUMENTS</span><h2>Recent files</h2></div><a href="/app/documents">Open vault</a></div>
            <div class="recent-documents">{''.join(document_html)}</div>
          </article>
        </section>

        <section class="dashboard-bottom-bar">
          <span><strong>Need help?</strong> {support_open} open support case{'s' if support_open != 1 else ''}.</span>
          <a class="button secondary small" href="/app/support">Contact support</a>
          <small>Order created {esc(timestamp_label(order['created_at']))} &middot; Simulated providers are clearly labeled.</small>
        </section>
        """
        self.send_html("Overview", body)

    def onboarding_page(self):
        if not self.require_auth():
            return
        ctx = get_onboarding(self.conn, self.user["id"])
        data = ctx["data"]
        company = ctx["company"]
        cost = ctx["cost"]
        states = self.render_states(ctx["states"], data.get("state_code") or company["state_code"] or "DE")
        plans = self.render_plans(ctx["plans"], data.get("plan_slug") or "formation_only")
        checkout_disabled = "" if self.user["email_verified"] else " disabled"
        checkout_help = "" if self.user["email_verified"] else "<p class='error-text'>Verify your email before checkout.</p>"
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Start a new company</span>
            <h1>Formation onboarding</h1>
            <p>Autosaves every step. You can leave and resume later.</p>
          </div>
          <span class="badge">Draft saved {esc(ctx['progress']['updated_at'] if ctx['progress'] else utcnow())}</span>
        </section>
        <form method="post" action="/api/onboarding/save" data-autosave class="onboarding-grid">
          <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
          <input type="hidden" name="current_step" value="onboarding">
          <div class="flow">
            <section class="panel step-panel">
              <span class="step-number">1</span>
              <h2>LLC vs C-Corp quiz</h2>
              <p class="muted">The recommendation updates as you answer. Final choice remains yours.</p>
              {self.yes_no('venture_funding', 'Will you seek venture capital or institutional investors?', data)}
              {self.yes_no('issue_equity', 'Do you expect to issue stock options or formal equity grants?', data)}
              {self.yes_no('pass_through_tax', 'Do you prefer simpler pass-through taxation where available?', data)}
              {self.yes_no('multiple_owners', 'Will there be multiple owners?', data)}
              {self.yes_no('international_founder', 'Is any founder outside the United States?', data)}
              <div class="recommendation">
                <strong>Recommendation: {entity_label(ctx['recommendation'])}</strong>
                <span>{esc(ctx['recommendation_reason'])}</span>
              </div>
              <label>Final entity choice
                <select name="entity_type">
                  <option value="llc"{selected(data.get('entity_type') or company['entity_type'] or ctx['recommendation'], 'llc')}>LLC</option>
                  <option value="c_corp"{selected(data.get('entity_type') or company['entity_type'] or ctx['recommendation'], 'c_corp')}>C-Corp</option>
                </select>
              </label>
            </section>
            <section class="panel step-panel">
              <span class="step-number">2</span>
              <h2>State selection</h2>
              <p class="muted">State fees are sandbox estimates and must be verified before real submission.</p>
              <div class="state-grid">{states}</div>
            </section>
            <section class="panel step-panel">
              <span class="step-number">3</span>
              <h2>Company details</h2>
              <div class="grid two">
                <label>Company name choice 1<input required name="name_choice_1" value="{esc(data.get('name_choice_1') or company['name_choice_1'])}"></label>
                <label>Company name choice 2<input name="name_choice_2" value="{esc(data.get('name_choice_2') or company['name_choice_2'])}"></label>
                <label>Company name choice 3<input name="name_choice_3" value="{esc(data.get('name_choice_3') or company['name_choice_3'])}"></label>
                <label>Industry<input name="industry" value="{esc(data.get('industry') or company['industry'])}"></label>
              </div>
              <label>Business purpose<textarea required name="business_purpose" rows="4">{esc(data.get('business_purpose') or company['business_purpose'])}</textarea></label>
              <label>Authorized shares or units<input inputmode="numeric" name="share_count" value="{esc(data.get('share_count') or company['share_count'] or '')}"></label>
            </section>
            <section class="panel step-panel">
              <span class="step-number">4</span>
              <h2>Founder information</h2>
              <div class="grid two">
                <label>Founder full name<input required name="founder_full_name" value="{esc(data.get('founder_full_name') or self.user['name'])}"></label>
                <label>Founder email<input required type="email" name="founder_email" value="{esc(data.get('founder_email') or self.user['email'])}"></label>
                <label>Ownership percent<input required inputmode="decimal" name="founder_ownership_percent" value="{esc(data.get('founder_ownership_percent') or '100')}"></label>
                <label>Founder shares or units<input inputmode="numeric" name="founder_shares" value="{esc(data.get('founder_shares') or '')}"></label>
                <label>Address line 1<input required name="address_line1" value="{esc(data.get('address_line1') or company['address_line1'])}"></label>
                <label>Address line 2<input name="address_line2" value="{esc(data.get('address_line2') or company['address_line2'])}"></label>
                <label>City<input required name="city" value="{esc(data.get('city') or company['city'])}"></label>
                <label>Region / State<input name="region" value="{esc(data.get('region') or company['region'])}"></label>
                <label>Postal code<input name="postal_code" value="{esc(data.get('postal_code') or company['postal_code'])}"></label>
                <label>Country<input required name="country" value="{esc(data.get('country') or company['country'])}"></label>
              </div>
            </section>
            <section class="panel step-panel">
              <span class="step-number">5</span>
              <h2>Plan and cost review</h2>
              <div class="plan-grid">{plans}</div>
              <button class="button secondary" type="submit">Save progress</button>
              <span class="autosave-state" role="status" aria-live="polite">Autosave ready</span>
            </section>
          </div>
          <aside class="panel cost-panel">
            <span class="eyebrow">Transparent checkout</span>
            <h2>First-year total</h2>
            <div class="price">{cents(cost['first_year_cents'])}</div>
            <ul class="cost-lines">{''.join(f"<li><span>{esc(line['label'])}</span><strong>{cents(line['amount_cents'])}</strong></li>" for line in cost['lines'])}</ul>
            <div class="renewal"><span>Renewal estimate</span><strong>{cents(cost['renewal_cents'])}</strong></div>
            <p class="muted">{esc(cost['renewal_note'])}</p>
            <p class="muted">Sandbox checkout only. No card is charged and no legal filing is submitted.</p>
          </aside>
        </form>
        <form method="post" action="/api/checkout/sandbox" class="checkout-bar">
          <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
          <div>
            <strong>Ready to create the formation order?</strong>
            <span>Checkout creates a sandbox payment, operations queue item, timeline, vault documents, and compliance calendar.</span>
          </div>
          <button class="button" type="submit"{checkout_disabled}>Sandbox checkout</button>
          {checkout_help}
        </form>
        """
        self.send_html("Onboarding", body)

    def render_states(self, states: dict, active: str) -> str:
        cards = []
        for code, state in states.items():
            cards.append(
                f"""
                <label class="choice-card">
                  <input type="radio" name="state_code" value="{esc(code)}"{checked(active, code)}>
                  <span class="choice-title">{esc(state['name'])}</span>
                  <span class="badge">{cents(state['fee_cents'])} est. fee</span>
                  <span>{esc(state['timeline'])}</span>
                  <small><strong>Benefits:</strong> {esc(state['benefits'])}</small>
                  <small><strong>Limitations:</strong> {esc(state['limitations'])}</small>
                </label>
                """
            )
        return "".join(cards)

    def render_plans(self, plans, active: str) -> str:
        rows = []
        for plan in plans:
            rows.append(
                f"""
                <label class="choice-card plan-card">
                  <input type="radio" name="plan_slug" value="{esc(plan['slug'])}"{checked(active, plan['slug'])}>
                  <span class="choice-title">{esc(plan['name'])}</span>
                  <strong>{cents(plan['service_fee_cents'])} first year</strong>
                  <span>{esc(plan['description'])}</span>
                  <div class="badge-row">
                    {bool_badge(plan['registered_agent_included'], 'RA included', 'RA not included')}
                    {bool_badge(plan['mailroom_included'], 'Mailroom', 'No mailroom')}
                    {bool_badge(plan['bookkeeping_included'], 'Books', 'No books')}
                    {bool_badge(plan['tax_included'], 'Tax workflow', 'No tax workflow')}
                  </div>
                </label>
                """
            )
        return "".join(rows)

    def yes_no(self, name: str, label: str, data: dict) -> str:
        return f"""
        <fieldset class="segmented">
          <legend>{esc(label)}</legend>
          <label><input type="radio" name="{esc(name)}" value="yes"{checked(data.get(name), 'yes')}> Yes</label>
          <label><input type="radio" name="{esc(name)}" value="no"{checked(data.get(name), 'no')}> No</label>
        </fieldset>
        """

    def onboarding_save_post(self):
        if not self.require_auth():
            return
        is_json = "application/json" in (self.headers.get("Content-Type") or "")
        data = self.json_data() if is_json else self.form_data()
        if not self.verify_csrf(data):
            return self.send_json({"ok": False, "error": "Security token expired."}, status=403)
        ctx = save_onboarding(self.conn, self.user["id"], data)
        cost = ctx["cost"]
        self.send_json(
            {
                "ok": True,
                "saved_at": utcnow(),
                "recommendation": ctx["recommendation"],
                "recommendation_reason": ctx["recommendation_reason"],
                "first_year": cents(cost["first_year_cents"]),
                "renewal": cents(cost["renewal_cents"]),
            }
        )

    def checkout_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        if not self.user["email_verified"]:
            return self.redirect("/app/onboarding?notice=Verify your email before checkout.")
        try:
            order = create_checkout_and_order(self.conn, self.user, config.base_url)
            self.redirect(f"/app/orders/{order['id']}?notice=Sandbox checkout complete. Formation order created.")
        except Exception as exc:
            self.redirect(f"/app/onboarding?notice={quote(str(exc))}")

    def order_page(self, order_id: str):
        if not self.require_auth():
            return
        order = get_order(self.conn, order_id)
        if not order or (order["user_id"] != self.user["id"] and self.user["role"] not in OPS_ROLES):
            return self.send_error_page(404, "Order not found", "This order is not available.")
        timeline = get_timeline(self.conn, order_id)
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Formation tracking</span>
            <h1>{esc(order['name_choice_1'] or order['legal_name'] or 'Formation order')}</h1>
            <p>{entity_label(order['entity_type'])} in {esc(order['state_code'])} · {esc(order['plan_name'])} · <strong>{esc(status_label(order['status']))}</strong></p>
          </div>
          <span class="badge warning">Sandbox services labeled</span>
        </section>
        {self.blocked_box(order)}
        <section class="panel">
          <h2>Status timeline</h2>
          <div class="timeline">{''.join(self.timeline_item(step) for step in timeline)}</div>
        </section>
        <section class="grid two">
          <a class="card link-card" href="/app/documents"><span class="icon">DV</span><h2>Document vault</h2><p>Open generated receipts and evidence.</p></a>
          <a class="card link-card" href="/app/compliance"><span class="icon">CA</span><h2>Compliance calendar</h2><p>Review upcoming and action-required items.</p></a>
        </section>
        """
        self.send_html("Formation order", body)

    def blocked_box(self, order) -> str:
        if order["status"] != "blocked":
            return ""
        return f"""
        <section class="notice error">
          <strong>Action required:</strong> {esc(order['blocked_reason'] or 'Operations needs more information.')}
        </section>
        """

    def timeline_item(self, step) -> str:
        is_completed = step["status"] == "completed" and step["evidence_id"] and step["receipt_id"] and step["completed_at"]
        status = "completed" if is_completed else step["status"]
        evidence = ""
        if is_completed:
            evidence = f"<a href='/api/evidence/{esc(step['evidence_id'])}/download'>Download evidence</a>"
        meta = ""
        if is_completed:
            meta = f"""
            <dl>
              <div><dt>Completed</dt><dd>{esc(step['completed_at'])}</dd></div>
              <div><dt>Responsible</dt><dd>{esc(step['responsible_party'])}</dd></div>
              <div><dt>Actor</dt><dd>{esc(step['actor_name'] or 'System')}</dd></div>
              <div><dt>Receipt</dt><dd>{esc(step['receipt_id'])}</dd></div>
            </dl>
            """
        elif step["status"] == "blocked":
            meta = f"<p class='error-text'>{esc(step['blocked_reason'] or 'More information is required.')}</p>"
        else:
            meta = f"<p class='muted'>Responsible party: {esc(step['responsible_party'])}</p>"
        sim = "<span class='badge warning'>Simulated</span>" if step["is_simulated"] else ""
        return f"""
        <article class="timeline-item {esc(status)}">
          <div class="timeline-dot" aria-hidden="true"></div>
          <div>
            <div class="timeline-title"><h3>{esc(step['label'])}</h3><span class="badge {esc(status)}">{esc(status_label(status))}</span>{sim}</div>
            {meta}
            {evidence}
          </div>
        </article>
        """

    def documents_page(self):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        search = (self.query.get("search") or [""])[0]
        category = (self.query.get("category") or [""])[0]
        docs = list_documents(self.conn, company["id"], search, category)
        rows = "".join(self.document_card(doc) for doc in docs) or "<p class='muted'>No documents match this filter.</p>"
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Secure vault</span>
            <h1>Documents</h1>
            <p>Search, preview, version, and download company records.</p>
          </div>
        </section>
        <section class="panel">
          <form method="get" action="/app/documents" class="toolbar">
            <label>Search<input name="search" value="{esc(search)}"></label>
            <label>Category
              <select name="category">
                <option value="">All</option>
                {''.join(f"<option value='{esc(cat)}'{selected(category, cat)}>{esc(status_label(cat))}</option>" for cat in ['receipt','formation','articles','ein_letter','resolution','tax'])}
              </select>
            </label>
            <button class="button secondary" type="submit">Filter</button>
          </form>
        </section>
        <section class="doc-grid">{rows}</section>
        """
        self.send_html("Documents", body)

    def document_card(self, doc) -> str:
        excerpt = (doc["content"] or "")[:280]
        return f"""
        <article class="card document-card">
          <div class="card-head">
            <span class="icon">DOC</span>
            <span class="badge">{esc(status_label(doc['category']))}</span>
          </div>
          <h2>{esc(doc['title'])}</h2>
          <p>{esc(excerpt)}{'...' if len(doc['content'] or '') > 280 else ''}</p>
          <dl class="compact-dl">
            <div><dt>Status</dt><dd>{esc(status_label(doc['status']))}</dd></div>
            <div><dt>Version</dt><dd>v{esc(doc['current_version'])}</dd></div>
            <div><dt>Source</dt><dd>{esc(doc['source'])}</dd></div>
          </dl>
          {'<span class="badge warning">Simulated</span>' if doc['is_simulated'] else ''}
          <a class="button secondary full" href="/api/documents/{esc(doc['id'])}/download">Download</a>
        </article>
        """

    def download_document(self, document_id: str):
        if not self.require_auth():
            return
        doc = get_document_for_user(self.conn, document_id, self.user)
        if not doc:
            return self.send_error_page(404, "Document not found", "This document is unavailable.")
        filename = quote(re.sub(r"[^a-zA-Z0-9_.-]+", "-", doc["title"]).strip("-") + ".txt")
        data = doc["content"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", doc["mime_type"])
        self.send_header("Content-Disposition", f"attachment; filename={filename}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def download_evidence(self, evidence_id: str):
        if not self.require_auth():
            return
        evidence = self.conn.execute("SELECT * FROM evidence_files WHERE id = ?", (evidence_id,)).fetchone()
        if not evidence:
            return self.send_error_page(404, "Evidence not found", "This evidence record is unavailable.")
        if self.user["role"] not in OPS_ROLES:
            allowed = self.conn.execute(
                "SELECT 1 FROM company_members WHERE company_id = ? AND user_id = ?",
                (evidence["company_id"], self.user["id"]),
            ).fetchone()
            if not allowed:
                return self.send_error_page(403, "Access denied", "You cannot access this evidence.")
        filename = quote(re.sub(r"[^a-zA-Z0-9_.-]+", "-", evidence["title"]).strip("-") + ".txt")
        data = evidence["content"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", evidence["mime_type"])
        self.send_header("Content-Disposition", f"attachment; filename={filename}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def compliance_page(self):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        items = list_compliance(self.conn, company["id"])
        cards = "".join(self.compliance_card(item) for item in items) or "<p class='muted'>Compliance items appear after checkout.</p>"
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Compliance calendar</span>
            <h1>Deadlines and reminders</h1>
            <p>Every submitted or accepted item needs a receipt and audit trail.</p>
          </div>
        </section>
        <section class="grid two">{cards}</section>
        """
        self.send_html("Compliance", body)

    def compliance_card(self, item) -> str:
        evidence = f"<a href='/api/evidence/{esc(item['evidence_id'])}/download'>Receipt evidence</a>" if item["evidence_id"] else "<span class='muted'>No submission evidence yet</span>"
        return f"""
        <article class="card">
          <div class="card-head"><span class="badge {esc(item['status'])}">{esc(status_label(item['status']))}</span><span>{esc(item['due_date'])}</span></div>
          <h2>{esc(item['title'])}</h2>
          <p>{esc(item['description'])}</p>
          <dl class="compact-dl">
            <div><dt>Category</dt><dd>{esc(status_label(item['category']))}</dd></div>
            <div><dt>Owner</dt><dd>{esc(item['responsible_party'])}</dd></div>
            <div><dt>Rule source</dt><dd>{esc(item['source_rule'])}</dd></div>
          </dl>
          {evidence}
        </article>
        """

    def billing_page(self):
        if not self.require_auth():
            return
        ctx = get_onboarding(self.conn, self.user["id"])
        cost = ctx["cost"]
        plan_rows = "".join(
            f"<tr><td>{esc(plan['name'])}</td><td>{cents(plan['service_fee_cents'])}</td><td>{cents(plan['renewal_fee_cents'])}</td><td>{esc(plan['description'])}</td></tr>"
            for plan in ctx["plans"]
        )
        body = f"""
        <section class="panel">
          <span class="eyebrow">Plans and billing</span>
          <h1>Transparent cost review</h1>
          <p>No hidden platform fees. Variable government fees, taxes, postage, rejected-filing cures, and banking partner fees are excluded until verified.</p>
          <div class="price">{cents(cost['first_year_cents'])}</div>
          <p>Current first-year estimate for {esc(cost['plan']['name'])} in {esc(cost['state']['name'])}. Renewal estimate: <strong>{cents(cost['renewal_cents'])}</strong>.</p>
          <div class="table-wrap"><table><thead><tr><th>Plan</th><th>First-year service</th><th>Renewal</th><th>Coverage</th></tr></thead><tbody>{plan_rows}</tbody></table></div>
        </section>
        """
        self.send_html("Billing", body)

    def support_page(self, message: str = ""):
        if not self.require_auth():
            return
        company = self.current_company()
        tickets = self.conn.execute(
            "SELECT * FROM support_tickets WHERE opened_by_user_id = ? ORDER BY created_at DESC",
            (self.user["id"],),
        ).fetchall()
        rows = "".join(
            f"<article class='card'><div class='card-head'><span class='badge'>{esc(status_label(ticket['status']))}</span><span>{esc(ticket['priority'])}</span></div><h2>{esc(ticket['subject'])}</h2><p>Created {esc(ticket['created_at'])}</p></article>"
            for ticket in tickets
        ) or "<p class='muted'>No support cases yet.</p>"
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Support</span>
            <h1>Cases</h1>
            <p>Customer and staff views share the same case status.</p>
          </div>
        </section>
        {self.message_box(message)}
        <section class="panel">
          <form method="post" action="/app/support" class="stack">
            <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
            <label>Subject<input required name="subject"></label>
            <label>Priority
              <select name="priority"><option value="normal">Normal</option><option value="deadline_critical">Deadline critical</option></select>
            </label>
            <label>Message<textarea required name="body" rows="4"></textarea></label>
            <button class="button" type="submit">Open case</button>
          </form>
        </section>
        <section class="grid two">{rows}</section>
        """
        self.send_html("Support", body)

    def support_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        now = utcnow()
        ticket_id = new_id("tkt")
        critical = 1 if data.get("priority") == "deadline_critical" else 0
        self.conn.execute(
            """
            INSERT INTO support_tickets (
              id, company_id, opened_by_user_id, subject, priority, deadline_critical, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticket_id, company["id"] if company else None, self.user["id"], data.get("subject", "").strip(), data.get("priority", "normal"), critical, now, now),
        )
        self.conn.execute(
            """
            INSERT INTO ticket_messages (id, ticket_id, author_user_id, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (new_id("msg"), ticket_id, self.user["id"], data.get("body", "").strip(), now),
        )
        self.support_page("Support case opened.")

    def assistant_page(self, error: str = ""):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        ctx = assistant_context(self.conn, company["id"], self.user["id"])
        messages = []
        for message in ctx["messages"]:
            citations = json.loads(message["citations_json"] or "[]")
            citation_html = "".join(
                f"<a href='{esc(item.get('href', '/app'))}'><strong>{esc(item.get('label'))}</strong><small>{esc(item.get('source'))}</small></a>"
                for item in citations
            )
            messages.append(f"""
              <article class="assistant-message {esc(message['role'])}">
                <span class="message-role">{'You' if message['role'] == 'user' else 'Cedar guide'}</span>
                <p>{esc(message['content'])}</p>
                {f'<div class="citation-list">{citation_html}</div>' if citation_html else ''}
              </article>
            """)
        if not messages:
            messages.append("""
              <div class="assistant-empty">
                <span class="assistant-mark">AI</span>
                <h2>Ask about your company record</h2>
                <p>Answers use your formation, compliance, bookkeeping, tax, and analytics records. Sources appear below each answer.</p>
              </div>
            """)
        actions = []
        for action in ctx["actions"]:
            buttons = ""
            if action["status"] == "pending_approval":
                buttons = f"""
                  <form method="post" action="/api/assistant/actions/{esc(action['id'])}" class="actions">
                    <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
                    <button class="button small" name="decision" value="approve" type="submit">Approve request</button>
                    <button class="button secondary small" name="decision" value="reject" type="submit">Reject</button>
                  </form>
                """
            actions.append(f"<div class='approval-row'><div><strong>{esc(action['summary'])}</strong><small>No external action has been executed.</small></div><span class='badge {esc(action['status'])}'>{esc(status_label(action['status']))}</span>{buttons}</div>")
        body = f"""
        <section class="workspace-head product-head">
          <div><span class="eyebrow">Cedar guide</span><h1>AI business assistant</h1><p>Context-aware guidance with visible sources and approval-gated actions.</p></div>
          <span class="badge warning">Rules-based sandbox</span>
        </section>
        {self.error_box(error)}
        <section class="assistant-layout">
          <div class="assistant-panel">
            <div class="assistant-messages">{''.join(messages)}</div>
            <form method="post" action="/api/assistant/message" class="assistant-composer">
              <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
              <label for="assistant-question">Message</label>
              <textarea id="assistant-question" name="question" required maxlength="2000" rows="3" placeholder="What compliance deadlines need my attention?"></textarea>
              <button class="button" type="submit">Send</button>
            </form>
          </div>
          <aside class="assistant-side">
            <div><span class="section-kicker">GUARDRAILS</span><h2>Approval required</h2><p>The assistant can explain and recommend. It cannot silently submit filings, send payments, sign documents, or contact authorities.</p></div>
            <div class="approval-list">{''.join(actions) if actions else '<p class="muted">No consequential actions awaiting approval.</p>'}</div>
          </aside>
        </section>
        """
        self.send_html("AI assistant", body)

    def assistant_message_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            ask_assistant(self.conn, company["id"], self.user["id"], data.get("question", ""))
            self.redirect("/app/assistant")
        except ValueError as exc:
            self.assistant_page(str(exc))

    def assistant_action_post(self, action_id: str):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            decide_assistant_action(self.conn, company["id"], action_id, self.user["id"], data.get("decision", ""))
            self.redirect("/app/assistant?notice=Decision recorded. No external action was executed.")
        except ValueError as exc:
            self.assistant_page(str(exc))

    def bookkeeping_page(self, error: str = ""):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        ctx = bookkeeping_context(self.conn, company["id"])
        if not ctx["accounts"]:
            body = f"""
            <section class="workspace-head product-head"><div><span class="eyebrow">Financial operations</span><h1>Bookkeeping</h1><p>Connect accounts, categorize transactions, reconcile activity, and close each month.</p></div></section>
            {self.error_box(error)}
            <section class="connection-empty">
              <div><span class="section-kicker">SANDBOX CONNECTOR</span><h2>Connect your first financial accounts</h2><p>This adapter creates clearly labeled sample accounts and transactions. Production will use a replaceable aggregation provider.</p></div>
              <form method="post" action="/api/bookkeeping/connect-sandbox"><input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}"><button class="button" type="submit">Connect sandbox ledger</button></form>
            </section>
            """
            return self.send_html("Bookkeeping", body)
        account_cards = "".join(
            f"<article class='account-tile'><span class='account-bank'>{esc(account['institution_name'])}</span><strong>{esc(account['account_name'])} &middot; {esc(account['mask'])}</strong><b>{esc(cents(account['balance_cents']))}</b><span class='badge ok'>Sandbox connected</span></article>"
            for account in ctx["accounts"]
        )
        transaction_rows = []
        for transaction in ctx["transactions"]:
            options = "".join(f"<option value='{esc(category)}'{selected(transaction['category'], category)}>{esc(category)}</option>" for category in BOOKKEEPING_CATEGORIES)
            transaction_rows.append(f"""
              <tr>
                <td>{esc(date_label(transaction['posted_at']))}</td>
                <td><strong>{esc(transaction['description'])}</strong><small>{esc(transaction['account_name'])} &middot; {esc(transaction['mask'])}</small></td>
                <td class="money {'positive' if transaction['amount_cents'] > 0 else 'negative'}">{esc(cents(transaction['amount_cents']))}</td>
                <td>
                  <form method="post" action="/api/bookkeeping/transactions/{esc(transaction['id'])}" class="transaction-form">
                    <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
                    <select name="category" aria-label="Category for {esc(transaction['description'])}"><option value="">Choose category</option>{options}</select>
                    <label class="compact-check"><input type="checkbox" name="reconcile" value="yes"{checked(transaction['status'], 'reconciled')}> Reconciled</label>
                    <button class="button secondary small" type="submit">Save</button>
                  </form>
                </td>
                <td><span class="badge {esc(transaction['status'])}">{esc(status_label(transaction['status']))}</span></td>
              </tr>
            """)
        close = ctx["closes"][0] if ctx["closes"] else None
        close_action = ""
        if close and close["status"] != "closed":
            labels = {"not_started": "Start close", "in_progress": "Send to review", "review_ready": "Complete close"}
            close_action = f"<form method='post' action='/api/bookkeeping/closes/{esc(close['id'])}'><input type='hidden' name='csrf_token' value='{esc(self.csrf_token())}'><button class='button small' type='submit'>{labels.get(close['status'], 'Advance')}</button></form>"
        body = f"""
        <section class="workspace-head product-head">
          <div><span class="eyebrow">Financial operations</span><h1>Bookkeeping</h1><p>Sandbox ledger last synced {esc(timestamp_label(ctx['accounts'][0]['last_synced_at']))}.</p></div>
          <a class="button secondary" href="/api/bookkeeping/export.csv">Export CSV</a>
        </section>
        {self.error_box(error)}
        <section class="metric-grid four">
          <article><span>Revenue</span><strong>{esc(cents(ctx['revenue_cents']))}</strong><small>Connected transactions</small></article>
          <article><span>Expenses</span><strong>{esc(cents(ctx['expenses_cents']))}</strong><small>Connected transactions</small></article>
          <article><span>Net result</span><strong>{esc(cents(ctx['profit_cents']))}</strong><small>Revenue less expenses</small></article>
          <article><span>Needs review</span><strong>{ctx['uncategorized_count']}</strong><small>Uncategorized items</small></article>
        </section>
        <section class="account-grid">{account_cards}</section>
        <section class="workspace-columns">
          <article class="dashboard-card span-main">
            <div class="section-heading"><div><span class="section-kicker">LEDGER</span><h2>Transactions</h2></div><span>{len(ctx['transactions'])} total</span></div>
            <div class="table-wrap"><table class="transaction-table"><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Category and reconciliation</th><th>Status</th></tr></thead><tbody>{''.join(transaction_rows)}</tbody></table></div>
          </article>
          <aside class="workspace-rail">
            <article class="dashboard-card"><span class="section-kicker">MONTHLY CLOSE</span><h2>{esc(close['month'] if close else 'Not started')}</h2><p>Status: <span class="badge {esc(close['status'] if close else 'pending')}">{esc(status_label(close['status']) if close else 'Pending')}</span></p>{close_action}</article>
            <article class="dashboard-card"><span class="section-kicker">REPORTS</span><h2>Current ledger summary</h2><dl class="record-list compact"><div><dt>Profit and loss</dt><dd>{esc(cents(ctx['profit_cents']))}</dd></div><div><dt>Balance sheet cash</dt><dd>{esc(cents(ctx['cash_cents']))}</dd></div><div><dt>Cash flow</dt><dd>{esc(cents(ctx['profit_cents']))}</dd></div></dl><p class="disclosure">Sandbox ledger report; not an audited financial statement.</p></article>
          </aside>
        </section>
        """
        self.send_html("Bookkeeping", body)

    def bookkeeping_connect_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        connect_sandbox_finance(self.conn, company["id"], self.user["id"])
        self.redirect("/app/bookkeeping?notice=Sandbox financial accounts connected.")

    def bookkeeping_transaction_post(self, transaction_id: str):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            update_transaction(self.conn, company["id"], transaction_id, self.user["id"], data.get("category", ""), data.get("reconcile") == "yes")
            self.redirect("/app/bookkeeping?notice=Transaction updated.")
        except ValueError as exc:
            self.bookkeeping_page(str(exc))

    def bookkeeping_close_post(self, close_id: str):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            advance_monthly_close(self.conn, company["id"], close_id, self.user["id"])
            self.redirect("/app/bookkeeping?notice=Monthly close advanced.")
        except ValueError as exc:
            self.bookkeeping_page(str(exc))

    def bookkeeping_export(self):
        if not self.require_auth():
            return
        company = self.current_company()
        ctx = bookkeeping_context(self.conn, company["id"])
        rows = [["date", "account", "description", "merchant", "amount_usd", "category", "status"]]
        rows.extend([[row["posted_at"], row["account_name"], row["description"], row["merchant"] or "", f"{row['amount_cents'] / 100:.2f}", row["category"] or "", row["status"]] for row in ctx["transactions"]])
        self.send_csv("cedarhq-bookkeeping.csv", rows)

    def taxes_page(self, error: str = ""):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        ctx = taxes_context(self.conn, company["id"])
        filing_options = "".join(f"<option value='{esc(key)}'>{esc(label)}</option>" for key, label in TAX_TYPES.items())
        filing_cards = "".join(
            f"<article class='tax-filing-tile'><div><strong>{esc(TAX_TYPES.get(filing['filing_type'], filing['filing_type']))}</strong><small>{filing['tax_year']} &middot; {esc(filing['jurisdiction'])}</small></div><span class='badge {esc(filing['status'])}'>{esc(status_label(filing['status']))}</span><small>Planning due date: {esc(date_label(filing['due_date']))}</small></article>"
            for filing in ctx["filings"]
        )
        active_html = ""
        if ctx["active"]:
            filing = ctx["active"]
            answers = "".join(
                f"<label>{esc(answer['question_label'])}<textarea name='answer_{esc(answer['question_key'])}' rows='2' required>{esc(answer['answer'])}</textarea></label>"
                for answer in ctx["answers"]
            )
            documents = "".join(
                f"<label class='document-check'><input type='checkbox' name='document_{esc(document['id'])}' value='yes'{checked(document['status'], 'provided')}><span><strong>{esc(document['label'])}</strong><small>{'Marked provided' if document['status'] == 'provided' else 'Required before preparation'}</small></span></label>"
                for document in ctx["documents"]
            )
            action = ""
            if filing["status"] in {"questionnaire", "documents_pending", "blocked"}:
                action = f"""
                  <form method="post" action="/api/taxes/{esc(filing['id'])}/action">
                    <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
                    <button class="button" name="action" value="submit_questionnaire" type="submit">Submit for preparation</button>
                  </form>
                """
            elif filing["status"] == "founder_review":
                action = f"<form method='post' action='/api/taxes/{esc(filing['id'])}/action'><input type='hidden' name='csrf_token' value='{esc(self.csrf_token())}'><button class='button' name='action' value='approve_return' type='submit'>Approve return for signature</button></form>"
            elif filing["status"] == "signature_required":
                action = f"<form method='post' action='/api/taxes/{esc(filing['id'])}/action'><input type='hidden' name='csrf_token' value='{esc(self.csrf_token())}'><button class='button' name='action' value='sign_return' type='submit'>Record sandbox signature</button></form>"
            elif filing["status"] == "ready_to_submit":
                action = "<div class='notice warning'>Ready for staff submission. Nothing has been sent to a tax authority.</div>"
            else:
                action = f"<p class='muted'>Current owner: {esc(filing['responsible_party'])}. Status changes appear here after staff review.</p>"
            active_html = f"""
              <section class="tax-workspace">
                <form method="post" action="/api/taxes/{esc(filing['id'])}/save" class="tax-questionnaire">
                  <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
                  <div class="section-heading"><div><span class="section-kicker">QUESTIONNAIRE</span><h2>{esc(TAX_TYPES.get(filing['filing_type']))} &middot; {filing['tax_year']}</h2></div><span class="badge {esc(filing['status'])}">{esc(status_label(filing['status']))}</span></div>
                  <div class="question-list">{answers}</div>
                  <div><span class="section-kicker">REQUIRED DOCUMENTS</span><div class="document-checklist">{documents}</div></div>
                  <button class="button secondary" type="submit">Save questionnaire</button>
                </form>
                <aside class="tax-rail">
                  <div><span class="section-kicker">WORKFLOW</span><h2>Preparation status</h2><div class="tax-status-line"><span class="active"></span>Questionnaire</div><div class="tax-status-line"><span class="{'active' if filing['status'] not in {'questionnaire','documents_pending'} else ''}"></span>Preparation</div><div class="tax-status-line"><span class="{'active' if filing['status'] in {'founder_review','signature_required','ready_to_submit','submitted','accepted'} else ''}"></span>Review and signature</div><div class="tax-status-line"><span class="{'active' if filing['status'] in {'submitted','accepted'} else ''}"></span>Submission and response</div></div>
                  <div class="tax-disclaimer"><strong>Sandbox tax workflow</strong><p>Dates are planning estimates. CedarHQ has not submitted this return to the IRS, a state, or a city.</p></div>
                  {action}
                </aside>
              </section>
            """
        body = f"""
        <section class="workspace-head product-head">
          <div><span class="eyebrow">Tax center</span><h1>Tax workflows</h1><p>Questionnaire, document collection, preparation, review, signature, submission, and authority response.</p></div>
        </section>
        {self.error_box(error)}
        <section class="tax-overview">
          <form method="post" action="/api/taxes/start" class="new-tax-form">
            <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
            <label>Filing type<select name="filing_type">{filing_options}</select></label>
            <label>Tax year<input type="number" name="tax_year" min="2020" max="{datetime.now().year + 1}" value="{datetime.now().year - 1}"></label>
            <button class="button" type="submit">Start workflow</button>
          </form>
          <div class="tax-filing-list">{filing_cards or '<p class="muted">No tax workflows started.</p>'}</div>
        </section>
        {active_html}
        """
        self.send_html("Taxes", body)

    def tax_start_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            create_tax_filing(self.conn, company["id"], self.user["id"], data.get("filing_type", ""), int(data.get("tax_year", "0")))
            self.redirect("/app/taxes?notice=Sandbox tax workflow created.")
        except (ValueError, TypeError) as exc:
            self.taxes_page(str(exc))

    def tax_save_post(self, filing_id: str):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            save_tax_questionnaire(self.conn, company["id"], filing_id, self.user["id"], data)
            self.redirect("/app/taxes?notice=Tax questionnaire saved.")
        except ValueError as exc:
            self.taxes_page(str(exc))

    def tax_action_post(self, filing_id: str):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            tax_action(self.conn, company["id"], filing_id, self.user["id"], data.get("action", ""))
            self.redirect("/app/taxes?notice=Tax workflow status updated.")
        except ValueError as exc:
            self.taxes_page(str(exc))

    def analytics_page(self, error: str = ""):
        if not self.require_auth():
            return
        company = self.current_company()
        if not company:
            return self.redirect("/app/onboarding")
        start = (self.query.get("start") or [""])[0]
        end = (self.query.get("end") or [""])[0]
        ctx = analytics_context(self.conn, company["id"], start, end)
        connections = {row["provider"]: row for row in ctx["connections"]}
        connector_html = []
        for provider in ["shopify", "amazon"]:
            connected = connections.get(provider)
            if connected:
                connector_html.append(f"<div class='commerce-connector connected'><span class='provider-logo'>{provider[0].upper()}</span><span><strong>{esc(connected['display_name'])}</strong><small>Last synced {esc(timestamp_label(connected['last_synced_at']))}</small></span><span class='badge ok'>Sandbox connected</span></div>")
            else:
                connector_html.append(f"<form method='post' action='/api/commerce/connect-sandbox' class='commerce-connector'><input type='hidden' name='csrf_token' value='{esc(self.csrf_token())}'><input type='hidden' name='provider' value='{provider}'><span class='provider-logo'>{provider[0].upper()}</span><span><strong>{provider.title()}</strong><small>Connect sample store data</small></span><button class='button secondary small' type='submit'>Connect</button></form>")
        chart_html = ""
        max_revenue = max((values["revenue_cents"] for values in ctx["daily"].values()), default=1)
        for metric_date, values in ctx["daily"].items():
            height = max(4, round(values["revenue_cents"] / max_revenue * 100))
            chart_html += f"<div class='chart-column' title='{esc(metric_date)}: {esc(cents(values['revenue_cents']))}'><span style='height:{height}%'></span><small>{esc(metric_date[8:10])}</small></div>"
        daily_rows = "".join(
            f"<tr><td>{esc(date_label(metric_date))}</td><td>{values['orders_count']}</td><td>{esc(cents(values['revenue_cents']))}</td><td>{esc(cents(values['margin_cents']))}</td></tr>"
            for metric_date, values in reversed(list(ctx["daily"].items())[-14:])
        )
        export_query = urlencode({"start": ctx["start"], "end": ctx["end"]})
        totals = ctx["totals"]
        body = f"""
        <section class="workspace-head product-head">
          <div><span class="eyebrow">Commerce intelligence</span><h1>Analytics</h1><p>Revenue, orders, fees, refunds, ad spend, margins, payouts, and trends from replaceable connectors.</p></div>
          <a class="button secondary" href="/api/analytics/export.csv?{esc(export_query)}">Export CSV</a>
        </section>
        {self.error_box(error)}
        <section class="commerce-connectors">{''.join(connector_html)}</section>
        <form method="get" action="/app/analytics" class="date-filter"><label>From<input type="date" name="start" value="{esc(ctx['start'])}"></label><label>To<input type="date" name="end" value="{esc(ctx['end'])}"></label><button class="button secondary" type="submit">Apply dates</button></form>
        <section class="metric-grid four analytics-metrics">
          <article><span>Revenue</span><strong>{esc(cents(totals['revenue_cents']))}</strong><small>{totals['orders_count']} orders</small></article>
          <article><span>Contribution margin</span><strong>{esc(cents(totals['margin_cents']))}</strong><small>After tracked costs</small></article>
          <article><span>Fees and refunds</span><strong>{esc(cents(totals['fees_cents'] + totals['refunds_cents']))}</strong><small>Platform deductions</small></article>
          <article><span>Ad spend</span><strong>{esc(cents(totals['ad_spend_cents']))}</strong><small>Connected channel spend</small></article>
        </section>
        <section class="analytics-layout">
          <article class="dashboard-card chart-card"><div class="section-heading"><div><span class="section-kicker">TREND</span><h2>Daily revenue</h2></div><span>{esc(ctx['start'])} to {esc(ctx['end'])}</span></div><div class="bar-chart">{chart_html or '<p class="muted">Connect a sandbox store to populate this chart.</p>'}</div></article>
          <article class="dashboard-card"><div class="section-heading"><div><span class="section-kicker">UNIT ECONOMICS</span><h2>Cost breakdown</h2></div></div><dl class="record-list compact"><div><dt>COGS</dt><dd>{esc(cents(totals['cogs_cents']))}</dd></div><div><dt>Fees</dt><dd>{esc(cents(totals['fees_cents']))}</dd></div><div><dt>Refunds</dt><dd>{esc(cents(totals['refunds_cents']))}</dd></div><div><dt>Ad spend</dt><dd>{esc(cents(totals['ad_spend_cents']))}</dd></div><div><dt>Payouts</dt><dd>{esc(cents(totals['payouts_cents']))}</dd></div></dl></article>
        </section>
        <section class="dashboard-card"><div class="section-heading"><div><span class="section-kicker">DAILY DETAIL</span><h2>Performance ledger</h2></div></div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Orders</th><th>Revenue</th><th>Margin</th></tr></thead><tbody>{daily_rows or '<tr><td colspan="4">No data in this period.</td></tr>'}</tbody></table></div></section>
        """
        self.send_html("Analytics", body)

    def commerce_connect_post(self):
        if not self.require_auth():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        company = self.current_company()
        try:
            connect_sandbox_commerce(self.conn, company["id"], self.user["id"], data.get("provider", ""))
            self.redirect("/app/analytics?notice=Sandbox commerce store connected.")
        except ValueError as exc:
            self.analytics_page(str(exc))

    def analytics_export(self):
        if not self.require_auth():
            return
        company = self.current_company()
        ctx = analytics_context(self.conn, company["id"], (self.query.get("start") or [""])[0], (self.query.get("end") or [""])[0])
        rows = [["date", "provider", "orders", "revenue_usd", "fees_usd", "refunds_usd", "ad_spend_usd", "cogs_usd", "payouts_usd"]]
        rows.extend([[row["metric_date"], row["provider"], str(row["orders_count"]), f"{row['revenue_cents'] / 100:.2f}", f"{row['fees_cents'] / 100:.2f}", f"{row['refunds_cents'] / 100:.2f}", f"{row['ad_spend_cents'] / 100:.2f}", f"{row['cogs_cents'] / 100:.2f}", f"{row['payouts_cents'] / 100:.2f}"] for row in ctx["metrics"]])
        self.send_csv("cedarhq-commerce-analytics.csv", rows)

    def ops_taxes_page(self, error: str = ""):
        if not self.require_ops():
            return
        filings = self.conn.execute(
            """
            SELECT tf.*, c.name_choice_1, c.legal_name, u.email AS customer_email
            FROM tax_filings tf
            JOIN companies c ON c.id = tf.company_id
            JOIN users u ON u.id = c.owner_user_id
            ORDER BY CASE tf.status WHEN 'ready_to_submit' THEN 0 WHEN 'preparation' THEN 1 WHEN 'submitted' THEN 2 ELSE 3 END,
                     tf.due_date
            """
        ).fetchall()
        cards = []
        for filing in filings:
            controls = ""
            if filing["status"] == "preparation":
                controls = "<button class='button small' name='action' value='mark_review_ready' type='submit'>Send to founder review</button>"
            elif filing["status"] == "ready_to_submit":
                controls = "<button class='button small' name='action' value='sandbox_submit' type='submit'>Sandbox submit</button>"
            elif filing["status"] == "submitted":
                controls = "<button class='button small' name='action' value='sandbox_accept' type='submit'>Record sandbox acceptance</button><button class='button danger small' name='action' value='sandbox_reject' type='submit'>Record rejection</button>"
            evidence = f"<a href='/api/evidence/{esc(filing['evidence_id'])}/download'>Download evidence</a>" if filing["evidence_id"] else "<span class='muted'>No authority evidence</span>"
            cards.append(f"""
              <article class="card">
                <div class="card-head"><span class="badge {esc(filing['status'])}">{esc(status_label(filing['status']))}</span><span>{esc(date_label(filing['due_date']))}</span></div>
                <h2>{esc(filing['name_choice_1'] or filing['legal_name'] or 'Unnamed company')}</h2>
                <p>{esc(TAX_TYPES.get(filing['filing_type'], filing['filing_type']))} &middot; {filing['tax_year']} &middot; {esc(filing['customer_email'])}</p>
                <p>{evidence}</p>
                <form method="post" action="/api/ops/taxes/{esc(filing['id'])}/action" class="actions">
                  <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">{controls or '<span class="muted">Waiting on founder or no staff action available.</span>'}
                </form>
              </article>
            """)
        body = f"""
        <section class="workspace-head"><div><span class="eyebrow">Tax operations</span><h1>Preparation and filing queue</h1><p>Sandbox submissions generate evidence; they are never represented as authority filings.</p></div></section>
        {self.error_box(error)}
        <section class="grid two">{''.join(cards) if cards else '<p class="muted">No tax workflows yet.</p>'}</section>
        """
        self.send_html("Tax queue", body)

    def ops_tax_action_post(self, filing_id: str):
        if not self.require_ops():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        filing = self.conn.execute("SELECT company_id FROM tax_filings WHERE id = ?", (filing_id,)).fetchone()
        if not filing:
            return self.send_error_page(404, "Tax workflow not found", "The requested workflow does not exist.")
        try:
            tax_action(self.conn, filing["company_id"], filing_id, self.user["id"], data.get("action", ""), is_ops=True)
            self.redirect("/ops/taxes?notice=Sandbox tax status updated with an audit entry.")
        except ValueError as exc:
            self.ops_taxes_page(str(exc))

    def ops_orders_page(self):
        if not self.require_ops():
            return
        orders = list_orders_for_ops(self.conn)
        rows = "".join(self.ops_order_card(order) for order in orders) or "<p class='muted'>No operations orders yet.</p>"
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Operations</span>
            <h1>Formation review queue</h1>
            <p>Staff actions create receipts, evidence, and audit logs.</p>
          </div>
          <form method="post" action="/api/jobs/reminders">
            <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
            <button class="button secondary" type="submit">Run reminder job</button>
          </form>
        </section>
        <section class="grid two">{rows}</section>
        """
        self.send_html("Operations queue", body)

    def ops_order_card(self, order) -> str:
        return f"""
        <article class="card">
          <div class="card-head"><span class="badge {esc(order['status'])}">{esc(status_label(order['status']))}</span><span>{esc(order['state_code'])}</span></div>
          <h2>{esc(order['name_choice_1'] or order['legal_name'] or 'Unnamed company')}</h2>
          <p>{esc(order['customer_email'])} · {entity_label(order['entity_type'])} · {esc(order['plan_name'])}</p>
          <dl class="compact-dl">
            <div><dt>First year</dt><dd>{cents(order['total_first_year_cents'])}</dd></div>
            <div><dt>Renewal</dt><dd>{cents(order['total_renewal_cents'])}</dd></div>
          </dl>
          <a class="button secondary full" href="/ops/orders/{esc(order['id'])}">Review</a>
        </article>
        """

    def ops_order_page(self, order_id: str):
        if not self.require_ops():
            return
        order = get_order(self.conn, order_id)
        if not order:
            return self.send_error_page(404, "Order not found", "This order is unavailable.")
        timeline = get_timeline(self.conn, order_id)
        body = f"""
        <section class="workspace-head">
          <div>
            <span class="eyebrow">Operations review</span>
            <h1>{esc(order['name_choice_1'] or order['legal_name'] or 'Formation order')}</h1>
            <p>{esc(order['customer_email'])} · {entity_label(order['entity_type'])} in {esc(order['state_code'])} · {esc(status_label(order['status']))}</p>
          </div>
          <a class="button secondary" href="/app/orders/{esc(order_id)}">Customer view</a>
        </section>
        {self.blocked_box(order)}
        <section class="grid two">
          <div class="panel">
            <h2>Action queue</h2>
            {self.ops_action_form(order)}
          </div>
          <div class="panel">
            <h2>Founder details</h2>
            <dl class="compact-dl">
              <div><dt>Purpose</dt><dd>{esc(order['business_purpose'] or 'Not provided')}</dd></div>
              <div><dt>Industry</dt><dd>{esc(order['industry'] or 'Not provided')}</dd></div>
              <div><dt>Address</dt><dd>{esc(order['address_line1'] or '')}, {esc(order['city'] or '')}, {esc(order['country'] or '')}</dd></div>
            </dl>
          </div>
        </section>
        <section class="panel">
          <h2>Evidence-backed timeline</h2>
          <div class="timeline">{''.join(self.timeline_item(step) for step in timeline)}</div>
        </section>
        """
        self.send_html("Ops order", body)

    def ops_action_form(self, order) -> str:
        next_actions = {
            "information_received": ("complete_review", "Complete review"),
            "operations_review": ("prepare_state_packet", "Mark ready for state submission"),
            "state_submission_ready": ("submit_state_sandbox", "Submit to sandbox state"),
            "state_submitted": ("approve_state_sandbox", "Record sandbox approval"),
            "state_approved": ("submit_ein_sandbox", "Submit sandbox EIN"),
            "ein_submitted": ("receive_ein_sandbox", "Record sandbox EIN"),
            "ein_received": ("mark_bank_ready", "Mark bank-ready"),
        }
        action = next_actions.get(order["status"])
        complete_form = ""
        if action:
            complete_form = f"""
            <form method="post" action="/api/ops/orders/{esc(order['id'])}/transition" class="stack">
              <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
              <input type="hidden" name="action" value="{esc(action[0])}">
              <label>Evidence note<textarea name="note" rows="3" placeholder="Review result, provider receipt detail, or next-step summary"></textarea></label>
              <button class="button" type="submit">{esc(action[1])}</button>
            </form>
            """
        elif order["status"] == "bank_ready":
            complete_form = "<p class='notice'>This order is bank-ready. Further provider work belongs in later workflows.</p>"
        else:
            complete_form = "<p class='muted'>No direct action is available from this status.</p>"
        block_form = f"""
        <form method="post" action="/api/ops/orders/{esc(order['id'])}/transition" class="stack danger-zone">
          <input type="hidden" name="csrf_token" value="{esc(self.csrf_token())}">
          <input type="hidden" name="action" value="block_order">
          <label>Block reason<textarea required name="note" rows="3" placeholder="What exactly does the founder need to fix?"></textarea></label>
          <button class="button danger" type="submit">Block and request action</button>
        </form>
        """
        return complete_form + block_form

    def ops_transition_post(self, order_id: str):
        if not self.require_ops():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        try:
            ops_transition_order(self.conn, order_id, self.user["id"], data.get("action", ""), data.get("note", ""))
            self.redirect(f"/ops/orders/{order_id}?notice=Order updated with audit evidence.")
        except Exception as exc:
            self.redirect(f"/ops/orders/{order_id}?notice={quote(str(exc))}")

    def jobs_reminders_post(self):
        if not self.require_ops():
            return
        data = self.form_data()
        if not self.verify_csrf(data):
            return self.send_error_page(403, "Security token expired", "Reload and try again.")
        result = process_reminders(self.conn, self.user["id"])
        self.redirect(f"/ops/orders?notice=Reminder job processed: {result['action_required']} action-required, {result['overdue']} overdue.")

    def ops_compliance_page(self):
        if not self.require_ops():
            return
        items = self.conn.execute(
            """
            SELECT ci.*, c.name_choice_1, c.legal_name
            FROM compliance_items ci
            JOIN companies c ON c.id = ci.company_id
            ORDER BY ci.due_date ASC
            """
        ).fetchall()
        cards = "".join(
            f"<article class='card'><div class='card-head'><span class='badge {esc(item['status'])}'>{esc(status_label(item['status']))}</span><span>{esc(item['due_date'])}</span></div><h2>{esc(item['title'])}</h2><p>{esc(item['name_choice_1'] or item['legal_name'])}</p><p>{esc(item['description'])}</p></article>"
            for item in items
        ) or "<p class='muted'>No compliance items yet.</p>"
        body = f"""
        <section class="workspace-head"><div><span class="eyebrow">Operations</span><h1>Compliance risk queue</h1><p>Upcoming, action-required, rejected, and overdue items.</p></div></section>
        <section class="grid two">{cards}</section>
        """
        self.send_html("Ops compliance", body)

    def ops_audit_page(self):
        if not self.require_ops():
            return
        logs = self.conn.execute(
            """
            SELECT al.*, u.email
            FROM activity_logs al
            LEFT JOIN users u ON u.id = al.actor_user_id
            ORDER BY al.created_at DESC LIMIT 200
            """
        ).fetchall()
        rows = "".join(
            f"<tr><td>{esc(log['created_at'])}</td><td>{esc(log['event_type'])}</td><td>{esc(log['email'] or 'System')}</td><td>{esc(log['summary'])}</td></tr>"
            for log in logs
        )
        body = f"""
        <section class="panel">
          <span class="eyebrow">Immutable activity</span>
          <h1>Audit log</h1>
          <div class="table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Summary</th></tr></thead><tbody>{rows}</tbody></table></div>
        </section>
        """
        self.send_html("Audit", body)

    def current_company(self):
        if not self.user:
            return None
        return self.conn.execute(
            """
            SELECT c.* FROM companies c
            JOIN company_members cm ON cm.company_id = c.id
            WHERE cm.user_id = ?
            ORDER BY c.created_at DESC LIMIT 1
            """,
            (self.user["id"],),
        ).fetchone()

    def error_box(self, error: str) -> str:
        return f"<div class='notice error'>{esc(error)}</div>" if error else ""

    def message_box(self, message: str) -> str:
        return f"<div class='notice'>{esc(message)}</div>" if message else ""


def parse_args():
    parser = argparse.ArgumentParser(description="Run CedarHQ.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8088, type=int)
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.migrate:
        migrate()
        with transaction() as conn:
            ensure_reference_data(conn)
            if args.seed_demo:
                seed_demo(conn)
        if args.init_only:
            print("CedarHQ database initialized.")
            return
    else:
        migrate()
        with transaction() as conn:
            ensure_reference_data(conn)
    server = ThreadingHTTPServer((args.host, args.port), CedarHandler)
    print(f"CedarHQ running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CedarHQ")


if __name__ == "__main__":
    main()
