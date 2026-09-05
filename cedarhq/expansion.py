from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import utcnow
from .services import audit, create_document, create_evidence, new_id
from .status import (
    FOREIGN_QUALIFICATION_TRANSITIONS,
    MAIL_ITEM_TRANSITIONS,
    PARTNER_APPLICATION_TRANSITIONS,
    SALES_TAX_RETURN_TRANSITIONS,
    validate_transition,
)


MAIL_ADDRESS_OPTIONS = [
    ("Downtown desk", "214 North Market Street, Suite 400", "Wilmington", "DE", "19801", 3500),
    ("Mountain desk", "30 North Gould Street, Suite R", "Sheridan", "WY", "82801", 3500),
    ("Coastal desk", "100 South Ashley Drive, Suite 600", "Tampa", "FL", "33602", 5000),
]

PARTNER_APPLICATIONS = [
    (
        "banking",
        "Northstar Banking Partner",
        [
            "Formation approval or existing company certificate",
            "EIN confirmation letter",
            "Founder identity and address",
            "Ownership information",
        ],
        "Approval belongs to the banking partner. CedarHQ can prepare and transmit a sandbox checklist but never guarantees approval.",
    ),
    (
        "payments",
        "Stripe sandbox processor",
        [
            "Business website or product description",
            "Expected processing volume",
            "Beneficial owner information",
            "Bank-ready account details",
        ],
        "Payment processor onboarding, risk review, reserve rules, and account approval are controlled by the processor.",
    ),
    (
        "payroll",
        "Payroll registration partner",
        [
            "Operating states",
            "First payroll date",
            "Officer and employee details",
            "State withholding registration needs",
        ],
        "Payroll and tax registrations are simulated here until production provider credentials are connected.",
    ),
]

REWARD_ROWS = [
    ("Cloud credits bundle", "Infrastructure", 1000000, "Available after formation evidence and active company record."),
    ("Startup legal office hours", "Advisory", 75000, "Available to founders with a completed intake packet."),
    ("Commerce tooling credits", "E-commerce", 250000, "Best fit for companies that connect Shopify or Amazon data."),
    ("Investor readiness review", "Discovery", 150000, "Requires explicit founder opt-in before any profile sharing."),
]

SALES_TAX_STATE_ROWS = [
    ("CA", 50000000, 41800000, 142),
    ("NY", 50000000, 11700000, 54),
    ("TX", 50000000, 23800000, 87),
]


def ensure_operating_services(conn, company_id: str, actor_user_id: str | None = None) -> None:
    now = utcnow()
    for label, line1, city, state, postal, fee in MAIL_ADDRESS_OPTIONS:
        conn.execute(
            """
            INSERT OR IGNORE INTO mail_addresses (
              id, company_id, label, address_line1, city, state_code, postal_code,
              monthly_fee_cents, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("mad"), company_id, label, line1, city, state, postal, fee, now, now),
        )
    order = conn.execute(
        "SELECT * FROM formation_orders WHERE company_id = ? ORDER BY created_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    if order:
        renewal = (datetime.now(timezone.utc).date() + timedelta(days=365)).isoformat()
        evidence = _ensure_service_evidence(
            conn,
            company_id,
            order["id"],
            "Registered-agent sandbox coverage",
            f"Sandbox registered-agent coverage for {order['state_code']} through {renewal}.",
            actor_user_id,
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO registered_agent_services (
              id, company_id, state_code, renewal_date, evidence_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("ras"), company_id, order["state_code"], renewal, evidence["id"], now, now),
        )
    for partner_type, partner_name, checklist, disclaimer in PARTNER_APPLICATIONS:
        conn.execute(
            """
            INSERT OR IGNORE INTO partner_applications (
              id, company_id, partner_type, partner_name, checklist_json, disclaimer, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("pap"), company_id, partner_type, partner_name, json.dumps(checklist), disclaimer, now, now),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO discovery_profiles (
          id, company_id, founder_headline, target_investor, created_at, updated_at
        ) VALUES (?, ?, '', '', ?, ?)
        """,
        (new_id("dsp"), company_id, now, now),
    )
    for title, category, value, eligibility in REWARD_ROWS:
        conn.execute(
            """
            INSERT OR IGNORE INTO partner_rewards (
              id, title, category, estimated_value_cents, eligibility, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("rew"), title, category, value, eligibility, now),
        )


def _ensure_service_evidence(conn, company_id: str, order_id: str | None, title: str, content: str, actor_user_id: str | None):
    existing = conn.execute(
        "SELECT * FROM evidence_files WHERE company_id = ? AND title = ? ORDER BY created_at DESC LIMIT 1",
        (company_id, title),
    ).fetchone()
    if existing:
        return existing
    receipt_id = f"SBOX-SVC-{new_id('rcpt').upper()}"
    return create_evidence(
        conn,
        company_id,
        order_id,
        title,
        "Sandbox service evidence. This is not a government notice or filing receipt.",
        content + f"\nReceipt: {receipt_id}\nCreated: {utcnow()}",
        receipt_id,
        "sandbox_operations",
        actor_user_id,
        True,
    )


def mailroom_context(conn, company_id: str) -> dict[str, Any]:
    ensure_operating_services(conn, company_id)
    addresses = conn.execute(
        "SELECT * FROM mail_addresses WHERE company_id = ? ORDER BY selected_at DESC, monthly_fee_cents, label",
        (company_id,),
    ).fetchall()
    selected = next((row for row in addresses if row["selected_at"]), None)
    items = conn.execute(
        """
        SELECT mi.*, ma.label AS address_label, d.title AS scan_title
        FROM mail_items mi
        LEFT JOIN mail_addresses ma ON ma.id = mi.mail_address_id
        LEFT JOIN documents d ON d.id = mi.scan_document_id
        WHERE mi.company_id = ?
        ORDER BY mi.created_at DESC
        """,
        (company_id,),
    ).fetchall()
    events = conn.execute(
        """
        SELECT me.*, mi.sender
        FROM mail_events me
        JOIN mail_items mi ON mi.id = me.mail_item_id
        WHERE mi.company_id = ?
        ORDER BY me.created_at DESC LIMIT 20
        """,
        (company_id,),
    ).fetchall()
    return {"addresses": addresses, "selected": selected, "items": items, "events": events}


def choose_mail_address(conn, company_id: str, address_id: str, actor_user_id: str) -> None:
    address = conn.execute(
        "SELECT * FROM mail_addresses WHERE id = ? AND company_id = ?",
        (address_id, company_id),
    ).fetchone()
    if not address:
        raise ValueError("Choose an available business address.")
    now = utcnow()
    conn.execute("UPDATE mail_addresses SET selected_at = NULL, updated_at = ? WHERE company_id = ?", (now, company_id))
    conn.execute(
        "UPDATE mail_addresses SET selected_at = ?, form_1583_status = 'notarization_required', updated_at = ? WHERE id = ?",
        (now, now, address_id),
    )
    if not conn.execute("SELECT id FROM mail_items WHERE company_id = ?", (company_id,)).fetchone():
        for sender, mail_type in [("Delaware Division of Corporations", "notice"), ("Northstar Banking Partner", "letter")]:
            item_id = new_id("mai")
            conn.execute(
                """
                INSERT INTO mail_items (
                  id, company_id, mail_address_id, status, sender, mail_type, recipient_name,
                  tracking_number, created_at, updated_at
                ) VALUES (?, ?, ?, 'received', ?, ?, 'Founder', ?, ?, ?)
                """,
                (item_id, company_id, address_id, sender, mail_type, f"SBOX-MAIL-{item_id[-8:].upper()}", now, now),
            )
            conn.execute(
                "INSERT INTO mail_events (id, mail_item_id, actor_user_id, to_status, note, created_at) VALUES (?, ?, ?, 'received', ?, ?)",
                (new_id("mev"), item_id, actor_user_id, "Sandbox mail received at selected address.", now),
            )
    audit(conn, actor_user_id, company_id, None, "mailroom.address_selected", f"Selected virtual mail address {address['label']}.")


def request_mail_action(conn, company_id: str, item_id: str, actor_user_id: str, action: str) -> None:
    mapping = {
        "scan": "scan_requested",
        "forward": "forward_requested",
        "archive": "archive_requested",
        "recycle": "recycle_requested",
    }
    target = mapping.get(action)
    if not target:
        raise ValueError("Choose a supported mail action.")
    item = conn.execute("SELECT * FROM mail_items WHERE id = ? AND company_id = ?", (item_id, company_id)).fetchone()
    if not item:
        raise ValueError("Mail item not found.")
    validate_transition(MAIL_ITEM_TRANSITIONS, item["status"], target)
    now = utcnow()
    cost = 1295 if target == "forward_requested" else item["forwarding_cost_cents"]
    conn.execute(
        "UPDATE mail_items SET status = ?, action_requested_at = ?, forwarding_cost_cents = ?, updated_at = ? WHERE id = ?",
        (target, now, cost, now, item_id),
    )
    conn.execute(
        "INSERT INTO mail_events (id, mail_item_id, actor_user_id, from_status, to_status, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id("mev"), item_id, actor_user_id, item["status"], target, f"Founder requested {action}.", now),
    )
    audit(conn, actor_user_id, company_id, None, "mailroom.action_requested", f"Mail item moved to {target}.")


def ops_mailroom_context(conn) -> list[Any]:
    return conn.execute(
        """
        SELECT mi.*, c.name_choice_1, c.legal_name, ma.label AS address_label
        FROM mail_items mi
        JOIN companies c ON c.id = mi.company_id
        LEFT JOIN mail_addresses ma ON ma.id = mi.mail_address_id
        ORDER BY CASE mi.status
          WHEN 'scan_requested' THEN 0
          WHEN 'forward_requested' THEN 1
          WHEN 'received' THEN 2
          ELSE 3 END,
          mi.updated_at DESC
        """
    ).fetchall()


def ops_process_mail_action(conn, item_id: str, actor_user_id: str) -> None:
    item = conn.execute("SELECT * FROM mail_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise ValueError("Mail item not found.")
    target_by_status = {
        "scan_requested": "scanned",
        "forward_requested": "forwarded",
        "archive_requested": "archived",
        "recycle_requested": "recycled",
    }
    target = target_by_status.get(item["status"])
    if not target:
        raise ValueError("No staff processing action is available for this mail status.")
    validate_transition(MAIL_ITEM_TRANSITIONS, item["status"], target)
    now = utcnow()
    scan_document_id = item["scan_document_id"]
    evidence = _ensure_service_evidence(
        conn,
        item["company_id"],
        None,
        f"Mailroom {target} receipt {item_id}",
        f"Sandbox mail item {item_id} moved from {item['status']} to {target}.",
        actor_user_id,
    )
    if target == "scanned":
        document = create_document(
            conn,
            item["company_id"],
            None,
            f"Scanned mail from {item['sender'] or 'sender'}",
            "mail",
            "generated",
            "sandbox_mailroom",
            f"Sandbox scanned mail\nSender: {item['sender']}\nTracking: {item['tracking_number']}\nCreated: {now}",
            actor_user_id,
            evidence_id=evidence["id"],
            simulated=True,
        )
        scan_document_id = document["id"]
    conn.execute(
        """
        UPDATE mail_items SET status = ?, processed_by_user_id = ?, scan_document_id = ?,
          archived_at = ?, updated_at = ? WHERE id = ?
        """,
        (target, actor_user_id, scan_document_id, now if target in {"archived", "recycled"} else item["archived_at"], now, item_id),
    )
    conn.execute(
        "INSERT INTO mail_events (id, mail_item_id, actor_user_id, from_status, to_status, note, evidence_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("mev"), item_id, actor_user_id, item["status"], target, "Sandbox staff processing completed.", evidence["id"], now),
    )
    audit(conn, actor_user_id, item["company_id"], None, "mailroom.processed", f"Mail item moved to {target} with evidence.")


def registered_agent_context(conn, company_id: str) -> dict[str, Any]:
    ensure_operating_services(conn, company_id)
    services = conn.execute(
        "SELECT * FROM registered_agent_services WHERE company_id = ? ORDER BY state_code",
        (company_id,),
    ).fetchall()
    notices = conn.execute(
        """
        SELECT ran.*, d.title AS document_title
        FROM registered_agent_notices ran
        LEFT JOIN documents d ON d.id = ran.document_id
        WHERE ran.company_id = ?
        ORDER BY ran.received_at DESC
        """,
        (company_id,),
    ).fetchall()
    qualifications = conn.execute(
        "SELECT * FROM foreign_qualifications WHERE company_id = ? ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()
    return {"services": services, "notices": notices, "qualifications": qualifications}


def start_foreign_qualification(conn, company_id: str, actor_user_id: str, state_code: str, reason: str) -> None:
    state_code = state_code.strip().upper()
    if len(state_code) != 2:
        raise ValueError("Enter a two-letter state code.")
    now = utcnow()
    conn.execute(
        """
        INSERT OR IGNORE INTO foreign_qualifications (
          id, company_id, state_code, status, reason, created_at, updated_at
        ) VALUES (?, ?, ?, 'questionnaire', ?, ?, ?)
        """,
        (new_id("fqa"), company_id, state_code, reason.strip(), now, now),
    )
    audit(conn, actor_user_id, company_id, None, "registered_agent.foreign_qualification_started", f"Foreign qualification started for {state_code}.")


def partners_context(conn, company_id: str) -> dict[str, Any]:
    ensure_operating_services(conn, company_id)
    applications = conn.execute(
        "SELECT * FROM partner_applications WHERE company_id = ? ORDER BY partner_type",
        (company_id,),
    ).fetchall()
    return {"applications": applications}


def partner_application_action(conn, company_id: str, application_id: str, actor_user_id: str, action: str) -> None:
    row = conn.execute("SELECT * FROM partner_applications WHERE id = ? AND company_id = ?", (application_id, company_id)).fetchone()
    if not row:
        raise ValueError("Partner application not found.")
    target_by_action = {
        "complete_checklist": "ready_to_send",
        "send_sandbox": "sent_to_partner",
        "mark_review": "partner_review",
        "request_more_info": "more_info_required",
        "sandbox_approve": "approved",
        "sandbox_decline": "declined",
    }
    target = target_by_action.get(action)
    if not target:
        raise ValueError("Unsupported partner application action.")
    validate_transition(PARTNER_APPLICATION_TRANSITIONS, row["status"], target)
    now = utcnow()
    receipt_id = row["receipt_id"]
    evidence_id = row["evidence_id"]
    sent_at = row["sent_at"]
    decided_at = row["decided_at"]
    if target in {"sent_to_partner", "approved", "declined", "more_info_required"}:
        receipt_id = f"SBOX-PARTNER-{new_id('rcpt').upper()}"
        evidence = create_evidence(
            conn,
            company_id,
            None,
            f"{row['partner_name']} {target.replace('_', ' ')} receipt",
            "Sandbox partner application evidence. This is not an approval guarantee.",
            f"Partner: {row['partner_name']}\nStatus: {target}\nNotice: {row['disclaimer']}\nCreated: {now}",
            receipt_id,
            "sandbox_partner",
            actor_user_id,
            True,
        )
        evidence_id = evidence["id"]
    if target == "sent_to_partner":
        sent_at = now
    if target in {"approved", "declined"}:
        decided_at = now
    conn.execute(
        """
        UPDATE partner_applications SET status = ?, receipt_id = ?, evidence_id = ?,
          sent_at = ?, decided_at = ?, updated_at = ? WHERE id = ?
        """,
        (target, receipt_id, evidence_id, sent_at, decided_at, now, application_id),
    )
    audit(conn, actor_user_id, company_id, None, "partners.application_updated", f"{row['partner_name']} moved to {target}.")


def rewards_context(conn, company_id: str) -> dict[str, Any]:
    ensure_operating_services(conn, company_id)
    profile = conn.execute("SELECT * FROM discovery_profiles WHERE company_id = ?", (company_id,)).fetchone()
    rewards = conn.execute("SELECT * FROM partner_rewards ORDER BY category, title").fetchall()
    return {"profile": profile, "rewards": rewards}


def save_discovery_profile(conn, company_id: str, actor_user_id: str, payload: dict[str, str]) -> None:
    ensure_operating_services(conn, company_id, actor_user_id)
    status = "opted_in" if payload.get("permission_to_share") == "yes" else "draft"
    now = utcnow()
    conn.execute(
        """
        UPDATE discovery_profiles
        SET status = ?, founder_headline = ?, target_investor = ?, permission_to_share = ?,
            updated_by_user_id = ?, updated_at = ?
        WHERE company_id = ?
        """,
        (
            status,
            payload.get("founder_headline", "").strip()[:500],
            payload.get("target_investor", "").strip()[:500],
            1 if status == "opted_in" else 0,
            actor_user_id,
            now,
            company_id,
        ),
    )
    audit(conn, actor_user_id, company_id, None, "rewards.discovery_profile_saved", f"Discovery profile saved with status {status}.")


def sales_tax_context(conn, company_id: str) -> dict[str, Any]:
    account = conn.execute(
        "SELECT * FROM sales_tax_accounts WHERE company_id = ? ORDER BY created_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    nexus = conn.execute(
        "SELECT * FROM sales_tax_nexus WHERE company_id = ? ORDER BY state_code",
        (company_id,),
    ).fetchall()
    returns = conn.execute(
        "SELECT * FROM sales_tax_returns WHERE company_id = ? ORDER BY due_date ASC",
        (company_id,),
    ).fetchall()
    products = conn.execute(
        "SELECT * FROM sales_tax_products WHERE company_id = ? ORDER BY sku",
        (company_id,),
    ).fetchall()
    return {"account": account, "nexus": nexus, "returns": returns, "products": products}


def connect_sales_tax_sandbox(conn, company_id: str, actor_user_id: str) -> None:
    now = utcnow()
    conn.execute(
        """
        INSERT OR IGNORE INTO sales_tax_accounts (
          id, company_id, status, external_id, last_synced_at, created_at, updated_at
        ) VALUES (?, ?, 'connected', ?, ?, ?, ?)
        """,
        (new_id("sta"), company_id, f"sandbox-sales-{company_id[-8:]}", now, now, now),
    )
    today = datetime.now(timezone.utc).date()
    period = today.strftime("%Y-Q") + str(((today.month - 1) // 3) + 1)
    for state_code, threshold, revenue, orders in SALES_TAX_STATE_ROWS:
        status = "threshold_close" if revenue >= int(threshold * 0.8) else "monitoring"
        conn.execute(
            """
            INSERT OR IGNORE INTO sales_tax_nexus (
              id, company_id, state_code, status, threshold_cents, trailing_revenue_cents,
              trailing_orders, next_review_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("nex"), company_id, state_code, status, threshold, revenue, orders, (today + timedelta(days=30)).isoformat(), now, now),
        )
        return_status = "registration_required" if status == "threshold_close" else "nexus_review"
        conn.execute(
            """
            INSERT OR IGNORE INTO sales_tax_returns (
              id, company_id, state_code, period, status, due_date, tax_collected_cents, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("str"), company_id, state_code, period, return_status, (today + timedelta(days=45)).isoformat(), int(revenue * 0.0625), now, now),
        )
    for sku, name, code in [("CEDAR-001", "Digital setup package", "digital_service"), ("CEDAR-BOX", "Welcome kit", "general_tangible_goods")]:
        conn.execute(
            "INSERT OR IGNORE INTO sales_tax_products (id, company_id, sku, name, tax_code, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("stp"), company_id, sku, name, code, now),
        )
    audit(conn, actor_user_id, company_id, None, "sales_tax.sandbox_connected", "Sandbox sales-tax account, nexus records, returns, and product tax codes created.")


def sales_tax_action(conn, company_id: str, return_id: str, actor_user_id: str, action: str) -> None:
    row = conn.execute("SELECT * FROM sales_tax_returns WHERE id = ? AND company_id = ?", (return_id, company_id)).fetchone()
    if not row:
        raise ValueError("Sales-tax return not found.")
    target_by_action = {
        "mark_registered": "registered",
        "prepare_return": "return_preparation",
        "send_for_approval": "ready_for_approval",
        "approve_to_file": "approved_to_file",
        "sandbox_submit": "submitted",
        "sandbox_accept": "accepted",
        "sandbox_reject": "rejected",
        "not_required": "not_required",
    }
    target = target_by_action.get(action)
    if not target:
        raise ValueError("Unsupported sales-tax action.")
    validate_transition(SALES_TAX_RETURN_TRANSITIONS, row["status"], target)
    now = utcnow()
    receipt_id = row["receipt_id"]
    evidence_id = row["evidence_id"]
    submitted_at = row["submitted_at"]
    accepted_at = row["accepted_at"]
    rejected_at = row["rejected_at"]
    if target in {"submitted", "accepted", "rejected"}:
        receipt_id = f"SBOX-STAX-{new_id('rcpt').upper()}"
        evidence = create_evidence(
            conn,
            company_id,
            None,
            f"Sales tax {row['state_code']} {target} receipt",
            "Sandbox sales-tax evidence. No filing was submitted to a state authority.",
            f"State: {row['state_code']}\nPeriod: {row['period']}\nStatus: {target}\nReceipt: {receipt_id}\nCreated: {now}",
            receipt_id,
            "sandbox_sales_tax",
            actor_user_id,
            True,
        )
        evidence_id = evidence["id"]
    if target == "submitted":
        submitted_at = now
    elif target == "accepted":
        accepted_at = now
    elif target == "rejected":
        rejected_at = now
    conn.execute(
        """
        UPDATE sales_tax_returns
        SET status = ?, receipt_id = ?, evidence_id = ?, submitted_at = ?,
            accepted_at = ?, rejected_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (target, receipt_id, evidence_id, submitted_at, accepted_at, rejected_at, now, return_id),
    )
    audit(conn, actor_user_id, company_id, None, "sales_tax.return_updated", f"Sales-tax return moved from {row['status']} to {target}.")
