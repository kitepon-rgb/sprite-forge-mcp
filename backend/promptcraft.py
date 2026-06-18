"""AI prompt-crafting — reproduce the user's old "have Claude/Codex write the prompt" step.

The backend shells the user's OWN `claude -p` (Claude Code non-interactive) or `codex` CLI
on THIS Mac (no Anthropic API key, no extra cost — uses the existing CC/Codex login). It runs
with cwd = a project root so the CLI loads THAT project's CLAUDE.md / .mcp.json / memory
(e.g. rpgdev's world & character context), giving on-world prompt expansion. Failures are
raised loudly (no silent fallback — global rule).
"""
from __future__ import annotations
import asyncio
from pathlib import Path

from . import config

SYS = (
    "You are a prompt engineer for an Illustrious / SDXL anime game-sprite generator that makes "
    "retro-JRPG character sprites. Expand the user's rough idea into ONE detailed, high-quality "
    "English danbooru-tag prompt describing ONLY the SUBJECT / character (appearance, outfit, hair, "
    "colors, accessories, vibe). Do NOT add art-style or background tags — the generator auto-adds the "
    "retro-JRPG style and a clean background. Output ONLY the comma-separated prompt on a single line: "
    "no preamble, no quotes, no markdown, no explanation.")


async def craft(idea: str, kind: str = "character", project: str | None = None) -> dict:
    """Expand a rough idea into a generation prompt via the configured CLI (cwd=project).
    Returns {prompt, cli, model, project}. Raises on CLI failure / empty output."""
    idea = (idea or "").strip()
    if not idea:
        raise ValueError("idea is empty")
    project_dir = Path(project or config.PROMPT_PROJECT_DIR)
    if not project_dir.is_dir():
        raise RuntimeError(f"project dir not found: {project_dir}")
    cli, model = config.PROMPT_CLI, config.PROMPT_MODEL

    if cli == "codex":
        # codex non-interactive; embed the system instruction in the prompt
        argv = ["codex", "exec", "--model", model, f"{SYS}\n\nRough idea: {idea}"]
    else:  # claude -p (validated path)
        argv = ["claude", "-p", "--model", model, "--append-system-prompt", SYS, idea]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=config.PROMPT_TIMEOUT)
    except FileNotFoundError as e:
        raise RuntimeError(f"{cli} CLI not found on PATH ({e})") from e
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"{cli} timed out after {config.PROMPT_TIMEOUT}s") from e

    text = out.decode("utf-8", "replace").strip()
    if (proc.returncode or 0) != 0 or not text:
        tail = (err.decode("utf-8", "replace").strip() or text)[-300:]
        raise RuntimeError(f"{cli} failed (rc={proc.returncode}): {tail}")
    # the print response is the prompt; if multi-line, keep the longest non-empty line
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prompt = max(lines, key=len) if lines else text
    return {"prompt": prompt, "cli": cli, "model": model, "project": str(project_dir)}
