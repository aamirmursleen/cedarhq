from __future__ import annotations

FORMATION_STATUS_ORDER = [
    "draft",
    "checkout_pending",
    "paid",
    "information_received",
    "operations_review",
    "state_submission_ready",
    "state_submitted",
    "state_approved",
    "ein_submitted",
    "ein_received",
    "bank_ready",
]

FORMATION_TRANSITIONS = {
    "draft": {"checkout_pending", "cancelled"},
    "checkout_pending": {"paid", "cancelled", "blocked"},
    "paid": {"information_received", "blocked", "cancelled"},
    "information_received": {"operations_review", "blocked", "cancelled"},
    "operations_review": {"state_submission_ready", "blocked", "cancelled"},
    "state_submission_ready": {"state_submitted", "blocked", "cancelled"},
    "state_submitted": {"state_approved", "blocked", "cancelled"},
    "state_approved": {"ein_submitted", "blocked", "cancelled"},
    "ein_submitted": {"ein_received", "blocked", "cancelled"},
    "ein_received": {"bank_ready", "blocked", "cancelled"},
    "blocked": set(FORMATION_STATUS_ORDER[3:]) | {"cancelled"},
    "bank_ready": set(),
    "cancelled": set(),
}

FORMATION_EVIDENCE_REQUIRED = {
    "information_received",
    "operations_review",
    "state_submission_ready",
    "state_submitted",
    "state_approved",
    "ein_submitted",
    "ein_received",
    "bank_ready",
}

FORMATION_STEPS = [
    ("information_received", "Information received", "CedarHQ intake"),
    ("operations_review", "Operations review", "CedarHQ operations"),
    ("state_submission_ready", "Ready for state submission", "CedarHQ operations"),
    ("state_submitted", "State submission", "Sandbox filing provider"),
    ("state_approved", "State approved", "Sandbox filing provider"),
    ("ein_submitted", "EIN submitted", "Sandbox tax provider"),
    ("ein_received", "EIN received", "Sandbox tax provider"),
    ("bank_ready", "Bank-ready package", "CedarHQ operations"),
]

COMPLIANCE_TRANSITIONS = {
    "upcoming": {"action_required", "submitted", "overdue", "waived"},
    "action_required": {"submitted", "overdue", "waived"},
    "submitted": {"accepted", "rejected"},
    "rejected": {"action_required", "submitted", "waived"},
    "overdue": {"submitted", "waived"},
    "accepted": set(),
    "waived": set(),
}

PAYMENT_TRANSITIONS = {
    "created": {"sandbox_paid", "failed"},
    "sandbox_paid": {"refunded"},
    "failed": set(),
    "refunded": set(),
}

DOCUMENT_TRANSITIONS = {
    "draft": {"generated", "pending_signature", "archived"},
    "generated": {"pending_signature", "signed", "archived"},
    "pending_signature": {"signed", "archived"},
    "signed": {"archived"},
    "archived": set(),
}

FINANCIAL_ACCOUNT_TRANSITIONS = {
    "pending": {"connected", "failed"},
    "connected": {"reauth_required", "disconnected"},
    "reauth_required": {"connected", "disconnected"},
    "failed": {"pending"},
    "disconnected": {"pending"},
}

TRANSACTION_TRANSITIONS = {
    "uncategorized": {"categorized"},
    "categorized": {"uncategorized", "reconciled"},
    "reconciled": {"categorized"},
}

MONTHLY_CLOSE_TRANSITIONS = {
    "not_started": {"in_progress"},
    "in_progress": {"review_ready", "blocked"},
    "blocked": {"in_progress"},
    "review_ready": {"closed", "in_progress"},
    "closed": set(),
}

TAX_FILING_TRANSITIONS = {
    "questionnaire": {"documents_pending", "preparation"},
    "documents_pending": {"preparation", "questionnaire"},
    "preparation": {"founder_review", "blocked"},
    "blocked": {"preparation", "questionnaire"},
    "founder_review": {"signature_required", "preparation"},
    "signature_required": {"ready_to_submit", "founder_review"},
    "ready_to_submit": {"submitted"},
    "submitted": {"accepted", "rejected"},
    "rejected": {"preparation"},
    "accepted": set(),
}

COMMERCE_CONNECTION_TRANSITIONS = {
    "pending": {"connected", "failed"},
    "connected": {"reauth_required", "disconnected"},
    "reauth_required": {"connected", "disconnected"},
    "failed": {"pending"},
    "disconnected": {"pending"},
}

ASSISTANT_ACTION_TRANSITIONS = {
    "pending_approval": {"approved", "rejected"},
    "approved": {"executed", "cancelled"},
    "rejected": set(),
    "executed": set(),
    "cancelled": set(),
}


class StatusError(ValueError):
    pass


def validate_transition(machine: dict[str, set[str]], current: str, target: str) -> None:
    if target not in machine.get(current, set()):
        raise StatusError(f"Cannot transition from {current} to {target}.")


def validate_formation_transition(current: str, target: str, evidence_id: str | None = None) -> None:
    validate_transition(FORMATION_TRANSITIONS, current, target)
    if target in FORMATION_EVIDENCE_REQUIRED and not evidence_id:
        raise StatusError(f"Transition to {target} requires downloadable evidence.")


def entity_recommendation(answers: dict[str, str]) -> tuple[str, str]:
    venture = answers.get("venture_funding") == "yes"
    stock = answers.get("issue_equity") == "yes"
    simple_tax = answers.get("pass_through_tax") == "yes"
    owners = answers.get("multiple_owners") == "yes"
    international = answers.get("international_founder") == "yes"
    if venture or stock:
        return "c_corp", "C-Corp fits companies planning venture financing or formal stock grants."
    if simple_tax and not venture:
        return "llc", "LLC keeps administration simpler for owner-operated businesses."
    if owners and international:
        return "llc", "LLC may be simpler to start, but tax review is recommended for non-US owners."
    return "llc", "LLC is a flexible default when venture financing is not planned."
