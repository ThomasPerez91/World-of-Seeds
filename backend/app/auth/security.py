import hashlib
import hmac
import ipaddress
import re
import secrets
import string

from pwdlib import PasswordHash

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
AUTH_SEED_LENGTH = 25
AUTH_SEED_ALPHABET = string.ascii_letters + string.digits

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


def password_needs_rehash(encoded_hash: str) -> bool:
    """Whether the stored hash predates the current hashing parameters (no crypto involved)."""
    for hasher in password_hash.hashers:
        if hasher.identify(encoded_hash):
            return hasher != password_hash.current_hasher or bool(
                hasher.check_needs_rehash(encoded_hash)
            )
    return False


def rehash_verified_password(value: str) -> str:
    """Hash a password that was just verified, even if it predates today's length policy."""
    return password_hash.hash(value)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokens_match(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def network_identity(peer: str) -> str:
    """The network an address belongs to: an IPv6 /64 (one subscriber), otherwise the address."""
    try:
        address = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)


def throttle_key(client_ip: str, username: str) -> str:
    # An IPv6 attacker owns a whole /64: keying on the exact address would give a fresh budget
    # to every attempt.
    return hash_token(f"{network_identity(client_ip)}\0{username.lower()}")


def generate_initial_username() -> str:
    return f"guest-{secrets.token_hex(3)}"


def generate_initial_password() -> str:
    return secrets.token_urlsafe(18)


def generate_auth_seed() -> str:
    """Generate the stable, URL-neutral external identity secret for one account."""

    return "".join(secrets.choice(AUTH_SEED_ALPHABET) for _ in range(AUTH_SEED_LENGTH))
