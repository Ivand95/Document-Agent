"""Native tools: capabilities written in Python and shipped with the app.

A native tool is code, so admins don't create one: they enable a tool that the code provides. Tools come
from two places:
- modules in `capabilities/tools/` that call `register(NativeTool(...))` when imported;
- installed packages that declare an entry point in the group `coop_agent.tools` pointing at a
  NativeTool (or a function returning a list of them), so a developer can add tools without touching
  the core.
At start-up the registry copies them into the `capabilities` table (new ones disabled). The code stays
the source of truth for their operations, argument schemas and read-only flags.
"""
import importlib
import logging
import pkgutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

from capabilities.catalog import DATA_CLASSES, KEY_PATTERN, OPERATION_PATTERN, RISKS

log = logging.getLogger("capabilities")

ENTRY_POINT_GROUP = "coop_agent.tools"


@dataclass(frozen=True)
class ToolContext:
    """What a handler gets besides its arguments."""

    user_id: str
    capability_key: str
    operation: str
    config: dict
    limits: dict
    # Returns the decrypted secret of the capability's connection with that name (or the only one)
    secret: Callable[[str | None], dict]


@dataclass(frozen=True)
class NativeOperation:
    name: str
    description: str
    handler: Callable[[dict, ToolContext], Any]  # sync or async; returns text or JSON-serialisable data
    arg_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    read_only: bool = True  # False: changes something outside the app, so the gateway never runs it


@dataclass(frozen=True)
class NativeTool:
    key: str
    name: str
    description: str
    operations: tuple[NativeOperation, ...]
    data_class: str = "sensitive"  # the least restricted class an admin may set without super_admin
    risk: str = "read"
    config_schema: dict | None = None  # JSON Schema for the admin-editable config
    default_config: dict = field(default_factory=dict)
    check: Callable[[dict, dict | None], str] | None = None  # (config, secret) -> detail; raises if broken
    version: int = 1

    def operation(self, name: str) -> NativeOperation | None:
        return next((o for o in self.operations if o.name == name), None)


_lock = threading.Lock()
_tools: dict[str, NativeTool] = {}
_discovered = False


def _validate(tool: NativeTool) -> None:
    import re

    if not re.match(KEY_PATTERN, tool.key):
        raise ValueError(f"Native tool key '{tool.key}' must match {KEY_PATTERN}")
    if tool.data_class not in DATA_CLASSES or tool.risk not in RISKS:
        raise ValueError(f"Native tool '{tool.key}': bad data_class or risk")
    names = [o.name for o in tool.operations]
    if not names or len(names) != len(set(names)):
        raise ValueError(f"Native tool '{tool.key}' needs operations with unique names")
    for name in names:
        if not re.match(OPERATION_PATTERN, name):
            raise ValueError(f"Native tool '{tool.key}': operation '{name}' must match {OPERATION_PATTERN}")


def register(tool: NativeTool) -> NativeTool:
    _validate(tool)
    with _lock:
        existing = _tools.get(tool.key)
        if existing is not None and existing is not tool:
            raise ValueError(f"Two native tools use the key '{tool.key}'")
        _tools[tool.key] = tool
    return tool


def unregister(key: str) -> None:
    """For tests."""
    with _lock:
        _tools.pop(key, None)


def _discover() -> None:
    import capabilities.tools as package

    for module in pkgutil.iter_modules(package.__path__):
        try:
            importlib.import_module(f"{package.__name__}.{module.name}")
        except Exception:
            log.exception("Could not load native tool module %s", module.name)
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            loaded = entry.load()
            loaded = loaded() if callable(loaded) and not isinstance(loaded, NativeTool) else loaded
            for tool in loaded if isinstance(loaded, (list, tuple)) else [loaded]:
                register(tool)
        except Exception:
            log.exception("Could not load native tool plugin %s", entry.name)


def native_tools() -> dict[str, NativeTool]:
    """Every native tool, loading the shipped modules and plugins the first time."""
    global _discovered
    if not _discovered:
        _discover()
        _discovered = True
    with _lock:
        return dict(_tools)


def get(key: str) -> NativeTool | None:
    return native_tools().get(key)
