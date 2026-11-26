"""The capabilities a user may call, as LangChain tools for an agent (WP5; used by the agents of WP12).

Every tool calls the capability gateway, so each call runs the same checks as any other (RBAC,
whitelist, read-only, arguments, data classes, the chat's switched-off MCP servers) and is audited. A
refused or failed call returns its message to the model as the tool's answer instead of raising, so the
agent can tell the user. (langchain-mcp-adapters is not used: its tools would call MCP servers directly,
around the whitelist, the guard and the audit.)
"""
from langchain_core.tools import StructuredTool

from capabilities import gateway
from capabilities.gateway import CapabilityError
from rbac.model import Access


def tools_for_agent(access: Access, claims: dict, *, reader: str | None = None, session_off=frozenset(),
                    input_data_class: str = "sensitive") -> list[StructuredTool]:
    """reader: the agent's model ("provider:model"); session_off: MCP servers switched off in this chat;
    input_data_class: the most restricted data the agent may put in tool arguments (unknown = sensitive)."""
    session_off = frozenset(session_off)
    tools = []
    for spec in gateway.tools_for(access, reader, session_off):

        async def run(_spec=spec, **arguments) -> str:
            try:
                result = await gateway.call(access, claims, _spec.capability_key, _spec.operation, arguments,
                                            input_data_class=input_data_class, reader=reader, session_off=session_off)
            except CapabilityError as e:
                return e.message
            return result.text

        tools.append(StructuredTool.from_function(coroutine=run, name=spec.name, description=spec.description,
                                                  args_schema=spec.parameters))
    return tools
