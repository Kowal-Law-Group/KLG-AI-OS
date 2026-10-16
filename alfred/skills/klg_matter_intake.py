"""
alfred/skills/klg_matter_intake.py — Create a new KLG matter project page.

This is the first concrete skill in the KLG AI OS. It handles the intake
step for brand-new matters — creating the Layer 1 project page that all
subsequent skills operate against.

Because intake creates a page rather than working on an existing one, this
skill overrides run() to skip Steps 1 and 2 (there is no matter to find yet)
and logs the creation to the newly created page instead of a pre-existing one.
"""

from __future__ import annotations

import logging
from typing import Any

from alfred.skills.base import Skill, SkillContext, SkillResult
from config import settings
from notion_bridge.project_pages import _PROP_DEADLINE
from notion_bridge.work_schema import WorkSchema

logger = logging.getLogger(__name__)


class KLGMatterIntake(Skill):
    """
    Create a new matter project page in KLG's Notion Projects database.

    Invoke via Alfred's create_new_matter tool when the team opens a new case
    or project. The resulting page becomes the Layer 1 anchor that all
    subsequent Alfred skills read and write.

    Expected ctx.extra keys:
        matter_name  (str, required) — display name of the new matter
        category     (str) — "Case Project", "Case Support", or "Operations"
        case_stage   (str) — e.g., "Intake", "Evaluation", "Briefing — AOB"
        priority     (str) — "Low", "Medium", or "High"
        target_date  (str) — ISO date string, e.g., "2026-06-30" (optional)
        summary      (str) — one-paragraph matter description (optional)
    """

    name = "klg-matter-intake"
    description = "Create a new KLG matter project page in Notion."

    async def execute(self, ctx: SkillContext) -> SkillResult:
        extra = ctx.extra
        matter_name = extra.get("matter_name") or ctx.matter_name
        bridge = extra.get("_bridge")

        if not bridge:
            return SkillResult(
                summary="Intake failed: Notion bridge not available.",
                output="",
                success=False,
            )

        if not matter_name:
            return SkillResult(
                summary="Intake failed: matter_name is required.",
                output="",
                success=False,
            )

        category = extra.get("category", "Case Project")
        case_stage = extra.get("case_stage", "Intake")
        priority = extra.get("priority", "Medium")
        target_date = extra.get("target_date", "")
        summary_text = extra.get("summary", "")

        schema = WorkSchema(bridge, settings.notion_projects_db_id)
        title_prop = await schema.title_prop()
        category_prop, category_option = await schema.category_property(category)

        properties: dict[str, Any] = {
            title_prop: {
                "title": [{"text": {"content": matter_name}}]
            },
            "Status": {
                "status": {"name": "Planning"}
            },
            category_prop: {
                "select": {"name": category_option}
            },
            "Case Stage": {
                "select": {"name": case_stage}
            },
            "Priority": {
                "select": {"name": priority}
            },
        }

        if target_date:
            properties[_PROP_DEADLINE] = {"date": {"start": target_date}}

        if summary_text:
            properties["Summary"] = {
                "rich_text": [{"text": {"content": summary_text[:2000]}}]
            }

        # If the user provided intake notes, include them as the page body
        children = None
        if ctx.user_instruction and ctx.user_instruction != summary_text:
            children = [{
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": ctx.user_instruction[:2000]},
                    }]
                },
            }]

        page = await bridge.create_page(
            database_id=settings.notion_projects_db_id,
            properties=properties,
            children=children,
        )

        page_id = page.get("id", "")
        page_url = page.get("url", "")

        return SkillResult(
            summary=f"Opened new matter: {matter_name}. Project page created in Notion.",
            output=(
                f"Matter '{matter_name}' created.\n"
                f"Stage: {case_stage} | Priority: {priority} | Category: {category}\n"
                f"Notion: {page_url}"
            ),
            next_action=(
                "Review the new project page and add the case number, "
                "opposing counsel, and any known court deadlines."
            ),
            notion_updates={"_new_page_id": page_id},
            success=True,
        )

    async def run(
        self,
        ctx: SkillContext,
        project_pages: Any,
    ) -> SkillResult:
        # Intake creates a page rather than working on an existing one.
        # Inject the bridge so execute() can call create_page().
        ctx.extra["_bridge"] = project_pages._bridge

        matter_label = ctx.extra.get("matter_name") or ctx.matter_name or "(unnamed)"
        logger.info("Skill '%s' starting: creating new matter '%s'", self.name, matter_label)

        result = await self.execute(ctx)

        # Step 4: log to the newly created page (not a pre-existing matter)
        new_page_id = result.notion_updates.pop("_new_page_id", None)
        if result.success and new_page_id:
            try:
                await project_pages.log_skill_action(
                    page_id=new_page_id,
                    skill_name=self.name,
                    action_summary=result.summary,
                )
            except Exception as e:
                logger.warning("Intake Step 4 logging failed: %s", e)

        logger.info(
            "Skill '%s' completed for matter '%s'. Success: %s",
            self.name, matter_label, result.success,
        )
        return result
