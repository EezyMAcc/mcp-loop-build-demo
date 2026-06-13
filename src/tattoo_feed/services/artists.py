"""ArtistService — managing the tracked list of artists."""

from __future__ import annotations

from tattoo_feed.graph.client import BusinessDiscoveryClient
from tattoo_feed.models import Artist, normalize_handle
from tattoo_feed.repositories.json_repo import ArtistRepository


class ArtistService:
    """Adds, lists, and removes the tattoo artists the user tracks."""

    def __init__(self, repo: ArtistRepository, client: BusinessDiscoveryClient) -> None:
        """Initialise the service.

        Args:
            repo: Persistence for the tracked artists.
            client: Graph client used to validate handles before saving.
        """
        self._repo = repo
        self._client = client

    def list_artists(self) -> list[Artist]:
        """Return the tracked artists in the order they were added."""
        return self._repo.list()

    def add_artist(self, handle: str) -> Artist:
        """Validate ``handle`` resolves to a professional account, then save it.

        Args:
            handle: The Instagram handle to track (with or without ``@``).

        Returns:
            The saved :class:`Artist`, including its resolved Instagram id.

        Raises:
            AccountNotFoundError: If no account resolves for the handle.
            NotAProfessionalAccountError: If the account is not Business/Creator.
            TokenExpiredError: If the access token has expired.
            RateLimitedError: If Instagram rate-limited the request.
            TattooFeedError: For any other Graph API failure.
        """
        clean = normalize_handle(handle)
        # Validate before persisting so the store only ever holds real,
        # reachable professional accounts.
        account_id = self._client.validate_account(clean)
        return self._repo.add(Artist(handle=clean, ig_user_id=account_id))

    def remove_artist(self, handle: str) -> None:
        """Stop tracking ``handle`` (no error if it was not tracked)."""
        self._repo.remove(normalize_handle(handle))
