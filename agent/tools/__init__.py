"""Tool registry assembly. The tools form the navigate-don't-index funnel:
list_dir/glob (broad) -> grep (narrow) -> read (deep)."""

from .. import config
from .base import Registry, Tool
from .bash_tool import bash_tool
from .edit_tool import edit_tool, multi_edit_tool, write_tool
from .glob_tool import glob_tool
from .grep_tool import grep_tool
from .list_dir_tool import list_dir_tool
from .read_tool import read_tool

__all__ = ["Registry", "Tool", "default_registry"]


def default_registry(allow_exec=None, allow_edit=None) -> Registry:
    """Build the tool registry. The read-only navigation tools are always present.
    The `bash` tool (arbitrary command execution) is added ONLY when execution is
    enabled — `allow_exec=True`, or (when None) config.ALLOW_EXEC — so a default
    agent stays read-only and the model never sees a tool it can't use.
    The write tools (`edit`/`write`/`multi_edit`) are added ONLY when editing is
    enabled — `allow_edit=True`, or (when None) config.ALLOW_EDIT — and are gated by
    the permission engine at call time."""
    if allow_exec is None:
        allow_exec = config.ALLOW_EXEC
    if allow_edit is None:
        allow_edit = config.ALLOW_EDIT
    reg = Registry()
    reg.register(list_dir_tool)  # broad — orient
    reg.register(glob_tool)  # broad — locate files
    reg.register(grep_tool)  # narrow — find symbols inside them
    reg.register(read_tool)  # deep  — read the lines that matter
    if allow_exec:
        reg.register(bash_tool)  # execute — compile/lint/test to find real errors
    if allow_edit:
        reg.register(edit_tool)  # change — permission-gated file edits
        reg.register(write_tool)
        reg.register(multi_edit_tool)
    return reg
