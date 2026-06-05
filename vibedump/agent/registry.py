"""ToolRegistry: bind a list of ToolDefinitions into an LLM-callable surface.

The registry is the single object the OpenClaude runtime hands to the
LLM layer. It exposes:

* :meth:`register` to add a tool (raises on duplicate names)
* :meth:`schemas` to render the LLM function-calling payload
* :meth:`dispatch` to invoke a tool by name with structured error
  wrapping and Pydantic validation
* :meth:`names` and ``__contains__`` for membership / iteration

``dispatch`` validates the raw ``args`` dict against the tool's
``input_model`` before forwarding, so every handler invocation is
guaranteed to receive a populated Pydantic model instance.
Non-:class:`ToolError` exceptions are wrapped in a fresh ``ToolError``
so the LLM gets a uniform failure shape.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from .tools import ToolDefinition, ToolError, build_tools

# ``build_tools`` is re-exported here for callers that prefer to wire
# a registry in one import. It avoids forcing two ``from`` statements.
__all__ = ["ToolRegistry", "ToolDefinition", "ToolError", "build_tools"]


class ToolRegistry:
    """In-memory tool registry keyed by tool name.

    The registry validates arguments against the tool's Pydantic
    ``input_model`` on every dispatch, so the contract with handlers
    is "you always receive a valid model instance".
    """

    def __init__(self, tools: Iterable[ToolDefinition] | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        if tools is not None:
            for tool in tools:
                self.register(tool)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def register(self, tool: ToolDefinition) -> None:
        """Add ``tool`` to the registry.

        Raises ``ValueError`` (not ``ToolError``) when the name is
        already taken. ``ToolError`` is reserved for runtime dispatch
        failures, not configuration errors.
        """
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def register_mcp_tool(self, mcp_schema: dict[str, Any], handler: Callable[[Any], dict[str, Any]]) -> None:
        """Parse a standard Model Context Protocol JSON schema and register it as a tool."""
        from pydantic import create_model, Field

        name = mcp_schema["name"]
        description = mcp_schema.get("description", "")
        input_schema = mcp_schema.get("inputSchema", {})

        properties = input_schema.get("properties", {})
        required_fields = input_schema.get("required", [])

        fields = {}
        for field_name, field_info in properties.items():
            json_type = field_info.get("type", "string")
            if json_type == "string":
                py_type = str
            elif json_type == "integer":
                py_type = int
            elif json_type == "number":
                py_type = float
            elif json_type == "boolean":
                py_type = bool
            elif json_type == "array":
                py_type = list
            elif json_type == "object":
                py_type = dict
            else:
                py_type = Any

            field_desc = field_info.get("description", "")
            if field_name in required_fields:
                default_val = ...
            else:
                default_val = field_info.get("default", None)

            fields[field_name] = (py_type, Field(default=default_val, description=field_desc))

        if fields:
            input_model = create_model(f"{name}_input", **fields)
        else:
            input_model = create_model(f"{name}_input")

        tool = ToolDefinition(
            name=name,
            description=description,
            input_model=input_model,
            handler=handler,
        )
        self.register(tool)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        """Return a stable list of registered tool names."""
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """Return the LLM function-calling payload.

        Each entry has the shape ``{name, description, input_schema}``
        where ``input_schema`` is generated from the Pydantic input
        model via ``model_json_schema()``. Tools are returned in
        registration order so the LLM prompt stays stable.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema(),
            }
            for tool in self._tools.values()
        ]

    def get(self, name: str) -> ToolDefinition | None:
        """Return the tool registered under ``name`` or ``None``."""
        return self._tools.get(name)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, name: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Invoke the tool registered under ``name``.

        ``args`` is validated against the tool's Pydantic ``input_model``
        before being forwarded; an empty dict is substituted when
        ``None`` is passed so tools with no parameters can be called
        with ``dispatch("name")`` or ``dispatch("name", None)``
        interchangeably.

        Exceptions raised by the handler:
        * :class:`ToolError` is re-raised unchanged.
        * ``pydantic.ValidationError`` is wrapped in a :class:`ToolError`
          so the LLM layer never sees Pydantic internals.
        * Any other exception is wrapped in a :class:`ToolError`` whose
          ``name`` is the tool name and whose ``message`` is the
          stringified cause.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(name, f"unknown tool: {name!r}")
        raw_args: dict[str, Any] = dict(args) if args is not None else {}
        try:
            validated = tool.input_model.model_validate(raw_args)
        except ValidationError as exc:
            raise ToolError(name, f"invalid arguments: {exc}") from exc
        try:
            return tool.handler(validated)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - intentional broad catch
            raise ToolError(name, str(exc)) from exc

    # ------------------------------------------------------------------
    # Dunder surface
    # ------------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())
