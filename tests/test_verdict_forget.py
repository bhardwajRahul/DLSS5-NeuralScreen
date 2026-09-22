"""The feature-18 verdict dies with the worker that gave it.

st.gpu_ok is latched: refresh_gpu_ok() decides once and returns immediately
ever after, which is what keeps it off the per-frame path. The latch belongs
to ONE worker process though - a replacement calls CreateFeature again and
can answer differently.

Only rebuild_pipeline used to clear it, so the monitor and window switches
were right and the four restart paths were not: a card that started working
kept the red dot, and a pipeline that came back BROKEN kept the green one -
while TECHNICAL.md tells the user that dot is the thing to trust.

A live RNSZ resize is the exception that has to stay an exception:
resize_window_live keeps the same process, so the verdict it gave still
stands and must NOT be forgotten (that would put the dot back to "unknown"
for no reason, every time a captured window is resized).

Checked:
* forget_verdict clears both the verdict and the alert latch;
* every restart_worker() call site forgets the verdict right after;
* resize_window_live does not;
* the guides' bypass contract: clearing previous_gray makes the next real
  frame a scene cut rather than a correlation against a stale screen.
"""
import os
import re
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))  # the modules live in app/

import numpy as np  # noqa: E402
import channels  # noqa: E402
from guides import TemporalGuideGenerator  # noqa: E402


def source(name: str) -> str:
    """A module's source: app/ for the modules, the root for main.py."""
    path = os.path.join(BASE, "app", name)
    if not os.path.exists(path):
        path = os.path.join(BASE, name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def scan_targets() -> list:
    """Every tracked .py file that could call restart_worker().

    This used to be a hand-written pair (main.py, pipeline.py), and a fifth
    call site in commands.py - the manual revive - went unnoticed because it
    was in neither. A list of files to check is itself a thing to maintain, so
    the check derives it from git: any tracked source can hold a call, and a
    new one is covered the moment it is committed.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=BASE, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout
    names = []
    for line in out.splitlines():
        name = line.strip()
        if not name or "/" in name or name.startswith("test"):
            continue
        if name in ("build_release_zip.py", "verify_github.py"):
            continue  # build tooling, never starts a worker
        names.append(name)
    return sorted(names)


def restart_sites_forget(src: str) -> list:
    """Line numbers of restart_worker() calls whose block never forgets.

    Parsed, not pattern-matched. Three text-based shapes failed on real code:
    a fixed 14-line window let a comment inside the block push the call out of
    range, a naive indent walk stopped on the call's own continuation lines,
    and a whole-file search would pass on an unrelated call far below.

    The rule, in AST terms: find every call to restart_worker, take the
    innermost block that contains it (a `try:` body, a branch, a function
    body), and require a forget_verdict call in that same block at or after
    the restart call. That is exactly the contract channels.py states, and it
    survives reindentation, comments and multi-line calls.
    """
    import ast

    tree = ast.parse(src)
    missing = []

    def block_of(node, parents):
        """The statement list directly containing the statement that holds
        `node`, plus that statement."""
        statement = node
        for parent in reversed(parents):
            if isinstance(parent, ast.stmt):
                statement = parent
                break
        for parent in reversed(parents):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if isinstance(block, list) and statement in block:
                    return block, statement
        return None, None

    def has_forget_after(block, statement):
        seen = False
        for stmt in block:
            if stmt is statement:
                seen = True
                continue
            if not seen:
                continue
            for inner in ast.walk(stmt):
                if isinstance(inner, ast.Call):
                    name = ast.unparse(inner.func)
                    if name.endswith("forget_verdict"):
                        return True
        return False

    def visit(node, parents):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name.endswith("restart_worker"):
                block, statement = block_of(node, parents)
                if block is None or not has_forget_after(block, statement):
                    missing.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, parents + [node])

    visit(tree, [])
    return sorted(missing)


def main() -> int:
    failures = []

    # 1. It clears both fields.
    st = SimpleNamespace(gpu_ok=True, gpu_alerted=True)
    channels.forget_verdict(st)
    if st.gpu_ok is not None:
        failures.append(f"gpu_ok is {st.gpu_ok!r}, want None")
    if st.gpu_alerted is not False:
        failures.append(f"gpu_alerted is {st.gpu_alerted!r}, want False")

    # 2. Every restart path forgets the verdict.
    for name in scan_targets():
        missing = restart_sites_forget(source(name))
        if missing:
            failures.append(f"{name}: restart_worker at line(s) {missing} "
                            f"does not forget the verdict")

    # 3. A live resize keeps it: the worker survives an RNSZ.
    pipeline_src = source("pipeline.py")
    match = re.search(r"def resize_window_live\(.*?(?=\ndef )", pipeline_src,
                      re.S)
    if match is None:
        failures.append("resize_window_live not found in pipeline.py")
    elif "forget_verdict" in match.group(0):
        failures.append("resize_window_live forgets the verdict, but RNSZ "
                        "keeps the same worker process")

    # 4. The bypass contract in guides: no history -> a scene cut, no motion.
    g = TemporalGuideGenerator(640, 360, flow_width=320, emit_small=True)
    frame = np.zeros((360, 640, 4), dtype=np.uint8)
    frame[..., 3] = 255
    frame[80:280, 100:500, :3] = 210
    g.process(frame)                      # history exists now
    g.previous_gray = None                # what the bypass branch does
    moved = frame.copy()
    moved[80:280, 100:500, :3] = 0
    moved[80:280, 180:580, :3] = 210      # a large, unmistakable move
    out = g.process(moved)
    if not out.reset:
        failures.append("a cleared history must report a scene cut")
    if np.abs(out.motion).max() != 0:
        failures.append("a cleared history must produce no motion, got "
                        f"{np.abs(out.motion).max():.3f}")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} - {failures}")
        return 1
    print("OK: the verdict dies with its worker, a live resize keeps it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
