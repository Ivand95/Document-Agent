"""What permissions can name: resource types, actions, and the known resources of each type.

Agents, admin areas and document categories are fixed in code. Capabilities and models live in their
registries (capabilities/registry.py, providers/registry.py), which register themselves as the source.
"""
from collections.abc import Callable

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
    "capabilities": "Capacidades",
    "models": "Modelos",
    "prompt_tools": "Herramientas de prompt",
    "code_tools": "Herramientas de código",
}

# Resource types whose ids are known today: a permission for anything else is a typo
KNOWN = {
    "agent": AGENTS,
    "admin_area": ADMIN_AREAS,
    "doc_category": CATEGORY_LABELS,
}

SUPER_ADMIN = "super_admin"

# Resource types whose ids are stored elsewhere: type -> function returning {id: label}
_SOURCES: dict[str, Callable[[], dict]] = {}


def register_source(resource_type: str, source: Callable[[], dict]) -> None:
    """Lets a module that owns a resource type (e.g. the capability registry) say which ids exist."""
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"Tipo de recurso desconocido: {resource_type}")
    _SOURCES[resource_type] = source


def resources(resource_type: str) -> dict | None:
    """{id: label} of the type, or None if its ids aren't checked."""
    if resource_type in KNOWN:
        return KNOWN[resource_type]
    if resource_type in _SOURCES:
        return _SOURCES[resource_type]()
    return None


def known_ids(resource_type: str) -> list[str]:
    return list(resources(resource_type) or {})


def validate_resource(resource_type: str, resource_id: str) -> None:
    """Raises ValueError for an unknown type, or an unknown id of a type whose ids are known."""
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"Tipo de recurso desconocido: {resource_type}")
    if not resource_id or len(resource_id) > 200:
        raise ValueError("El identificador del recurso debe tener entre 1 y 200 caracteres")
    if resource_id == WILDCARD:
        return
    existing = resources(resource_type)
    if existing is not None and resource_id not in existing:
        raise ValueError(f"'{resource_id}' no es un recurso conocido de tipo {resource_type}")


def catalog() -> dict:
    """For the permission matrix: the resources that exist, with their labels."""
    listed = {t: resources(t) for t in RESOURCE_TYPES}
    return {
        "resource_types": list(RESOURCE_TYPES),
        "actions": list(ACTIONS),
        "mapping_kinds": list(MAPPING_KINDS),
        "resources": {
            t: [{"id": k, "label": v} for k, v in items.items()] for t, items in listed.items() if items is not None
        },
    }
