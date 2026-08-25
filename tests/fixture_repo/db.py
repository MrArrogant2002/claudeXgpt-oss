"""Tiny in-memory user store (fixture for the agent end-to-end test)."""

_USERS = {
    1: {"id": 1, "name": "alice", "token": "abc123"},
    2: {"id": 2, "name": "bob", "token": "def456"},
}


def get_user(user_id):
    """Return the user record for user_id, or None if not found."""
    return _USERS.get(user_id)
