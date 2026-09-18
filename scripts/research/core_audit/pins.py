"""Exact pins for the audit. A Core tree at another commit or with local edits fails closed."""

from __future__ import annotations

import subprocess

from . import CORE, PALLET, FailClosed

CORE_COMMIT = "63f3a49124cecf73dee2c34f2b531b1de546c836"
CORE_BRANCH = "wip/engine-port"
WOWLAB_DATA_BASE = "9448f80c6f2d87c47a93e89b1946f7aaa72e9384"
SIDECAR_COMMIT = "350de53ef783f6415f7d2e7c86959e22f6d8403a"
TRINITY_COMMIT = "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f"
SIMC_COMMIT = "b48def9c26d7532db2e612d97d433ec66bd8eede"
CLIENT_BUILD = "12.1.0.69497"
TDB = "TDB_full_world_1200.26021_2026_02_06.sql"
TDB_SHA256_PREFIX = "54ddf4c12d6034a3"
RUST_TOOLCHAIN = "1.96.1"

PINS = {
    "core": {"commit": CORE_COMMIT, "branch": CORE_BRANCH},
    "wowlab_data_base": WOWLAB_DATA_BASE,
    "core_sidecar": SIDECAR_COMMIT,
    "trinity": TRINITY_COMMIT,
    "simc": SIMC_COMMIT,
    "client_build": CLIENT_BUILD,
    "tdb": {"file": TDB, "sha256_prefix": TDB_SHA256_PREFIX, "used": False},
    "rust_toolchain": RUST_TOOLCHAIN,
}


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def verify_core() -> None:
    """Core must be exactly the pinned commit with no tracked modifications."""
    head = _git(CORE, "rev-parse", "HEAD")
    if head != CORE_COMMIT:
        raise FailClosed(f"Core HEAD {head[:12]} != pinned {CORE_COMMIT[:12]}")
    dirty = _git(CORE, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise FailClosed(f"Core tree has tracked modifications:\n{dirty}")


def observed() -> dict[str, str]:
    return {
        "core": _git(CORE, "rev-parse", "HEAD"),
        "core_sidecar": _git(PALLET / "core-sidecar", "rev-parse", "HEAD"),
        "trinity": _git(PALLET / "TrinityCore", "rev-parse", "HEAD"),
        "simc": _git(PALLET / "simc", "rev-parse", "HEAD"),
    }
