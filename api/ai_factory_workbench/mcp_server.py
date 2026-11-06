"""Local, proposal-only MCP adapter for one operator-configured workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp.types import (INVALID_PARAMS, CallToolResult, InputRequiredResult, PaginatedRequestParams,
                       RequestParams, ToolAnnotations)
from pydantic import Field, ValidationError

from . import Actor, Workbench, WorkbenchError
from .interface_models import CASE_ID_PATTERN, OpportunityInput, ReviewInput
from .profiles import WorkProfile, get_profile, list_profiles

SKILL_NAME = "ai-factory-workbench"
SKILL_DESCRIPTION = "Prepare evidence-bound AI governance, platform, and operations reviews with AI Factory Workbench."
SKILL_URI = f"skill://{SKILL_NAME}/SKILL.md"
SKILLS_CACHE = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
PROPOSAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


class SkillInput(RequestParams):
    uri: str = Field(max_length=256)


class BundledSkill(Extension):
    """Publish the single wheel-bundled Agent Skill using SEP-2640 discovery."""

    identifier = "io.modelcontextprotocol/skills"

    def __init__(self) -> None:
        content = files(__package__).joinpath("agent_skills", SKILL_NAME, "SKILL.md").read_bytes()
        self.resource = TextResource(uri=SKILL_URI, name="AI Factory Workbench Skill",
                                     mime_type="text/markdown", text=content.decode("utf-8"))
        self.entry = {
            "uri": SKILL_URI,
            "frontmatter": {"name": SKILL_NAME, "description": SKILL_DESCRIPTION},
            "resources": [{"uri": SKILL_URI, "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                           "size": len(content)}],
        }

    def resources(self) -> tuple[ResourceBinding, ...]:
        return (ResourceBinding(self.resource),)

    def methods(self) -> tuple[MethodBinding, ...]:
        versions = frozenset({"2026-07-28"})
        return (MethodBinding("skills/list", PaginatedRequestParams, self._list, versions),
                MethodBinding("skills/get", SkillInput, self._get, versions))

    async def _list(self, _context: Any, params: PaginatedRequestParams | None) -> dict[str, Any]:
        if params is not None and params.cursor is not None:
            raise MCPError(code=INVALID_PARAMS, message="Invalid skill cursor.")
        return {**SKILLS_CACHE, "skills": [self.entry]}

    async def _get(self, _context: Any, params: SkillInput) -> dict[str, Any]:
        if params.uri != SKILL_URI:
            raise MCPError(code=INVALID_PARAMS, message="Unknown skill URI.")
        return {**SKILLS_CACHE, "skill": self.entry}


class _WorkbenchMCPServer(MCPServer[None]):
    async def call_tool(self, name: str, arguments: dict[str, Any],
                        context: Context[None, Any] | None = None) -> CallToolResult | InputRequiredResult:
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as error:
            # The SDK's synthesized tool models may echo rejected evidence or fields.
            if isinstance(error.__cause__, ValidationError):
                raise ToolError("invalid_tool_arguments") from None
            raise


@contextmanager
def _tool_errors():
    try:
        yield
    except WorkbenchError as error:
        raise ToolError(str(error)) from None
    except (sqlite3.Error, OSError):
        raise ToolError("workbench_unavailable") from None


def build_server(engine: Workbench, profile: WorkProfile) -> MCPServer:
    """Expose a fixed non-reviewer agent; database and profile are host configuration."""
    agent = Actor("ai-factory-mcp-agent", frozenset({"editor"}))
    server = _WorkbenchMCPServer(
        "ai_factory_workbench",
        title="AI Factory Workbench",
        instructions=("Read the active work profile before preparing a proposal. The server creates "
                      "dossiers for separate review. A proposal is never an approval or a write to the ERP."),
        extensions=[BundledSkill()],
    )

    @server.tool(annotations=READ_ONLY)
    def workbench_get_profile() -> dict[str, Any]:
        """Read the workspace's active profile and the evidence required for a review."""
        return {"workspace": engine.workspace, "profile": profile.describe(), "skill_uri": SKILL_URI}

    @server.tool(annotations=READ_ONLY)
    def workbench_list_cases(limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 20) -> dict[str, Any]:
        """List a bounded set of case summaries. Use workbench_get_case to read one dossier."""
        with _tool_errors():
            cases = engine.cases(agent, limit=limit)
        fields = ("id", "title", "kind", "status", "revision", "priority")
        return {"cases": [{field: case[field] for field in fields} for case in cases]}

    @server.tool(annotations=READ_ONLY)
    def workbench_get_case(case_id: Annotated[str, Field(pattern=CASE_ID_PATTERN)]) -> dict[str, Any]:
        """Read the selected dossier, exact revision, evidence and any recorded decision."""
        with _tool_errors():
            return engine.get_case(case_id, agent)

    @server.tool(annotations=PROPOSAL)
    def workbench_prepare_review(proposal: ReviewInput) -> dict[str, Any]:
        """Prepare a review-only dossier using the active profile's evidence checklist."""
        with _tool_errors():
            return profile.prepare(engine, agent, **proposal.model_dump())

    @server.tool(annotations=PROPOSAL)
    def workbench_propose_opportunity(event: OpportunityInput) -> dict[str, Any]:
        """Create a proposal for the reference CRM opportunity; a separate reviewer decides it."""
        with _tool_errors():
            return engine.submit(event.model_dump(exclude_none=True), agent, profile=profile.snapshot())

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="SQLite file for this workspace.")
    parser.add_argument("--workspace", default="demo", help="Workspace ID permanently bound to the file.")
    profile_options = parser.add_mutually_exclusive_group()
    profile_options.add_argument("--profile", choices=[profile.id for profile in list_profiles()],
                                 help="Built-in workflow and evidence requirements (default: governance).")
    profile_options.add_argument("--profile-file", type=Path, help="Operator-controlled JSON work profile.")
    args = parser.parse_args()
    try:
        profile = get_profile(args.profile or "governance")
        if args.profile_file is not None:
            with args.profile_file.open("rb") as source:
                data = source.read(16_385)
            if len(data) > 16_384:
                parser.error("Work profile exceeds 16 KiB")
            profile = WorkProfile.from_dict(json.loads(data.decode("utf-8")))
        engine = Workbench(args.database, mode="protected_demo", workspace=args.workspace)
    except WorkbenchError as error:
        parser.error(str(error))
    except (OSError, UnicodeError, json.JSONDecodeError):
        parser.error("Unable to load the configured work profile or workspace")
    build_server(engine, profile).run(transport="stdio")


if __name__ == "__main__":
    main()
