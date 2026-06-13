"""The abstract repository contract.

A :class:`Repository` is a persistent collection of one model type, keyed by a
stable string. Keeping this an ABC (rather than a concrete class) lets a future
GUI or an alternative backend (SQLite, say) swap in without touching the
services that depend on it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class Repository[T: BaseModel](ABC):
    """A persistent, key-addressable collection of a single model type."""

    @abstractmethod
    def list(self) -> list[T]:
        """Return every stored item, in insertion order.

        Raises:
            RepositoryError: If the backing store cannot be read.
        """

    @abstractmethod
    def get(self, key: str) -> T | None:
        """Return the item with the given key, or ``None`` if absent.

        Raises:
            RepositoryError: If the backing store cannot be read.
        """

    @abstractmethod
    def add(self, item: T) -> T:
        """Insert ``item``, replacing any existing item with the same key.

        Args:
            item: The model instance to persist.

        Returns:
            The persisted item.

        Raises:
            RepositoryError: If the backing store cannot be written.
        """

    @abstractmethod
    def remove(self, key: str) -> bool:
        """Remove the item with the given key.

        Args:
            key: The key of the item to remove.

        Returns:
            ``True`` if an item was removed, ``False`` if none matched.

        Raises:
            RepositoryError: If the backing store cannot be written.
        """
