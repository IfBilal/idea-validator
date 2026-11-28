"""Prompt, rubric and config registry (docs/harness/12 section 4).

Every ``prompts/<agent>@v<N>.md`` and ``rubrics/<dimension>@v<N>.yaml`` is *immutable*: a change
means a new file with the next version, never an edit in place, and an old version is never
deleted (replay and old verdicts refer to it). Config files carry their own version keys
(``weights_version``, ``version``...): a change of content needs a bump of that key.
``registry.lock.json`` records ``key -> sha256`` and ``compare`` finds every way the working tree
breaks that promise.

Plain functions over a directory, standard library plus PyYAML: used by ``GET /registry`` (M13)
and by ``scripts/check_registry.py`` (CI), which loads this file by path, so it must not import
siblings.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LOCK_NAME = "registry.lock.json"
LOCK_SCHEMA = 1
IMMUTABLE_KINDS = frozenset({"prompt", "rubric"})
_FILE = re.compile(r"(?P<name>[a-z][a-z0-9_]*)@(?P<version>v[1-9][0-9]*)\.(?P<ext>md|yaml)")
_VERDICT_VERSION_KEYS = ("engine_version", "weights_version", "thresholds_version", "rubric_set")
MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class Entry:
    kind: str  # prompt | rubric | config
    name: str
    version: str
    sha256: str
    path: str  # relative to the registry root, for humans

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}@{self.version}"


@dataclass(frozen=True)
class ModelAlias:
    alias: str
    provider: str
    model: str
    sha256: str


@dataclass(frozen=True)
class Scan:
    entries: dict[str, Entry]
    models: list[ModelAlias]
    problems: list[str]  # the tree itself is malformed (unversioned file, dangling reference)
    active_prompts: dict[str, str]
    active_rubrics: dict[str, str]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tagged(value: Any) -> dict[str, str]:
    """YAML dates and the like: keep the type in the hash so 2026-01-01 and "2026-01-01" differ."""
    return {"__type__": type(value).__name__, "value": str(value)}


def _canonical(value: Any) -> str:
    """Config hashes cover the parsed content: comments, key order and quoting style do not
    change them, a changed value, key or type does."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_tagged)


def _hash_data(value: Any) -> str:
    return sha256_bytes(_canonical(value).encode())


def _read(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _load_yaml(path: Path, problems: list[str]) -> dict[str, Any] | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        problems.append(f"{path.name}: not valid YAML")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: must be a mapping")
        return None
    return data


def _versioned_files(
    directory: Path, kind: str, ext: str, root: Path, problems: list[str]
) -> list[Entry]:
    if not directory.is_dir():
        return []
    found: list[Entry] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_dir() or path.is_symlink():
            problems.append(
                f"{directory.name}/{path.name}: directories and symlinks are not allowed here"
            )
            continue
        match = _FILE.fullmatch(path.name)
        raw = _read(path)
        if match is None or match["ext"] != ext or raw is None:
            problems.append(
                f"{directory.name}/{path.name}: not a versioned {kind} file "
                f"(expected <name>@v<N>.{ext}, a regular file)"
            )
            continue
        found.append(
            Entry(
                kind,
                match["name"],
                match["version"],
                sha256_bytes(raw),
                f"{directory.name}/{path.name}",
            )
        )
    return found


def _config_entry(
    name: str, version: Any, section: Any, path: str, problems: list[str]
) -> Entry | None:
    if not isinstance(version, str) or not version:
        problems.append(f"{path}: {name} needs a non-empty string version key")
        return None
    return Entry("config", name, version, _hash_data(section), path)


def _scan_verdict(data: dict[str, Any], problems: list[str]) -> list[Entry]:
    path = "config/verdict.yaml"
    rest = {
        k: v
        for k, v in data.items()
        if k not in {"weights", "thresholds", "rubric_versions", *_VERDICT_VERSION_KEYS}
    }
    parts = [
        ("verdict.engine", data.get("engine_version"), rest),
        ("verdict.weights", data.get("weights_version"), data.get("weights")),
        ("verdict.thresholds", data.get("thresholds_version"), data.get("thresholds")),
        (
            "verdict.rubric_set",
            data.get("rubric_set"),
            {"rubric_set": data.get("rubric_set"), "rubric_versions": data.get("rubric_versions")},
        ),
    ]
    found = [_config_entry(n, v, s, path, problems) for n, v, s in parts]
    return [e for e in found if e is not None]


def scan(root: Path) -> Scan:
    """Read every versioned file and config under ``root`` (a repo checkout or the mounted dir)."""
    problems: list[str] = []
    entries: list[Entry] = []
    entries += _versioned_files(root / "prompts", "prompt", "md", root, problems)
    entries += _versioned_files(root / "rubrics", "rubric", "yaml", root, problems)
    config = root / "config"

    verdict = _load_yaml(config / "verdict.yaml", problems)
    active_rubrics: dict[str, str] = {}
    if verdict is not None:
        entries += _scan_verdict(verdict, problems)
        versions = verdict.get("rubric_versions")
        if isinstance(versions, dict):
            active_rubrics = {str(k): str(v) for k, v in versions.items()}

    for file, name, key in (
        ("budgets.yaml", "budgets", "version"),
        ("prompts.yaml", "prompts", "version"),
        ("llm.yaml", "llm", "version"),
    ):
        data = _load_yaml(config / file, problems)
        if data is not None and (
            e := _config_entry(name, data.get(key), data, f"config/{file}", problems)
        ):
            entries.append(e)

    prompts_cfg = _load_yaml(config / "prompts.yaml", [])
    active_prompts: dict[str, str] = {}
    if prompts_cfg is not None and isinstance(prompts_cfg.get("active"), dict):
        active_prompts = {str(k): str(v) for k, v in prompts_cfg["active"].items()}

    by_key = {e.key: e for e in entries}
    have = {f"{e.name}@{e.version}" for e in entries if e.kind in IMMUTABLE_KINDS}
    for agent, ref in sorted({**active_prompts, **active_rubrics}.items()):
        if ref not in have:
            problems.append(f"config references {ref} ({agent}) but no such file exists")

    models: list[ModelAlias] = []
    llm = _load_yaml(config / "llm.yaml", [])
    if llm is not None and isinstance(llm.get("aliases"), dict):
        for alias, spec in sorted(llm["aliases"].items()):
            if isinstance(spec, dict):
                models.append(
                    ModelAlias(
                        str(alias),
                        str(spec.get("provider", "")),
                        str(spec.get("model", "")),
                        _hash_data(spec),
                    )
                )
    return Scan(by_key, models, problems, active_prompts, active_rubrics)


def compare(lock: dict[str, str], current: dict[str, Entry]) -> list[str]:
    """Every way the tree breaks the lock; an empty list means the registry is consistent."""
    problems: list[str] = []
    for key, locked in sorted(lock.items()):
        kind = key.split(":", 1)[0]
        now = current.get(key)
        if now is None:
            if kind in IMMUTABLE_KINDS:
                problems.append(
                    f"deleted: {key} is registered; versions are never deleted or renamed "
                    "(old verdicts and replays refer to them)"
                )
            elif not _superseded(key, current):
                problems.append(f"deleted: {key} is registered but no longer in the tree")
        elif now.sha256 != locked:
            if kind in IMMUTABLE_KINDS:
                problems.append(
                    f"edited in place: {key} ({now.path}) changed; a version is immutable, "
                    "copy it to the next version number and edit the copy"
                )
            else:
                problems.append(
                    f"changed without a version bump: {key} ({now.path}); bump its version key"
                )
    problems += [
        f"not registered: {key} ({entry.path}); run `python scripts/check_registry.py --update`"
        for key, entry in sorted(current.items())
        if key not in lock
    ]
    return problems


def compare_to_base(base: dict[str, str], pr_lock: dict[str, str]) -> list[str]:
    """What the PR's lock changed about entries the base branch already registered.

    The PR's own lock proves nothing (a PR can edit a file and its hash together), so every base
    key must survive with the same hash. Only a config key may go, and only for a newer version of
    the same name.
    """
    problems: list[str] = []
    for key, locked in sorted(base.items()):
        mine = pr_lock.get(key)
        if mine is None:
            kind, _, rest = key.partition(":")
            name = rest.partition("@")[0]
            bumped = kind not in IMMUTABLE_KINDS and any(
                k != key and k.startswith(f"{kind}:{name}@") for k in pr_lock
            )
            if not bumped:
                problems.append(f"removed from the lock: {key} is registered on the base branch")
        elif mine != locked:
            problems.append(
                f"{key} differs from the base branch's lock; registered hashes are never rewritten"
            )
    return problems


def _superseded(key: str, current: dict[str, Entry]) -> bool:
    """A config entry whose name now has another version was bumped, not deleted."""
    kind, _, rest = key.partition(":")
    name = rest.partition("@")[0]
    return any(e.kind == kind and e.name == name for e in current.values())


def load_lock(path: Path) -> dict[str, str]:
    """The lock as ``key -> sha256``. Raises ``ValueError`` when the file is unusable."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is missing or not valid JSON") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if (
        not isinstance(entries, dict)
        or data.get("schema") != LOCK_SCHEMA
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in entries.items())
    ):
        raise ValueError(f"{path.name} does not match schema {LOCK_SCHEMA}")
    return dict(entries)


def render_lock(entries: dict[str, str]) -> str:
    body = {"schema": LOCK_SCHEMA, "entries": dict(sorted(entries.items()))}
    return json.dumps(body, indent=2) + "\n"
