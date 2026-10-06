"""Read-side transition job metadata; results never establish current authority."""

from typing import Any, Protocol


class JobQueries(Protocol):
    def get(self, job_id: str, *, active_only: bool = False) -> dict[str, Any] | None:
        """Read active metadata first; optionally exclude completed records entirely."""
        ...

    def list_jobs(self, client: str | None = None, kind: str | None = None,
                  recent: float = 600) -> list[dict[str, Any]]:
        """Return active then recent completed metadata in the existing queue order."""
        ...
