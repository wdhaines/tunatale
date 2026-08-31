"""TEMPORARY diagnostic for tunatale-ov0m. Delete once the runner is understood.

Self-diagnosing on purpose: it asserts the thing that HOLDS here and is believed
to fail on the runner (that `git add -- .beads-tasks` moves the index entry for
a drifted submodule). So it passes locally — the commit gate stays honestly
green — and on CI it fails with the whole dump attached, which is the only way
to see runner state without a deliberately-red commit.
"""

from __future__ import annotations

import os
import subprocess

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.t",
}


def _g(args, cwd):
    p = subprocess.run(["git", *args], capture_output=True, cwd=cwd, timeout=60, text=True, env=_GIT_ENV)
    return f"rc={p.returncode} out={p.stdout.strip()!r} err={p.stderr.strip()!r}"


def test_dump_runner_gitlink_behaviour(tmp_path):
    log: list[str] = []

    def rec(label, args, cwd):
        log.append(f"{label}: git {' '.join(args)} -> {_g(args, cwd)}")

    sub_origin = tmp_path / "sub-origin.git"
    _g(["init", "-q", "--bare", "-b", "main", str(sub_origin)], tmp_path)
    sub = tmp_path / "sub"
    _g(["init", "-q", "-b", "main", str(sub)], tmp_path)
    (sub / "f").write_text("one")
    _g(["add", "f"], sub)
    _g(["commit", "-qm", "one"], sub)
    _g(["remote", "add", "origin", str(sub_origin)], sub)
    _g(["push", "-q", "origin", "main"], sub)

    parent = tmp_path / "parent"
    _g(["init", "-q", "-b", "main", str(parent)], tmp_path)
    (parent / "README").write_text("base")
    _g(["add", "README"], parent)
    _g(["commit", "-qm", "base"], parent)
    rec(
        "SUBMODULE_ADD",
        ["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_origin), ".beads-tasks"],
        parent,
    )
    _g(["config", "-f", ".gitmodules", "submodule..beads-tasks.ignore", "all"], parent)
    _g(["add", ".gitmodules"], parent)
    _g(["commit", "-qm", "add submodule"], parent)

    subdir = parent / ".beads-tasks"
    (subdir / "g").write_text("two")
    _g(["add", "g"], subdir)
    _g(["commit", "-qm", "two"], subdir)
    _g(["push", "-q", "origin", "HEAD:main"], subdir)
    _g(["fetch", "-q", "origin"], subdir)

    log.append("=== VERSION ===")
    log.append(_g(["--version"], parent))
    log.append("=== .gitmodules (verbatim) ===")
    log.append(repr((parent / ".gitmodules").read_text()))
    log.append("=== parent local config, submodule.* ===")
    log.append(_g(["config", "--local", "-l"], parent))
    log.append("=== .git/modules listing ===")
    log.append(
        repr(sorted(p.name for p in (parent / ".git" / "modules").iterdir()))
        if (parent / ".git" / "modules").exists()
        else "NO .git/modules"
    )
    log.append(f"=== .beads-tasks/.git is dir? {(subdir / '.git').is_dir()} is file? {(subdir / '.git').is_file()} ===")

    log.append("=== BEFORE ADD ===")
    rec("status", ["submodule", "status"], parent)
    rec("ls-files", ["ls-files", "-s", ".beads-tasks"], parent)
    rec("porcelain", ["status", "--porcelain", "--ignore-submodules=none"], parent)
    rec("recorded", ["rev-parse", "HEAD:.beads-tasks"], parent)
    rec("subhead", ["rev-parse", "HEAD"], subdir)

    log.append("=== THE ADD ===")
    before_idx = _g(["ls-files", "-s", ".beads-tasks"], parent)
    rec("plain_add", ["add", "--", ".beads-tasks"], parent)
    after_idx = _g(["ls-files", "-s", ".beads-tasks"], parent)

    log.append("=== AFTER ADD ===")
    rec("ls-files", ["ls-files", "-s", ".beads-tasks"], parent)
    rec("cached_diff_none", ["diff", "--cached", "--name-only", "--ignore-submodules=none"], parent)
    rec("cached_diff_bare", ["diff", "--cached", "--name-only"], parent)
    rec("porcelain", ["status", "--porcelain", "--ignore-submodules=none"], parent)
    rec("commit_dryrun", ["commit", "--dry-run", "--porcelain"], parent)

    log.append("=== ALTERNATIVES ===")
    _g(["reset", "-q"], parent)
    rec("add_ignore_none", ["-c", "submodule..beads-tasks.ignore=none", "add", "--", ".beads-tasks"], parent)
    rec("ls-files_after_override", ["ls-files", "-s", ".beads-tasks"], parent)
    _g(["reset", "-q"], parent)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=subdir, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()
    rec("update_index", ["update-index", "--add", "--cacheinfo", f"160000,{head},.beads-tasks"], parent)
    rec("ls-files_after_update_index", ["ls-files", "-s", ".beads-tasks"], parent)
    rec("commit_dryrun_after_update_index", ["commit", "--dry-run", "--porcelain"], parent)

    # THE claim under test. Holds on git 2.50.1 here; believed to fail on the
    # runner, where the following commit reports "nothing to commit".
    assert before_idx != after_idx, "RUNNER_DIAG add-did-not-move-the-index\n" + "\n".join(log)
