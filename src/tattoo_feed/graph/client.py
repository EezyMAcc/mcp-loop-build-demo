"""Instagram Business Discovery API client.

Wraps the one Graph API call this project makes — ``business_discovery`` on a
querying Business/Creator account — and turns its responses into clean domain
:class:`Post` objects. Responsibilities that live here (and nowhere else):

* **Video filtering** — ``VIDEO`` media never leave this layer (see ``PLAN.md``).
* **Carousel handling** — a ``CAROUSEL_ALBUM`` is represented by its first image.
* **A short TTL cache** — repeat lookups within the window skip the network.
* **429 backoff** — rate-limited requests are retried, then surfaced as a typed
  :class:`RateLimitedError`.
* **Typed error mapping** — every Graph error becomes a member of the
  ``TattooFeedError`` hierarchy; no raw HTTP or JSON leaks upward.

All HTTP is performed through an injected :class:`httpx.Client`, so tests drive
it entirely with ``respx`` and never touch the network.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, NoReturn

import httpx

from tattoo_feed.errors import (
    AccountNotFoundError,
    NotAProfessionalAccountError,
    RateLimitedError,
    TattooFeedError,
    TokenExpiredError,
)
from tattoo_feed.models import MediaType, Post

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"

# Media fields requested per post. ``children`` lets us resolve a carousel's
# first image without a second round-trip.
_MEDIA_FIELDS = (
    "id,caption,media_type,media_url,permalink,timestamp,children{media_type,media_url}"
)

# Graph API error codes/subcodes we recognise. Documented here so the mapping
# below reads as intent rather than magic numbers.
_TOKEN_ERROR_CODE = 190
_RATE_LIMIT_CODES = frozenset({4, 17, 32})
_NOT_PROFESSIONAL_SUBCODE = 2207013
_NOT_FOUND_SUBCODE = 2207006


def _discovery_field(handle: str, subfields: str) -> str:
    """Build a ``business_discovery.username(...)`` field expression."""
    return f"business_discovery.username({handle})" + "{" + subfields + "}"


class BusinessDiscoveryClient:
    """Fetches and validates artists via Instagram Business Discovery."""

    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        *,
        http_client: httpx.Client,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        cache_ttl_seconds: float = 300.0,
        max_retries: int = 2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise the client.

        Args:
            access_token: Long-lived Graph API access token.
            ig_user_id: The querying Business/Creator account id.
            http_client: The httpx client used for every request (injected so
                tests can mock it with respx).
            base_url: Graph API base URL.
            api_version: Graph API version segment (e.g. ``v21.0``).
            cache_ttl_seconds: How long a media fetch stays fresh in-process.
            max_retries: How many times to retry a 429 before giving up.
            clock: Monotonic clock source (injected for deterministic TTL tests).
            sleep: Sleep function (injected so tests don't actually wait).
        """
        self._access_token = access_token
        self._ig_user_id = ig_user_id
        self._http = http_client
        self._endpoint = f"{base_url}/{api_version}/{ig_user_id}"
        self._cache_ttl = cache_ttl_seconds
        self._max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[str, int], tuple[float, list[Post]]] = {}

    def validate_account(self, handle: str) -> str:
        """Confirm ``handle`` is a reachable professional account.

        Args:
            handle: The Instagram handle to validate (with or without ``@``).

        Returns:
            The discovered account's Instagram id.

        Raises:
            AccountNotFoundError: If no account resolves for the handle.
            NotAProfessionalAccountError: If the account is not Business/Creator.
            TokenExpiredError: If the access token has expired.
            RateLimitedError: If Instagram rate-limited the request.
            TattooFeedError: For any other Graph API failure.
        """
        clean = handle.strip().removeprefix("@").lower()
        payload = self._get(_discovery_field(clean, "id,username"))
        discovery = self._business_discovery(payload, clean)
        account_id = discovery.get("id")
        if not isinstance(account_id, str):
            raise AccountNotFoundError(f"no account id returned for @{clean}")
        return account_id

    def fetch_recent_media(self, handle: str, limit: int = 10) -> list[Post]:
        """Fetch an artist's recent image posts, newest as returned by Instagram.

        Videos are dropped and carousels are reduced to their first image. The
        result is cached per ``(handle, limit)`` for ``cache_ttl_seconds``.

        Args:
            handle: The artist's handle (with or without ``@``).
            limit: Maximum number of posts to request.

        Returns:
            The artist's posts as :class:`Post` objects (videos excluded).

        Raises:
            AccountNotFoundError: If no account resolves for the handle.
            NotAProfessionalAccountError: If the account is not Business/Creator.
            TokenExpiredError: If the access token has expired.
            RateLimitedError: If Instagram rate-limited the request.
            TattooFeedError: For any other Graph API failure.
        """
        clean = handle.strip().removeprefix("@").lower()
        cache_key = (clean, limit)
        cached = self._cache.get(cache_key)
        if cached is not None and self._clock() < cached[0]:
            return cached[1]

        subfields = f"media.limit({limit})" + "{" + _MEDIA_FIELDS + "}"
        payload = self._get(_discovery_field(clean, subfields))
        discovery = self._business_discovery(payload, clean)
        rows = discovery.get("media", {}).get("data", [])
        posts = self._parse_media(clean, rows)

        self._cache[cache_key] = (self._clock() + self._cache_ttl, posts)
        return posts

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    # --- internals ---------------------------------------------------------

    def _business_discovery(
        self, payload: dict[str, Any], handle: str
    ) -> dict[str, Any]:
        """Extract the ``business_discovery`` block or raise not-found."""
        discovery = payload.get("business_discovery")
        if not isinstance(discovery, dict):
            raise AccountNotFoundError(f"no account found for @{handle}")
        return discovery

    def _parse_media(self, handle: str, rows: list[Any]) -> list[Post]:
        """Convert raw media dicts into Posts, filtering videos and carousels."""
        posts: list[Post] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            media_type = row.get("media_type")
            if media_type == "VIDEO":
                continue  # videos are filtered out entirely (PLAN.md §2)
            image_url = self._resolve_image_url(row, media_type)
            if image_url is None:
                continue  # no usable still image (e.g. video-only carousel)
            try:
                posts.append(
                    Post(
                        id=row["id"],
                        artist_handle=handle,
                        caption=row.get("caption"),
                        media_type=MediaType(str(media_type)),
                        image_url=image_url,
                        permalink=row["permalink"],
                        timestamp=row["timestamp"],
                    )
                )
            except (KeyError, ValueError) as exc:
                # A single malformed post must not sink the whole fetch.
                logger.warning("skipping malformed post from @%s: %s", handle, exc)
        return posts

    def _resolve_image_url(self, row: dict[str, Any], media_type: Any) -> str | None:
        """Return the still-image URL for a post, or None if there is none.

        For a carousel we prefer the parent's ``media_url`` (the cover) and fall
        back to the first child image — never a video frame.
        """
        url = row.get("media_url")
        if isinstance(url, str) and url:
            return url
        if media_type == "CAROUSEL_ALBUM":
            children = row.get("children", {}).get("data", [])
            for child in children:
                if isinstance(child, dict) and child.get("media_type") != "VIDEO":
                    child_url = child.get("media_url")
                    if isinstance(child_url, str) and child_url:
                        return child_url
        return None

    def _get(self, fields: str) -> dict[str, Any]:
        """Perform a Graph API GET with retry-on-429 and typed error mapping."""
        params = {"fields": fields, "access_token": self._access_token}
        attempt = 0
        while True:
            try:
                response = self._http.get(self._endpoint, params=params)
            except httpx.RequestError as exc:
                raise TattooFeedError(
                    f"could not reach Instagram Graph API: {exc}"
                ) from exc

            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                if attempt < self._max_retries:
                    self._sleep(self._retry_after(response, attempt))
                    attempt += 1
                    continue
                raise RateLimitedError(
                    "Instagram rate limit hit; try again later",
                    retry_after_seconds=self._retry_after(response, attempt),
                )

            if response.is_success:
                return self._json(response)

            _raise_for_graph_error(self._json(response), response.status_code)

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        """Decode a JSON object body, mapping a non-object to a typed error."""
        try:
            data: object = response.json()
        except ValueError as exc:
            raise TattooFeedError("Instagram returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise TattooFeedError("Instagram returned an unexpected payload")
        return data

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        """Compute a backoff delay, honouring a ``Retry-After`` header if given."""
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return float(header)
            except ValueError:
                pass
        # Exponential backoff: 1s, 2s, 4s, ...
        return float(2**attempt)


def _raise_for_graph_error(payload: dict[str, Any], status_code: int) -> NoReturn:
    """Map a Graph API error payload to a typed exception and raise it.

    Args:
        payload: The decoded JSON error body.
        status_code: The HTTP status code that accompanied it.

    Raises:
        TokenExpiredError, NotAProfessionalAccountError, AccountNotFoundError,
        RateLimitedError, or TattooFeedError, depending on the error.
    """
    error = payload.get("error", {})
    code = error.get("code")
    subcode = error.get("error_subcode")
    message = error.get("message", "Instagram Graph API error")
    lowered = message.lower()

    if code == _TOKEN_ERROR_CODE:
        raise TokenExpiredError(
            f"Instagram access token is invalid or expired: {message}. "
            "Mint a new long-lived token and update IG_ACCESS_TOKEN."
        )
    if code in _RATE_LIMIT_CODES or status_code == httpx.codes.TOO_MANY_REQUESTS:
        raise RateLimitedError(message)
    if subcode == _NOT_PROFESSIONAL_SUBCODE or "not a business" in lowered:
        raise NotAProfessionalAccountError(
            f"that account is not a professional (Business/Creator) account: {message}"
        )
    if subcode == _NOT_FOUND_SUBCODE or "cannot be found" in lowered or code == 100:
        raise AccountNotFoundError(f"no Instagram account found: {message}")
    raise TattooFeedError(f"Instagram Graph API error: {message}")
