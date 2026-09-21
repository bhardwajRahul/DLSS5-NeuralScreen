"""Run the production pass reconciler with deterministic allocation/fence failures."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / "native/dlss5-feed-host64.cpp").read_text(encoding="utf-8")
    start = source.index("static unsigned EnsurePassFeatures(")
    end = source.index("\n// ---------------------------------------------------------------------------", start)
    reconcile = source[start:end]
    harness = r'''
#include <algorithm>
#include <cassert>
#include <memory>
#include <vector>
using UINT = unsigned;
using UINT64 = unsigned long long;
struct NVSDK_NGX_Handle { unsigned id; };
using NVSDK_NGX_Result = unsigned;
constexpr unsigned NVSDK_NGX_Result_Fail = 0xbad00000, NR_MAX_PASSES = 4;
struct { NVSDK_NGX_Handle *feature; void *fence; UINT64 fence_value; } h;
NVSDK_NGX_Handle *g_nr_pass[NR_MAX_PASSES] = {};
bool g_submission_failed = false, fail_wait = false, fail_create = false;
bool fail_create_wait = false;
unsigned waits = 0, creates = 0;
std::vector<std::unique_ptr<NVSDK_NGX_Handle>> handles;
std::vector<NVSDK_NGX_Handle *> released;
void Log(const char *, ...) {}
void LogVideoMemory(const char *) {}
bool WaitFenceValue(void *, UINT64 value, unsigned, const char *) {
    assert(value == h.fence_value);
    ++waits;
    if (fail_wait) { g_submission_failed = true; return false; }
    return true;
}
void SafeReleaseFeature(NVSDK_NGX_Handle *feature) {
    assert(!g_submission_failed && waits > 0);
    assert(feature != h.feature);
    assert(std::find(released.begin(), released.end(), feature) == released.end());
    released.push_back(feature);
}
bool CreateFeature(UINT, UINT, int, NVSDK_NGX_Result *result, UINT, UINT) {
    ++creates;
    if (fail_create) { *result = NVSDK_NGX_Result_Fail; return false; }
    handles.emplace_back(new NVSDK_NGX_Handle{creates});
    h.feature = handles.back().get();
    if (fail_create_wait) { g_submission_failed = true; return false; }
    *result = 1;
    return true;
}
RECONCILE
int main() {
    NVSDK_NGX_Handle primary{0}; h.feature = &primary; h.fence_value = 7;
    auto ensure = [](unsigned count) { return EnsurePassFeatures(640,360,0,0,0,count); };
    assert(ensure(4) == 4 && creates == 3 && waits == 0);
    auto second = g_nr_pass[1];
    assert(ensure(2) == 2 && released.size() == 2 && waits == 1);
    assert(h.feature == &primary && g_nr_pass[1] == second);
    assert(!g_nr_pass[2] && !g_nr_pass[3]);
    assert(ensure(1) == 1 && released.size() == 3 && !g_nr_pass[1]);
    const auto before = creates;
    assert(ensure(1) == 1 && creates == before); // repeated parameter apply
    assert(ensure(4) == 4 && creates == before + 3);
    assert(h.feature == &primary && g_nr_pass[1] != second);
    fail_wait = true;
    auto active = g_nr_pass[1]; const auto freed = released.size();
    assert(ensure(1) == 0 && g_submission_failed);
    assert(released.size() == freed && g_nr_pass[1] == active && h.feature == &primary);
    fail_wait = false; g_submission_failed = false; // test-only recovery
    assert(ensure(1) == 1);
    fail_create = true;
    assert(ensure(4) == 1 && h.feature == &primary);
    assert(!g_nr_pass[1] && !g_nr_pass[2] && !g_nr_pass[3]);
    fail_create = false;
    assert(ensure(2) == 2 && h.feature == &primary);
    second = g_nr_pass[1]; fail_create = true;
    assert(ensure(4) == 2 && g_nr_pass[1] == second && h.feature == &primary);
    fail_create = false;
    assert(ensure(99) == 4); // clamp to the wire ceiling
    assert(ensure(0) == 1 && !g_nr_pass[1] && !g_nr_pass[2] && !g_nr_pass[3]);
    fail_create_wait = true;
    const auto released_before_create = released.size();
    assert(ensure(2) == 0 && g_submission_failed && h.feature == &primary);
    assert(g_nr_pass[1] && released.size() == released_before_create);
    // A created feature whose commands did not retire belongs to process teardown.
    assert(ensure(1) == 0 && g_nr_pass[1] && released.size() == released_before_create);
}
'''.replace("RECONCILE", reconcile)
    with tempfile.TemporaryDirectory(prefix="ns-pass-resources-") as temp:
        work = Path(temp)
        (work / "check.cpp").write_text(harness, encoding="utf-8")
        (work / "build.bat").write_text(
            f'@echo off\ncall "{ROOT / "native/vcvars.bat"}" || exit /b 1\n'
            'cl /nologo /EHsc /std:c++17 check.cpp /Fe:check.exe\n', encoding="utf-8")
        build = subprocess.run([os.environ["COMSPEC"], "/d", "/c", "build.bat"],
                               cwd=work, capture_output=True, text=True, timeout=120)
        assert build.returncode == 0, build.stdout + build.stderr
        checked = subprocess.run([str(work / "check.exe")], cwd=work,
                                 capture_output=True, text=True, timeout=10)
        assert checked.returncode == 0, checked.stdout + checked.stderr
    print("OK: surplus passes retire before release, active history survives, failures stay safe")


def check_gpu():
    """Opt-in offscreen check of both RNSZ paths and unchanged full-size pixels."""
    import hashlib
    import re
    import struct
    import threading
    import numpy as np

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    from worker_reply import read_exact, read_reply
    import protocol as wire

    params = dict(style=1, auto_mask=1, intensity=1., local_tone=.5,
                  local_structure=1., skin_structure=-1.)
    w, height = 640, 360
    env = dict(os.environ, NS_NR_SMALL="0", NS_FRAMEGEN="0", NS_PHASE="0")
    worker = subprocess.Popen([str(ROOT / "native/nvngx.dll"), "--live"],
        cwd=ROOT / "native", env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    logs = []
    drain = threading.Thread(target=lambda: logs.extend(iter(worker.stderr.readline, b"")), daemon=True)
    drain.start()
    timeout = threading.Timer(90, worker.kill)
    timeout.start()
    hashes = []
    try:
        worker.stdin.write(struct.pack(wire.HEADER_FMT, wire.VIDEO_MAGIC, w, height,
            4, 0, 0, 0, 1, 1, 0, 1., .5, 1., -1., 0, 0))
        worker.stdin.flush()
        image = np.random.default_rng(7).integers(0, 256, (height, w, 4), dtype=np.uint8)
        image[:, :, 3] = 255
        steps = [(False, n) for n in (1, 2, 3, 4)] + [
            (True, 4), (True, 1), (True, 2), (True, 4), (False, 3), (True, 2)]
        for index, (small, passes) in enumerate(steps):
            ww, hh = (w // 2, height // 2) if small else (w, height)
            wire.send_resize(worker, params, ww, hh, 4, w if small else 0,
                             height if small else 0, nr_small=small, nr_passes=passes)
            ack = struct.unpack(wire.RACK_FMT, read_reply(worker.stdout, struct.calcsize(wire.RACK_FMT)))
            assert ack[0] == wire.RESIZE_ACK_MAGIC and ack[1] == 1, ack
            motion = np.zeros((hh, ww, 2), np.float16)
            wire.send_frame(worker, index, image, motion, True, index)
            out = struct.unpack(wire.OUT_FMT, read_reply(worker.stdout, struct.calcsize(wire.OUT_FMT)))
            assert out[1] == index and out[2] == 1 and out[3] == image.nbytes, out
            pixels = read_exact(worker.stdout, out[3])
            if not small:
                hashes.append(hashlib.sha256(pixels).hexdigest())
    finally:
        worker.stdin.close()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
        timeout.cancel()
        drain.join(timeout=3)
    log = b"".join(logs).decode("utf-8", "replace")
    assert worker.returncode == 0, log
    assert len(set(hashes)) == 1, "saved unused pass count changed full-size pixels"
    counts = re.findall(r"NR cascade built: asked=(\d+) effective=(\d+) allocated=(\d+)", log)
    expected = [(str(passes), str(passes if small else 1), str(passes if small else 1))
                for small, passes in steps[1:]]
    assert counts == expected, (counts, expected, log)
    assert "video memory after NR cascade:" in log
    assert "[failure]" not in log, log
    print("OK: GPU resize paths keep saved counts, release/re-enable passes, and preserve full-size pixels")


if __name__ == "__main__":
    main()
    if "--gpu" in sys.argv:
        check_gpu()
