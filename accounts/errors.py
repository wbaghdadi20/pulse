from core.errors import AuthenticationError, ConflictError


class EmailTaken(ConflictError):
    def __init__(self) -> None:
        super().__init__("An account with this email already exists.")


class InvalidCredentials(AuthenticationError):
    """Unknown email and wrong password are one error: which it was stays private."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password.")
