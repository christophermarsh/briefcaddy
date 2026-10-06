"""Current staff session authority supplied by trusted composition."""
from typing import Protocol


class StaffIdentity(Protocol):
    def current(self, session_token: str | None) -> dict:
        """Reload a complete active staff session, or raise PermissionError."""
        ...
