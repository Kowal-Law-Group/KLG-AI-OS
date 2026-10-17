"""
alfred/skills — Individual KLG skill implementations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT SKILLS ARE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

In the KLG AI OS, a "skill" is a structured, named workflow that follows
the standard 5-step lifecycle:

  Step 1 — Locate the project page (find the matter in Notion)
  Step 2 — Read context (Layer 0 source data + Layer 1 project state)
  Step 3 — Do the work (draft, analyze, compose, synthesize)
  Step 4 — Update Layer 1 (write status/notes back to the project page)
  Step 5 — Tee up what's next (identify and surface the next action)

Every skill in this package follows this pattern. This standardization is
what makes Alfred a SINGLE assistant the team converses with — not a
collection of disconnected one-off scripts.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SKILL NAMING CONVENTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Skills are named with the prefix "klg-" followed by a hyphenated descriptor:
  klg-brief-elevation    — elevate a brief from draft to filing-ready
  klg-matter-intake      — onboard a new matter, clear conflicts, intake form
  klg-engagement-letter  — draft the engagement letter for a new matter

The skill name is logged to the project page on every execution (Step 4),
so the audit trail shows exactly which skill touched the page.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADDING A NEW SKILL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Create a new file: alfred/skills/klg_<name>.py
  2. Define a class that inherits from Skill (alfred/skills/base.py)
  3. Implement the `execute()` method following the 5-step lifecycle
  4. Add an entry to _SKILL_MODULES below — the registry builds automatically.
"""

import importlib
import logging

from alfred.skills.base import Skill, SkillResult, skill_generate, skill_read_file_text, skill_fetch_sharepoint

logger = logging.getLogger(__name__)

# Each tuple is (module_path, ClassName). Skills are loaded individually so
# one broken import cannot crash the entire registry.
_SKILL_MODULES: list[tuple[str, str]] = [
    ("alfred.skills.klg_case_assessment",           "KLGCaseAssessment"),
    ("alfred.skills.klg_case_novella",              "KLGCaseNovella"),
    ("alfred.skills.klg_record_digest",             "KLGRecordDigest"),
    ("alfred.skills.klg_opposition_separate_statement", "KLGOppositionSeparateStatement"),
    ("alfred.skills.klg_dz_overlay",                "KLGDZOverlay"),
    ("alfred.skills.klg_notebooklm_handoff",        "KLGNotebookLMHandoff"),
    ("alfred.skills.klg_court_doc_renamer",         "KLGCourtDocRenamer"),
    ("alfred.skills.klg_authority_library",         "KLGAuthorityLibrary"),
    ("alfred.skills.klg_appendix_cites",            "KLGAppendixCites"),
    ("alfred.skills.klg_content_research",          "KLGContentResearch"),
    ("alfred.skills.klg_oral_argument_full",        "KLGOralArgumentFull"),
    ("alfred.skills.klg_matter_intake",             "KLGMatterIntake"),
    ("alfred.skills.klg_deep_research_prompts",     "KLGDeepResearchPrompts"),
    ("alfred.skills.klg_conflict_waiver",           "KLGConflictWaiver"),
    ("alfred.skills.klg_podcast_guest_prep",        "KLGPodcastGuestPrep"),
    ("alfred.skills.klg_style_guide_check",         "KLGStyleGuideCheck"),
    ("alfred.skills.klg_cite_check",                "KLGCiteCheck"),
    ("alfred.skills.klg_response_plan",             "KLGResponsePlan"),
    ("alfred.skills.klg_appendix_audit",            "KLGAppendixAudit"),
    ("alfred.skills.klg_brief_elevation",           "KLGBriefElevation"),
    ("alfred.skills.klg_authority_map",             "KLGAuthorityMap"),
    ("alfred.skills.klg_issue_framing",             "KLGIssueFraming"),
    ("alfred.skills.klg_standard_of_review",        "KLGStandardOfReview"),
    ("alfred.skills.klg_oral_argument_prep",        "KLGOralArgumentPrep"),
    ("alfred.skills.klg_amicus_assessment",         "KLGAmicusAssessment"),
    ("alfred.skills.klg_record_navigator",          "KLGRecordNavigator"),
    ("alfred.skills.klg_daily_triage",              "KLGDailyTriage"),
    ("alfred.skills.klg_prebill_audit",             "KLGPrebillAudit"),
    ("alfred.skills.klg_research_compilation",      "KLGResearchCompilation"),
    ("alfred.skills.klg_brief_assembly",            "KLGBriefAssembly"),
    ("alfred.skills.klg_brief_map",                 "KLGBriefMap"),
]

# Registry of all successfully loaded skills, keyed by name.
# Alfred's run_skill tool looks skills up here by name.
SKILL_REGISTRY: dict[str, Skill] = {}

for _module_path, _class_name in _SKILL_MODULES:
    try:
        _mod = importlib.import_module(_module_path)
        _cls = getattr(_mod, _class_name)
        SKILL_REGISTRY[_cls.name] = _cls()
    except Exception as _e:
        logger.warning("Skill '%s' failed to load and was excluded from registry: %s", _class_name, _e)

__all__ = ["Skill", "SkillResult", "SKILL_REGISTRY"]
