"""What permissions can name: resource types, actions, and the known resources of each type.

Capabilities and models are registered later (WP4, WP4b), so their ids aren't checked here yet.
"""
from core.config import CATEGORY_LABELS

RESOURCE_TYPES = ("capability", "agent", "admin_area", "doc_category", "model")
ACTIONS = ("use", "configure", "admin")
EFFECTS = ("allow", "deny")
SUBJECT_TYPES = ("role", "user")
MAPPING_KINDS = ("department", "position_prefix", "everyone")
WILDCARD = "*"

AGENTS = {
    "documents": "Documentos",
    "audio": "Llamadas",
}

ADMIN_AREAS = {
    "ingestion": "Ingesta",
    "users": "Usuarios",
    "rbac": "Roles y permisos",
    "audit": "Auditoría",
    "code_tools": "Herramientas de código",
}

# Resource types whose ids are known today: a permission for anything else is a typo
KNOWN = {
    "agent": AGENTS,
    "admin_area": ADMIN_AREAS,
    "doc_category": CATEGORY_LABELS,
}

SUPER_ADMIN = "super_admin"


def known_ids(resource_type: str) -> list[str]:
    return list(KNOWN.get(resource_type, {}))


def validate_resource(resource_type: str, resource_id: str) -> None:
    """Raises ValueError for an unknown type, or an unknown id of a type whose ids are known."""
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"Tipo de recurso desconocido: {resource_type}")
    if not resource_id or len(resource_id) > 200:
        raise ValueError("El identificador del recurso debe tener entre 1 y 200 caracteres")
    if resource_id != WILDCARD and resource_type in KNOWN and resource_id not in KNOWN[resource_type]:
        raise ValueError(f"'{resource_id}' no es un recurso conocido de tipo {resource_type}")


def catalog() -> dict:
    """For the permission matrix: the resources that exist, with their labels."""
    return {
        "resource_types": list(RESOURCE_TYPES),
        "actions": list(ACTIONS),
        "mapping_kinds": list(MAPPING_KINDS),
        "resources": {t: [{"id": k, "label": v} for k, v in items.items()] for t, items in KNOWN.items()},
    }
