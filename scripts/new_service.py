"""Render templates/service/ into services/<name>/.

Usage: python scripts/new_service.py <name> <module-id> "<title>"
Example: python scripts/new_service.py verdict M11 "Verdict Engine"
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "service"


def render(name: str, module: str, title: str, services_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9]*", name):
        raise SystemExit("name must be lowercase letters/digits, e.g. 'verdict'")
    target = (services_dir or ROOT / "services") / name
    if target.exists():
        raise SystemExit(f"{target} already exists")
    values = {"{{name}}": name, "{{module}}": module, "{{title}}": title}
    for source in sorted(TEMPLATE.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(TEMPLATE).as_posix()
        relative = relative.replace("__pkg__", f"svc_{name}").replace("__name__", name)
        destination = target / relative.removesuffix(".tmpl")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".tmpl":
            text = source.read_text()
            for key, value in values.items():
                text = text.replace(key, value)
            destination.write_text(text)
        else:
            destination.write_bytes(source.read_bytes())
    return target


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    created = render(*sys.argv[1:])
    print(f"created {created.relative_to(ROOT)}")
    print(
        "next: uv sync --all-packages; add it to mypy `files`, Makefile SERVICES, the CI docker matrix,"
    )
    print(
        "      a Kustomize Deployment/Service and a NetworkPolicy rule (docs/harness/16_KUBERNETES.md)."
    )
