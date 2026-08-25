"""Tool interface + registry (M2/§9). Self-describing tools: each carries its own
name, description, JSON-Schema params, and run function. Fail-closed default:
read_only=True (v1 ships only read-only navigation tools)."""

from dataclasses import dataclass
from typing import Callable

from .. import harmony_codec as hc


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments
    run: Callable  # (args: dict, sandbox) -> str
    read_only: bool = True


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def all(self):
        return list(self._tools.values())

    def harmony_tools(self):
        """Turn every registered tool into an openai-harmony ToolDescription for
        the developer message."""
        return [
            hc.tool_description(t.name, t.description, t.parameters)
            for t in self._tools.values()
        ]
