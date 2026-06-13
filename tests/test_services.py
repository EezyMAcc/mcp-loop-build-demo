"""Tests for the core services, with tmp_path repos and a fake Graph client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from tattoo_feed.errors import NotAProfessionalAccountError, TattooFeedError
from tattoo_feed.models import Artist, MediaType, Post
from tattoo_feed.repositories.json_repo import (
    ArtistRepository,
    InspirationRepository,
    PreferenceRepository,
    SeenSetRepository,
)
from tattoo_feed.services.artists import ArtistService
from tattoo_feed.services.feed import FeedService
from tattoo_feed.services.inspiration import InspirationService
from tattoo_feed.services.preferences import PreferenceService

BASE_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _post(post_id: str, handle: str, minutes: int) -> Post:
    return Post(
        id=post_id,
        artist_handle=handle,
        media_type=MediaType.IMAGE,
        image_url=f"https://cdn/{post_id}.jpg",
        permalink=f"https://ig/p/{post_id}",
        timestamp=BASE_TS + timedelta(minutes=minutes),
    )


class FakeClient:
    """Duck-typed stand-in for BusinessDiscoveryClient."""

    def __init__(
        self,
        media: dict[str, list[Post]] | None = None,
        invalid: set[str] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self._media = media or {}
        self._invalid = invalid or set()
        self._failing = failing or set()

    def validate_account(self, handle: str) -> str:
        if handle in self._invalid:
            raise NotAProfessionalAccountError(f"@{handle} is not professional")
        return f"id-{handle}"

    def fetch_recent_media(self, handle: str, limit: int = 10) -> list[Post]:
        if handle in self._failing:
            raise TattooFeedError(f"fetch failed for @{handle}")
        return self._media.get(handle, [])


# --- ArtistService ---------------------------------------------------------


def test_add_artist_validates_then_stores_with_id(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    service = ArtistService(repo, FakeClient())  # type: ignore[arg-type]
    artist = service.add_artist("@CoolArtist")
    assert artist.handle == "coolartist"
    assert artist.ig_user_id == "id-coolartist"
    assert [a.handle for a in service.list_artists()] == ["coolartist"]


def test_add_artist_propagates_validation_error(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    service = ArtistService(repo, FakeClient(invalid={"personal"}))  # type: ignore[arg-type]
    with pytest.raises(NotAProfessionalAccountError):
        service.add_artist("personal")
    assert service.list_artists() == []  # nothing persisted on failure


def test_remove_artist_is_idempotent(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    service = ArtistService(repo, FakeClient())  # type: ignore[arg-type]
    service.add_artist("alice")
    service.remove_artist("@Alice")  # normalized match
    assert service.list_artists() == []
    service.remove_artist("alice")  # no error when absent


# --- FeedService -----------------------------------------------------------


def test_get_feed_merges_and_sorts_newest_first(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    repo.add(Artist(handle="alice"))
    repo.add(Artist(handle="bob"))
    client = FakeClient(
        media={
            "alice": [_post("a1", "alice", 0), _post("a2", "alice", 30)],
            "bob": [_post("b1", "bob", 15)],
        }
    )
    feed = FeedService(repo, client)  # type: ignore[arg-type]
    posts = feed.get_feed()
    assert [p.id for p in posts] == ["a2", "b1", "a1"]  # newest-first by timestamp


def test_get_feed_skips_failing_artist(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    repo.add(Artist(handle="alice"))
    repo.add(Artist(handle="bob"))
    client = FakeClient(
        media={"alice": [_post("a1", "alice", 0)]},
        failing={"bob"},
    )
    feed = FeedService(repo, client)  # type: ignore[arg-type]
    posts = feed.get_feed()
    assert [p.id for p in posts] == ["a1"]  # bob's failure did not sink the feed


def test_get_feed_empty_when_no_artists(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "a.json")
    feed = FeedService(repo, FakeClient())  # type: ignore[arg-type]
    assert feed.get_feed() == []


# --- InspirationService ----------------------------------------------------


def _inspiration(tmp_path: Path) -> InspirationService:
    artist_repo = ArtistRepository(tmp_path / "a.json")
    artist_repo.add(Artist(handle="alice"))
    client = FakeClient(
        media={"alice": [_post("p1", "alice", 10), _post("p2", "alice", 0)]}
    )
    feed = FeedService(artist_repo, client)  # type: ignore[arg-type]
    return InspirationService(
        feed,
        InspirationRepository(tmp_path / "insp.json"),
        SeenSetRepository(tmp_path / "seen.json"),
    )


def test_next_inspiration_serves_unseen_newest_first(tmp_path: Path) -> None:
    service = _inspiration(tmp_path)
    first = service.next_inspiration()
    second = service.next_inspiration()
    third = service.next_inspiration()
    assert first is not None and first.id == "p1"  # newest first
    assert second is not None and second.id == "p2"
    assert third is None  # nothing new left


def test_reset_seen_restarts_inspiration(tmp_path: Path) -> None:
    service = _inspiration(tmp_path)
    service.next_inspiration()
    service.next_inspiration()
    assert service.next_inspiration() is None
    service.reset_seen()
    again = service.next_inspiration()
    assert again is not None and again.id == "p1"


def test_save_list_and_remove_inspiration(tmp_path: Path) -> None:
    service = _inspiration(tmp_path)
    saved = service.save_to_inspiration("p1", notes="love it")
    assert saved.artist_handle == "alice"
    assert saved.notes == "love it"
    listed = service.list_inspiration()
    assert [i.post_id for i in listed] == ["p1"]
    service.remove_from_inspiration("p1")
    assert service.list_inspiration() == []


def test_save_unknown_post_raises(tmp_path: Path) -> None:
    service = _inspiration(tmp_path)
    with pytest.raises(TattooFeedError):
        service.save_to_inspiration("does-not-exist")


# --- PreferenceService -----------------------------------------------------


def test_record_and_summarise_preferences(tmp_path: Path) -> None:
    service = PreferenceService(PreferenceRepository(tmp_path / "p.json"))
    service.record_preference("loves fine-line botanical work")
    service.record_preference("dislikes heavy black fill")
    summary = service.get_preference_summary()
    assert [p.observation for p in summary] == [
        "loves fine-line botanical work",
        "dislikes heavy black fill",
    ]


def test_record_blank_preference_raises(tmp_path: Path) -> None:
    service = PreferenceService(PreferenceRepository(tmp_path / "p.json"))
    with pytest.raises(ValidationError):
        service.record_preference("   ")
