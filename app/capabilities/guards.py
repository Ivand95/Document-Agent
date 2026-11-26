"""Read-only guards: checked right before every call to an external server (MCP, later CLI).

The whitelist already refuses any operation not marked read_only. A guard adds per-server rules on the
arguments, for servers whose "read-only" tools can still be steered into writing (e.g. a generic
`execute_operation` tool, WP8). A capability names its guard in config ("guard"); a named guard that
isn't registered refuses every call, so a typo never means "no guard".
"""
from collections.abc import Callable

from capabilities.gateway import CapabilityError
from capabilities.registry import Capability, Operation

# guard(capability, operation, args): raises CapabilityError("not_read_only", ...) to refuse
Guard = Callable[[Capability, Operation, dict], None]
GUARDS: dict[str, Guard] = {}


def register(name: str, guard: Guard) -> None:
    GUARDS[name] = guard


def check(capability: Capability, op: Operation, args: dict) -> None:
    if not op.read_only:  # never reached through the gateway, which refuses it first: defence in depth
        raise CapabilityError("not_read_only", f"{capability.key}.{op.operation}")
    name = (capability.config or {}).get("guard")
    if name is None:
        return
    guard = GUARDS.get(name)
    if guard is None:
        raise CapabilityError("not_available", f"{capability.key}: guard {name!r} is not registered")
    guard(capability, op, args)
