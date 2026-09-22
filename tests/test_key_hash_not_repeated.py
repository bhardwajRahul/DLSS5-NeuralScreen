"""The compatibility guard must not re-hash the runtime on every call (audit H2).

`pipeline.require_compatibility` is a fail-closed guard called before every
production worker start, and it delegates to `compatibility_runtime.require_pass`,
which builds a fresh key every time:

    current_key = build_key(st)
    if current_key.digest != passed_key.digest: raise ...

build_key hashes the 165,840,496-byte `native/nvngx_dlssnr.dll` twice on the
same call - once as `runtime_sha256` (CompatibilityKey.from_files) and once
through `_runtime_route` -> `_fingerprint`, which resolves to the same file.

Measured on this machine: sha256 of that file 93 ms, build_key 355 ms cold /
184 ms warm. Those call sites run on a user action, not once at startup:
rebuild_pipeline (every monitor switch, window switch, Spout toggle, HDR
toggle, motion-backend change, GPU switch), the do_restart fallback, the
auto-revive, the worker-lost paths and the manual NR revive. The main loop is
single-threaded, so each one blocks it for ~0.2-0.4 s of pure file hashing on
top of the rebuild the user is already waiting for - the overlay does not pump
and the hotkeys do not drain.

The guard itself must stay: it is what refuses a worker whose runtime changed
underneath a PASS. What it must not do is repeat work whose answer cannot have
changed. Checked here:

* an unchanged file is hashed once, however many times the key is built;
* the cached digest equals a fresh hash of the same bytes (never a stale or
  wrong value);
* a file that CHANGED is hashed again and the new digest differs - the whole
  point of the guard;
* a fake filesystem (the test seam) is never cached: every caller that passes
  one keeps its exact behaviour.

Run:  runtime\\python.exe tests\\test_key_hash_not_repeated.py
"""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import compatibility  # noqa: E402
import compatibility_runtime as cr  # noqa: E402


def _clear_cache() -> None:
    """Empty the digest cache if the guard has one (it must, per this test)."""
    cache = getattr(compatibility, "_HASH_CACHE", None)
    if cache is None:
        return
    cache.clear()


class _CountingFS:
    """Wraps the real filesystem and counts the reads that hash a file."""

    def __init__(self):
        self.reads = []

    def read_bytes(self, path):
        self.reads.append(str(path))
        return Path(path).read_bytes()

    def read_text(self, path):
        return Path(path).read_text(encoding="utf-8")

    def atomic_write_text(self, path, text):
        Path(path).write_text(text, encoding="utf-8")


def _make_file(directory: Path, name: str, size: int) -> Path:
    path = directory / name
    # Deterministic but not compressible-by-accident content.
    path.write_bytes(bytes((i * 7 + 13) % 251 for i in range(size)))
    return path


def main() -> int:
    failures = []
    root = Path(tempfile.mkdtemp(prefix="ns-key-hash-"))
    runtime = _make_file(root, "nvngx_dlssnr.dll", 4 * 1024 * 1024)
    worker = _make_file(root, "nvngx.dll", 64 * 1024)

    # --- 1. sha256_file itself: one read for repeated calls ---------------
    fs = _CountingFS()
    first = compatibility.sha256_file(runtime, filesystem=fs)
    second = compatibility.sha256_file(runtime, filesystem=fs)
    if first != second:
        failures.append("sha256_file returned two different digests for the "
                        "same unchanged file")
    if first != hashlib.sha256(runtime.read_bytes()).hexdigest():
        failures.append("sha256_file returned a wrong digest")
    # A filesystem that is passed in is a test seam: it must NOT be cached,
    # or a test could never simulate a changed file.
    if len(fs.reads) != 2:
        failures.append(f"a caller-supplied filesystem was cached: the file "
                        f"was read {len(fs.reads)} times, expected 2 - the "
                        f"test seam must stay uncached")

    # --- 2. The production path (no filesystem): reads once ---------------
    fs = _CountingFS()
    original_local = compatibility.LocalFileSystem
    compatibility.LocalFileSystem = lambda: fs
    try:
        a = compatibility.sha256_file(runtime)
        b = compatibility.sha256_file(runtime)
        c = compatibility.sha256_file(runtime)
    finally:
        compatibility.LocalFileSystem = original_local
    if a != b or b != c:
        failures.append("the cached digest changed between calls")
    if len(fs.reads) != 1:
        failures.append(
            f"the runtime was read {len(fs.reads)} times across three "
            f"sha256_file calls on the production path - the guard re-hashes "
            f"the same unchanged 166 MB file every time it runs")

    # --- 3. A changed file is noticed -------------------------------------
    # The cache is keyed on (path, size, mtime_ns), so a change is caught by
    # the stat: the next call reads the file again and returns the new digest.
    _clear_cache()
    before = compatibility.sha256_file(runtime)
    original = runtime.read_bytes()
    runtime.write_bytes(original + b"\x00")          # new content AND size
    after = compatibility.sha256_file(runtime)
    if before == after:
        failures.append("a CHANGED file kept its old digest - the guard would "
                        "pass a worker running a different runtime")
    if after != hashlib.sha256(runtime.read_bytes()).hexdigest():
        failures.append("the rehash after a change returned a wrong digest")

    # Same size, new content: the mtime is what has to catch this one.
    _clear_cache()
    size_before = runtime.stat().st_size
    time.sleep(0.02)                                  # a visible mtime step
    flipped = bytearray(runtime.read_bytes())
    flipped[0] ^= 0xFF
    runtime.write_bytes(bytes(flipped))
    if runtime.stat().st_size != size_before:
        failures.append("the same-size probe changed the file size")
    same_size = compatibility.sha256_file(runtime)
    if same_size == after:
        failures.append("a same-size edit kept its old digest - the cache is "
                        "not watching the mtime")

    # --- 4. The production key build must not re-read the runtime ---------
    _clear_cache()
    saved_runtime, saved_worker = cr.runtime_path, cr.WORKER_EXE
    saved_native = cr.NATIVE_DIR
    cr.runtime_path = lambda: runtime
    cr.WORKER_EXE = worker
    cr.NATIVE_DIR = root          # so the BYO candidate resolves under root
    try:
        class _St:
            cfg = {"gpu": 0}
            environment = {"driver": "test"}

        fs = _CountingFS()
        compatibility.LocalFileSystem = lambda: fs
        try:
            first_key = cr.build_key(_St())
            cold_reads = len(fs.reads)
            fs.reads.clear()
            second_key = cr.build_key(_St())
            warm_reads = len(fs.reads)
        finally:
            compatibility.LocalFileSystem = original_local
    finally:
        cr.runtime_path, cr.WORKER_EXE = saved_runtime, saved_worker
        cr.NATIVE_DIR = saved_native

    if first_key.digest != second_key.digest:
        failures.append("build_key returned two different digests for an "
                        "unchanged runtime")
    # The cold build reads the runtime once (the route fingerprint and the key
    # itself are the same file, and one of them warms the cache for the other).
    if cold_reads > 2:
        failures.append(
            f"the first build_key read {cold_reads} files - the runtime is "
            f"hashed more than once per call")
    if warm_reads != 0:
        failures.append(
            f"a second build_key with nothing changed still read {warm_reads} "
            f"file(s) - this is the ~0.2-0.4 s hitch on every monitor switch, "
            f"Spout/HDR toggle, GPU switch, auto-revive and NR revive")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: an unchanged runtime is hashed once, a changed one is hashed "
          "again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
