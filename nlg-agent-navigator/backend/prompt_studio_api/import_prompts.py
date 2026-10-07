"""One-time prompt import utility; imported source files are not runtime dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from prompt_library import (
    create_prompt_definition,
    create_prompt_version,
    get_prompt_definition,
    select_prompt_version,
)
from prompt_studio_api.database import connect, initialize_database
from prompt_studio_api.settings import PromptStudioSettings


def _instructions(item: dict[str, Any], manifest_root: Path) -> str:
    if "instructions" in item:
        content = str(item["instructions"])
    else:
        source = Path(str(item["source_path"]))
        if not source.is_absolute():
            source = manifest_root / source
        content = source.read_text(encoding="utf-8")
    prefix = str(item.get("prefix", ""))
    suffix = str(item.get("suffix", ""))
    rendered = "\n\n".join(part.strip() for part in (prefix, content, suffix) if part.strip())
    if not rendered:
        raise ValueError(f"Prompt {item.get('key')!r} has no instructions")
    return rendered


def import_manifest(path: Path) -> None:
    settings = PromptStudioSettings()
    initialize_database(settings)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    with connect(settings) as conn:
        for item in manifest["prompts"]:
            key = str(item["key"])
            instructions = _instructions(item, path.parent)
            existing = get_prompt_definition(conn, key)
            if existing is None:
                created = create_prompt_definition(
                    conn,
                    key=key,
                    purpose=str(item["purpose"]),
                    instructions=instructions,
                    created_by="prompt-import",
                )
                version = int(created["selected_version"])
            else:
                created = create_prompt_version(
                    conn,
                    key=key,
                    instructions=instructions,
                    change_notes=str(item.get("change_notes", "Imported prompt update")),
                    created_by="prompt-import",
                )
                assert created is not None
                version = int(created["version"])
                select_prompt_version(conn, key=key, version=version)
            print(f"Imported and selected {key} v{version}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and select prompts from a JSON manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    import_manifest(args.manifest.resolve())


if __name__ == "__main__":
    main()
