"""Authentication helpers (fixture for the agent end-to-end test)."""

from db import get_user


def validate_token(token, user_id):
    """Validate a token for a user.

    Checks, in order:
      1. token and user_id are both non-empty
      2. the user exists
      3. the stored token matches the provided token
    Returns the user record if all checks pass, otherwise None.
    """
    if not token or not user_id:
        return None
    user = get_user(user_id)
    if user is None:
        return None
    if user["token"] != token:
        return None
    return user
