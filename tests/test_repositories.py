"""Tests for the JSON-file repositories and atomic writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tattoo_feed.errors import RepositoryError
from tattoo_feed.models import Artist, InspirationItem, Preference
from tattoo_feed.repositories import json_repo
from tattoo_feed.repositories.json_repo import (
    ArtistRepository,
    InspirationRepository,
    PreferenceRepository,
    SeenSetRepository,
)

TS = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def _item(post_id: str = "p1", handle: str = "artist") -> InspirationItem:
    return InspirationItem(
        post_id=post_id,
        artist_handle=handle,
        image_url="https://example.com/a.jpg",
        permalink="https://instagram.com/p/abc",
        timestamp=TS,
    )


# --- Artist repository (representative of the generic model repo) ----------


def test_add_list_get_and_persistence(tmp_path: Path) -> None:
    path = tmp_path / "artists.json"
    repo = ArtistRepository(path)

    assert repo.list() == []  # absent file reads as empty
    repo.add(Artist(handle="alice"))
    repo.add(Artist(handle="bob"))

    # A fresh instance reads the same file back — true persistence.
    reloaded = ArtistRepository(path)
    handles = [a.handle for a in reloaded.list()]
    assert handles == ["alice", "bob"]
    assert reloaded.get("alice") is not None
    assert reloaded.get("missing") is None


def test_add_replaces_in_place_preserving_order(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "artists.json")
    repo.add(Artist(handle="alice"))
    repo.add(Artist(handle="bob"))
    repo.add(Artist(handle="alice", ig_user_id="123"))  # upsert

    artists = repo.list()
    assert [a.handle for a in artists] == ["alice", "bob"]
    assert artists[0].ig_user_id == "123"


def test_remove_returns_true_then_false(tmp_path: Path) -> None:
    repo = ArtistRepository(tmp_path / "artists.json")
    repo.add(Artist(handle="alice"))
    assert repo.remove("alice") is True
    assert repo.remove("alice") is False
    assert repo.list() == []


def test_inspiration_preserves_save_order(tmp_path: Path) -> None:
    repo = InspirationRepository(tmp_path / "insp.json")
    repo.add(_item("p1"))
    repo.add(_item("p2"))
    repo.add(_item("p3"))
    assert [i.post_id for i in repo.list()] == ["p1", "p2", "p3"]
    repo.remove("p2")
    assert [i.post_id for i in repo.list()] == ["p1", "p3"]


def test_preference_round_trips(tmp_path: Path) -> None:
    repo = PreferenceRepository(tmp_path / "prefs.json")
    pref = Preference(observation="bold blackwork")
    repo.add(pref)
    assert repo.get(pref.id) == pref


# --- Error mapping ---------------------------------------------------------


def test_unreadable_corrupt_json_raises_repository_error(tmp_path: Path) -> None:
    path = tmp_path / "artists.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RepositoryError):
        ArtistRepository(path).list()


def test_non_list_json_raises_repository_error(tmp_path: Path) -> None:
    path = tmp_path / "artists.json"
    path.write_text('{"handle": "alice"}', encoding="utf-8")
    with pytest.raises(RepositoryError):
        ArtistRepository(path).list()


def test_invalid_model_data_raises_repository_error(tmp_path: Path) -> None:
    path = tmp_path / "artists.json"
    # Valid JSON list, but the row is not a valid Artist (missing handle).
    path.write_text(json.dumps([{"ig_user_id": "x"}]), encoding="utf-8")
    with pytest.raises(RepositoryError):
        ArtistRepository(path).list()


# --- Atomicity -------------------------------------------------------------


def test_write_is_atomic_original_survives_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artists.json"
    repo = ArtistRepository(path)
    repo.add(Artist(handle="alice"))  # establish a good file
    good_contents = path.read_text(encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(json_repo.os, "replace", boom)

    with pytest.raises(RepositoryError):
        repo.add(Artist(handle="bob"))

    # Original file is intact and no temp debris is left behind.
    assert path.read_text(encoding="utf-8") == good_contents
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "artists.json"]
    assert leftovers == []


def test_atomic_write_creates_missing_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "artists.json"
    repo = ArtistRepository(nested)
    repo.add(Artist(handle="alice"))
    assert nested.exists()


# --- Seen-set --------------------------------------------------------------


def test_seen_set_add_contains_clear_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    seen = SeenSetRepository(path)

    assert seen.all() == set()
    assert seen.contains("p1") is False
    seen.add("p1")
    seen.add("p1")  # idempotent
    seen.add("p2")

    reloaded = SeenSetRepository(path)
    assert reloaded.all() == {"p1", "p2"}
    assert reloaded.contains("p1") is True

    reloaded.clear()
    assert SeenSetRepository(path).all() == set()


def test_seen_set_rejects_malformed_store(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")  # not strings
    with pytest.raises(RepositoryError):
        SeenSetRepository(path).all()


def test_seen_set_rejects_unreadable_store(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(RepositoryError):
        SeenSetRepository(path).all()
