"""Values the registry accepts, and the rules about secrets in configuration."""
import re

KINDS = ("tool", "skill", "cli", "mcp", "prompt_tool", "code_tool")
# Kinds an admin registers by hand from the API today. Native tools come from code (synced at start-up);
# skills are uploaded (WP13); prompt and code tools are built in their own editors (WP4b).
CREATABLE_KINDS = ("mcp", "cli")
RISKS = ("read", "write", "financial")
# Ordered from least to most restricted
DATA_CLASSES = ("public", "internal", "sensitive")
AUTH_TYPES = ("none", "token", "oauth", "dsn")
CONNECTION_STATUSES = ("unchecked", "ok", "error", "disabled")

KEY_PATTERN = r"^[a-z][a-z0-9_]{1,62}$"
OPERATION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"

# The secret each auth type needs: at least one of these keys
SECRET_FIELDS = {
    "none": (),
    "token": ("token",),
    "oauth": ("refresh_token", "access_token"),
    "dsn": ("dsn", "password"),
}

# Words that mark a configuration key as a credential (compared word by word: "max_tokens" is fine,
# "access_token" is not)
_SECRET_WORDS = {
    "password", "passwd", "pwd", "secret", "token", "apikey", "credential", "credentials", "dsn",
    "authorization", "cookie", "privatekey",
}
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def data_class_rank(data_class: str) -> int:
    return DATA_CLASSES.index(data_class)


def _looks_secret(key: str) -> bool:
    words = [w.lower() for w in _WORD.findall(str(key))]
    joined = {a + b for a, b in zip(words, words[1:])}  # "api_key" -> "apikey", "private_key" -> "privatekey"
    return bool(_SECRET_WORDS & (set(words) | joined))


def secret_keys(value, path="") -> list[str]:
    """Paths of keys in a JSON value that look like credentials."""
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            here = f"{path}.{key}" if path else str(key)
            if _looks_secret(key):
                found.append(here)
            found.extend(secret_keys(inner, here))
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            found.extend(secret_keys(inner, f"{path}[{i}]"))
    return found


def reject_secrets(value, what="la configuración") -> None:
    keys = secret_keys(value)
    if keys:
        raise ValueError(
            f"{what} no puede contener credenciales ({', '.join(keys[:5])}): guárdalas como secreto de la conexión"
        )
