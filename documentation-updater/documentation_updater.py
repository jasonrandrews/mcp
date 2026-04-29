#!/usr/bin/env python3
"""Generate a documentation update plan from the local Arm MCP README."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPORTS_DIR = SCRIPT_DIR / "reports"

SOURCE_README = REPO_ROOT / "README.md"
AGENT_INSTALL_GUIDE = REPO_ROOT / "agent-integrations" / "agent-install-instructions.md"
MCP_SERVER_JSON = REPO_ROOT / "mcp-local" / "server.json"
OPENAI_MODEL = "gpt-5.5"


@dataclass(frozen=True)
class SourceConfig:
    source_path: Path
    docker_image: str
    generic_json: str
    generic_toml: str
    performix_note: str


@dataclass(frozen=True)
class RepoPlanTarget:
    alias: str
    repo_url: str
    clone_url: str
    urls: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class ManualTarget:
    name: str
    url: str
    notes: str


REPO_TARGETS: tuple[RepoPlanTarget, ...] = (
    RepoPlanTarget(
        alias="arm-learning-paths",
        repo_url="https://github.com/ArmDeveloperEcosystem/arm-learning-paths",
        clone_url="https://github.com/ArmDeveloperEcosystem/arm-learning-paths.git",
        urls=(
            "https://learn.arm.com/learning-paths/servers-and-cloud-computing/arm-mcp-server/1-overview/",
            "https://learn.arm.com/learning-paths/servers-and-cloud-computing/docker-mcp-toolkit/2-setup/",
            "https://learn.arm.com/install-guides/github-copilot/",
            "https://learn.arm.com/install-guides/codex-cli/",
            "https://learn.arm.com/install-guides/claude-code/",
            "https://learn.arm.com/install-guides/gemini/",
        ),
        notes="This repo backs the Learn site pages and install guides.",
    ),
    RepoPlanTarget(
        alias="awesome-copilot",
        repo_url="https://github.com/github/awesome-copilot",
        clone_url="https://github.com/github/awesome-copilot.git",
        urls=(
            "https://github.com/github/awesome-copilot/blob/main/agents/arm-migration.agent.md",
        ),
        notes="Update the Arm migration agent doc if its Docker MCP config is stale.",
    ),
    RepoPlanTarget(
        alias="arm-mcp-gemini",
        repo_url="https://github.com/arm/arm-mcp-gemini",
        clone_url="https://github.com/arm/arm-mcp-gemini.git",
        urls=(
            "https://github.com/arm/arm-mcp-gemini",
        ),
        notes="Search the repo for Arm MCP setup/config references and align them with the local README.",
    ),
)


MANUAL_TARGETS: tuple[ManualTarget, ...] = (
    ManualTarget(
        name="Developer Arm landing page",
        url="https://developer.arm.com/servers-and-cloud-computing/arm-mcp-server",
        notes="Manual publishing surface outside this repo.",
    ),
    ManualTarget(
        name="Docker Hub / Docker MCP catalog",
        url="https://hub.docker.com/mcp/server/arm-mcp/config",
        notes="Manual Docker publishing/catalog surface.",
    ),
    ManualTarget(
        name="MCP Registry",
        url="https://github.com/modelcontextprotocol/registry",
        notes="Review ../mcp-local/server.json and publish through the separate registry workflow.",
    ),
)


def log(message: str) -> None:
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[documentation-updater {timestamp}] {message}", flush=True)


def format_path(path: Path) -> str:
    try:
        relative = path.relative_to(SCRIPT_DIR)
        return f"./{relative}" if str(relative) != "." else "."
    except ValueError:
        try:
            relative = path.relative_to(REPO_ROOT)
            return f"../{relative}"
        except ValueError:
            return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a documentation update plan and Codex prompt snippets."
    )
    parser.add_argument(
        "-m",
        "--message",
        required=True,
        help="Required explanation of the MCP/documentation change that needs to be propagated",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional explicit report path",
    )
    args = parser.parse_args()
    if not args.message.strip():
        parser.error("A non-empty -m/--message explaining the change is required.")
    return args


def detect_base_url() -> Optional[str]:
    for env_name in (
        "OPENAI_API_PROXY_URL",
        "OPENAI_API_PROXY",
        "DOC_UPDATER_OPENAI_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        value = __import__("os").getenv(env_name)
        if value:
            value = value.rstrip("/")
            if value.endswith("/models"):
                value = value[: -len("/models")]
            return value
    return None


def validate_local_source_readme() -> Path:
    if not SOURCE_README.exists():
        raise FileNotFoundError(
            f"Local canonical source README not found: {format_path(SOURCE_README)}"
        )
    return SOURCE_README


def extract_source_config(readme_path: Path) -> SourceConfig:
    import re

    log(f"Extracting canonical MCP configuration from {format_path(readme_path)}")
    content = readme_path.read_text(encoding="utf-8")

    section_match = re.search(
        r"### 2\. Configure Your MCP Client\s*(.*?)\n### 3\. Restart Your MCP Client",
        content,
        re.DOTALL,
    )
    if not section_match:
        raise ValueError(f"Unable to find MCP client config section in {readme_path}")
    section_text = section_match.group(1)

    json_match = re.search(r"#### Claude Code.*?```json\n(.*?)\n```", section_text, re.DOTALL)
    toml_match = re.search(
        r"#### MCP Clients using TOML format \(e\.g\. Codex CLI\).*?```toml\n(.*?)\n```",
        section_text,
        re.DOTALL,
    )
    if not json_match or not toml_match:
        raise ValueError("Unable to extract canonical JSON/TOML config from local README")

    json_block = json_match.group(1)
    json_config = json.loads(json_block)
    args_list = json_config["mcpServers"]["arm-mcp"]["args"]
    generic_json = json.dumps(
        {
            "command": json_config["mcpServers"]["arm-mcp"]["command"],
            "args": args_list,
        },
        indent=2,
    )

    toml_lines = ["[mcp_servers.arm-mcp]", 'command = "docker"', "args = ["]
    for item in args_list:
        toml_lines.append(f'  "{item}",')
    toml_lines.append("]")

    docker_image = next((item for item in reversed(args_list) if "arm-mcp" in item), "armlimited/arm-mcp")
    return SourceConfig(
        source_path=readme_path,
        docker_image=docker_image,
        generic_json=generic_json,
        generic_toml="\n".join(toml_lines),
        performix_note=(
            "The SSH-related volume mounts are optional and are only needed when enabling Arm Performix."
        ),
    )


def build_report_path(explicit_path: Optional[Path]) -> Path:
    if explicit_path:
        explicit_path.parent.mkdir(parents=True, exist_ok=True)
        return explicit_path
    DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_REPORTS_DIR / f"documentation-update-plan-{stamp}.md"


def generate_codex_prompts(
    *,
    source: SourceConfig,
    change_message: str,
    targets: tuple[RepoPlanTarget, ...],
) -> dict[str, str]:
    import os

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = detect_base_url()
    if not api_key or not base_url:
        raise RuntimeError(
            "OPENAI_API_KEY and an OpenAI base URL env var are required to generate Codex prompt snippets."
        )

    log(f"Generating Codex prompt snippets with {OPENAI_MODEL}")
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You generate short, actionable prompts for Codex. "
                            "The prompts are for a user to paste into Codex after cloning a repo. "
                            "Do not mention opening PRs, titles, branches, or commit messages. "
                            "Focus only on the doc changes to make so the repo matches the canonical Arm MCP configuration source. "
                            "Return strict JSON with a top-level key prompts containing an array of objects with alias and prompt."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "change_message": change_message,
                                "canonical_source_path": format_path(source.source_path),
                                "canonical_json_config": source.generic_json,
                                "canonical_toml_config": source.generic_toml,
                                "performix_note": source.performix_note,
                                "repo_targets": [
                                    {
                                        "alias": target.alias,
                                        "repo_url": target.repo_url,
                                        "clone_url": target.clone_url,
                                        "urls": list(target.urls),
                                        "notes": target.notes,
                                    }
                                    for target in targets
                                ],
                            },
                            indent=2,
                        ),
                    }
                ],
            },
        ],
        "reasoning": {"effort": "medium"},
        "text": {"format": {"type": "json_object"}},
    }

    request = Request(
        f"{base_url}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc

    output_text = data.get("output_text")
    if not output_text:
        output = data.get("output", [])
        for item in output:
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    output_text = content.get("text", "")
                    break
            if output_text:
                break
    if not output_text:
        raise RuntimeError("OpenAI response did not include output_text")

    parsed = json.loads(output_text)
    prompts: dict[str, str] = {}
    for item in parsed.get("prompts", []):
        alias = item.get("alias", "").strip()
        prompt = item.get("prompt", "").strip()
        if alias and prompt:
            prompts[alias] = prompt
    return prompts


def write_report(
    report_path: Path,
    *,
    source: SourceConfig,
    change_message: str,
    prompts: dict[str, str],
) -> None:
    log(f"Writing report to {report_path}")

    lines: list[str] = []
    lines.append("# Arm MCP Documentation Update Plan")
    lines.append("")
    lines.append(f"Generated: {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Canonical Source")
    lines.append("")
    lines.append(f"- Source file: `{format_path(source.source_path)}`")
    lines.append(f"- Change message: {change_message}")
    lines.append(f"- Docker image: `{source.docker_image}`")
    lines.append(f"- Note: {source.performix_note}")
    lines.append("")
    lines.append("### Generic JSON Config")
    lines.append("")
    lines.append("```json")
    lines.append(source.generic_json)
    lines.append("```")
    lines.append("")
    lines.append("### Generic TOML Config")
    lines.append("")
    lines.append("```toml")
    lines.append(source.generic_toml)
    lines.append("```")
    lines.append("")
    lines.append("## Local Files To Update")
    lines.append("")
    lines.append(f"- `{format_path(AGENT_INSTALL_GUIDE)}`")
    lines.append("")
    lines.append("## Repos To Clone")
    lines.append("")
    for target in REPO_TARGETS:
        lines.append(f"### {target.alias}")
        lines.append("")
        lines.append(f"- Repo URL: {target.repo_url}")
        lines.append(f"- Clone command: `git clone {target.clone_url}`")
        if target.notes:
            lines.append(f"- Notes: {target.notes}")
        lines.append("- Relevant URLs:")
        for url in target.urls:
            lines.append(f"  - {url}")
        prompt = prompts.get(target.alias)
        if prompt:
            lines.append("- Optional Codex prompt:")
            lines.append("")
            lines.append("```text")
            lines.append(prompt)
            lines.append("```")
        lines.append("")
    lines.append("## Manual Follow-Up")
    lines.append("")
    for target in MANUAL_TARGETS:
        lines.append(f"- `{target.name}`: {target.url}")
        lines.append(f"  - {target.notes}")
    lines.append("")
    lines.append("## Registry Check")
    lines.append("")
    lines.append(f"- Local metadata file: `{format_path(MCP_SERVER_JSON)}`")
    lines.append("- Verification command:")
    lines.append("")
    lines.append("```bash")
    lines.append('curl -s "https://registry.modelcontextprotocol.io/v0.1/servers?search=arm/arm-mcp" | jq \'.\'')
    lines.append("```")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log("Report write complete")


def main() -> int:
    log("Starting documentation updater")
    args = parse_args()
    log(f"Change message: {args.message}")

    source = extract_source_config(validate_local_source_readme())
    report_path = build_report_path(args.report)
    prompts = generate_codex_prompts(
        source=source,
        change_message=args.message,
        targets=REPO_TARGETS,
    )
    write_report(
        report_path,
        source=source,
        change_message=args.message,
        prompts=prompts,
    )
    log("Documentation updater finished successfully")
    print(f"Report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
