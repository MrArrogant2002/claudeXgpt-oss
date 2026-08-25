"""Login entry point (fixture for the agent end-to-end test)."""

from auth import validate_token


def login(token, user_id):
    user = validate_token(token, user_id)
    if user is None:
        return {"status": 401, "error": "unauthorized"}
    return {"status": 200, "user": user["name"]}


if __name__ == "__main__":
    print(login("abc123", 1))
    print(login("wrong", 1))
