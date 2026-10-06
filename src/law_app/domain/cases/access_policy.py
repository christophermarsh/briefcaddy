"""Case operations use current canonical access, never list visibility."""


class CaseAccessPolicy:
    def __init__(self, reader):
        self.reader = reader

    def require(self, actor, client_id, *, prospect=False):
        if not self.reader.may_open(actor, client_id, prospect=prospect):
            # Hidden and nonexistent objects have the same denial.
            raise LookupError('unknown client')
