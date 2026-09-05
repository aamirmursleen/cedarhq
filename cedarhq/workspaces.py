from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import utcnow
from .services import audit, create_evidence, new_id
from .status import (
    ASSISTANT_ACTION_TRANSITIONS,
    MONTHLY_CLOSE_TRANSITIONS,
    TAX_FILING_TRANSITIONS,
    TRANSACTION_TRANSITIONS,
    validate_transition,
)


BOOKKEEPING_CATEGORIES = [
    "Advertising",
    "Contractors",
    "Income",
    "Insurance",
    "Meals",
    "Office",
    "Professional services",
    "Software",
    "Travel",
]

TAX_QUESTIONS = [
    ("activity", "Describe the company's principal business activity."),
    ("revenue", "Confirm total gross revenue for the tax year."),
    ("expenses", "Confirm deductible business expenses are categorized."),
    ("owners", "Confirm all owners and ownership changes for the tax year."),
    ("foreign_activity", "Did the company have foreign owners, accounts, or related-party transactions?"),
]

TAX_DOCUMENTS = [
    "Year-end profit and loss",
    "Year-end balance sheet",
    "Bank statements",
    "Formation and ownership documents",
    "Prior-year return, if any",
]

TAX_TYPES = {
    "1120": "Federal Form 1120",
    "5472": "Federal Form 5472",
    "1099": "1099 information returns",
    "state": "State income/franchise return",
    "city": "City business tax return",
    "extension": "Tax filing extension",
}


def connect_sandbox_finance(conn, company_id: str, actor_user_id: str) -> None:
    existing = conn.execute(
        "SELECT id FROM financial_accounts WHERE company_id = ? AND provider = 'sandbox_ledger'",
        (company_id,),
    ).fetchone()
    if existing:
        return
    now = utcnow()
    accounts = [
        ("operating", "Northstar Sandbox Bank", "Operating account", "checking", "4821", 2_486_200),
        ("card", "Northstar Sandbox Bank", "Business card", "credit", "1934", -184_500),
    ]
    account_ids: dict[str, str] = {}
    for external_id, institution, name, account_type, mask, balance in accounts:
        account_id = new_id("fac")
        account_ids[external_id] = account_id
        conn.execute(
            """
            INSERT INTO financial_accounts (
              id, company_id, provider, external_id, institution_name, account_name,
              account_type, mask, currency, status, balance_cents, is_sandbox,
              last_synced_at, created_at, updated_at
            ) VALUES (?, ?, 'sandbox_ledger', ?, ?, ?, ?, ?, 'USD', 'connected', ?, 1, ?, ?, ?)
            """,
            (account_id, company_id, external_id, institution, name, account_type, mask, balance, now, now, now),
        )

    today = datetime.now(timezone.utc).date()
    transactions = [
        (1, "operating", "Stripe payout", "Stripe", 845_000, "Income", "categorized"),
        (3, "card", "Google Ads", "Google", -126_400, "Advertising", "categorized"),
        (5, "operating", "Client wire transfer", "Acme Retail", 620_000, "Income", "categorized"),
        (7, "card", "AWS cloud services", "Amazon Web Services", -84_900, "Software", "categorized"),
        (9, "card", "Notion subscription", "Notion", -2_400, "Software", "reconciled"),
        (12, "operating", "Design contractor", "Studio North", -210_000, "Contractors", "categorized"),
        (15, "card", "Airline ticket", "United Airlines", -58_700, None, "uncategorized"),
        (18, "card", "Team lunch", "Local Kitchen", -16_850, None, "uncategorized"),
        (21, "operating", "Insurance premium", "Founder Cover", -31_000, "Insurance", "reconciled"),
        (25, "card", "Office supplies", "Office Market", -12_600, "Office", "categorized"),
    ]
    for index, (days_ago, account_key, description, merchant, amount, category, status) in enumerate(transactions, start=1):
        transaction_id = new_id("txn")
        conn.execute(
            """
            INSERT INTO bookkeeping_transactions (
              id, company_id, account_id, external_id, posted_at, description, merchant,
              amount_cents, category, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                company_id,
                account_ids[account_key],
                f"sandbox-{index}",
                (today - timedelta(days=days_ago)).isoformat(),
                description,
                merchant,
                amount,
                category,
                status,
                now,
                now,
            ),
        )
    conn.execute(
        "INSERT INTO monthly_closes (id, company_id, month, status, created_at, updated_at) VALUES (?, ?, ?, 'not_started', ?, ?)",
        (new_id("mcl"), company_id, today.strftime("%Y-%m"), now, now),
    )
    audit(conn, actor_user_id, company_id, None, "bookkeeping.sandbox_connected", "Sandbox financial accounts and transactions connected.")


def bookkeeping_context(conn, company_id: str) -> dict[str, Any]:
    accounts = conn.execute(
        "SELECT * FROM financial_accounts WHERE company_id = ? ORDER BY account_type, account_name",
        (company_id,),
    ).fetchall()
    transactions = conn.execute(
        """
        SELECT bt.*, fa.account_name, fa.mask
        FROM bookkeeping_transactions bt
        JOIN financial_accounts fa ON fa.id = bt.account_id
        WHERE bt.company_id = ?
        ORDER BY bt.posted_at DESC, bt.created_at DESC
        """,
        (company_id,),
    ).fetchall()
    closes = conn.execute(
        "SELECT * FROM monthly_closes WHERE company_id = ? ORDER BY month DESC",
        (company_id,),
    ).fetchall()
    invoices = conn.execute(
        "SELECT * FROM bookkeeping_invoices WHERE company_id = ? ORDER BY due_date DESC",
        (company_id,),
    ).fetchall()
    revenue = sum(row["amount_cents"] for row in transactions if row["amount_cents"] > 0)
    expenses = -sum(row["amount_cents"] for row in transactions if row["amount_cents"] < 0)
    uncategorized = sum(1 for row in transactions if row["status"] == "uncategorized")
    reconciled = sum(1 for row in transactions if row["status"] == "reconciled")
    return {
        "accounts": accounts,
        "transactions": transactions,
        "closes": closes,
        "invoices": invoices,
        "revenue_cents": revenue,
        "expenses_cents": expenses,
        "profit_cents": revenue - expenses,
        "cash_cents": sum(row["balance_cents"] for row in accounts),
        "uncategorized_count": uncategorized,
        "reconciled_count": reconciled,
    }


def update_transaction(conn, company_id: str, transaction_id: str, actor_user_id: str, category: str, reconcile: bool) -> None:
    row = conn.execute(
        "SELECT * FROM bookkeeping_transactions WHERE id = ? AND company_id = ?",
        (transaction_id, company_id),
    ).fetchone()
    if not row:
        raise ValueError("Transaction not found.")
    if category not in BOOKKEEPING_CATEGORIES:
        raise ValueError("Choose a supported bookkeeping category.")
    current = row["status"]
    target = "reconciled" if reconcile else "categorized"
    if current != target:
        if current == "uncategorized" and target == "reconciled":
            validate_transition(TRANSACTION_TRANSITIONS, current, "categorized")
        else:
            validate_transition(TRANSACTION_TRANSITIONS, current, target)
    conn.execute(
        "UPDATE bookkeeping_transactions SET category = ?, status = ?, updated_at = ? WHERE id = ?",
        (category, target, utcnow(), transaction_id),
    )
    audit(conn, actor_user_id, company_id, None, "bookkeeping.transaction_updated", f"Transaction categorized as {category}; status {target}.")


def advance_monthly_close(conn, company_id: str, close_id: str, actor_user_id: str) -> None:
    close = conn.execute("SELECT * FROM monthly_closes WHERE id = ? AND company_id = ?", (close_id, company_id)).fetchone()
    if not close:
        raise ValueError("Monthly close not found.")
    target_by_status = {"not_started": "in_progress", "in_progress": "review_ready", "review_ready": "closed"}
    target = target_by_status.get(close["status"])
    if not target:
        raise ValueError("This monthly close cannot be advanced.")
    validate_transition(MONTHLY_CLOSE_TRANSITIONS, close["status"], target)
    completed_at = utcnow() if target == "closed" else None
    conn.execute(
        "UPDATE monthly_closes SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (target, completed_at, utcnow(), close_id),
    )
    audit(conn, actor_user_id, company_id, None, "bookkeeping.close_updated", f"Monthly close {close['month']} moved to {target}.")


def create_tax_filing(conn, company_id: str, actor_user_id: str, filing_type: str, tax_year: int) -> Any:
    if filing_type not in TAX_TYPES:
        raise ValueError("Choose a supported filing type.")
    if tax_year < 2020 or tax_year > datetime.now(timezone.utc).year + 1:
        raise ValueError("Choose a valid tax year.")
    jurisdiction = "Federal" if filing_type in {"1120", "5472", "1099", "extension"} else "State/local"
    due_month_day = (1, 31) if filing_type == "1099" else (4, 15)
    due_date = date(tax_year + 1, *due_month_day).isoformat()
    filing_id = new_id("tax")
    now = utcnow()
    try:
        conn.execute(
            """
            INSERT INTO tax_filings (
              id, company_id, tax_year, filing_type, jurisdiction, status, due_date,
              extension_requested, responsible_party, is_sandbox, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'questionnaire', ?, ?, 'Founder and CedarHQ tax team', 1, ?, ?)
            """,
            (filing_id, company_id, tax_year, filing_type, jurisdiction, due_date, int(filing_type == "extension"), now, now),
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise ValueError("This tax workflow already exists.") from exc
        raise
    for key, label in TAX_QUESTIONS:
        conn.execute(
            "INSERT INTO tax_questionnaire_answers (id, filing_id, question_key, question_label, updated_at) VALUES (?, ?, ?, ?, ?)",
            (new_id("tqa"), filing_id, key, label, now),
        )
    for label in TAX_DOCUMENTS:
        conn.execute(
            "INSERT INTO tax_required_documents (id, filing_id, label, status, updated_at) VALUES (?, ?, ?, 'required', ?)",
            (new_id("trd"), filing_id, label, now),
        )
    audit(conn, actor_user_id, company_id, None, "tax.workflow_created", f"Sandbox {TAX_TYPES[filing_type]} workflow created for {tax_year}.")
    return conn.execute("SELECT * FROM tax_filings WHERE id = ?", (filing_id,)).fetchone()


def taxes_context(conn, company_id: str) -> dict[str, Any]:
    filings = conn.execute(
        "SELECT * FROM tax_filings WHERE company_id = ? ORDER BY tax_year DESC, created_at DESC",
        (company_id,),
    ).fetchall()
    active = filings[0] if filings else None
    answers = []
    documents = []
    if active:
        answers = conn.execute(
            "SELECT * FROM tax_questionnaire_answers WHERE filing_id = ? ORDER BY rowid",
            (active["id"],),
        ).fetchall()
        documents = conn.execute(
            "SELECT * FROM tax_required_documents WHERE filing_id = ? ORDER BY rowid",
            (active["id"],),
        ).fetchall()
    return {"filings": filings, "active": active, "answers": answers, "documents": documents}


def save_tax_questionnaire(conn, company_id: str, filing_id: str, actor_user_id: str, payload: dict[str, str]) -> None:
    filing = conn.execute("SELECT * FROM tax_filings WHERE id = ? AND company_id = ?", (filing_id, company_id)).fetchone()
    if not filing:
        raise ValueError("Tax workflow not found.")
    if filing["status"] not in {"questionnaire", "documents_pending", "blocked"}:
        raise ValueError("Questionnaire editing is closed at this stage.")
    now = utcnow()
    answers = conn.execute("SELECT * FROM tax_questionnaire_answers WHERE filing_id = ?", (filing_id,)).fetchall()
    for answer in answers:
        value = payload.get(f"answer_{answer['question_key']}", "").strip()
        conn.execute("UPDATE tax_questionnaire_answers SET answer = ?, updated_at = ? WHERE id = ?", (value, now, answer["id"]))
    docs = conn.execute("SELECT * FROM tax_required_documents WHERE filing_id = ?", (filing_id,)).fetchall()
    for document in docs:
        status = "provided" if payload.get(f"document_{document['id']}") == "yes" else "required"
        conn.execute("UPDATE tax_required_documents SET status = ?, updated_at = ? WHERE id = ?", (status, now, document["id"]))
    audit(conn, actor_user_id, company_id, None, "tax.questionnaire_saved", f"Tax questionnaire saved for {filing['filing_type']} {filing['tax_year']}.")


def tax_action(conn, company_id: str, filing_id: str, actor_user_id: str, action: str, is_ops: bool = False) -> Any:
    filing = conn.execute("SELECT * FROM tax_filings WHERE id = ? AND company_id = ?", (filing_id, company_id)).fetchone()
    if not filing:
        raise ValueError("Tax workflow not found.")
    target = None
    if action == "submit_questionnaire":
        answers_missing = conn.execute(
            "SELECT COUNT(*) AS count FROM tax_questionnaire_answers WHERE filing_id = ? AND required = 1 AND TRIM(answer) = ''",
            (filing_id,),
        ).fetchone()["count"]
        docs_missing = conn.execute(
            "SELECT COUNT(*) AS count FROM tax_required_documents WHERE filing_id = ? AND status != 'provided'",
            (filing_id,),
        ).fetchone()["count"]
        if answers_missing:
            raise ValueError(f"Complete {answers_missing} required questionnaire answer(s) first.")
        target = "documents_pending" if docs_missing else "preparation"
    elif action == "approve_return" and not is_ops:
        target = "signature_required"
    elif action == "sign_return" and not is_ops:
        target = "ready_to_submit"
    elif action == "mark_review_ready" and is_ops:
        target = "founder_review"
    elif action == "sandbox_submit" and is_ops:
        target = "submitted"
    elif action == "sandbox_accept" and is_ops:
        target = "accepted"
    elif action == "sandbox_reject" and is_ops:
        target = "rejected"
    else:
        raise ValueError("Unsupported tax action for this role.")
    validate_transition(TAX_FILING_TRANSITIONS, filing["status"], target)
    now = utcnow()
    receipt_id = filing["receipt_id"]
    evidence_id = filing["evidence_id"]
    submitted_at = filing["submitted_at"]
    accepted_at = filing["accepted_at"]
    rejected_at = filing["rejected_at"]
    if target in {"submitted", "accepted", "rejected"}:
        receipt_id = f"SBOX-TAX-{new_id('rcpt').upper()}"
        evidence = create_evidence(
            conn,
            company_id,
            None,
            f"Sandbox tax {target} receipt",
            "Evidence generated by the sandbox tax adapter. It is not an IRS or state receipt.",
            f"Sandbox tax workflow\nFiling: {filing['filing_type']}\nTax year: {filing['tax_year']}\nStatus: {target}\nReceipt: {receipt_id}\nCreated: {now}",
            receipt_id,
            "sandbox_tax",
            actor_user_id,
            True,
        )
        evidence_id = evidence["id"]
        if target == "submitted":
            submitted_at = now
        elif target == "accepted":
            accepted_at = now
        else:
            rejected_at = now
    conn.execute(
        """
        UPDATE tax_filings SET status = ?, receipt_id = ?, evidence_id = ?, submitted_at = ?,
          accepted_at = ?, rejected_at = ?, updated_at = ? WHERE id = ?
        """,
        (target, receipt_id, evidence_id, submitted_at, accepted_at, rejected_at, now, filing_id),
    )
    audit(conn, actor_user_id, company_id, None, "tax.status_updated", f"Tax workflow moved from {filing['status']} to {target}.")
    return conn.execute("SELECT * FROM tax_filings WHERE id = ?", (filing_id,)).fetchone()


def connect_sandbox_commerce(conn, company_id: str, actor_user_id: str, provider: str) -> None:
    if provider not in {"shopify", "amazon"}:
        raise ValueError("Choose Shopify or Amazon.")
    existing = conn.execute(
        "SELECT id FROM commerce_connections WHERE company_id = ? AND provider = ?",
        (company_id, provider),
    ).fetchone()
    if existing:
        return
    connection_id = new_id("com")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO commerce_connections (
          id, company_id, provider, external_shop_id, display_name, status,
          is_sandbox, last_synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'connected', 1, ?, ?, ?)
        """,
        (connection_id, company_id, provider, f"sandbox-{provider}", f"{provider.title()} Sandbox Store", now, now, now),
    )
    today = datetime.now(timezone.utc).date()
    multiplier = 1.0 if provider == "shopify" else 0.72
    for offset in range(30):
        metric_date = today - timedelta(days=offset)
        base = int((18_000 + ((offset * 7919) % 29_000)) * multiplier)
        orders = max(1, int((3 + (offset * 7) % 9) * multiplier))
        fees = int(base * (0.029 if provider == "shopify" else 0.15))
        refunds = int(base * 0.04) if offset % 8 == 0 else 0
        ad_spend = int(base * (0.12 if provider == "shopify" else 0.09))
        cogs = int(base * 0.34)
        payouts = base - fees - refunds
        conn.execute(
            """
            INSERT INTO commerce_daily_metrics (
              id, connection_id, metric_date, revenue_cents, orders_count, fees_cents,
              refunds_cents, ad_spend_cents, cogs_cents, payouts_cents, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("met"), connection_id, metric_date.isoformat(), base, orders, fees, refunds, ad_spend, cogs, payouts, now),
        )
    audit(conn, actor_user_id, company_id, None, "analytics.sandbox_connected", f"{provider.title()} sandbox store connected.")


def analytics_context(conn, company_id: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    end_date = _safe_date(end) or today
    start_date = _safe_date(start) or (end_date - timedelta(days=29))
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    connections = conn.execute(
        "SELECT * FROM commerce_connections WHERE company_id = ? ORDER BY provider",
        (company_id,),
    ).fetchall()
    metrics = conn.execute(
        """
        SELECT cdm.*, cc.provider, cc.display_name
        FROM commerce_daily_metrics cdm
        JOIN commerce_connections cc ON cc.id = cdm.connection_id
        WHERE cc.company_id = ? AND cdm.metric_date BETWEEN ? AND ?
        ORDER BY cdm.metric_date ASC, cc.provider
        """,
        (company_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    totals = {
        "revenue_cents": sum(row["revenue_cents"] for row in metrics),
        "orders_count": sum(row["orders_count"] for row in metrics),
        "fees_cents": sum(row["fees_cents"] for row in metrics),
        "refunds_cents": sum(row["refunds_cents"] for row in metrics),
        "ad_spend_cents": sum(row["ad_spend_cents"] for row in metrics),
        "cogs_cents": sum(row["cogs_cents"] for row in metrics),
        "payouts_cents": sum(row["payouts_cents"] for row in metrics),
    }
    totals["margin_cents"] = totals["revenue_cents"] - totals["fees_cents"] - totals["refunds_cents"] - totals["ad_spend_cents"] - totals["cogs_cents"]
    daily: dict[str, dict[str, int]] = {}
    for row in metrics:
        bucket = daily.setdefault(row["metric_date"], {"revenue_cents": 0, "margin_cents": 0, "orders_count": 0})
        bucket["revenue_cents"] += row["revenue_cents"]
        bucket["orders_count"] += row["orders_count"]
        bucket["margin_cents"] += row["revenue_cents"] - row["fees_cents"] - row["refunds_cents"] - row["ad_spend_cents"] - row["cogs_cents"]
    return {
        "connections": connections,
        "metrics": metrics,
        "daily": daily,
        "totals": totals,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }


def _safe_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def assistant_context(conn, company_id: str, user_id: str) -> dict[str, Any]:
    thread = conn.execute(
        "SELECT * FROM assistant_threads WHERE company_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (company_id, user_id),
    ).fetchone()
    if not thread:
        now = utcnow()
        thread_id = new_id("ath")
        conn.execute(
            "INSERT INTO assistant_threads (id, company_id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, 'Business assistant', ?, ?)",
            (thread_id, company_id, user_id, now, now),
        )
        thread = conn.execute("SELECT * FROM assistant_threads WHERE id = ?", (thread_id,)).fetchone()
    messages = conn.execute(
        "SELECT * FROM assistant_messages WHERE thread_id = ? ORDER BY created_at, rowid",
        (thread["id"],),
    ).fetchall()
    actions = conn.execute(
        "SELECT * FROM assistant_actions WHERE thread_id = ? ORDER BY created_at DESC",
        (thread["id"],),
    ).fetchall()
    return {"thread": thread, "messages": messages, "actions": actions}


def ask_assistant(conn, company_id: str, user_id: str, question: str) -> None:
    question = question.strip()
    if not question:
        raise ValueError("Enter a question.")
    if len(question) > 2000:
        raise ValueError("Keep the question under 2,000 characters.")
    ctx = assistant_context(conn, company_id, user_id)
    thread_id = ctx["thread"]["id"]
    now = utcnow()
    conn.execute(
        "INSERT INTO assistant_messages (id, thread_id, role, content, citations_json, created_at) VALUES (?, ?, 'user', ?, '[]', ?)",
        (new_id("ams"), thread_id, question, now),
    )
    company = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    order = conn.execute(
        "SELECT * FROM formation_orders WHERE company_id = ? ORDER BY created_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    lowered = question.lower()
    citations: list[dict[str, str]] = []
    consequential = any(word in lowered for word in ["submit", "file for me", "pay", "cancel", "sign", "send to irs", "send to state"])
    if any(word in lowered for word in ["tax", "deadline", "annual", "compliance", "boi"]):
        items = conn.execute(
            "SELECT * FROM compliance_items WHERE company_id = ? ORDER BY due_date LIMIT 3",
            (company_id,),
        ).fetchall()
        if items:
            detail = "; ".join(f"{row['title']} is {row['status'].replace('_', ' ')} with a tracked date of {row['due_date']}" for row in items)
            answer = f"For {company['name_choice_1'] or company['legal_name']}, the current compliance record shows: {detail}. Treat sandbox dates as planning prompts and verify tax/legal deadlines with the cited authority or your CPA before relying on them."
            citations = [{"label": row["title"], "source": row["source_rule"], "href": "/app/compliance"} for row in items]
        else:
            answer = "No compliance obligations have been generated for this company yet. Complete formation onboarding before relying on deadline guidance."
    elif any(word in lowered for word in ["formation", "ein", "company", "state", "llc", "corp"]):
        if order:
            answer = f"Your company record is {company['entity_type'] or 'not yet classified'} in {company['state_code'] or 'an unselected state'}. The formation workflow is currently {order['status'].replace('_', ' ')}. A step is only shown as complete when its timestamp, receipt, and downloadable evidence all exist."
            citations = [{"label": "Formation timeline", "source": f"CedarHQ order {order['id']} and its evidence records", "href": f"/app/orders/{order['id']}"}]
        else:
            answer = "This company does not have a formation order yet. Complete onboarding to generate an evidence-backed recommendation and state workflow."
            citations = [{"label": "Founder onboarding", "source": "Founder-provided company profile", "href": "/app/onboarding"}]
    elif any(word in lowered for word in ["revenue", "expense", "profit", "bookkeeping", "transaction"]):
        books = bookkeeping_context(conn, company_id)
        answer = f"The connected sandbox ledger shows {len(books['transactions'])} transactions, {books['uncategorized_count']} uncategorized items, and a current net result of ${(books['profit_cents'] / 100):,.2f}. These are sandbox records, not filed financial statements."
        citations = [{"label": "Bookkeeping ledger", "source": "Connected financial account and transaction records", "href": "/app/bookkeeping"}]
    elif any(word in lowered for word in ["shopify", "amazon", "analytics", "orders", "margin"]):
        analytics = analytics_context(conn, company_id)
        answer = f"For {analytics['start']} through {analytics['end']}, connected sandbox commerce data contains {analytics['totals']['orders_count']} orders and ${(analytics['totals']['revenue_cents'] / 100):,.2f} in revenue. Margin reflects fees, refunds, ad spend, and COGS stored in CedarHQ."
        citations = [{"label": "Commerce analytics", "source": "Shopify/Amazon sandbox adapter metrics", "href": "/app/analytics"}]
    else:
        answer = f"I can explain the current record for {company['name_choice_1'] or company['legal_name'] or 'this company'}, including formation, compliance, bookkeeping, tax workflows, and commerce analytics. I use only records and rules stored in this workspace and show the source for each answer."
        citations = [{"label": "Company record", "source": "Founder-provided company profile", "href": "/app"}]

    if consequential:
        action_id = new_id("aac")
        conn.execute(
            """
            INSERT INTO assistant_actions (
              id, thread_id, requested_by_user_id, action_type, summary, payload_json,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, 'consequential_request', ?, ?, 'pending_approval', ?, ?)
            """,
            (action_id, thread_id, user_id, question[:240], json.dumps({"request": question}), now, now),
        )
        answer += " I did not perform the requested action. It has been recorded for explicit approval; approval still does not submit anything to a government or tax authority."
    conn.execute(
        "INSERT INTO assistant_messages (id, thread_id, role, content, citations_json, created_at) VALUES (?, ?, 'assistant', ?, ?, ?)",
        (new_id("ams"), thread_id, answer, json.dumps(citations), utcnow()),
    )
    conn.execute("UPDATE assistant_threads SET updated_at = ? WHERE id = ?", (utcnow(), thread_id))
    audit(conn, user_id, company_id, None, "assistant.answer_created", "Context-aware assistant answer created with workspace citations.")


def decide_assistant_action(conn, company_id: str, action_id: str, actor_user_id: str, decision: str) -> None:
    row = conn.execute(
        """
        SELECT aa.* FROM assistant_actions aa
        JOIN assistant_threads at ON at.id = aa.thread_id
        WHERE aa.id = ? AND at.company_id = ?
        """,
        (action_id, company_id),
    ).fetchone()
    if not row:
        raise ValueError("Assistant action not found.")
    target = "approved" if decision == "approve" else "rejected" if decision == "reject" else None
    if not target:
        raise ValueError("Choose approve or reject.")
    validate_transition(ASSISTANT_ACTION_TRANSITIONS, row["status"], target)
    now = utcnow()
    conn.execute(
        "UPDATE assistant_actions SET status = ?, approved_at = ?, rejected_at = ?, updated_at = ? WHERE id = ?",
        (target, now if target == "approved" else None, now if target == "rejected" else None, now, action_id),
    )
    audit(conn, actor_user_id, company_id, None, "assistant.action_decided", f"Assistant action {action_id} was {target}; no external action executed.")
