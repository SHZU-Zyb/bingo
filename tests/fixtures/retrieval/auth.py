def authenticate_user(password, expected_hash):
    """Validate a user's credentials before granting access to an account."""
    return password == expected_hash


def login_user(password, expected_hash):
    """Grant login only after authentication succeeds."""
    return authenticate_user(password, expected_hash)
