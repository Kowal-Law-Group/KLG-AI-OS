"""Work rename regression tests use fake pages and bridges, never live APIs."""

from __future__ import annotations

import asyncio

import pytest

from notion_bridge.client import page_to_dict
from notion_bridge import work_schema


@pytest.fixture(autouse=True)
def clear_schema_cache():
    work_schema._schema_cache.clear()
    work_schema._schema_locks.clear()
    work_schema._area_warnings.clear()
    yield
    work_schema._schema_cache.clear()
    work_schema._schema_locks.clear()
    work_schema._area_warnings.clear()


@pytest.mark.parametrize("names", work_schema.ALIASES)
@pytest.mark.parametrize("direction", [0, 1])
def test_flat_pages_expose_both_names(names, direction):
    source, alias = names[direction], names[1 - direction]
    raw = {"id": "fake-page", "properties": {
        source: {"type": "rich_text", "rich_text": [{"plain_text": "Example"}]},
    }}
    page = page_to_dict(raw)
    assert page[source] == page[alias] == "Example"
    assert page["id"] == "fake-page"


@pytest.mark.parametrize("names", work_schema.ALIASES)
def test_flat_pages_preserve_both_existing_values(names):
    raw = {"properties": {
        names[0]: {"type": "select", "select": {"name": "New value"}},
        names[1]: {"type": "select", "select": {"name": "Old value"}},
    }}
    page = page_to_dict(raw)
    assert page[names[0]] == "New value"
    assert page[names[1]] == "Old value"


@pytest.mark.parametrize("title_name", ["Name", "Project name", "Unrelated title"])
def test_title_is_found_by_type(title_name):
    page = page_to_dict({"properties": {
        title_name: {"type": "title", "title": [{"plain_text": "Example matter"}]},
    }})
    assert page["title"] == "Example matter"
    assert page[title_name] == "Example matter"


def test_missing_and_empty_properties_are_safe():
    assert page_to_dict({})["id"] == ""
    assert "title" not in page_to_dict({})
    page = page_to_dict({"properties": {"Name": {"type": "title", "title": []}}})
    assert page["title"] == page["Name"] == page["Project name"] == ""
    assert work_schema.first_present({"Name": None, "Project name": "Old"}, work_schema.TITLE) == "Name"
    assert work_schema.first_present({}, work_schema.TITLE) is None


def test_relation_alias_preserves_ids_and_existing_matter_relation():
    page = page_to_dict({"properties": {
        "Case Portal": {"type": "relation", "relation": [{"id": "fake-case"}]},
        "Matter": {"type": "relation", "relation": [{"id": "fake-legacy"}]},
    }})
    assert page["Case Portal"] == ["fake-case"]
    assert page["Matter"] == ["fake-legacy"]
    renamed = page_to_dict({"properties": {
        "Matter": {"type": "relation", "relation": [{"id": "fake-case"}]},
    }})
    assert renamed["Case Portal"] == renamed["Matter"] == ["fake-case"]
