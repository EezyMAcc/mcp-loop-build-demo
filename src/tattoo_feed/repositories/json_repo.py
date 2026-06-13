"""JSON-file repository implementations with atomic writes.

Each store is a single JSON file holding a list (artists, inspiration,
preferences) or a list of ids (the seen-set). Writes are atomic: we write a
sibling temp file and ``os.replace`` it over the target, so a crash mid-write
can never leave a half-written or truncated store — the reader sees either the
old file or the new one, never a corrupt one.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from tattoo_feed.errors import RepositoryError
from tattoo_feed.models import Artist, InspirationItem, Preference
from tattoo_feed.repositories.base import Repository


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically (temp file + ``os.replace``).

    Args:
        path: Destination file.
        payload: Text to write.

    Raises:
        RepositoryError: If the directory or file cannot be written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Temp file lives in the same directory so os.replace is a same-
        # filesystem rename (atomic). A temp on another fs would fall back to
        # a non-atomic copy.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
        except OSError:
            # Replace failed: drop the temp so no debris is left, and leave the
            # original file untouched.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as exc:
        raise RepositoryError(f"could not write store at {path}") from exc


class JsonModelRepository[T: BaseModel](Repository[T]):
    """A :class:`Repository` backed by a JSON file of model dumps."""

    def __init__(self, path: Path, model: type[T], key: Callable[[T], str]) -> None:
        """Initialise the repository.

        Args:
            path: Path to the backing JSON file (need not exist yet).
            model: The Pydantic model type stored here.
            key: Function returning the stable key for an item.
        """
        self._path = path
        self._model = model
        self._key = key

    def _read_raw(self) -> list[Any]:
        """Load the raw JSON list from disk (``[]`` if the file is absent).

        Raises:
            RepositoryError: If the file is unreadable or not a JSON list.
        """
        if not self._path.exists():
            return []
        try:
            parsed: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"could not read store at {self._path}") from exc
        if not isinstance(parsed, list):
            raise RepositoryError(f"store at {self._path} is not a JSON list")
        return parsed

    def list(self) -> list[T]:
        """Return every stored item in insertion order.

        Raises:
            RepositoryError: If the store is unreadable or holds invalid data.
        """
        try:
            return [self._model.model_validate(row) for row in self._read_raw()]
        except ValidationError as exc:
            raise RepositoryError(f"store at {self._path} holds invalid data") from exc

    def get(self, key: str) -> T | None:
        """Return the item with ``key``, or ``None`` if not present."""
        for item in self.list():
            if self._key(item) == key:
                return item
        return None

    def add(self, item: T) -> T:
        """Insert ``item``, replacing any existing item with the same key.

        Replacement is done in place so insertion/save order is preserved when
        an item is updated.
        """
        items = self.list()
        key = self._key(item)
        replaced = False
        for index, existing in enumerate(items):
            if self._key(existing) == key:
                items[index] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        self._write(items)
        return item

    def remove(self, key: str) -> bool:
        """Remove the item with ``key``; return whether anything was removed."""
        items = self.list()
        kept = [item for item in items if self._key(item) != key]
        if len(kept) == len(items):
            return False
        self._write(kept)
        return True

    def _write(self, items: Sequence[T]) -> None:
        """Serialise ``items`` to the backing file atomically."""
        payload = json.dumps([item.model_dump(mode="json") for item in items], indent=2)
        _atomic_write(self._path, payload)


class ArtistRepository(JsonModelRepository[Artist]):
    """Persists the tracked artists, keyed by handle."""

    def __init__(self, path: Path) -> None:
        """Store artists at ``path`` (one JSON file)."""
        super().__init__(path, Artist, lambda artist: artist.handle)


class InspirationRepository(JsonModelRepository[InspirationItem]):
    """Persists saved inspiration items, keyed by post id, in save order."""

    def __init__(self, path: Path) -> None:
        """Store inspiration items at ``path`` (one JSON file)."""
        super().__init__(path, InspirationItem, lambda item: item.post_id)


class PreferenceRepository(JsonModelRepository[Preference]):
    """Persists recorded taste preferences, keyed by id."""

    def __init__(self, path: Path) -> None:
        """Store preferences at ``path`` (one JSON file)."""
        super().__init__(path, Preference, lambda pref: pref.id)


class SeenSetRepository:
    """Persists the set of post ids already shown via ``next_inspiration``.

    Stored as a JSON list of ids. This is a plain set, not a model collection,
    so it does not implement the :class:`Repository` contract.
    """

    def __init__(self, path: Path) -> None:
        """Store the seen-set at ``path`` (one JSON file)."""
        self._path = path

    def all(self) -> set[str]:
        """Return every seen post id.

        Raises:
            RepositoryError: If the store is unreadable or malformed.
        """
        if not self._path.exists():
            return set()
        try:
            parsed: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"could not read seen-set at {self._path}") from exc
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise RepositoryError(f"seen-set at {self._path} is not a list of ids")
        return set(parsed)

    def contains(self, post_id: str) -> bool:
        """Return whether ``post_id`` has already been seen."""
        return post_id in self.all()

    def add(self, post_id: str) -> None:
        """Mark ``post_id`` as seen (idempotent)."""
        seen = self.all()
        seen.add(post_id)
        _atomic_write(self._path, json.dumps(sorted(seen), indent=2))

    def clear(self) -> None:
        """Forget every seen post id, so inspiration starts fresh."""
        _atomic_write(self._path, json.dumps([], indent=2))
