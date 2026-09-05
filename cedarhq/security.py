from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Tuple

from .config import config


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_value(value: str) -> str:
    signature = hmac.new(config.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256)
    return f"{value}.{base64.urlsafe_b64encode(signature.digest()).decode('ascii').rstrip('=')}"


def unsign_value(signed: str) -> str | None:
    if "." not in signed:
        return None
    value, provided = signed.rsplit(".", 1)
    expected = sign_value(value).rsplit(".", 1)[1]
    if hmac.compare_digest(provided, expected):
        return value
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return "pbkdf2_sha256$210000$%s$%s" % (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, rounds, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password_strength(password: str) -> Tuple[bool, str]:
    if len(password) < 10:
        return False, "Use at least 10 characters."
    if not any(ch.isdigit() for ch in password):
        return False, "Include at least one number."
    if not any(ch.isalpha() for ch in password):
        return False, "Include at least one letter."
    return True, ""

