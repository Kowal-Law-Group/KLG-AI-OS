"""
alfred/skills/klg_brief_map.py — Issue-by-issue brief map across the briefing.

Alfred port of Tim's klg-brief-map skill (saved 2026-10-16 in a Cowork session; source of
truth is KLG-AI-CONFIG/skills/klg-brief-map/SKILL.md). Reads the opening, response, and
reply briefs (and optionally our own working draft), then returns one row per argument
with page cites, a grade for the response, draft coverage, and an assessment or exposure
column.

SCOPE OF THIS PORT (differs from the Cowork skill):
  - Alfred returns the map as markdown tables for attorney review. It does not create the
    Notion database, the two views, or the per-cluster subagents. Use the Cowork skill when
    the matter needs the Notion database.
  - Page markers come from pdfplumber, one marker per PDF page. The model confirms the
    offset between PDF page and printed page from footers and the table of contents, and
    marks any cite it cannot confirm.
  - Each brief is capped at _MAX_BRIEF_CHARS characters. A truncated brief is flagged in
    the output so the attorney knows the map is partial.

UPLOAD ORDER: 1) opening brief, 2) response brief, 3) reply brief (optional),
4) our working draft of the brief we are writing (optional). Say so in the instruction if
the order differs.

CONFIDENTIALITY RULE: never echo client names or case facts into web searches.
"""
from __future__ import annotations

import logging
from datetime import date

from alfred.skills.base import Skill, SkillContext, SkillResult, skill_read_file_text

logger = logging.getLogger(__name__)

_MAX_BRIEF_CHARS = 120_000
_MAX_FILES = 4
_FILE_LABELS = [
    "OPENING BRIEF",
    "RESPONSE BRIEF",
    "REPLY BRIEF",
    "OUR WORKING DRAFT",
]

_BRIEF_MAP_PROMPT = """\
You are a KLG senior appellate attorney building a brief map: one row per argument, \
tracking each argument across the briefing.

KLG is a California appellate specialty firm.

WRITING RULES (non-negotiable):
- Active verbs; no nominalizations or gerunds where a verb works better
- Em dashes without spaces—like this—not like this —
- No "furthermore", "therefore", "clearly", "it is axiomatic", "as such",
  "instant case", "aforementioned", "hereinabove", "it is well established"
- No doubled modifiers
- Normal varied sentences, not fragments
- Write placeholders in ALL CAPS
- Never call the map or any brief "final" or "file-ready"

CONFIDENTIALITY: This map is privileged work product.

HARD RULES:
- Never invent authorities, quotations, dollar figures, or page or record cites. \
Report what the briefs say. Authorities and record cites stay unverified until \
klg-cite-check runs.
- Every page cite uses PRINTED brief pages. Each extract below carries \
"=== PDF PAGE n ===" markers. Confirm the offset between the PDF page index and the \
printed page number from the page footers and the table of contents before citing. \
If you cannot confirm the offset for a brief, cite the PDF page and tag the cite \
[UNCONFIRMED OFFSET].
- If an extract is marked TRUNCATED, say so at the top of the output and list the \
parts of the briefing the map does not cover.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MATTER CONTEXT (from Notion)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{matter_summary}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BRIEFS PROVIDED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{briefs_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADDITIONAL INSTRUCTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{instruction}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIELDS FOR EACH ROW

- Issue (title): one sentence stating the key argument. Frame it as "[Appellant] \
argues that ..." for opening-brief rows, "[Respondent] argues independently that ..." \
for rows the response adds, and "[Party] argues for the first time in reply that ..." \
for rows new in the reply.
- Opening brief: start with the brief, pages, and heading, for example "AOB 39–42 \
(V.A.1)." Then the core supporting arguments in full sentences, with key authorities by \
short name and pincite, and record cites as the brief gives them. For a row the opening \
brief never raised, write "Not raised." and say whether the brief touched the point at all.
- Response brief: start with "RB pp (section)." Then the core counterarguments, ending on \
a new line with a grade: 🟢 full answer, 🟡 partial answer or exploitable weakness (name \
it), 🔴 no answer or effective concession, ⚪ not graded (the argument first appeared in \
reply).
- Reply: the filed reply, or our draft cited by its headings when it has no page numbers. \
End with COVERED, "PARTIAL: what is missing," "GAP: not addressed," or "NEW: not in the \
opening brief or below." If no reply or draft was provided, write "Not provided."
- Assessment: an emoji and one to three sentences on where the point stands, followed by \
the single most useful move. When we are the appellant, the emoji measures strength for \
our client. When we are the respondent, it measures reversal risk, and the column is \
named Exposure. Name the dollar exposure where the briefs state it. Flag any concession \
or narrowing in our draft relative to our filed brief.
- Row type: AOB issue, Supp. AOB issue, RB added, XAOB issue (cross-appeal), or ARB new.

On a cross-appeal the columns shift. The opening brief is the XAOB, the response is our \
XRB or our draft of it, and the reply is the cross-appellant's XRB, or "not yet filed" \
with a predicted cross-reply move.

Granularity: one row per argument heading or distinct sub-argument, plus a row for each \
standalone argument the response adds. Keep standard-of-review rows only when the \
standard is contested.

Work out whether we are appellant or respondent from the matter context and the \
instructions. If you cannot tell, say so at the top and use "Assessment" as the column name.

OUTPUT FORMAT

1. A 3–5 sentence summary: row count, the gaps, the weakest points, and any concession \
that needs a client or co-counsel decision.
2. One section per issue group, each with a numbered list of rows. Use this layout for \
every row:

**#N — [Issue (title)]** (Row type: ...)
- Opening brief: ...
- Response brief: ...
- Reply: ...
- Assessment (or Exposure): ...

3. A "Cites to confirm" list: every cite you marked unconfirmed and every opposing-brief \
cite that falls outside the record range it claims.
4. A "Not covered" list: anything the map omits because a brief was missing or truncated.

Close with this line, with today's date:
Unverified Claude draft — {today}. DRAFT — attorney review required.\
"""


class KLGBriefMap(Skill):
    name = "klg-brief-map"
    required_tools = ["search_notion", "search_sharepoint"]
    description = (
        "Map each argument across the opening, response, and reply briefs (and our own "
        "working draft): page cites, a grade for the response, draft coverage, and an "
        "assessment or exposure column. Upload the briefs in order, then run this skill."
    )

    async def execute(self, ctx: SkillContext) -> SkillResult:
        file_tokens: list[str] = ctx.extra.get("file_tokens", [])
        instruction = ctx.user_instruction.strip()

        briefs = await _extract_briefs(file_tokens)
        labels = {label for label, _ in briefs}
        if not {"OPENING BRIEF", "RESPONSE BRIEF"} <= labels:
            return SkillResult(
                summary="klg-brief-map: needs at least the opening and response briefs.",
                output=(
                    "To build a brief map, attach the briefs in this order, then run:\n"
                    "`Alfred, run klg-brief-map on [Matter Name]`\n\n"
                    "1. Opening brief (required)\n"
                    "2. Response brief (required)\n"
                    "3. Reply brief (optional)\n"
                    "4. Our working draft of the brief we are writing (optional)\n\n"
                    "Use PDFs when you can. Alfred reads the printed page numbers from "
                    "the page footers to build the cites."
                ),
                next_action="Attach the opening and response briefs, then re-run.",
                success=False,
            )

        matter_label = ctx.matter_name or "this matter"
        matter_text = ctx.matter_summary or "(No Notion project page found for this matter.)"

        briefs_block = "\n\n".join(
            f"##### {label}\n{text}" for label, text in briefs
        )
        prompt = _BRIEF_MAP_PROMPT.format(
            matter_summary=matter_text[:5000],
            briefs_block=briefs_block,
            instruction=instruction[:1000] or "(none)",
            today=date.today().isoformat(),
        )

        output_text = await self.generate(prompt, ctx)

        truncated = [label for label, text in briefs if "TRUNCATED" in text[-200:]]
        trunc_note = (
            f" Truncated input: {', '.join(truncated)}; the map is partial."
            if truncated
            else ""
        )

        return SkillResult(
            summary=(
                f"Brief map drafted for {matter_label} from {len(briefs)} document(s)."
                f"{trunc_note} Unverified; attorney review required."
            ),
            output=f"**Brief Map — {matter_label}**\n\n{output_text}",
            next_action=(
                "Read the gaps and weakest points first. Spot-check at least eight page "
                "cites against the briefs. Run klg-cite-check on the authorities. For the "
                "Notion database with views, run klg-brief-map in Cowork."
            ),
            success=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _read_with_page_markers(path: str) -> str:
    """Read a brief. PDFs get an '=== PDF PAGE n ===' marker before each page."""
    if path.lower().endswith(".pdf"):
        try:
            import pdfplumber

            parts: list[str] = []
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    parts.append(f"=== PDF PAGE {i} ===\n{text}")
            return "\n".join(parts)
        except Exception as e:
            logger.warning("klg-brief-map: PDF extraction failed: %s", e)
    return skill_read_file_text(path)


async def _extract_briefs(file_tokens: list[str]) -> list[tuple[str, str]]:
    """Return [(label, text)] in upload order, capped and flagged if truncated."""
    briefs: list[tuple[str, str]] = []
    for idx, token in enumerate(file_tokens[:_MAX_FILES]):
        try:
            from alfred.file_store import consume_token, delete_file

            path = consume_token(token)
            if not path:
                continue
            text = _read_with_page_markers(path)
            delete_file(path)
            if not text:
                continue
            if len(text) > _MAX_BRIEF_CHARS:
                text = (
                    text[:_MAX_BRIEF_CHARS]
                    + "\n\n[TRUNCATED: extract cut at "
                    + f"{_MAX_BRIEF_CHARS:,} characters]"
                )
            briefs.append((_FILE_LABELS[idx], text))
        except Exception as e:
            logger.warning("klg-brief-map: file extraction failed: %s", e)
    return briefs
