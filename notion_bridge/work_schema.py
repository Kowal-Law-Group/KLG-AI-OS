"""Resolve Work properties while the Projects database is being renamed."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notion_bridge.client import NotionBridge

TITLE = ("Name", "Project name")
DUE = ("Due", "Project Deadline")
WORK_TYPE = ("Work type", "Project Type")
SUPPORT_KIND = ("Supporting kind", "Support Type")
HEARING = ("Hearing date", "Calendar Hearing")
MATTER_REL = ("Matter", "Case Portal")
ALIASES = (TITLE, DUE, WORK_TYPE, SUPPORT_KIND, HEARING, MATTER_REL)

CATEGORY_TO_AREA = {
    "Case Project": "Case",
    "Case Support": "Case",
    "Operations": "Firm operations",
    "Think Tank": "Think tank",
}

logger = logging.getLogger(__name__)
_CACHE_TTL = 600.0
_schema_cache: dict[str, tuple[float, dict[str, str]]] = {}
_schema_locks: dict[str, asyncio.Lock] = {}
_area_warnings: set[str] = set()


def first_present(props: dict, names: tuple) -> str | None:
    """Return the first existing key, including keys with empty values."""
    return next((name for name in names if name in props), None)


class WorkSchema:
    """Use the existing bridge and cache metadata per database for ten minutes."""

    def __init__(self, bridge: NotionBridge, database_id: str) -> None:
        self._bridge = bridge
        self._database_id = database_id

    async def properties(self) -> dict[str, str]:
        cached = _schema_cache.get(self._database_id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        lock = _schema_locks.setdefault(self._database_id, asyncio.Lock())
        async with lock:
            cached = _schema_cache.get(self._database_id)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            props = await self._bridge.get_database_properties(self._database_id)
            _schema_cache[self._database_id] = (time.monotonic() + _CACHE_TTL, props)
            return props

    async def resolve(self, names: tuple[str, ...]) -> str:
        name = first_present(await self.properties(), names)
        if name is None:
            raise ValueError(f"Work schema has none of the expected properties: {names}")
        return name

    async def title_prop(self) -> str:
        return await self.resolve(TITLE)

    async def category_property(self, value: str) -> tuple[str, str]:
        """Return the property and option for either a filter or a page write."""
        name = await self.resolve(("Category", "Area"))
        if name == "Category":
            return name, value
        if self._database_id not in _area_warnings:
            logger.warning(
                "Work schema only has Area: Case Project and Case Support "
                "can no longer be told apart."
            )
            _area_warnings.add(self._database_id)
        return name, CATEGORY_TO_AREA.get(value, value)

    async def category_filter(self, value: str) -> dict:
        name, option = await self.category_property(value)
        return {"property": name, "select": {"equals": option}}
