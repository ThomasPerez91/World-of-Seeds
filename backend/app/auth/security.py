import hashlib
import hmac
import re
import secrets

from pwdlib import PasswordHash

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash(secrets.token_urlsafe(24))


class CredentialValidationError(ValueError):
    pass


def normalize_username(value: str) -> str:
    username = value.strip()
    if USERNAME_PATTERN.fullmatch(username) is None:
        raise CredentialValidationError(
            "Le nom doit contenir 3 à 32 caractères parmi A-Z, a-z, 0-9, _ et -."
        )
    return username


def canonical_username(value: str) -> str:
    """Return the case-insensitive identity used for lookup and uniqueness."""

    return normalize_username(value).lower()


def validate_password(value: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise CredentialValidationError(
            f"Le mot de passe doit contenir entre {MIN_PASSWORD_LENGTH} et "
            f"{MAX_PASSWORD_LENGTH} caractères."
        )
    return value


def hash_password(value: str) -> str:
    return password_hash.hash(validate_password(value))


def verify_password(value: str, encoded_hash: str) -> bool:
    return password_hash.verify(value, encoded_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokens_match(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def throttle_key(client_ip: str, username: str) -> str:
    return hash_token(f"{client_ip}\0{username.lower()}")


def generate_temporary_username() -> str:
    return f"guest-{secrets.token_hex(3)}"


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(18)
