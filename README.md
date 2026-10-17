# KLG AI Operating System

Alfred — the firm's multi-model AI assistant — plus Bloodhound, background agents, and a full web interface for Kowal Law Group's California appellate practice.

**Production:** `https://klg-ai-os-production.up.railway.app`  
**Deploy:** `git push origin main` → Railway auto-redeploys (~2 min)  
**Access:** Password-protected. Master password in Railway env var `APP_PASSWORD`.

---

## What this is

A four-layer operating system for a boutique appellate firm:

| Layer | Description |
|---|---|
| 0 | Source-of-truth data — Notion, SharePoint, court dockets (read-only) |
| 1 | Notion project pages — the AI-readable visibility scaffold |
| 2 | Claude skills — 5-step lifecycle: Locate → Read → Work → Update L1 → Queue next |
| 3 | Background agents — deadline watch, agenda digest, hygiene (read L1, post to Slack) |

**Alfred** is the inward-facing executive assistant: chat interface, Notion read/write, skill routing, file analysis, multi-model switching.  
**Bloodhound** is the outward-facing surveillance engine: RSS + CourtListener feeds, Claude triage, Watch List maintenance.

---

## Architecture

```
web-next/          React + Vite + TypeScript frontend (Web + Mobile Responsive)
├── src/
│   ├── components/
│   │   ├── dashboard/   Dynamic Multi-View Dashboard (Kanban, Grid, List) + Metric Cards
│   │   ├── chat/        Alfred chat with SSE streaming & model selector
│   │   ├── cases/       Appellate case file folders & record sync dashboard
│   │   ├── accounting/  Billing, invoices & retainer trust accounting dashboard
│   │   ├── skills/      Skills launcher popup & execution navigator
│   │   └── layout/      App shell, Header, WorkspaceTabs, MobileBottomNav
│   ├── store/           Zustand stores (chat, matter, UI state)
│   ├── api/             API client functions
│   └── types/           Shared TypeScript types
docs/
├── handoff/
│   └── backend-agent-ui-spec.md  Agent & Reactive UI integration specification

api/               FastAPI backend (Python)
├── routes/
│   ├── alfred.py        Matter CRUD, task CRUD, chat endpoints
│   ├── bloodhound.py    Scan trigger, watch list
│   └── slack.py         Slack Events API handler
└── main.py              App entry, auth middleware, scheduler

alfred/            Alfred agent
├── agent.py             PydanticAI agent — all @tool definitions
└── model_factory.py     Multi-model routing (Claude, GPT-4o, Gemini, Perplexity)

bloodhound/        Bloodhound agent
└── agent.py             RSS triage, CourtListener queries, Watch List writes

notion_bridge/     Notion API wrapper
├── client.py            NotionBridge — page reads, block ops, search
└── tasks.py             TaskPages — per-matter task auto-detection and CRUD

agents/            Background scheduler
└── scheduler.py         APScheduler jobs (Bloodhound 7AM, Deadline 8AM, etc.)

skills/            Skill prompt library
└── *.md                 Individual skill definition files
```

---

## Alfred's tools

Alfred has 18 tools registered via `@AlfredAgent.tool`. These are what he can do autonomously when responding to a message — in chat, from a skill, or from Slack.

### Matters

| Tool | What it does |
|---|---|
| `find_and_summarize_matter` | Look up any matter by name (partial names work) and return its full current state — status, priority, deadlines, case notes, open questions |
| `update_matter_status` | Change a matter's status to Planning / In progress / Paused / Backlog / Done / Canceled |
| `update_matter` | Update any structured field on a matter: deadlines, case stage, priority, next deadline info, or append a timestamped note to the page body |
| `create_new_matter` | Open a new matter project page in the Notion Projects database with correct fields and initial stage |

### Deadlines & workload

| Tool | What it does |
|---|---|
| `get_upcoming_deadlines` | Get all matters with deadlines in the next N days, sorted soonest first |
| `get_team_workload` | Get all active matters assigned to any team member — useful for "what's on Brittney's plate?" |

### Tasks

| Tool | What it does |
|---|---|
| `get_matter_tasks` | Get all tasks for a matter, grouped by stage — reads from Notion inline DB or to-do blocks |
| `create_matter_task` | Create a new task on a matter page in Notion (with stage, assignee, and deadline) |
| `update_matter_task` | Update a task by name — change status, reassign, move to a different stage, or rename |

### Research & knowledge

| Tool | What it does |
|---|---|
| `search_notion` | Full-text search across all connected Notion pages — finds memos, research docs, skill pages, anything in the workspace |
| `search_sharepoint` | Search KLG's SharePoint document library for briefs, exhibits, and correspondence by keyword or folder path |
| `read_sharepoint_file` | Read the full text of a SharePoint `.docx` or `.txt` file after `search_sharepoint` returns its ID |
| `web_search` | Live internet search via Tavily — used for recent case developments, judge information, news, and any external knowledge. Requires `TAVILY_API_KEY` in Railway |
| `deep_research_with_chatgpt` | Hand off a complex research question to GPT-4o for a long-form memo (2,000–4,000 words). Requires `OPENAI_API_KEY` |

### Intelligence

| Tool | What it does |
|---|---|
| `get_bloodhound_watch_list` | Query the Bloodhound Watch List — filter by Tier (1/2/3) or issue keyword (e.g. "supersedeas", "First Amendment") |

### Communications

| Tool | What it does |
|---|---|
| `send_slack_message` | Post a message to any Slack channel (`#case-management`) or DM any team member by first name |
| `log_action_to_matter` | Write a timestamped action note to a matter's Notion page — Alfred does this automatically after completing skill work |

### Memory

| Tool | What it does |
|---|---|
| `save_note` | Save a persistent note to Alfred Notes in Notion — attorney preferences, matter observations, opposing counsel patterns, firm knowledge. Alfred saves these proactively without being asked. Requires `NOTION_ALFRED_NOTES_DB_ID` |
| `recall_notes` | Retrieve notes from Alfred Notes, filtered by matter name and/or category |

### Skills runner

| Tool | What it does |
|---|---|
| `run_skill` | Execute any named KLG skill programmatically — Alfred can chain skills together or invoke them in response to a chat message |

---

## Skills library

18 skills are available in the Skills launcher. Each skill is a structured workflow with a checklist and a prompt that Alfred executes immediately when selected.

### Intake

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **New Matter Intake** | 30 min | Brittney / Edwyn | Runs the KLG new matter intake protocol — conflicts check, scope definition, Notion setup, team assignment, initial deadlines, engagement letter outline |
| **Record Navigator** | 1–2 hours | Edwyn | Maps the trial record to locate key testimony, rulings, objections, and preserved issues — indexes transcripts and flags preservation gaps |
| **Conflict Waiver** | 20–30 min | Tim / Edwyn | Drafts a joint-representation conflict waiver letter per California Rules of Professional Conduct |

### Research

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **Case Research** | 45–90 min | William / Edwyn | Deep appellate case research — binding authority, circuit splits, analogous holdings, treatment history, 1-page memo |
| **Authority Map** | 30–60 min | William | Builds a hierarchical authority map SCOTUS → 9th Cir. → Cal. S. Ct. for a specific doctrine, ready for brief citation |
| **Amicus Assessment** | 45 min | Tim / Bloodhound | Evaluates whether a case warrants a KLG amicus brief — case posture, importance, coalition strategy, proposed argument angle |
| **Bloodhound Triage** | 20 min | Bloodhound / Edwyn | Reviews the full Watch List, escalates Tier 1 cases to active firm review, flags imminent deadlines |
| **Deep Research Prompts** | 30 min | William / Edwyn | Generates tiered ChatGPT Deep Research prompts from case materials — saves hours of prompt construction; produces Tier 1/2/3 prompts ready to paste |

### Drafting

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **Brief Elevation** | 2–4 hours | Tim / Edwyn | Elevates a draft brief to KLG standard — sharpens theory, improves structure, amplifies persuasion, verifies citations |
| **Issue Framing** | 30 min | Tim | Frames the issue presented at three levels of specificity (narrow / mid / broad) and selects the version with maximum persuasive impact |
| **Response Brief Strategy** | 30–60 min | Tim / Edwyn | Analyzes the appellant's opening brief — maps each argument, rates its strength, drafts counter-positions, record strategy, and research priorities |
| **Brief Map** | 20–40 min | Tim / Edwyn | Maps each argument across the opening, response, and reply briefs — printed-page cites, a grade for the response, draft coverage, and an assessment or exposure column. Returns markdown for attorney review; the Notion database build runs in the Cowork klg-brief-map skill |
| **Style Guide Check** | 15–20 min | Edwyn / Tim | Reviews a brief or memo against the KLG Style Guide — flags forbidden words, nominalizations, passive voice, and doubled modifiers with suggested fixes |
| **Citation Audit** | 30–60 min | Edwyn / William | Two-phase citation audit: Phase A formats citations and builds a Westlaw pull list; Phase B verifies each citation against the source text |

### Argument

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **Standard of Review** | 20–40 min | Edwyn | Identifies and argues the applicable standard of review — ruling type, circuit authority, preservation issues, forfeiture risks |
| **Oral Argument Prep** | 2–3 hours | Tim | Complete oral argument preparation — 60-second opening, 10 hardest panel questions with answers, one strategic concession, record citations |

### Record

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **Appendix Audit** | 15–30 min | Tim / Edwyn | Audits a proposed appendix compile folder for underinclusivity — compares full docket against proposed inclusions and flags excluded documents by risk level (HIGH / MEDIUM / LOW) |

### Operations

| Skill | Time | Owner | What it does |
|---|---|---|---|
| **Matter Hygiene Audit** | 15 min | Edwyn / Alfred | Alfred's weekly hygiene scan — surfaces stale matters, missing dates, blocked matters, and priority gaps; posts findings to Slack |
| **CALP Episode Prep** | 1 hour | Tim | Prepares a California Appellate Law Podcast episode — guest research, 10 interview questions, episode description, social media copy |

---

## Multi-model support

Alfred supports multiple AI providers. The model is selectable per conversation from the chat interface.

| Model | Provider | Key required |
|---|---|---|
| Claude Sonnet 4.6 | Anthropic | `ANTHROPIC_API_KEY` (default) |
| Claude Opus 4.8 | Anthropic | `ANTHROPIC_API_KEY` |
| Claude Haiku 4.5 | Anthropic | `ANTHROPIC_API_KEY` (rate-limit fallback) |
| GPT-4o | OpenAI | `OPENAI_API_KEY` |
| GPT-4o mini | OpenAI | `OPENAI_API_KEY` |
| Gemini 2.0 Flash | Google | `GOOGLE_API_KEY` |
| sonar-pro | Perplexity | `PERPLEXITY_API_KEY` ⚠ routing bug — fix pending |

**Rate-limit fallback chain:** If the primary model returns a rate-limit error, Alfred automatically falls back through `Sonnet → Haiku`. The fallback is active in both the web UI and Slack.

---

## Local development

**Requirements:** Python 3.11+, Node 20+

```bash
# Backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env         # fill in keys (see Environment Variables below)
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd web-next
npm install
npm run dev                  # proxies /api → localhost:8000
```

Open `http://localhost:5173`.

---

## Deployment

Railway handles everything. No manual steps after a push.

```bash
git add <files>
git commit -m "your message"
git push origin main
# Railway builds and redeploys automatically (~2 min)
```

One uvicorn worker only — `--workers 2` caused 502s on Railway. The APScheduler background jobs run in the same process.

---

## Environment variables

Set these in Railway → KLG AI OS → Variables.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key — get from console.anthropic.com |
| `NOTION_TOKEN` | Notion integration token (starts `ntn_A42282...`) |
| `NOTION_PROJECTS_DB_ID` | Notion Projects database ID |
| `NOTION_WATCH_LIST_DB_ID` | Notion Watch List database ID |
| `NOTION_ISSUES_DB_ID` | Notion Issues database ID |
| `NOTION_COMMS_LOG_DB_ID` | Notion Comms Log database ID |
| `APP_PASSWORD` | Master password for all users |

### Optional — unlock features

| Variable | Feature unlocked |
|---|---|
| `TAVILY_API_KEY` | `web_search` tool — Alfred internet search (free key at tavily.com) |
| `OPENAI_API_KEY` | GPT-4o / GPT-4o mini + `deep_research_with_chatgpt` tool |
| `GOOGLE_API_KEY` | Gemini 2.0 Flash in model switcher |
| `PERPLEXITY_API_KEY` | Perplexity sonar models (routing bug fix pending) |
| `SLACK_BOT_TOKEN` | Slack bot responses + `send_slack_message` tool |
| `SLACK_SIGNING_SECRET` | Slack webhook signature verification |
| `APP_PASSWORDS` | Per-user passwords (JSON map: `{"tim":"...", "brittney":"..."}`) |
| `NOTION_ALFRED_NOTES_DB_ID` | Alfred persistent memory — `save_note` and `recall_notes` tools |
| `NOTION_SYSTEM_STATE_DB_ID` | Alfred internal state tracking across sessions |
| `SHAREPOINT_TENANT_ID` | SharePoint access — `search_sharepoint` and `read_sharepoint_file` tools |
| `SHAREPOINT_CLIENT_ID` | SharePoint access |
| `SHAREPOINT_CLIENT_SECRET` | SharePoint access |
| `SHAREPOINT_SITE_URL` | SharePoint access |

---

## Notion databases

All databases must be shared with the Notion integration (Notion UI → ... → Connections → connect the integration token starting `ntn_A42282...`).

| Database | Env var | Contents |
|---|---|---|
| Projects | `NOTION_PROJECTS_DB_ID` | All KLG matters — the central source of truth Alfred reads and writes |
| Watch List | `NOTION_WATCH_LIST_DB_ID` | Bloodhound-tracked cases by tier and issue area |
| Issues | `NOTION_ISSUES_DB_ID` | Firm issues, blockers, and open questions |
| Comms Log | `NOTION_COMMS_LOG_DB_ID` | Alfred-logged actions and communications |
| Alfred Notes | `NOTION_ALFRED_NOTES_DB_ID` | Alfred's persistent memory (not yet created — see Known Issues) |
| System State | `NOTION_SYSTEM_STATE_DB_ID` | Alfred internal state (not yet created — see Known Issues) |

---

## Slack setup

1. Add `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` to Railway.
2. In Slack app settings → Event Subscriptions, set Request URL to `https://klg-ai-os-production.up.railway.app/slack/events`.
3. Subscribe to: `app_mention`, `message.channels`, `message.groups`.
4. Invite `@Alfred` to relevant channels: `#case-management`, `#alfred`, and per-case channels (`#riva-fourjays`, etc.).

Alfred responds in every channel he's been invited to. The rate-limit fallback chain is active in Slack — same behaviour as the web UI.

---

## Authentication

- All API endpoints require HTTP Basic auth except `/`, `/health`, `/static/`, `/slack/events`
- Frontend uses a custom password modal — no browser native dialog
- `APP_PASSWORD` is a master override that works for any username
- `APP_PASSWORDS` is a per-user JSON map: `{"tim": "password1", "brittney": "password2"}`
- Admin users (Tim, Edwyn) can switch identity via a clickable chip in the header
- Non-admin users (William, Brittney, Ted, Richard) have a non-clickable identity chip — they cannot impersonate others
- If `APP_PASSWORDS` contains escaped quotes or newlines (Railway raw editor bug), delete the variable and re-enter as a single clean JSON line on one line

---

## Background jobs

Defined in `agents/scheduler.py`. All times Pacific.

| Job | Schedule | What it does |
|---|---|---|
| Bloodhound scan | Daily 7:00 AM | Scans RSS feeds and CourtListener, triages new cases, updates Watch List |
| Deadline watch | Daily 8:00 AM | Checks all active matters for upcoming deadlines, posts alerts to Slack |
| Weekly agenda digest | Monday 7:30 AM | Generates a weekly matter status digest and posts to `#case-management` |
| Hygiene sweep | Monday 8:15 AM | Flags matters with missing dates, stale pages, or blocked status |

---

## Known issues and open items

| Issue | Priority | Notes |
|---|---|---|
| `TAVILY_API_KEY` not set in Railway | High | Alfred can't search the internet until this is added — free key at tavily.com |
| Anthropic API credits | Critical | Alfred stops responding when balance hits zero — add credits at console.anthropic.com |
| `/alfred/chat` endpoints have no auth guard | Security | Must fix before firm-wide rollout — one `Depends(require_auth)` per route |
| Perplexity `sonar-pro` routing bug | Medium | Dead code in `alfred/agent.py` routes Perplexity requests to Anthropic — fix is one reorder |
| SharePoint access blocked | Blocked | Awaiting Ozzy's Azure admin consent at portal.azure.com |
| Alfred Notes + System State databases not created | Medium | Notion databases need creating; IDs need adding to Railway |
| Non-Claude model API keys unconfirmed | Low | `OPENAI_API_KEY`, `GOOGLE_API_KEY` status in Railway not verified |

---

## Team

| Person | Role | App access |
|---|---|---|
| Tim | Managing attorney | Admin |
| Edwyn | Systems partner, build lead | Admin |
| Brittney | Paralegal, primary daily user | Standard |
| Ted / William / Richard | Staff | Standard (identity-locked) |
| Ozzy | External IT (Azure/SharePoint) | n/a |

---

## Technical notes

- Notion API pinned to version `2022-06-28` in `notion_bridge/client.py` — do not change (2025 version removed `/databases/{id}/query`)
- Single uvicorn worker only — multiple workers break APScheduler's in-process job store
- `WWW-Authenticate` header deliberately omitted from 401 responses — adding it restores the browser native dialog, which is suppressed by design
- Task storage auto-detection: `TaskPages` in `notion_bridge/tasks.py` reads each matter's page blocks and routes to inline DB query or to-do block parsing automatically — no per-matter configuration
- Project categories: Case Project (default), Case Support, Operations, Think Tank — pass `?category=all` to endpoints to retrieve all categories
- Per-case Slack channels (e.g., `#riva-fourjays`) require `/invite @Alfred` before they are active
- `output_type=TriageDecision` in `bloodhound/agent.py` — not `result_type`. Do not change.
