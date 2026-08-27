"""Tool registry assembly. The tools form the navigate-don't-index funnel:
list_dir/glob (broad) -> grep (narrow) -> read (deep)."""

from .base import Registry, Tool
from .glob_tool import glob_tool
from .grep_tool import grep_tool
from .list_dir_tool import list_dir_tool
from .read_tool import read_tool

__all__ = ["Registry", "Tool", "default_registry"]


def default_registry() -> Registry:
    reg = Registry()
    reg.register(list_dir_tool)  # broad — orient
    reg.register(glob_tool)  # broad — locate files
    reg.register(grep_tool)  # narrow — find symbols inside them
    reg.register(read_tool)  # deep  — read the lines that matter
    return reg
