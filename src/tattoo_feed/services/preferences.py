"""PreferenceService — recording and recalling the user's taste notes."""

from __future__ import annotations

from tattoo_feed.models import Preference
from tattoo_feed.repositories.json_repo import PreferenceRepository


class PreferenceService:
    """Persists taste observations and reads them back.

    Preferences capture *taste* (e.g. "loves fine-line botanical work"), which
    is distinct from saving a specific image. The propose-then-confirm
    discipline (see ``CLAUDE.md`` §10) lives in the MCP tool description, not
    here — this service simply persists what it is given.
    """

    def __init__(self, repo: PreferenceRepository) -> None:
        """Initialise the service.

        Args:
            repo: Persistence for recorded preferences.
        """
        self._repo = repo

    def record_preference(self, observation: str) -> Preference:
        """Persist a taste observation.

        Args:
            observation: The taste note in natural language.

        Returns:
            The stored :class:`Preference`.

        Raises:
            pydantic.ValidationError: If ``observation`` is blank.
        """
        return self._repo.add(Preference(observation=observation))

    def get_preference_summary(self) -> list[Preference]:
        """Return every recorded preference, oldest first."""
        return self._repo.list()
