# OpenCode Agent Instructions - AstrBot Magic Context

This file guides future AI agents working on `astrbot_plugin_magic-context` (an AstrBot plugin implementing OpenCode magic-context context compression).

## 🛠 Developer Workflow & Verification

This is a Python 3.12/3.13 AstrBot plugin.

### Key Commands
- **Check/Lint**: Run `ruff check .` to check for formatting and lint issues (this project has `.ruff_cache_local`).
- **Tests**: Look for `test_*.py` or use `pytest` if writing core logic tests (e.g., `pytest test_comparison.py`).

---

## 🏛 Directory & Architecture Overview

- `main.py`: The main plugin entry point inheriting from `Star`. Registers handlers/filters.
- `metadata.yaml`: Core metadata detailing the plugin name `astrbot_plugin_magic_context`, version, and compatibility.
- `hooks/`: Filter and event interceptor hooks implementing magic-context behavior:
  - `hooks/tag.py`: Context tagging (`§N§` tag insertion).
  - `hooks/heuristic_cleanup.py`: Cleaning redundant tools outputs and logs.
  - `hooks/caveman.py`: Caveman context compression implementation.
  - `hooks/historian.py`: LLM-based Summarization/Historian Agent.
  - `hooks/injection.py` / `postprocess.py`: Injecting and rendering compressed context.
- `storage/`: Local data adapters, primarily `storage/database.py` and SQLite setup.
- `pages/overview/`: Custom WebUI frontend pages. Include HTML, JS (`app.js`), and CSS.

---

## ⚠️ Critical Constraints & Quirks

1. **Avoid Missing Refs (Hooks vs. Runner Hooks)**:
   - Do NOT mix up AstrBot Plugin event hooks (`@filter...`) in `hooks/` with Agent runner hooks (`BaseAgentRunHooks`).
   - Use async handler functions (`async def`) for all event interceptors.
2. **Context Compression Rule**:
   - The plugin inserts tags of the form `§N§` and compresses message histories when nearing `max_context_tokens`.
   - Never remove `_conf_schema.json` fields without updating both `main.py` and references to `self.config`.
3. **Database Rules**:
   - `tags_db.py` handles tag mapping. SQLite DB writes must be threaded or async-safe.
4. **AstrBot Dependency**:
   - Compatible with AstrBot version `">=4.16,<5"`. Use SDK patterns when possible (referenced in `skill-astrbot-dev`).
