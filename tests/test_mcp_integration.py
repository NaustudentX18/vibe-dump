from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from vibedump.agent.registry import ToolRegistry, ToolError

def test_mcp_tool_registration_and_dispatch() -> None:
    registry = ToolRegistry()

    # Mock MCP JSON Schema
    mcp_schema = {
        "name": "calculate_tax",
        "description": "Calculate simple tax for an amount",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "The subtotal amount"
                },
                "rate": {
                    "type": "number",
                    "description": "Tax rate decimal",
                    "default": 0.15
                },
                "item_name": {
                    "type": "string",
                    "description": "Name of the item"
                }
            },
            "required": ["amount", "item_name"]
        }
    }

    # Dynamic handler
    def handler(args) -> dict[str, Any]:
        tax = args.amount * args.rate
        return {
            "item_name": args.item_name,
            "tax": tax,
            "total": args.amount + tax
        }

    # Register the MCP tool
    registry.register_mcp_tool(mcp_schema, handler)

    # Verify registration metadata
    assert "calculate_tax" in registry.names()
    tool = registry.get("calculate_tax")
    assert tool is not None
    assert tool.name == "calculate_tax"
    assert tool.description == "Calculate simple tax for an amount"
    
    # Verify the generated input_schema
    schemas = registry.schemas()
    tax_schema = [s for s in schemas if s["name"] == "calculate_tax"][0]
    assert tax_schema["description"] == "Calculate simple tax for an amount"
    props = tax_schema["input_schema"]["properties"]
    assert "amount" in props
    assert "rate" in props
    assert "item_name" in props

    # Dispatch successfully with all required fields
    result = registry.dispatch("calculate_tax", {"amount": 100.0, "item_name": "Laptop"})
    assert result == {"item_name": "Laptop", "tax": 15.0, "total": 115.0}

    # Dispatch with custom rate override
    result_override = registry.dispatch("calculate_tax", {"amount": 200.0, "rate": 0.20, "item_name": "Phone"})
    assert result_override == {"item_name": "Phone", "tax": 40.0, "total": 240.0}

    # Dispatch missing required field should fail validation (wrapped in ToolError)
    with pytest.raises(ToolError) as exc_info:
        registry.dispatch("calculate_tax", {"rate": 0.10, "item_name": "Tablet"})
    assert "amount" in exc_info.value.message
