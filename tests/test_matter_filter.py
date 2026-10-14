"""
tests/test_matter_filter.py — Unit tests for the active-matter status filter.

These tests cover _is_active_matter() and _ACTIVE_PROJECT_STATUSES, which
together enforce the rule that a matter must have:
  - Matter Status: Active or On Hold
  - Project Status: Planning, In progress, Paused, or Waiting

Run from the repo root:
    venv\\Scripts\\python -m pytest tests/test_matter_filter.py -v
"""

from __future__ import annotations

import pytest

from notion_bridge.project_pages import _ACTIVE_PROJECT_STATUSES, _is_active_matter


def _m(matter_status: str, project_status: str = "") -> dict:
    return {"Matter Status": matter_status, "Project Status": project_status}


# =============================================================================
# Cases that SHOULD be included
# =============================================================================

@pytest.mark.parametrize("ps", ["Planning", "In progress", "Paused", "Waiting"])
def test_active_matter_status_with_qualifying_project_status(ps):
    assert _is_active_matter(_m("Active", ps))


@pytest.mark.parametrize("ps", ["Planning", "In progress", "Paused", "Waiting"])
def test_on_hold_matter_status_with_qualifying_project_status(ps):
    assert _is_active_matter(_m("On Hold", ps))


def test_on_hold_capitalisation_variants():
    # Notion may return "On hold" (lowercase h)
    assert _is_active_matter(_m("On hold", "In progress"))


# =============================================================================
# Cases that SHOULD be excluded
# =============================================================================

def test_backlog_excluded_even_when_matter_status_active():
    # Lan v. Eshak scenario: Matter Status Active, Project Status Backlog
    assert not _is_active_matter(_m("Active", "Backlog"))


@pytest.mark.parametrize("ps", ["Idea", "Done", "Canceled"])
def test_inactive_project_statuses_excluded(ps):
    assert not _is_active_matter(_m("Active", ps))


def test_blank_matter_status_excluded():
    assert not _is_active_matter(_m("", "In progress"))


def test_blank_project_status_excluded():
    # Blank Project Status is not in _ACTIVE_PROJECT_STATUSES
    assert not _is_active_matter(_m("Active", ""))


def test_completed_matter_status_excluded():
    assert not _is_active_matter(_m("Completed", "In progress"))


def test_archived_matter_status_excluded():
    assert not _is_active_matter(_m("Archived", "Planning"))


def test_fallback_to_status_field():
    # Some older records use "Status" rather than "Matter Status"
    m = {"Status": "Active", "Project Status": "Planning"}
    assert _is_active_matter(m)


# =============================================================================
# _ACTIVE_PROJECT_STATUSES constant
# =============================================================================

def test_active_project_statuses_contains_valid_values():
    assert "planning" in _ACTIVE_PROJECT_STATUSES
    assert "in progress" in _ACTIVE_PROJECT_STATUSES
    assert "paused" in _ACTIVE_PROJECT_STATUSES
    assert "waiting" in _ACTIVE_PROJECT_STATUSES


def test_active_project_statuses_excludes_invalid_values():
    assert "backlog" not in _ACTIVE_PROJECT_STATUSES
    assert "idea" not in _ACTIVE_PROJECT_STATUSES
    assert "done" not in _ACTIVE_PROJECT_STATUSES
    assert "canceled" not in _ACTIVE_PROJECT_STATUSES
    assert "" not in _ACTIVE_PROJECT_STATUSES
