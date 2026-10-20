#!/usr/bin/env python3
"""Check the local package without network access or changing host settings."""
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("mailbox-setup", "mailbox-lead", "mailbox-teammate")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(relative):
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{relative}: expected object")
    return value


def check():
    manifest = read_json("plugin.json")
    claude = read_json(".claude-plugin/plugin.json")
    for relative, metadata in (("plugin.json", manifest), (".claude-plugin/plugin.json", claude)):
        require(metadata.get("name") == "agent-mailbox", f"{relative}: unexpected name")
        require(re.fullmatch(r"\d+\.\d+\.\d+", metadata.get("version", "")), f"{relative}: invalid version")
        require(bool(metadata.get("description")), f"{relative}: missing description")
        require(metadata.get("license") == "MIT", f"{relative}: unexpected license")
    require(manifest["version"] == claude["version"], "Native manifests have different versions")
    require(manifest.get("$schema") == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "Portable plugin schema missing")
    require(manifest["extensions"]["com.openai"]["interface"]["displayName"] == "Agent Mailbox",
            "Unexpected plugin display name")
    module = ast.parse((ROOT / "src/agent_mailbox/__init__.py").read_text(encoding="utf-8"))
    version = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    require(version == manifest["version"], "CLI and plugin versions differ")
    require(os.access(ROOT / "bin/agent-mailbox", os.X_OK), "CLI launcher must be executable")
    require(not any((ROOT / path).exists() for path in ("mcp.json", ".mcp.json")), "Unexpected MCP configuration")

    for relative in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
        catalog = read_json(relative)
        require(catalog.get("name") == "agent-mailbox-local", f"{relative}: wrong catalog name")
        require(len(catalog.get("plugins", [])) == 1, f"{relative}: expected one plugin")
        entry = catalog["plugins"][0]
        require(entry.get("name") == "agent-mailbox", f"{relative}: wrong plugin name")
        source = entry.get("source")
        if isinstance(source, dict):
            require(source.get("source") == "local", f"{relative}: source is not local")
            source = source.get("path")
        require(source == "./" and (ROOT / source / "plugin.json").is_file(), f"{relative}: invalid package path")

    require({path.name for path in (ROOT / "skills").iterdir() if path.is_dir()} == set(SKILLS),
            "Missing or unexpected bundled skill")
    for name in SKILLS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        require(match is not None, f"{name}: frontmatter missing")
        fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
        require(fields.get("name", "").strip() == name, f"{name}: wrong skill name")
        require(fields.get("description", "").strip().startswith("Use when "), f"{name}: missing trigger description")
        require(not re.search(r"\b(?:TODO|TBD)\b", text), f"{name}: unfinished content")

    for relative in ("README.md", "LICENSE", "docs/architecture.md", ".github/workflows/ci.yml",
                     "scripts/demo.py", "docs/design.md", "docs/implementation-plan.md"):
        require((ROOT / relative).is_file(), f"Missing {relative}")
    for example in ("examples/startup-teammate.json", "examples/startup-successor.json"):
        packet = read_json(example)
        for key in ("mailbox_cli", "mailbox_db", "run_id", "agent_id", "lead_id", "task_message_id",
                    "original_task_message_id", "thread_id", "skill_path", "task_refs", "allowed_artifacts"):
            require(bool(packet.get(key)), f"{example}: missing {key}")
    for path in (ROOT / "README.md", ROOT / "docs/architecture.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if not target.startswith(("https://", "http://", "#")):
                require((path.parent / target.split("#")[0]).is_file(), f"{path.name}: broken link {target}")
    print("PASS: manifests, catalogs, launcher, skill metadata, startup examples, versions and local documentation links.")
    print("This static check does not replace native plugin validation or live-agent tests.")


if __name__ == "__main__":
    try:
        check()
    except (OSError, ValueError, KeyError, StopIteration, SyntaxError) as error:
        print(f"Package check failed: {error}", file=sys.stderr)
        sys.exit(1)
