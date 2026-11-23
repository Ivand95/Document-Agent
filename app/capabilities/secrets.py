"""Encryption of connection secrets (tokens, passwords, DSNs) with CAPABILITY_SECRET_KEY.

The key is a Fernet key (32 random bytes, URL-safe base64). Several keys separated by commas allow a
rotation: the first one encrypts, all of them decrypt; `python -m capabilities.cli rotate-secrets`
then re-encrypts everything with the first. Secrets are never logged and never returned by the API.
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

KEY_ENV = "CAPABILITY_SECRET_KEY"
MAX_SECRET_CHARS = 16_000


class SecretsUnavailable(RuntimeError):
    """The key is missing or malformed: secrets can't be stored or read."""


class SecretUnreadable(RuntimeError):
    """The stored secret can't be decrypted with the configured keys (key changed or data corrupted)."""


def _keys() -> list[str]:
    return [k.strip() for k in os.getenv(KEY_ENV, "").split(",") if k.strip()]


def configured() -> bool:
    return bool(_keys())


def setting_problems() -> list[str]:
    """What is wrong with CAPABILITY_SECRET_KEY, if it is set (an unset key is not a problem by itself:
    it only blocks storing secrets)."""
    problems = []
    for i, key in enumerate(_keys(), 1):
        try:
            Fernet(key)
        except (ValueError, TypeError):
            problems.append(f"{KEY_ENV}: la clave {i} no es una clave Fernet válida")
    return problems


def _fernet() -> MultiFernet:
    keys = _keys()
    if not keys:
        raise SecretsUnavailable(f"{KEY_ENV} no está configurada: no se pueden guardar ni leer secretos.")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError):
        raise SecretsUnavailable(f"{KEY_ENV} no es válida.") from None


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt(secret: dict) -> str:
    text = json.dumps(secret, separators=(",", ":"), sort_keys=True)
    if len(text) > MAX_SECRET_CHARS:
        raise ValueError(f"El secreto no puede superar {MAX_SECRET_CHARS} caracteres")
    return _fernet().encrypt(text.encode()).decode()


def decrypt(ciphertext: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(ciphertext.encode()))
    except (InvalidToken, ValueError):
        raise SecretUnreadable("No se pudo descifrar el secreto con las claves configuradas.") from None


def rotate(ciphertext: str) -> str:
    """The same secret, encrypted with the first key."""
    try:
        return _fernet().rotate(ciphertext.encode()).decode()
    except InvalidToken:
        raise SecretUnreadable("No se pudo descifrar el secreto con las claves configuradas.") from None
