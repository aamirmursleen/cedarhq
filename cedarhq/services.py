from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import utcnow
from .providers import (
    LocalOutboxEmailProvider,
    ProviderResult,
    SandboxCheckoutProvider,
    SandboxFormationProvider,
    serialize_provider_payload,
)
from .security import hash_password, hash_token, normalize_email, random_token, validate_password_strength, verify_password
from .status import FORMATION_STEPS, StatusError, entity_recommendation, validate_formation_transition


ROLES = {"founder", "team_member", "staff", "accountant", "admin"}
OPS_ROLES = {"staff", "accountant", "admin"}

STATE_OPTIONS = {
    "DE": {
        "name": "Delaware",
        "fee_cents": 14000,
        "timeline": "3-10 business days",
        "benefits": "Common investor expectations, mature corporate law, flexible for C-Corps.",
        "limitations": "Franchise tax and registered-agent renewals require careful tracking.",
    },
    "WY": {
        "name": "Wyoming",
        "fee_cents": 10200,
        "timeline": "2-7 business days",
        "benefits": "Simple annual reporting and privacy-friendly administration.",
        "limitations": "May be less familiar to institutional investors than Delaware.",
    },
    "FL": {
        "name": "Florida",
        "fee_cents": 12500,
        "timeline": "5-12 business days",
        "benefits": "Useful for founders operating in Florida with a straightforward state portal.",
        "limitations": "Annual report deadlines and fees still apply.",
    },
    "CA": {
        "name": "California",
        "fee_cents": 7000,
        "timeline": "7-15 business days",
        "benefits": "Natural choice for companies physically operating in California.",
        "limitations": "Extra franchise tax and compliance obligations can be material.",
    },
    "TX": {
        "name": "Texas",
        "fee_cents": 30000,
        "timeline": "5-10 business days",
        "benefits": "Good fit for Texas-operated companies and many domestic service businesses.",
        "limitations": "Higher initial state filing fee than many states.",
    },
    "NY": {
        "name": "New York",
        "fee_cents": 20000,
        "timeline": "7-20 business days",
        "benefits": "Useful when the company will primarily operate from New York.",
        "limitations": "Publication and local compliance requirements may add cost.",
    },
}

PLAN_ROWS = [
    {
        "slug": "formation_only",
        "name": "Formation Only",
        "description": "Company filing, EIN workflow, essential documents, and first-year registered-agent setup.",
        "service_fee_cents": 29900,
        "renewal_fee_cents": 19900,
        "registered_agent_included": 1,
        "mailroom_included": 0,
        "bookkeeping_included": 0,
        "tax_included": 0,
    },
    {
        "slug": "compliance",
        "name": "Compliance",
        "description": "Formation plus registered-agent renewals, compliance calendar, annual-report handling, and reminders.",
        "service_fee_cents": 79900,
        "renewal_fee_cents": 79900,
        "registered_agent_included": 1,
        "mailroom_included": 1,
        "bookkeeping_included": 0,
        "tax_included": 0,
    },
    {
        "slug": "complete_back_office",
        "name": "Complete Back Office",
        "description": "Formation, compliance, mailroom, bookkeeping close support, tax preparation workflow, and ecommerce analytics architecture.",
        "service_fee_cents": 199900,
        "renewal_fee_cents": 239900,
        "registered_agent_included": 1,
        "mailroom_included": 1,
        "bookkeeping_included": 1,
        "tax_included": 1,
    },
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def cents(value: int) -> str:
    return f"${value / 100:,.2f}"


def parse_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def ensure_reference_data(conn) -> None:
    now = utcnow()
    for plan in PLAN_ROWS:
        existing = conn.execute("SELECT id FROM plans WHERE slug = ?", (plan["slug"],)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE plans
                SET name = ?, description = ?, service_fee_cents = ?, renewal_fee_cents = ?,
                    registered_agent_included = ?, mailroom_included = ?, bookkeeping_included = ?,
                    tax_included = ?
                WHERE slug = ?
                """,
                (
                    plan["name"],
                    plan["description"],
                    plan["service_fee_cents"],
                    plan["renewal_fee_cents"],
                    plan["registered_agent_included"],
                    plan["mailroom_included"],
                    plan["bookkeeping_included"],
                    plan["tax_included"],
                    plan["slug"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO plans (
                  id, slug, name, description, service_fee_cents, renewal_fee_cents,
                  registered_agent_included, mailroom_included, bookkeeping_included, tax_included, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("plan"),
                    plan["slug"],
                    plan["name"],
                    plan["description"],
                    plan["service_fee_cents"],
                    plan["renewal_fee_cents"],
                    plan["registered_agent_included"],
                    plan["mailroom_included"],
                    plan["bookkeeping_included"],
                    plan["tax_included"],
                    now,
                ),
            )


def create_user(conn, email: str, password: str, name: str, role: str = "founder", auth_provider: str = "password", verified: bool = False):
    normalized = normalize_email(email)
    if role not in ROLES:
        raise ValueError("Unsupported role.")
    valid, message = validate_password_strength(password)
    if not valid:
        raise ValueError(message)
    now = utcnow()
    user_id = new_id("usr")
    conn.execute(
        """
        INSERT INTO users (id, email, name, password_hash, auth_provider, role, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, normalized, name.strip(), hash_password(password), auth_provider, role, int(verified), now, now),
    )
    audit(conn, user_id, None, None, "user.created", f"User {normalized} created with role {role}.")
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_or_create_google_sandbox_user(conn):
    email = "google.founder@cedarhq.local"
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        return row
    return create_user(
        conn,
        email=email,
        password=f"GoogleSandbox123{random_token(4)}",
        name="Google Sandbox Founder",
        role="founder",
        auth_provider="google_sandbox",
        verified=True,
    )


def authenticate_user(conn, email: str, password: str):
    user = conn.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_session(conn, user_id: str, user_agent: str | None = None, ip_hash: str | None = None) -> str:
    token = random_token(36)
    now = utcnow()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO sessions (id, user_id, token_hash, csrf_token, expires_at, created_at, last_seen_at, user_agent, ip_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id("ses"), user_id, hash_token(token), random_token(24), expires, now, now, user_agent, ip_hash),
    )
    audit(conn, user_id, None, None, "auth.session_created", "Session created.")
    return token


def get_user_by_session(conn, token: str | None):
    if not token:
        return None, None
    session = conn.execute(
        "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
        (hash_token(token), utcnow()),
    ).fetchone()
    if not session:
        return None, None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (utcnow(), session["id"]))
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    return user, session


def destroy_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def create_email_token(conn, user_id: str, token_type: str) -> str:
    token = random_token(32)
    now = utcnow()
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO email_tokens (id, user_id, token_type, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id("emt"), user_id, token_type, hash_token(token), expires, now),
    )
    return token


def send_auth_email(conn, user, token_type: str, token: str, base_url: str) -> None:
    path = "verify-email" if token_type == "verify_email" else "reset-password"
    subject = "Verify your CedarHQ email" if token_type == "verify_email" else "Reset your CedarHQ password"
    body = f"Open this secure link to continue: {base_url}/{path}?token={token}\n\nThis local MVP writes email to the outbox."
    provider = LocalOutboxEmailProvider()
    email = provider.send(user["email"], subject, body)
    conn.execute(
        """
        INSERT INTO outbox_emails (id, provider, external_id, to_email, subject, body, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id("email"), email["provider"], email["external_id"], email["to_email"], email["subject"], email["body"], email["created_at"]),
    )


def consume_email_token(conn, token: str, token_type: str):
    row = conn.execute(
        """
        SELECT * FROM email_tokens
        WHERE token_hash = ? AND token_type = ? AND used_at IS NULL AND expires_at > ?
        """,
        (hash_token(token), token_type, utcnow()),
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE id = ?", (utcnow(), row["id"]))
    return conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()


def verify_email(conn, token: str):
    user = consume_email_token(conn, token, "verify_email")
    if not user:
        return None
    conn.execute("UPDATE users SET email_verified = 1, updated_at = ? WHERE id = ?", (utcnow(), user["id"]))
    audit(conn, user["id"], None, None, "auth.email_verified", "Email address verified.")
    return conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


def reset_password(conn, token: str, password: str):
    user = consume_email_token(conn, token, "password_reset")
    if not user:
        return None
    valid, message = validate_password_strength(password)
    if not valid:
        raise ValueError(message)
    conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(password), utcnow(), user["id"]),
    )
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    audit(conn, user["id"], None, None, "auth.password_reset", "Password reset completed.")
    return conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


def seed_demo(conn) -> None:
    ensure_reference_data(conn)
    for email, name, role in [
        ("founder@cedarhq.local", "Demo Founder", "founder"),
        ("ops@cedarhq.local", "Operations Reviewer", "staff"),
        ("admin@cedarhq.local", "CedarHQ Admin", "admin"),
    ]:
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            continue
        create_user(conn, email, "ChangeMe123!", name, role=role, verified=True)


def ensure_user_company(conn, user_id: str):
    company = conn.execute(
        """
        SELECT c.* FROM companies c
        JOIN company_members cm ON cm.company_id = c.id
        WHERE cm.user_id = ?
        ORDER BY c.created_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if company:
        return company
    now = utcnow()
    company_id = new_id("cmp")
    conn.execute(
        """
        INSERT INTO companies (id, owner_user_id, status, created_at, updated_at)
        VALUES (?, ?, 'draft', ?, ?)
        """,
        (company_id, user_id, now, now),
    )
    conn.execute(
        "INSERT INTO company_members (id, company_id, user_id, role, created_at) VALUES (?, ?, ?, 'founder', ?)",
        (new_id("mem"), company_id, user_id, now),
    )
    conn.execute(
        """
        INSERT INTO onboarding_progress (id, user_id, company_id, current_step, data_json, updated_at)
        VALUES (?, ?, ?, 'quiz', '{}', ?)
        """,
        (new_id("onb"), user_id, company_id, now),
    )
    audit(conn, user_id, company_id, None, "company.draft_created", "Draft company workspace created.")
    return conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()


def get_onboarding(conn, user_id: str) -> dict[str, Any]:
    company = ensure_user_company(conn, user_id)
    progress = conn.execute("SELECT * FROM onboarding_progress WHERE user_id = ?", (user_id,)).fetchone()
    data = parse_json(progress["data_json"], {}) if progress else {}
    recommended_entity, recommendation_reason = entity_recommendation(data)
    plans = conn.execute("SELECT * FROM plans ORDER BY service_fee_cents").fetchall()
    selected_state = data.get("state_code") or company["state_code"] or "DE"
    selected_plan = data.get("plan_slug") or "formation_only"
    return {
        "company": company,
        "progress": progress,
        "data": data,
        "plans": plans,
        "states": STATE_OPTIONS,
        "recommendation": recommended_entity,
        "recommendation_reason": recommendation_reason,
        "cost": calculate_cost(conn, selected_state, selected_plan),
    }


def save_onboarding(conn, user_id: str, payload: dict[str, str]) -> dict[str, Any]:
    company = ensure_user_company(conn, user_id)
    progress = conn.execute("SELECT * FROM onboarding_progress WHERE user_id = ?", (user_id,)).fetchone()
    data = parse_json(progress["data_json"], {}) if progress else {}
    allowed = {
        "venture_funding",
        "issue_equity",
        "pass_through_tax",
        "multiple_owners",
        "international_founder",
        "entity_type",
        "state_code",
        "name_choice_1",
        "name_choice_2",
        "name_choice_3",
        "business_purpose",
        "industry",
        "share_count",
        "founder_full_name",
        "founder_email",
        "founder_ownership_percent",
        "founder_shares",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country",
        "plan_slug",
        "current_step",
    }
    for key, value in payload.items():
        if key in allowed:
            data[key] = str(value).strip()
    recommended_entity, _ = entity_recommendation(data)
    entity_type = data.get("entity_type") or recommended_entity
    state_code = data.get("state_code") if data.get("state_code") in STATE_OPTIONS else company["state_code"]
    share_count = _safe_int(data.get("share_count")) or (10_000_000 if entity_type == "c_corp" else None)
    now = utcnow()
    conn.execute(
        """
        UPDATE companies
        SET legal_name = COALESCE(NULLIF(?, ''), legal_name),
            entity_type = ?,
            state_code = COALESCE(?, state_code),
            business_purpose = COALESCE(NULLIF(?, ''), business_purpose),
            industry = COALESCE(NULLIF(?, ''), industry),
            address_line1 = COALESCE(NULLIF(?, ''), address_line1),
            address_line2 = COALESCE(NULLIF(?, ''), address_line2),
            city = COALESCE(NULLIF(?, ''), city),
            region = COALESCE(NULLIF(?, ''), region),
            postal_code = COALESCE(NULLIF(?, ''), postal_code),
            country = COALESCE(NULLIF(?, ''), country),
            name_choice_1 = COALESCE(NULLIF(?, ''), name_choice_1),
            name_choice_2 = COALESCE(NULLIF(?, ''), name_choice_2),
            name_choice_3 = COALESCE(NULLIF(?, ''), name_choice_3),
            share_count = COALESCE(?, share_count),
            updated_at = ?
        WHERE id = ?
        """,
        (
            data.get("name_choice_1", ""),
            entity_type,
            state_code,
            data.get("business_purpose", ""),
            data.get("industry", ""),
            data.get("address_line1", ""),
            data.get("address_line2", ""),
            data.get("city", ""),
            data.get("region", ""),
            data.get("postal_code", ""),
            data.get("country", ""),
            data.get("name_choice_1", ""),
            data.get("name_choice_2", ""),
            data.get("name_choice_3", ""),
            share_count,
            now,
            company["id"],
        ),
    )
    current_step = data.get("current_step", "quiz")
    conn.execute(
        """
        UPDATE onboarding_progress
        SET current_step = ?, data_json = ?, updated_at = ?
        WHERE user_id = ?
        """,
        (current_step, json.dumps(data, sort_keys=True), now, user_id),
    )
    _upsert_founder(conn, company["id"], user_id, data)
    audit(conn, user_id, company["id"], None, "onboarding.autosaved", f"Onboarding autosaved at {current_step}.")
    return get_onboarding(conn, user_id)


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _upsert_founder(conn, company_id: str, user_id: str, data: dict[str, str]) -> None:
    full_name = data.get("founder_full_name", "").strip()
    email = normalize_email(data.get("founder_email", ""))
    if not full_name or not email:
        return
    now = utcnow()
    ownership = _safe_float(data.get("founder_ownership_percent")) or 100.0
    shares = _safe_int(data.get("founder_shares"))
    row = conn.execute("SELECT id FROM company_founders WHERE company_id = ? AND user_id = ?", (company_id, user_id)).fetchone()
    values = (
        full_name,
        email,
        ownership,
        shares,
        data.get("address_line1"),
        data.get("city"),
        data.get("region"),
        data.get("postal_code"),
        data.get("country"),
        now,
    )
    if row:
        conn.execute(
            """
            UPDATE company_founders
            SET full_name = ?, email = ?, ownership_percent = ?, shares = ?,
                address_line1 = ?, city = ?, region = ?, postal_code = ?, country = ?, updated_at = ?
            WHERE id = ?
            """,
            (*values, row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO company_founders (
              id, company_id, user_id, full_name, email, ownership_percent, shares,
              address_line1, city, region, postal_code, country, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("fnd"), company_id, user_id, *values[:-1], now, now),
        )


def calculate_cost(conn, state_code: str, plan_slug: str) -> dict[str, Any]:
    state = STATE_OPTIONS.get(state_code, STATE_OPTIONS["DE"])
    plan = conn.execute("SELECT * FROM plans WHERE slug = ?", (plan_slug,)).fetchone()
    if not plan:
        plan = conn.execute("SELECT * FROM plans WHERE slug = 'formation_only'").fetchone()
    state_fee = state["fee_cents"]
    first_year = plan["service_fee_cents"] + state_fee
    renewal = plan["renewal_fee_cents"]
    return {
        "state": state,
        "state_code": state_code if state_code in STATE_OPTIONS else "DE",
        "plan": plan,
        "lines": [
            {"label": f"{plan['name']} first-year service", "amount_cents": plan["service_fee_cents"]},
            {"label": f"Estimated {state['name']} state filing fee", "amount_cents": state_fee},
        ],
        "first_year_cents": first_year,
        "renewal_cents": renewal,
        "renewal_note": "Renewal excludes variable government taxes, franchise taxes, postage, banking partner fees, and rejected-filing cure costs.",
    }


def validate_checkout_ready(conn, user_id: str) -> tuple[Any, Any, dict[str, Any]]:
    ctx = get_onboarding(conn, user_id)
    company = ctx["company"]
    data = ctx["data"]
    required = [
        ("state_code", "Choose a formation state."),
        ("name_choice_1", "Enter at least one company name choice."),
        ("business_purpose", "Describe the business purpose."),
        ("founder_full_name", "Enter founder full name."),
        ("founder_email", "Enter founder email."),
        ("address_line1", "Enter founder address."),
        ("city", "Enter city."),
        ("country", "Enter country."),
        ("plan_slug", "Choose a plan."),
    ]
    missing = [message for key, message in required if not (data.get(key) or company[key] if key in company.keys() else data.get(key))]
    if missing:
        raise ValueError(" ".join(missing))
    return company, data, calculate_cost(conn, data.get("state_code") or company["state_code"], data.get("plan_slug") or "formation_only")


def create_checkout_and_order(conn, user, base_url: str):
    company, data, cost = validate_checkout_ready(conn, user["id"])
    existing = conn.execute(
        """
        SELECT * FROM formation_orders
        WHERE company_id = ? AND status != 'cancelled'
        ORDER BY created_at DESC LIMIT 1
        """,
        (company["id"],),
    ).fetchone()
    if existing:
        return existing
    checkout = SandboxCheckoutProvider().create_paid_checkout(cost["first_year_cents"], "USD", user["email"])
    payment_id = new_id("pay")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO payments (
          id, user_id, company_id, provider, status, amount_cents, currency, receipt_id,
          external_id, is_sandbox, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            payment_id,
            user["id"],
            company["id"],
            checkout.provider,
            checkout.status,
            cost["first_year_cents"],
            "USD",
            checkout.receipt_id,
            checkout.external_id,
            now,
            now,
        ),
    )
    insert_provider_event(conn, checkout)
    plan_id = cost["plan"]["id"]
    order_id = new_id("ord")
    conn.execute(
        """
        INSERT INTO formation_orders (
          id, company_id, user_id, plan_id, payment_id, status, entity_type, state_code,
          total_first_year_cents, total_renewal_cents, state_fee_cents, service_fee_cents,
          renewal_fee_cents, sandbox, created_at, submitted_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            order_id,
            company["id"],
            user["id"],
            plan_id,
            payment_id,
            data.get("entity_type") or company["entity_type"] or "llc",
            data.get("state_code") or company["state_code"] or "DE",
            cost["first_year_cents"],
            cost["renewal_cents"],
            cost["state"]["fee_cents"],
            cost["plan"]["service_fee_cents"],
            cost["plan"]["renewal_fee_cents"],
            now,
            now,
            now,
        ),
    )
    for index, (step_key, label, responsible) in enumerate(FORMATION_STEPS, start=1):
        conn.execute(
            """
            INSERT INTO formation_steps (id, order_id, step_key, label, responsible_party, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("stp"), order_id, step_key, label, responsible, index),
        )
    evidence = create_evidence(
        conn,
        company_id=company["id"],
        order_id=order_id,
        title="Information received receipt",
        description="Founder intake, plan selection, cost review, and sandbox payment were received.",
        content=build_intake_summary(user, company, data, cost, checkout.receipt_id),
        receipt_id=checkout.receipt_id,
        provider="sandbox_checkout",
        created_by=user["id"],
        simulated=True,
    )
    complete_formation_step(conn, order_id, "information_received", user["id"], evidence["id"], evidence["receipt_id"])
    create_document(
        conn,
        company["id"],
        order_id,
        "Formation order receipt",
        "receipt",
        "generated",
        "sandbox_checkout",
        evidence["content"],
        user["id"],
        evidence_id=evidence["id"],
        simulated=True,
    )
    create_document(
        conn,
        company["id"],
        order_id,
        "Founder intake packet",
        "formation",
        "generated",
        "cedarhq_intake",
        build_founder_packet(company, data),
        user["id"],
        evidence_id=evidence["id"],
        simulated=False,
    )
    seed_compliance_items(conn, company["id"], order_id, user["id"])
    conn.execute("UPDATE companies SET status = 'formation_ordered', updated_at = ? WHERE id = ?", (utcnow(), company["id"]))
    audit(conn, user["id"], company["id"], order_id, "formation.order_created", "Sandbox formation order created after transparent cost review.")
    return conn.execute("SELECT * FROM formation_orders WHERE id = ?", (order_id,)).fetchone()


def build_intake_summary(user, company, data: dict[str, str], cost: dict[str, Any], receipt_id: str) -> str:
    return "\n".join(
        [
            "CedarHQ sandbox formation receipt",
            f"Receipt: {receipt_id}",
            "Simulation notice: This confirms CedarHQ recorded a sandbox checkout. It is not a government filing.",
            f"Founder: {user['name']} <{user['email']}>",
            f"Company name choice 1: {data.get('name_choice_1') or company['name_choice_1']}",
            f"Entity type: {(data.get('entity_type') or company['entity_type'] or 'llc').upper()}",
            f"State: {cost['state']['name']}",
            f"Plan: {cost['plan']['name']}",
            f"First-year total: {cents(cost['first_year_cents'])}",
            f"Renewal estimate: {cents(cost['renewal_cents'])}",
            f"Created at: {utcnow()}",
        ]
    )


def build_founder_packet(company, data: dict[str, str]) -> str:
    return "\n".join(
        [
            "Founder intake packet",
            "This document is generated from founder-provided onboarding data.",
            f"Preferred legal name: {data.get('name_choice_1') or company['name_choice_1'] or 'Not provided'}",
            f"Alternate names: {data.get('name_choice_2') or 'Not provided'}; {data.get('name_choice_3') or 'Not provided'}",
            f"Business purpose: {data.get('business_purpose') or company['business_purpose'] or 'Not provided'}",
            f"Industry: {data.get('industry') or company['industry'] or 'Not provided'}",
            f"Founder: {data.get('founder_full_name') or 'Not provided'}",
            f"Founder ownership: {data.get('founder_ownership_percent') or '100'}%",
            f"Address: {data.get('address_line1') or ''}, {data.get('city') or ''}, {data.get('country') or ''}",
        ]
    )


def create_evidence(
    conn,
    company_id: str | None,
    order_id: str | None,
    title: str,
    description: str,
    content: str,
    receipt_id: str,
    provider: str,
    created_by: str | None,
    simulated: bool,
):
    evidence_id = new_id("evd")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO evidence_files (
          id, company_id, order_id, title, description, storage_key, content, mime_type,
          receipt_id, provider, is_simulated, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'text/plain', ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            company_id,
            order_id,
            title,
            description,
            f"evidence/{evidence_id}.txt",
            content,
            receipt_id,
            provider,
            int(simulated),
            created_by,
            now,
        ),
    )
    return conn.execute("SELECT * FROM evidence_files WHERE id = ?", (evidence_id,)).fetchone()


def create_document(
    conn,
    company_id: str,
    order_id: str | None,
    title: str,
    category: str,
    status: str,
    source: str,
    content: str,
    created_by: str | None,
    evidence_id: str | None = None,
    simulated: bool = False,
):
    document_id = new_id("doc")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO documents (
          id, company_id, order_id, title, category, status, current_version,
          source, evidence_id, is_simulated, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (document_id, company_id, order_id, title, category, status, source, evidence_id, int(simulated), now, now),
    )
    conn.execute(
        """
        INSERT INTO document_versions (id, document_id, version, content, mime_type, created_by, created_at)
        VALUES (?, ?, 1, ?, 'text/plain', ?, ?)
        """,
        (new_id("dov"), document_id, content, created_by, now),
    )
    audit(conn, created_by, company_id, order_id, "document.created", f"Document created: {title}.")
    return conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def complete_formation_step(conn, order_id: str, step_key: str, actor_user_id: str | None, evidence_id: str, receipt_id: str):
    order = conn.execute("SELECT * FROM formation_orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        raise ValueError("Formation order not found.")
    validate_formation_transition(order["status"], step_key, evidence_id=evidence_id)
    step = conn.execute("SELECT * FROM formation_steps WHERE order_id = ? AND step_key = ?", (order_id, step_key)).fetchone()
    if not step:
        raise ValueError("Formation step not found.")
    evidence = conn.execute("SELECT * FROM evidence_files WHERE id = ?", (evidence_id,)).fetchone()
    if not evidence:
        raise ValueError("Evidence not found.")
    now = utcnow()
    conn.execute(
        """
        UPDATE formation_steps
        SET status = 'completed', completed_at = ?, actor_user_id = ?, receipt_id = ?, evidence_id = ?, blocked_reason = NULL
        WHERE id = ?
        """,
        (now, actor_user_id, receipt_id, evidence_id, step["id"]),
    )
    conn.execute(
        "UPDATE formation_orders SET status = ?, blocked_reason = NULL, updated_at = ? WHERE id = ?",
        (step_key, now, order_id),
    )
    audit(conn, actor_user_id, order["company_id"], order_id, "formation.step_completed", f"{step['label']} completed with evidence {receipt_id}.")
    return conn.execute("SELECT * FROM formation_orders WHERE id = ?", (order_id,)).fetchone()


def insert_provider_event(conn, result: ProviderResult) -> None:
    conn.execute(
        """
        INSERT INTO provider_events (id, provider, service, event_type, external_id, status, payload_json, is_simulated, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("pev"),
            result.provider,
            result.service,
            result.event_type,
            result.external_id,
            result.status,
            serialize_provider_payload(result),
            int(result.is_simulated),
            utcnow(),
        ),
    )


def seed_compliance_items(conn, company_id: str, order_id: str, actor_user_id: str) -> None:
    if conn.execute("SELECT id FROM compliance_items WHERE company_id = ?", (company_id,)).fetchone():
        return
    now_dt = datetime.now(timezone.utc)
    rows = [
        (
            "Beneficial ownership exemption review",
            "beneficial_ownership",
            "action_required",
            now_dt + timedelta(days=14),
            "Founder and CedarHQ compliance",
            "Confirm whether the entity is US-formed, foreign-registered, or otherwise exempt before any BOI-related action.",
            "FinCEN BOI final rule effective 2026-08-14; production must reverify against current FinCEN and counsel-approved rules.",
        ),
        (
            "Registered agent renewal",
            "registered_agent",
            "upcoming",
            now_dt + timedelta(days=365),
            "CedarHQ compliance",
            "Maintain registered-agent coverage and renewal evidence.",
            "Sandbox registered-agent coverage rule.",
        ),
        (
            "State annual report planning",
            "annual_report",
            "upcoming",
            now_dt + timedelta(days=300),
            "CedarHQ compliance",
            "Prepare annual-report or franchise-tax task list based on final state approval date.",
            "Sandbox state compliance calendar estimate.",
        ),
        (
            "Federal tax readiness checklist",
            "tax",
            "upcoming",
            now_dt + timedelta(days=180),
            "Founder and accountant",
            "Collect receipts, ownership records, bookkeeping exports, and tax questionnaire answers.",
            "Sandbox federal tax readiness workflow.",
        ),
    ]
    for title, category, status, due_dt, responsible, description, source_rule in rows:
        item_id = new_id("cmpc")
        due = due_dt.date().isoformat()
        now = utcnow()
        conn.execute(
            """
            INSERT INTO compliance_items (
              id, company_id, title, category, status, due_date, responsible_party,
              description, source_rule, next_escalation_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                company_id,
                title,
                category,
                status,
                due,
                responsible,
                description,
                source_rule,
                (due_dt - timedelta(days=14)).replace(microsecond=0).isoformat(),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO compliance_events (
              id, compliance_item_id, actor_user_id, event_type, to_status, note, created_at
            ) VALUES (?, ?, ?, 'created', ?, ?, ?)
            """,
            (new_id("cpe"), item_id, actor_user_id, status, f"Created from order {order_id}.", now),
        )


def get_latest_order_for_user(conn, user_id: str):
    return conn.execute(
        """
        SELECT fo.*, c.legal_name, c.name_choice_1, p.name AS plan_name
        FROM formation_orders fo
        JOIN companies c ON c.id = fo.company_id
        JOIN plans p ON p.id = fo.plan_id
        WHERE fo.user_id = ?
        ORDER BY fo.created_at DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()


def get_order(conn, order_id: str):
    return conn.execute(
        """
        SELECT fo.*, c.legal_name, c.name_choice_1, c.business_purpose, c.industry,
               c.address_line1, c.city, c.country, p.name AS plan_name, u.email AS customer_email
        FROM formation_orders fo
        JOIN companies c ON c.id = fo.company_id
        JOIN plans p ON p.id = fo.plan_id
        JOIN users u ON u.id = fo.user_id
        WHERE fo.id = ?
        """,
        (order_id,),
    ).fetchone()


def get_timeline(conn, order_id: str):
    return conn.execute(
        """
        SELECT fs.*, ef.title AS evidence_title, ef.is_simulated, u.name AS actor_name
        FROM formation_steps fs
        LEFT JOIN evidence_files ef ON ef.id = fs.evidence_id
        LEFT JOIN users u ON u.id = fs.actor_user_id
        WHERE fs.order_id = ?
        ORDER BY fs.sort_order
        """,
        (order_id,),
    ).fetchall()


def list_documents(conn, company_id: str, search: str = "", category: str = ""):
    params: list[Any] = [company_id]
    conditions = ["d.company_id = ?"]
    if search:
        conditions.append("(d.title LIKE ? OR dv.content LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if category:
        conditions.append("d.category = ?")
        params.append(category)
    return conn.execute(
        f"""
        SELECT d.*, dv.content, dv.mime_type
        FROM documents d
        JOIN document_versions dv ON dv.document_id = d.id AND dv.version = d.current_version
        WHERE {' AND '.join(conditions)}
        ORDER BY d.updated_at DESC
        """,
        params,
    ).fetchall()


def get_document_for_user(conn, document_id: str, user) -> Any:
    doc = conn.execute(
        """
        SELECT d.*, dv.content, dv.mime_type
        FROM documents d
        JOIN document_versions dv ON dv.document_id = d.id AND dv.version = d.current_version
        WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()
    if not doc:
        return None
    if user["role"] in OPS_ROLES:
        return doc
    allowed = conn.execute(
        "SELECT 1 FROM company_members WHERE company_id = ? AND user_id = ?",
        (doc["company_id"], user["id"]),
    ).fetchone()
    return doc if allowed else None


def list_compliance(conn, company_id: str):
    return conn.execute(
        "SELECT * FROM compliance_items WHERE company_id = ? ORDER BY due_date ASC",
        (company_id,),
    ).fetchall()


def get_dashboard_context(conn, user_id: str) -> dict[str, Any] | None:
    order = get_latest_order_for_user(conn, user_id)
    if not order:
        return None

    company = conn.execute("SELECT * FROM companies WHERE id = ?", (order["company_id"],)).fetchone()
    founder = conn.execute(
        "SELECT * FROM company_founders WHERE company_id = ? ORDER BY created_at LIMIT 1",
        (order["company_id"],),
    ).fetchone()
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (order["plan_id"],)).fetchone()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (order["payment_id"],)).fetchone()
    timeline = get_timeline(conn, order["id"])
    documents = list_documents(conn, order["company_id"])
    compliance = list_compliance(conn, order["company_id"])
    support = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END) AS open_count
        FROM support_tickets
        WHERE company_id = ?
        """,
        (order["company_id"],),
    ).fetchone()

    completed_steps = [
        step
        for step in timeline
        if step["status"] == "completed"
        and step["completed_at"]
        and step["receipt_id"]
        and step["evidence_id"]
    ]
    next_step = next((step for step in timeline if step["status"] != "completed"), None)
    attention_items = [
        item for item in compliance if item["status"] in {"action_required", "rejected", "overdue"}
    ]

    return {
        "company": company,
        "founder": founder,
        "order": order,
        "plan": plan,
        "payment": payment,
        "timeline": timeline,
        "documents": documents,
        "compliance": compliance,
        "support": support,
        "completed_steps": completed_steps,
        "next_step": next_step,
        "attention_items": attention_items,
        "progress_percent": round((len(completed_steps) / len(timeline)) * 100) if timeline else 0,
    }


def list_orders_for_ops(conn):
    return conn.execute(
        """
        SELECT fo.*, c.name_choice_1, c.legal_name, u.email AS customer_email, p.name AS plan_name
        FROM formation_orders fo
        JOIN companies c ON c.id = fo.company_id
        JOIN users u ON u.id = fo.user_id
        JOIN plans p ON p.id = fo.plan_id
        ORDER BY
          CASE fo.status
            WHEN 'blocked' THEN 0
            WHEN 'information_received' THEN 1
            WHEN 'operations_review' THEN 2
            ELSE 3
          END,
          fo.created_at ASC
        """
    ).fetchall()


def ops_transition_order(conn, order_id: str, actor_user_id: str, action: str, note: str = ""):
    order = get_order(conn, order_id)
    if not order:
        raise ValueError("Order not found.")
    provider = SandboxFormationProvider()
    mapping = {
        "complete_review": ("operations_review", "Operations review memo", "review", "review.completed"),
        "prepare_state_packet": ("state_submission_ready", "State filing readiness packet", "formation", "state_packet.ready"),
        "submit_state_sandbox": ("state_submitted", "Sandbox state submission receipt", "state", "state.submitted"),
        "approve_state_sandbox": ("state_approved", "Sandbox state approval evidence", "state", "state.approved"),
        "submit_ein_sandbox": ("ein_submitted", "Sandbox EIN submission receipt", "ein", "ein.submitted"),
        "receive_ein_sandbox": ("ein_received", "Sandbox EIN confirmation", "ein", "ein.received"),
        "mark_bank_ready": ("bank_ready", "Bank-ready package evidence", "banking", "bank_ready.completed"),
    }
    if action == "block_order":
        now = utcnow()
        conn.execute(
            "UPDATE formation_orders SET status = 'blocked', blocked_reason = ?, updated_at = ? WHERE id = ?",
            (note or "Additional founder action required.", now, order_id),
        )
        next_step = conn.execute(
            "SELECT id FROM formation_steps WHERE order_id = ? AND status != 'completed' ORDER BY sort_order LIMIT 1",
            (order_id,),
        ).fetchone()
        if next_step:
            conn.execute("UPDATE formation_steps SET status = 'blocked', blocked_reason = ? WHERE id = ?", (note, next_step["id"]))
        audit(conn, actor_user_id, order["company_id"], order_id, "formation.blocked", note or "Order blocked by operations.")
        return get_order(conn, order_id)
    if action not in mapping:
        raise ValueError("Unsupported operations action.")
    target, evidence_title, service, event_type = mapping[action]
    result = provider.receipt(service, event_type, order_id, {"note": note})
    insert_provider_event(conn, result)
    content = build_ops_evidence(order, target, result.receipt_id, note)
    evidence = create_evidence(
        conn,
        company_id=order["company_id"],
        order_id=order_id,
        title=evidence_title,
        description=f"Evidence for {target.replace('_', ' ')}.",
        content=content,
        receipt_id=result.receipt_id,
        provider=result.provider,
        created_by=actor_user_id,
        simulated=True,
    )
    updated = complete_formation_step(conn, order_id, target, actor_user_id, evidence["id"], result.receipt_id)
    if target == "state_approved":
        create_document(
            conn,
            order["company_id"],
            order_id,
            "Sandbox articles approval packet",
            "articles",
            "generated",
            "sandbox_formation",
            content,
            actor_user_id,
            evidence_id=evidence["id"],
            simulated=True,
        )
    if target == "ein_received":
        create_document(
            conn,
            order["company_id"],
            order_id,
            "Sandbox EIN letter",
            "ein_letter",
            "generated",
            "sandbox_ein",
            content,
            actor_user_id,
            evidence_id=evidence["id"],
            simulated=True,
        )
    if target == "bank_ready":
        create_document(
            conn,
            order["company_id"],
            order_id,
            "Bank-ready resolutions and checklist",
            "resolution",
            "generated",
            "cedarhq_operations",
            content + "\n\nBanking partner approval is never guaranteed and belongs to the banking partner.",
            actor_user_id,
            evidence_id=evidence["id"],
            simulated=False,
        )
    return updated


def build_ops_evidence(order, target: str, receipt_id: str, note: str) -> str:
    return "\n".join(
        [
            f"CedarHQ operations evidence: {target.replace('_', ' ').title()}",
            f"Receipt: {receipt_id}",
            "Simulation notice: This sandbox evidence is for product workflow validation only.",
            f"Company: {order['name_choice_1'] or order['legal_name']}",
            f"Entity: {order['entity_type'].upper()} in {order['state_code']}",
            f"Order: {order['id']}",
            f"Staff note: {note or 'No additional note.'}",
            f"Recorded at: {utcnow()}",
        ]
    )


def process_reminders(conn, actor_user_id: str | None = None) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(days=14)).date().isoformat()
    overdue = now.date().isoformat()
    action_required = conn.execute(
        """
        UPDATE compliance_items
        SET status = 'action_required', updated_at = ?
        WHERE status = 'upcoming' AND due_date <= ?
        """,
        (utcnow(), soon),
    ).rowcount
    overdue_count = conn.execute(
        """
        UPDATE compliance_items
        SET status = 'overdue', updated_at = ?
        WHERE status IN ('upcoming', 'action_required', 'rejected') AND due_date < ?
        """,
        (utcnow(), overdue),
    ).rowcount
    audit(conn, actor_user_id, None, None, "jobs.reminders_processed", f"Processed reminders: {action_required} action required, {overdue_count} overdue.")
    return {"action_required": action_required, "overdue": overdue_count}


def audit(conn, actor_user_id: str | None, company_id: str | None, order_id: str | None, event_type: str, summary: str, metadata: dict[str, Any] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO activity_logs (id, actor_user_id, company_id, order_id, event_type, summary, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id("log"), actor_user_id, company_id, order_id, event_type, summary, json.dumps(metadata or {}, sort_keys=True), utcnow()),
    )
