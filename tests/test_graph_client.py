"""Tests for the Business Discovery client — all HTTP mocked with respx."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from tattoo_feed.errors import (
    AccountNotFoundError,
    NotAProfessionalAccountError,
    RateLimitedError,
    TattooFeedError,
    TokenExpiredError,
)
from tattoo_feed.graph.client import BusinessDiscoveryClient
from tattoo_feed.models import MediaType

ENDPOINT = "https://graph.facebook.com/v21.0/123"


def _make_client(**overrides: Any) -> BusinessDiscoveryClient:
    params: dict[str, Any] = {
        "access_token": "token",
        "ig_user_id": "123",
        "http_client": httpx.Client(),
        "sleep": lambda _seconds: None,
    }
    params.update(overrides)
    return BusinessDiscoveryClient(**params)


def _media_response(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200, json={"business_discovery": {"media": {"data": rows}}, "id": "123"}
    )


# --- validate_account ------------------------------------------------------


@respx.mock
def test_validate_account_returns_id() -> None:
    respx.get(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"business_discovery": {"id": "999", "username": "artist"}}
        )
    )
    assert _make_client().validate_account("@Artist") == "999"


@respx.mock
def test_validate_account_missing_discovery_is_not_found() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json={"id": "123"}))
    with pytest.raises(AccountNotFoundError):
        _make_client().validate_account("ghost")


@respx.mock
def test_validate_account_missing_id_is_not_found() -> None:
    respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"business_discovery": {"username": "x"}})
    )
    with pytest.raises(AccountNotFoundError):
        _make_client().validate_account("x")


# --- fetch_recent_media: filtering & carousels -----------------------------


@respx.mock
def test_fetch_filters_videos_and_resolves_carousels() -> None:
    rows = [
        {
            "id": "1",
            "caption": "still",
            "media_type": "IMAGE",
            "media_url": "https://cdn/1.jpg",
            "permalink": "https://ig/p/1",
            "timestamp": "2024-09-29T17:24:54+0000",
        },
        {
            "id": "2",
            "media_type": "VIDEO",
            "media_url": "https://cdn/2.mp4",
            "permalink": "https://ig/p/2",
            "timestamp": "2024-09-28T17:24:54+0000",
        },
        {
            "id": "3",
            "media_type": "CAROUSEL_ALBUM",
            "permalink": "https://ig/p/3",
            "timestamp": "2024-09-27T17:24:54+0000",
            "children": {
                "data": [
                    {"media_type": "VIDEO", "media_url": "https://cdn/3v.mp4"},
                    {"media_type": "IMAGE", "media_url": "https://cdn/3a.jpg"},
                ]
            },
        },
    ]
    respx.get(ENDPOINT).mock(return_value=_media_response(rows))
    posts = _make_client().fetch_recent_media("artist")

    assert [p.id for p in posts] == ["1", "3"]  # video dropped, order preserved
    assert posts[0].media_type is MediaType.IMAGE
    assert posts[1].media_type is MediaType.CAROUSEL_ALBUM
    # First non-video child image is used when the parent has no media_url.
    assert posts[1].image_url == "https://cdn/3a.jpg"
    assert posts[0].artist_handle == "artist"


@respx.mock
def test_carousel_prefers_parent_media_url() -> None:
    rows = [
        {
            "id": "1",
            "media_type": "CAROUSEL_ALBUM",
            "media_url": "https://cdn/cover.jpg",
            "permalink": "https://ig/p/1",
            "timestamp": "2024-09-27T17:24:54+0000",
            "children": {
                "data": [{"media_type": "IMAGE", "media_url": "https://cdn/x.jpg"}]
            },
        }
    ]
    respx.get(ENDPOINT).mock(return_value=_media_response(rows))
    posts = _make_client().fetch_recent_media("artist")
    assert posts[0].image_url == "https://cdn/cover.jpg"


@respx.mock
def test_video_only_carousel_is_skipped() -> None:
    rows = [
        {
            "id": "1",
            "media_type": "CAROUSEL_ALBUM",
            "permalink": "https://ig/p/1",
            "timestamp": "2024-09-27T17:24:54+0000",
            "children": {
                "data": [{"media_type": "VIDEO", "media_url": "https://cdn/v.mp4"}]
            },
        }
    ]
    respx.get(ENDPOINT).mock(return_value=_media_response(rows))
    assert _make_client().fetch_recent_media("artist") == []


@respx.mock
def test_carousel_skips_junk_children_until_first_usable_image() -> None:
    rows = [
        {
            "id": "1",
            "media_type": "CAROUSEL_ALBUM",
            "permalink": "https://ig/p/1",
            "timestamp": "2024-09-27T17:24:54+0000",
            "children": {
                "data": [
                    "not-a-dict",
                    {"media_type": "IMAGE"},  # image child with no media_url
                    {"media_type": "IMAGE", "media_url": "https://cdn/good.jpg"},
                ]
            },
        }
    ]
    respx.get(ENDPOINT).mock(return_value=_media_response(rows))
    posts = _make_client().fetch_recent_media("artist")
    assert posts[0].image_url == "https://cdn/good.jpg"


@respx.mock
def test_malformed_post_is_skipped_not_fatal() -> None:
    rows = [
        {
            "id": "1",
            "media_type": "IMAGE",
            "media_url": "https://cdn/1.jpg",
        },  # no permalink
        {
            "id": "0",
            "media_type": "IMAGE",
            "permalink": "https://ig/p/0",
            "timestamp": "2024-09-27T17:24:54+0000",
        },  # IMAGE with no media_url -> skipped
        {
            "id": "2",
            "media_type": "IMAGE",
            "media_url": "https://cdn/2.jpg",
            "permalink": "https://ig/p/2",
            "timestamp": "2024-09-27T17:24:54+0000",
        },
        "not-a-dict",
    ]
    respx.get(ENDPOINT).mock(return_value=_media_response(rows))
    posts = _make_client().fetch_recent_media("artist")
    assert [p.id for p in posts] == ["2"]


# --- caching ---------------------------------------------------------------


@respx.mock
def test_fetch_is_cached_within_ttl() -> None:
    route = respx.get(ENDPOINT).mock(return_value=_media_response([]))
    client = _make_client(clock=lambda: 1000.0)
    client.fetch_recent_media("artist")
    client.fetch_recent_media("artist")
    assert route.call_count == 1  # second call served from cache


@respx.mock
def test_cache_expires_after_ttl() -> None:
    route = respx.get(ENDPOINT).mock(return_value=_media_response([]))
    now = {"t": 1000.0}
    client = _make_client(clock=lambda: now["t"], cache_ttl_seconds=300.0)
    client.fetch_recent_media("artist")
    now["t"] = 1400.0  # past the TTL window
    client.fetch_recent_media("artist")
    assert route.call_count == 2


# --- 429 backoff -----------------------------------------------------------


@respx.mock
def test_retries_then_succeeds_on_429() -> None:
    route = respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            _media_response([]),
        ]
    )
    _make_client(max_retries=2).fetch_recent_media("artist")
    assert route.call_count == 3


@respx.mock
def test_429_exhausted_raises_rate_limited() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimitedError):
        _make_client(max_retries=1).fetch_recent_media("artist")


@respx.mock
def test_retry_after_header_is_honoured() -> None:
    respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            _media_response([]),
        ]
    )
    slept: list[float] = []
    _make_client(max_retries=1, sleep=slept.append).fetch_recent_media("artist")
    assert slept == [7.0]


@respx.mock
def test_bad_retry_after_falls_back_to_backoff() -> None:
    respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "soon"}),
            _media_response([]),
        ]
    )
    slept: list[float] = []
    _make_client(max_retries=1, sleep=slept.append).fetch_recent_media("artist")
    assert slept == [1.0]  # 2**0


# --- typed error mapping ---------------------------------------------------


def _error(
    code: int, subcode: int | None = None, message: str = "boom"
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if subcode is not None:
        err["error_subcode"] = subcode
    return {"error": err}


@respx.mock
def test_expired_token_maps_to_token_error() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(400, json=_error(190)))
    with pytest.raises(TokenExpiredError):
        _make_client().validate_account("artist")


@respx.mock
def test_not_professional_maps_by_subcode() -> None:
    respx.get(ENDPOINT).mock(
        return_value=httpx.Response(400, json=_error(100, subcode=2207013))
    )
    with pytest.raises(NotAProfessionalAccountError):
        _make_client().validate_account("artist")


@respx.mock
def test_not_found_maps_by_subcode() -> None:
    respx.get(ENDPOINT).mock(
        return_value=httpx.Response(400, json=_error(100, subcode=2207006))
    )
    with pytest.raises(AccountNotFoundError):
        _make_client().validate_account("artist")


@respx.mock
def test_rate_limit_code_maps_to_rate_limited() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(400, json=_error(4)))
    with pytest.raises(RateLimitedError):
        _make_client().validate_account("artist")


@respx.mock
def test_unknown_error_maps_to_base_error() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(400, json=_error(999)))
    with pytest.raises(TattooFeedError):
        _make_client().validate_account("artist")


# --- transport & payload edge cases ----------------------------------------


@respx.mock
def test_connection_error_maps_to_typed_error() -> None:
    respx.get(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(TattooFeedError):
        _make_client().validate_account("artist")


@respx.mock
def test_non_json_body_maps_to_typed_error() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(TattooFeedError):
        _make_client().fetch_recent_media("artist")


@respx.mock
def test_non_object_json_maps_to_typed_error() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(TattooFeedError):
        _make_client().fetch_recent_media("artist")


def test_close_closes_http_client() -> None:
    http = httpx.Client()
    client = _make_client(http_client=http)
    client.close()
    assert http.is_closed
