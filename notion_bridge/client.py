"""
notion_bridge/client.py — Async Notion API client with retry logic.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PURPOSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This module wraps the official `notion-client` AsyncClient with:

  1. AUTOMATIC RETRIES — Notion's API returns 429 (rate limit) and occasional
     502/503 errors. Without retries, one bad API call kills a background agent
     run that took 30 minutes to queue up. tenacity handles this transparently.

  2. PROPERTY EXTRACTION HELPERS — Notion's API returns properties in a deeply
     nested format that's painful to read. Every property type (title, rich_text,
     date, select, multi_select, relation, formula) has a different shape.
     This module provides `extract_property()` which handles all of them so
     callers get plain Python strings/dicts instead of JSON archaeology.

  3. RICH TEXT CONVERSION — Notion stores all text as "rich text" arrays (lists
     of objects with text, annotations, links). We provide `rich_text_to_str()`
     to collapse them into plain strings for AI consumption.

  4. PAGE → DICT CONVERSION — `page_to_dict()` turns a full Notion page API
     response into a flat Python dict that Alfred/Bloodhound can reason over
     without knowing anything about Notion's response schema.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    from notion_bridge import NotionBridge

    bridge = NotionBridge()  # reads token from config.settings automatically

    # Get a page as a flat dict
    page = await bridge.get_page("3580fc06-a06c-8152-ba12-c5b9ebc0b6eb")

    # Query a database with optional filters
    rows = await bridge.query_database(
        database_id=settings.notion_projects_db_id,
        filter={"property": "Status", "select": {"equals": "In progress"}},
    )

    # Update a page property
    await bridge.update_page(
        page_id="3580fc06-...",
        properties={"Status": {"select": {"name": "Done"}}},
    )
"""

from __future__ import annotations

import logging
from typing import Any

from notion_client import AsyncClient
from notion_client.errors import APIResponseError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from notion_bridge.work_schema import ALIASES

logger = logging.getLogger(__name__)


def _is_retryable_notion_error(exc: BaseException) -> bool:
    """Only retry on 429 (rate limit) and 5xx (server errors). Never retry 4xx client errors."""
    if not isinstance(exc, APIResponseError):
        return False
    return exc.status == 429 or exc.status >= 500


# =============================================================================
# RETRY DECORATOR
# =============================================================================
#
# This decorator is applied to every Notion API call. It:
#   - Retries ONLY on 429 (rate limit) and 5xx (server errors)
#   - Does NOT retry 400/404 client errors — those are deterministic failures
#     that won't improve with retries and waste ~15 seconds per crash
#   - Waits exponentially: 1s → 2s → 4s → 8s between attempts
#   - Gives up after 5 attempts (total: ~15 seconds of waiting)
#
_notion_retry = retry(
    retry=retry_if_exception(_is_retryable_notion_error),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(5),
    before_sleep=lambda retry_state: logger.warning(
        "Notion API error — retrying (attempt %d/5)...",
        retry_state.attempt_number,
    ),
)


class NotionBridge:
    """
    Async client for all Notion API operations used by the KLG AI OS.

    This is the ONLY class in the codebase that should instantiate or call
    notion_client.AsyncClient. All other modules go through this class.

    Instantiate once and reuse — the AsyncClient maintains an internal
    connection pool, so creating one per request would be wasteful.

    Example (in FastAPI, create once at startup):

        @app.on_event("startup")
        async def startup():
            app.state.notion = NotionBridge()
    """

    def __init__(self) -> None:
        """
        Initialize the Notion API client using the token from config.

        We use AsyncClient (not the synchronous Client) because FastAPI runs
        in an async event loop. Calling synchronous blocking I/O from inside
        an async context freezes the event loop and blocks ALL requests until
        the I/O completes. AsyncClient uses httpx under the hood, which is
        fully async.
        """
        # Pin to 2022-06-28: the 2025-09-03 API removed /databases/{id}/query.
        self._client = AsyncClient(auth=settings.notion_token, notion_version="2022-06-28")
        logger.info("NotionBridge initialized (async client ready)")

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    @_notion_retry
    async def get_page(self, page_id: str) -> dict[str, Any]:
        """
        Retrieve a single Notion page by its ID, returned as a flat Python dict.

        The raw Notion API response looks like:
          {
            "id": "3580fc06-...",
            "properties": {
              "Project name": {"type": "title", "title": [{"plain_text": "..."}]},
              "Status": {"type": "select", "select": {"name": "In progress"}},
              ...
            }
          }

        This method converts that to:
          {
            "id": "3580fc06-...",
            "url": "https://www.notion.so/...",
            "Project name": "KLG AI Operating System: Architecture + Rollout",
            "Status": "In progress",
            ...
          }

        The flat dict is what Alfred and Bloodhound reason over. They should
        never need to know what a "rich_text" array is.

        Args:
            page_id: The Notion page ID. Can be a raw UUID (with or without
                     dashes) or a full notion.so URL — the SDK handles both.

        Returns:
            A flat dict with page metadata + all properties as plain values.

        Raises:
            APIResponseError: If the page doesn't exist or the integration
                              doesn't have access to it (404), or if rate
                              limits are exhausted after 5 retries (429).
        """
        raw = await self._client.pages.retrieve(page_id=page_id)
        return page_to_dict(raw)

    @_notion_retry
    async def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update one or more properties on a Notion page.

        This is how skills write back to Layer 1 after doing work — updating
        Status, adding a note, marking a milestone complete, etc.

        Properties must be in Notion's native update format. Example:

            await bridge.update_page(
                page_id="3580fc06-...",
                properties={
                    # Select property
                    "Status": {"select": {"name": "Done"}},

                    # Rich text property
                    "Summary": {
                        "rich_text": [{"text": {"content": "Brief filed."}}]
                    },

                    # Date property
                    "Target Date": {
                        "date": {"start": "2026-06-30"}
                    },

                    # Checkbox property
                    "Completed": {"checkbox": True},
                },
            )

        Args:
            page_id:    The Notion page ID to update.
            properties: Dict of property updates in Notion API format.
                        Only the properties you include are changed —
                        all other properties on the page are untouched.

        Returns:
            The updated page as a flat dict (same format as get_page()).
        """
        raw = await self._client.pages.update(
            page_id=page_id,
            properties=properties,
        )
        return page_to_dict(raw)

    @_notion_retry
    async def create_page(
        self,
        database_id: str,
        properties: dict[str, Any],
        children: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new page (row) inside a Notion database.

        Used by Bloodhound to add new entries to the Watch List when it
        detects a new case worth tracking, and by Alfred skills to create
        sub-pages (e.g., a research memo page inside a matter folder).

        Args:
            database_id: The database to create the page in.
            properties:  Property values for the new row, in Notion API format.
            children:    Optional list of Notion block objects to add as page
                         content (the "body" of the page, below the properties).
                         If None, the page is created with no body content.

        Returns:
            The newly created page as a flat dict.
        """
        payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children

        raw = await self._client.pages.create(**payload)
        return page_to_dict(raw)

    # ─────────────────────────────────────────────────────────────────────────
    # DATABASE OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    @_notion_retry
    async def query_database(
        self,
        database_id: str,
        filter: dict[str, Any] | None = None,
        sorts: list[dict] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query a Notion database and return all matching rows as flat dicts.

        Automatically handles pagination — Notion returns at most 100 results
        per API call. This method keeps calling the API with `start_cursor`
        until all pages are retrieved, then returns them all as one list.

        WHY AUTO-PAGINATION MATTERS:
            The Projects database could grow to hundreds of matters over years.
            A simple query that only returns the first 100 would silently miss
            older matters. Callers shouldn't have to think about cursors.

        Args:
            database_id: The Notion database to query.
            filter:      Optional Notion filter object. If None, all rows are
                         returned. See Notion API docs for filter syntax.
                         Example — get only "In progress" matters:
                           {
                             "property": "Status",
                             "select": {"equals": "In progress"}
                           }
            sorts:       Optional sort order. Example — newest first:
                           [{"property": "Target Date", "direction": "descending"}]
            page_size:   How many rows to fetch per API call (max 100).
                         Lower values are safer if properties are large.

        Returns:
            List of flat dicts, one per database row. Empty list if no matches.
        """
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None

        while True:
            # Build the query payload, only including optional fields if provided
            query: dict[str, Any] = {
                "page_size": page_size,
            }
            if filter:
                query["filter"] = filter
            if sorts:
                query["sorts"] = sorts
            if start_cursor:
                query["start_cursor"] = start_cursor

            response = await self._client.request(
                path=f"databases/{database_id}/query",
                method="POST",
                body=query,
            )

            # Convert each raw page object to a flat dict and accumulate
            for raw_page in response.get("results", []):
                results.append(page_to_dict(raw_page))

            # Notion signals there are more pages with has_more + next_cursor
            if response.get("has_more") and response.get("next_cursor"):
                start_cursor = response["next_cursor"]
                logger.debug(
                    "Paginating Notion query — fetched %d rows so far...",
                    len(results),
                )
            else:
                break  # All pages retrieved

        logger.debug(
            "query_database(%s) returned %d rows", database_id[:8], len(results)
        )
        return results

    @_notion_retry
    async def get_database_properties(self, database_id: str) -> dict[str, str]:
        """Return {property_name: property_type} for every property in the database."""
        response = await self._client.request(
            path=f"databases/{database_id}",
            method="GET",
        )
        props = response.get("properties", {})
        return {name: info.get("type", "unknown") for name, info in props.items()}

    @_notion_retry
    async def search(self, query: str, filter_type: str = "page") -> list[dict[str, Any]]:
        """
        Full-text search across all pages and databases the integration can access.

        This is Alfred's fallback when a skill doesn't know the exact page ID —
        it can search by matter name and get back the matching project page.

        Example:
            results = await bridge.search("Petersen")
            # Returns all pages with "Petersen" in the title or content

        Args:
            query:       The search string. Notion searches page titles and
                         some content (not all block types are indexed).
            filter_type: "page" to search only pages, "database" for databases.

        Returns:
            List of matching pages as flat dicts, sorted by Notion's relevance.
        """
        response = await self._client.search(
            query=query,
            filter={"value": filter_type, "property": "object"},
        )
        return [page_to_dict(r) for r in response.get("results", [])]

    # ─────────────────────────────────────────────────────────────────────────
    # BLOCK CONTENT OPERATIONS
    # ─────────────────────────────────────────────────────────────────────────

    @_notion_retry
    async def get_page_content(self, page_id: str) -> str:
        """
        Get the full text content of a Notion page's body blocks as a string.

        Notion pages have two layers:
          1. PROPERTIES — structured fields at the top (Status, Target Date, etc.)
          2. BLOCKS — the actual body content (paragraphs, headings, bullets, etc.)

        `get_page()` only returns properties. This method returns the body
        content as a plain text string, which Alfred can read and reason over.

        WHY WE NEED THIS:
            A matter's project page might have a free-form "Case Notes" section
            or a "Current Theory" paragraph that isn't in a structured property.
            Alfred needs to read that content to give useful answers about a case.

        Args:
            page_id: The Notion page ID.

        Returns:
            The page body as a single plain-text string. Headings are prefixed
            with "## " and "### " to preserve structure for the AI. Bullet
            points are prefixed with "- ". Empty pages return "".
        """
        response = await self._client.blocks.children.list(block_id=page_id)
        return blocks_to_text(response.get("results", []))

    async def get_page_snippet(self, page_id: str, max_chars: int = 220) -> str:
        """
        Return a short preview of a page's body — first few blocks only.

        Used by search_notion to enrich results so Alfred can judge relevance
        without fetching the full page. Intentionally lightweight: fetches at
        most 6 blocks and silently returns "" on any error.
        """
        try:
            response = await self._client.blocks.children.list(
                block_id=page_id, page_size=6
            )
            parts = []
            for block in response.get("results", []):
                btype = block.get("type", "")
                rich = block.get(btype, {}).get("rich_text", [])
                for rt in rich:
                    parts.append(rt.get("plain_text", ""))
            return " ".join(parts)[:max_chars].strip()
        except Exception:
            return ""

    @_notion_retry
    async def get_page_blocks(self, page_id: str) -> list[dict[str, Any]]:
        """
        Return all child blocks of a Notion page with auto-pagination.

        Used by TaskPages to detect whether a matter page contains an inline
        task database (child_database block) or to-do checkbox blocks.

        Args:
            page_id: The Notion page or block ID whose children to list.

        Returns:
            List of raw block dicts from the Notion API. Each block has a
            "type" field and a type-specific data dict (e.g., "to_do", "paragraph").
        """
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {"block_id": page_id}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = await self._client.blocks.children.list(**kwargs)
            results.extend(response.get("results", []))
            if response.get("has_more") and response.get("next_cursor"):
                start_cursor = response["next_cursor"]
            else:
                break

        return results

    @_notion_retry
    async def update_block(self, block_id: str, **props: Any) -> dict[str, Any]:
        """
        Update a Notion block's content.

        Used by TaskPages to toggle to-do block checked state or rename a task.

        The kwargs should match the block's type key. For a to_do block:
            await bridge.update_block(block_id, to_do={"checked": True})

        Args:
            block_id: The block to update.
            **props:  Block-type payload, e.g. to_do={...}, paragraph={...}.

        Returns:
            The raw updated block dict from the Notion API.
        """
        return await self._client.blocks.update(block_id=block_id, **props)

    @_notion_retry
    async def append_block(self, page_id: str, text: str) -> None:
        """
        Append a paragraph of text to the bottom of a Notion page.

        Used by skills to add timestamped notes to a project page after
        completing work — e.g., "Alfred added Bloodhound Watch List entry
        for HB v. California — 2026-05-11 14:32 UTC".

        Args:
            page_id: The Notion page to append to.
            text:    Plain text string. Will be added as a paragraph block.
        """
        await self._client.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    },
                }
            ],
        )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
# These are module-level functions (not methods) because they are pure
# data transformations — they don't need access to the API client.


def rich_text_to_str(rich_text: list[dict]) -> str:
    """
    Convert a Notion rich_text array to a plain Python string.

    Notion stores all text as a list of objects. Each object has a type
    (usually "text"), content, and optional annotations (bold, italic, etc.)
    and links. Example raw value:

        [
          {"type": "text", "text": {"content": "Brief filed "}, "annotations": {...}},
          {"type": "text", "text": {"content": "on time."}, "href": "https://..."},
        ]

    We only care about the plain text for AI consumption — annotations and
    links are visual formatting that doesn't affect meaning in a text prompt.

    Args:
        rich_text: The raw Notion rich_text array from the API.

    Returns:
        A plain string with all text segments joined. Empty string if input is
        None, empty, or contains no text content.
    """
    if not rich_text:
        return ""
    # Each element has a "plain_text" field that Notion pre-computes for us.
    # Using plain_text is simpler than extracting text.content from each type.
    return "".join(segment.get("plain_text", "") for segment in rich_text)


def extract_property(prop: dict[str, Any]) -> Any:
    """
    Extract a human-readable value from a Notion property object.

    Notion's API returns properties in a type-discriminated format where
    the property type determines the shape of the value. This function
    handles all common property types and returns plain Python values.

    Supported types and their return values:
      - title         → str  (the page title)
      - rich_text     → str
      - select        → str  (the selected option name)
      - multi_select  → list[str] (list of selected option names)
      - date          → str  (ISO 8601 date string, e.g. "2026-06-30")
      - checkbox      → bool
      - number        → float | int
      - url           → str
      - email         → str
      - phone_number  → str
      - formula       → str | float | bool (depends on formula result type)
      - relation      → list[str] (list of related page IDs)
      - rollup        → str (simplified — rollup values vary widely)
      - people        → list[str] (list of user names)
      - files         → list[str] (list of file URLs)

    Args:
        prop: A single property object from a Notion page's "properties" dict.

    Returns:
        The extracted value in a plain Python type. Returns None if the
        property is empty or its type is not recognized.
    """
    prop_type = prop.get("type")

    if prop_type == "title":
        return rich_text_to_str(prop.get("title", []))

    elif prop_type == "rich_text":
        return rich_text_to_str(prop.get("rich_text", []))

    elif prop_type in ("select", "status"):
        sel = prop.get(prop_type)
        return sel["name"] if sel else None

    elif prop_type == "multi_select":
        return [item["name"] for item in prop.get("multi_select", [])]

    elif prop_type == "date":
        date_obj = prop.get("date")
        if not date_obj:
            return None
        # Return start date; include end if it's a date range
        start = date_obj.get("start", "")
        end = date_obj.get("end")
        return f"{start} → {end}" if end else start

    elif prop_type == "checkbox":
        return prop.get("checkbox", False)

    elif prop_type == "number":
        return prop.get("number")

    elif prop_type in ("url", "email", "phone_number"):
        return prop.get(prop_type)

    elif prop_type == "formula":
        formula = prop.get("formula", {})
        result_type = formula.get("type")
        return formula.get(result_type)

    elif prop_type == "relation":
        # Relations link to other pages; return their IDs so callers can
        # fetch the related pages if needed
        return [r["id"] for r in prop.get("relation", [])]

    elif prop_type == "rollup":
        rollup = prop.get("rollup", {})
        rollup_type = rollup.get("type")
        if rollup_type == "array":
            return [extract_property(item) for item in rollup.get("array", [])]
        if rollup_type == "number":
            return rollup.get("number")  # None if no related rows, float 0–100 for percent
        if rollup_type == "date":
            return extract_property({"type": "date", "date": rollup.get("date")})
        return None  # unknown rollup type — return None rather than a garbled repr

    elif prop_type == "people":
        return [
            person.get("name", person.get("id", ""))
            for person in prop.get("people", [])
        ]

    elif prop_type == "files":
        files = prop.get("files", [])
        urls = []
        for f in files:
            if f.get("type") == "external":
                urls.append(f["external"]["url"])
            elif f.get("type") == "file":
                urls.append(f["file"]["url"])
        return urls

    # Unknown or unsupported type — log it and return None rather than crash
    logger.debug("extract_property: unhandled type '%s'", prop_type)
    return None


def page_to_dict(raw_page: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a raw Notion API page response to a flat Python dict.

    This is the core translation function that makes Notion data usable
    everywhere else in the codebase. Callers get a clean, flat dict with
    string keys and plain Python values — no Notion API schema knowledge needed.

    Input (raw Notion API response, abbreviated):
        {
          "id": "3580fc06-a06c-8152-ba12-c5b9ebc0b6eb",
          "url": "https://www.notion.so/3580fc06...",
          "properties": {
            "Project name": {"type": "title", "title": [...]},
            "Status": {"type": "select", "select": {"name": "In progress"}},
          }
        }

    Output (flat dict):
        {
          "id": "3580fc06-a06c-8152-ba12-c5b9ebc0b6eb",
          "url": "https://www.notion.so/3580fc06...",
          "created_time": "2026-05-04T00:00:00.000Z",
          "last_edited_time": "2026-05-11T14:30:00.000Z",
          "Project name": "KLG AI Operating System: Architecture + Rollout",
          "Status": "In progress",
        }

    Args:
        raw_page: A raw Notion page object from the API (pages.retrieve,
                  databases.query results, pages.create, pages.update, etc.)

    Returns:
        Flat dict with page metadata fields plus all property values.
        Property names are used as dict keys exactly as they appear in Notion.
    """
    result: dict[str, Any] = {
        "id": raw_page.get("id", ""),
        "url": raw_page.get("url", ""),
        "created_time": raw_page.get("created_time", ""),
        "last_edited_time": raw_page.get("last_edited_time", ""),
    }

    # Extract every property from the properties dict
    for prop_name, prop_value in raw_page.get("properties", {}).items():
        result[prop_name] = extract_property(prop_value)

    for prop_name, prop_value in raw_page.get("properties", {}).items():
        if prop_value.get("type") == "title":
            result["title"] = extract_property(prop_value)
            break

    for new_name, old_name in ALIASES:
        if new_name in result and old_name not in result:
            result[old_name] = result[new_name]
        elif old_name in result and new_name not in result:
            result[new_name] = result[old_name]

    return result


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    """
    Convert a list of Notion block objects to a plain-text string.

    Used by get_page_content() to extract the body of a page for AI consumption.
    Preserves semantic structure (headings, bullets) but strips visual formatting.

    Supported block types:
      - paragraph       → plain text line
      - heading_1       → "# Heading"
      - heading_2       → "## Heading"
      - heading_3       → "### Heading"
      - bulleted_list_item → "- item"
      - numbered_list_item → "1. item" (simplified — actual numbers not tracked)
      - to_do           → "[ ] item" or "[x] item"
      - quote           → "> quote"
      - code            → "```\ncode\n```"
      - divider         → "---"
      - callout         → plain text of callout content

    Args:
        blocks: List of Notion block objects from blocks.children.list.

    Returns:
        Multi-line string with one block per line (approximately).
        Returns empty string if blocks is empty.
    """
    lines: list[str] = []

    for block in blocks:
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        rich_text = block_data.get("rich_text", [])
        text = rich_text_to_str(rich_text)

        if block_type == "paragraph":
            lines.append(text)
        elif block_type == "heading_1":
            lines.append(f"# {text}")
        elif block_type == "heading_2":
            lines.append(f"## {text}")
        elif block_type == "heading_3":
            lines.append(f"### {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"- {text}")
        elif block_type == "numbered_list_item":
            lines.append(f"1. {text}")
        elif block_type == "to_do":
            checked = block_data.get("checked", False)
            lines.append(f"{'[x]' if checked else '[ ]'} {text}")
        elif block_type == "quote":
            lines.append(f"> {text}")
        elif block_type == "code":
            code_text = rich_text_to_str(block_data.get("rich_text", []))
            lang = block_data.get("language", "")
            lines.append(f"```{lang}\n{code_text}\n```")
        elif block_type == "divider":
            lines.append("---")
        elif block_type == "callout":
            lines.append(text)
        # Unsupported block types (e.g., image, embed, video) are silently
        # skipped — they don't have text content meaningful to an AI.

    return "\n".join(lines)
