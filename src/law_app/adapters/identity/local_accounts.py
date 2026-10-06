"""Resolve sessions using the existing canonical Accounts store."""
from pathlib import Path


class LocalAccountsIdentity:
    def __init__(self, accounts, expected_path):
        self.accounts = accounts
        self.expected_path = Path(expected_path).absolute()

    def current(self, session_token):
        denied = PermissionError("Sign in with a current staff account first.")
        if (self.accounts is None or type(session_token) is not str or not session_token
                or self.accounts.path.absolute() != self.expected_path):
            raise denied
        try:
            actor = self.accounts.session_user(session_token)
        except (OSError, ValueError, KeyError, TypeError):
            raise denied from None
        if (not isinstance(actor, dict) or type(actor.get('email')) is not str or not actor['email']
                or actor.get('active') is not True or actor.get('must_change') is not False
                or type(actor.get('role')) is not str or actor['role'] not in {'attorney', 'paralegal'}):
            raise denied
        return actor
