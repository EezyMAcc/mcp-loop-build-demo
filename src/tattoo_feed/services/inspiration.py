"""InspirationService — the one-at-a-time discovery and saving experience."""

from __future__ import annotations

from tattoo_feed.errors import TattooFeedError
from tattoo_feed.models import InspirationItem, Post
from tattoo_feed.repositories.json_repo import (
    InspirationRepository,
    SeenSetRepository,
)
from tattoo_feed.services.feed import FeedService


class InspirationService:
    """Serves unseen posts one at a time and manages the saved collection.

    Candidate posts are drawn from the merged :class:`FeedService` feed, so
    inspiration and the feed stay in lock-step. A persistent seen-set prevents
    the same post being shown twice until it is reset.
    """

    def __init__(
        self,
        feed_service: FeedService,
        inspiration_repo: InspirationRepository,
        seen_repo: SeenSetRepository,
    ) -> None:
        """Initialise the service.

        Args:
            feed_service: Source of candidate posts.
            inspiration_repo: Persistence for saved inspiration items.
            seen_repo: Persistence for the set of already-shown post ids.
        """
        self._feed = feed_service
        self._repo = inspiration_repo
        self._seen = seen_repo

    def next_inspiration(self) -> Post | None:
        """Return the newest post not yet shown, marking it seen.

        Returns:
            The next unseen :class:`Post`, or ``None`` if every current post
            has already been seen (suggest :meth:`reset_seen`).
        """
        seen = self._seen.all()
        for post in self._feed.get_feed():
            if post.id not in seen:
                self._seen.add(post.id)
                return post
        return None

    def save_to_inspiration(
        self, post_id: str, notes: str | None = None
    ) -> InspirationItem:
        """Save the post with ``post_id`` (from the current feed) for later.

        Args:
            post_id: The Instagram media id to save.
            notes: Optional free-text note to attach.

        Returns:
            The saved :class:`InspirationItem`.

        Raises:
            TattooFeedError: If no post with that id is in the current feed.
        """
        post = self._find_in_feed(post_id)
        if post is None:
            raise TattooFeedError(
                f"post {post_id} is not in the current feed; open it via "
                "next_inspiration or get_feed first"
            )
        item = InspirationItem(
            post_id=post.id,
            artist_handle=post.artist_handle,
            image_url=post.image_url,
            permalink=post.permalink,
            timestamp=post.timestamp,
            notes=notes,
        )
        return self._repo.add(item)

    def list_inspiration(self) -> list[InspirationItem]:
        """Return saved inspiration items in save order."""
        return self._repo.list()

    def remove_from_inspiration(self, post_id: str) -> None:
        """Remove a saved item (no error if it was not saved)."""
        self._repo.remove(post_id)

    def reset_seen(self) -> None:
        """Clear the seen-set so inspiration starts fresh."""
        self._seen.clear()

    def _find_in_feed(self, post_id: str) -> Post | None:
        """Return the feed post with ``post_id``, or ``None``."""
        for post in self._feed.get_feed():
            if post.id == post_id:
                return post
        return None
