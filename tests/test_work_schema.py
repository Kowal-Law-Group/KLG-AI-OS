"""Work rename regression tests use fake pages and bridges, never live APIs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from notion_bridge.client import page_to_dict
from notion_bridge import work_schema
from notion_bridge.project_pages import ProjectPages, _PROP_DEADLINE, _PROP_COURT_DEADLINE
from config import settings

OLD_SCHEMA = {"Project name": "title", "Category": "select", "Project Type": "select"}
NEW_SCHEMA = {"Name": "title", "Area": "select", "Work type": "select"}


class FakeBridge:
    def __init__(self, schema, rows=None):
        self.schema = schema
        self.rows = rows or []
        self.schema_calls = []
        self.queries = []
        self.created = []

    async def get_database_properties(self, database_id):
        self.schema_calls.append(database_id)
        await asyncio.sleep(0)
        return dict(self.schema)

    async def query_database(self, **kwargs):
        self.queries.append(kwargs)
        return self.rows

    async def create_page(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "fake-new-page", "url": "https://example.invalid/matter"}


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


@pytest.mark.parametrize("schema,title", [(OLD_SCHEMA, "Project name"), (NEW_SCHEMA, "Name")])
def test_title_resolver_prefers_new_name(schema, title):
    bridge = FakeBridge(schema)
    resolver = work_schema.WorkSchema(bridge, "fake-db")
    assert asyncio.run(resolver.title_prop()) == title
    bridge = FakeBridge({**OLD_SCHEMA, **NEW_SCHEMA})
    assert asyncio.run(work_schema.WorkSchema(bridge, "other-db").title_prop()) == "Name"


def test_schema_refreshes_after_ttl_and_is_scoped_by_database(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(work_schema.time, "monotonic", lambda: clock[0])
    bridge = FakeBridge(OLD_SCHEMA)
    resolver = work_schema.WorkSchema(bridge, "fake-db")

    async def run():
        assert await resolver.title_prop() == "Project name"
        bridge.schema = NEW_SCHEMA
        clock[0] = 699.0
        assert await resolver.title_prop() == "Project name"
        assert bridge.schema_calls == ["fake-db"]
        assert await work_schema.WorkSchema(bridge, "other-db").title_prop() == "Name"
        clock[0] = 700.0
        assert await resolver.title_prop() == "Name"
        assert bridge.schema_calls == ["fake-db", "other-db", "fake-db"]

    asyncio.run(run())


def test_concurrent_resolvers_share_one_metadata_request():
    bridge = FakeBridge(OLD_SCHEMA)

    async def run():
        resolvers = [work_schema.WorkSchema(bridge, "fake-db") for _ in range(4)]
        assert await asyncio.gather(*(r.title_prop() for r in resolvers)) == ["Project name"] * 4
        assert bridge.schema_calls == ["fake-db"]

    asyncio.run(run())


def test_failed_metadata_request_is_not_cached():
    class UnavailableBridge(FakeBridge):
        async def get_database_properties(self, database_id):
            self.schema_calls.append(database_id)
            if len(self.schema_calls) == 1:
                raise RuntimeError("Fake schema unavailable")
            return self.schema

    bridge = UnavailableBridge(NEW_SCHEMA)
    resolver = work_schema.WorkSchema(bridge, "fake-db")

    async def run():
        with pytest.raises(RuntimeError, match="Fake schema unavailable"):
            await resolver.title_prop()
        assert await resolver.title_prop() == "Name"
        assert bridge.schema_calls == ["fake-db", "fake-db"]

    asyncio.run(run())


def test_missing_title_fails_explicitly():
    resolver = work_schema.WorkSchema(FakeBridge({}), "fake-db")
    with pytest.raises(ValueError, match="expected properties"):
        asyncio.run(resolver.title_prop())


@pytest.mark.parametrize("value,mapped", list(work_schema.CATEGORY_TO_AREA.items()) + [("Content", "Content")])
def test_category_filters_keep_category_until_removed(value, mapped, caplog):
    async def run():
        both = work_schema.WorkSchema(FakeBridge({**OLD_SCHEMA, **NEW_SCHEMA}), "both-db")
        assert await both.category_filter(value) == {"property": "Category", "select": {"equals": value}}
        new = work_schema.WorkSchema(FakeBridge(NEW_SCHEMA), "new-db")
        assert await new.category_filter(value) == {"property": "Area", "select": {"equals": mapped}}
        await new.category_filter("Case Support")

    asyncio.run(run())
    assert sum("can no longer be told apart" in r.message for r in caplog.records) == 1


@pytest.mark.parametrize("schema,title", [(OLD_SCHEMA, "Project name"), (NEW_SCHEMA, "Name")])
def test_exact_title_query_uses_current_schema(schema, title, monkeypatch):
    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge(schema, [{"id": "fake-page"}])
    result = asyncio.run(ProjectPages(bridge)._query_projects_by_name("Example matter"))
    assert result == [{"id": "fake-page"}]
    assert bridge.queries == [{
        "database_id": "fake-db", "filter": {"property": title, "title": {"contains": "Example matter"}},
    }]


def test_title_schema_failure_keeps_search_fallback(monkeypatch):
    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge({})

    async def search(query, filter_type):
        assert query == "Example matter" and filter_type == "page"
        return [{"id": "fake-page", "Name": "Example matter", "Project name": "Example matter"}]

    bridge.search = search
    result, runners = asyncio.run(ProjectPages(bridge).find_matter("Example matter"))
    assert result["id"] == "fake-page"
    assert runners == []
    assert bridge.queries == []


@pytest.mark.parametrize("schema,category_prop,category_value,type_prop", [
    (OLD_SCHEMA, "Category", "Case Project", "Project Type"),
    (NEW_SCHEMA, "Area", "Case", "Work type"),
])
def test_exact_active_matter_filters(schema, category_prop, category_value, type_prop, monkeypatch):
    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge(schema, [
        {"id": "active", "Matter Status": "Active", "Project Status": "Planning"},
        {"id": "done", "Matter Status": "Completed", "Project Status": "Done"},
    ])
    result = asyncio.run(ProjectPages(bridge).get_all_active_matters(project_type="Appellate"))
    assert [m["id"] for m in result] == ["active"]
    assert bridge.queries == [{
        "database_id": "fake-db",
        "filter": {"and": [
            {"property": category_prop, "select": {"equals": category_value}},
            {"property": type_prop, "select": {"equals": "Appellate"}},
        ]},
        "sorts": [{"property": "Priority", "direction": "descending"}],
    }]


def test_unfiltered_matter_query_needs_no_schema(monkeypatch):
    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge({})
    asyncio.run(ProjectPages(bridge).get_all_active_matters(category=None))
    assert bridge.schema_calls == []
    assert bridge.queries[0]["filter"] is None


@pytest.mark.parametrize("schema,category_prop,category_value,type_prop", [
    (OLD_SCHEMA, "Category", "Case Project", "Project Type"),
    (NEW_SCHEMA, "Area", "Case", "Work type"),
])
def test_exact_deadline_filters(schema, category_prop, category_value, type_prop, monkeypatch):
    from notion_bridge import project_pages

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 14, 12, tzinfo=tz)

    monkeypatch.setattr(project_pages, "datetime", FixedDatetime)
    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge(schema, [{"id": "fake-page", _PROP_DEADLINE: "2026-10-15"}])
    result = asyncio.run(ProjectPages(bridge).get_matters_with_upcoming_deadlines(project_type="Appellate"))
    assert len(result) == 1  # The two query results are deduplicated.
    assert bridge.queries == [{
        "database_id": "fake-db",
        "filter": {"and": [
            {"property": date_prop, "date": {"on_or_after": "2026-10-14"}},
            {"property": date_prop, "date": {"on_or_before": "2026-10-21"}},
            {"property": category_prop, "select": {"equals": category_value}},
            {"property": type_prop, "select": {"equals": "Appellate"}},
        ]},
    } for date_prop in (_PROP_DEADLINE, _PROP_COURT_DEADLINE)]


@pytest.mark.parametrize("schema,title,category_prop,category_value", [
    (OLD_SCHEMA, "Project name", "Category", "Case Project"),
    (NEW_SCHEMA, "Name", "Area", "Case"),
])
def test_intake_writes_current_title_and_category(schema, title, category_prop, category_value, monkeypatch):
    from alfred.skills.base import SkillContext
    from alfred.skills.klg_matter_intake import KLGMatterIntake

    monkeypatch.setattr(settings, "notion_projects_db_id", "fake-db")
    bridge = FakeBridge(schema)
    ctx = SkillContext("", "Example matter", "", {}, extra={"_bridge": bridge})
    result = asyncio.run(KLGMatterIntake().execute(ctx))
    assert result.success
    assert bridge.created == [{
        "database_id": "fake-db",
        "properties": {
            title: {"title": [{"text": {"content": "Example matter"}}]},
            "Status": {"status": {"name": "Planning"}},  # Existing issue deliberately unchanged.
            category_prop: {"select": {"name": category_value}},
            "Case Stage": {"select": {"name": "Intake"}},
            "Priority": {"select": {"name": "Medium"}},
        },
        "children": None,
    }]
