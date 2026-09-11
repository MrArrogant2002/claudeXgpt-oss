"""Local LSP (Language Server Protocol) client + server management.

Gives the agent semantic code intelligence (definitions, references, types,
outline) by talking to a language server running ON THIS MACHINE over stdio.
No network. Used by the `lsp` tool; falls back to grep when no server is present.
"""
