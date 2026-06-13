"""FeedService — the merged, newest-first feed across all tracked artists."""

from __future__ import annotations

import logging

from tattoo_feed.errors import TattooFeedError
from tattoo_feed.graph.client import BusinessDiscoveryClient
from tattoo_feed.models import Post
from tattoo_feed.repositories.json_repo import ArtistRepository

logger = logging.getLogger(__name__)

DEFAULT_LIMIT_PER_ARTIST = 10


class FeedService:
    """Builds a single feed by merging each artist's recent media."""

    def __init__(self, repo: ArtistRepository, client: BusinessDiscoveryClient) -> None:
        """Initialise the service.

        Args:
            repo: Source of the tracked artists.
            client: Graph client used to fetch each artist's media.
        """
        self._repo = repo
        self._client = client

    def get_feed(self, limit_per_artist: int = DEFAULT_LIMIT_PER_ARTIST) -> list[Post]:
        """Return all tracked artists' recent posts, merged newest-first.

        A single artist whose fetch fails is skipped and logged — one bad
        account never sinks the whole feed.

        Args:
            limit_per_artist: Max posts to request per artist.

        Returns:
            Image posts across all artists, sorted newest-first by timestamp.
        """
        posts: list[Post] = []
        for artist in self._repo.list():
            try:
                posts.extend(
                    self._client.fetch_recent_media(artist.handle, limit_per_artist)
                )
            except TattooFeedError as exc:
                # Skip-and-note: keep the rest of the feed alive.
                logger.warning("skipping artist @%s in feed: %s", artist.handle, exc)
        posts.sort(key=lambda post: post.timestamp, reverse=True)
        return posts
