"""Frame Generation takes only NEW pictures, and window capture waits for one (#132).

The report: NR off, FG x4, a video playing. While the mouse moved the video
stuttered, the frame counter went up and the GPU load rose to ~50%; with the
mouse still it was smooth. In window mode FG "did not work at all". Both came
from one place: every frame the loop produced went into FG as a new real
frame, whether the capture had anything new in it or not.

* Desktop Duplication hands over a pointer-only update as a frame, with the
  same pixels as the last one (the cursor is not drawn into the capture);
* WGC's TryGetNextFrame returns at once, so a window that is not redrawing
  spun the whole loop - capture, FG - on the same picture every few ms.

Each of those frames cost a full set of DLSS-G evaluates and queued a slot a
few ms after the real one, which superseded the generated frames of the real
step: the motion arrived as a burst and a hold.

Checked on the worker itself (NR off, FG 4x, the client not pacing the loop -
frame_limit "unlimited", the default - beyond a cap far above the source):

1. WGC, a window that does not redraw: the loop does not spin - about ten
   frames a second (the capture waits for the window, bounded like the DDA
   acquire), not hundreds;
2. WGC, a window redrawn at 30 fps: FG takes one slot per new picture, and
   its generated frames are not superseded by slots that carried nothing
   new, nor real frames discarded unseen;
3. DDA, the pointer moving over a still picture: the pointer-only frames are
   held - no slot, no DLSS-G evaluate - so FG takes no more slots than with
   the pointer at rest (the report: the rate went up when the mouse moved).
   Duplication reports a change anywhere on the output, so the rate at rest
   is measured first and is whatever the rest of the desktop makes it.

All three fail on v2.1.6: (1) runs at the client's cap, (2) supersedes about
half the generated frames and discards a third of the slots, (3) holds
nothing and queues a slot per pointer update.

Phases 1-2 show nothing (the worker's window is far off every monitor, the
target is a ghost - tests/offscreen_target.py). Phase 3 shows a plain square
in the top-left corner of the primary monitor for about four seconds and
moves the pointer over it; the pointer is put back afterwards. Needs an RTX
GPU with DLSS-G.

Run:  runtime\\python.exe tests\\test_fg_fresh_frames.py
"""
import ctypes
import math
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from offscreen_target import Target  # noqa: E402
from paths import WORKER_EXE  # noqa: E402

W, H = 640, 360
GW, GH = 160, 90
# The client's own cap: well above anything the capture produces, so the rate
# the loop runs at is the worker's, as with frame_limit "unlimited" - but
# never an uncapped loop on the GPU.
CLIENT_CAP = 250
VIDEO_FPS = 30
PHASE_RE = re.compile(
    r"\[phase\] fg presenter: .*?generated shown (\d+) of (\d+) \(late (\d+), "
    r"superseded (\d+)\) \| real skipped (\d+), without generated (\d+)"
    r"(?:.*?held (\d+))?")


def presenter_totals(lines):
    """Sums of the presenter's phase lines: shown, planned, late, superseded,
    real skipped, real without generated, held."""
    totals = [0] * 7
    for line in lines:
        m = PHASE_RE.search(line)
        if m:
            for k in range(7):
                totals[k] += int(m.group(k + 1) or 0)
    return totals


class Session:
    """One worker with FG 4x on the bypass path, and the frames it answers."""

    def __init__(self, params):
        from pipeline import start_worker
        from protocol import send_frame
        self._send_frame = send_frame
        self.worker, self.logs, self.reader, self.stop = start_worker(
            params, W, H, 2, 0, 0, None)
        self.index = 0
        self.due = 0.0
        self.motion = np.zeros((GH, GW, 2), np.float16)
        self._seen: set = set()

    def frame(self):
        wait = self.due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        self.due = max(self.due, time.perf_counter()) + 1.0 / CLIENT_CAP
        i = self.index
        self.index += 1
        self._send_frame(self.worker, i, None, self.motion, i == 0, i, no_color=True,
                         motion_small=True, bypass=True,
                         frame_generation=True, frame_multiplier=4)
        self.reader.recv(i, timeout=10.0)

    def started(self) -> bool:
        return any("[fg] 4x enabled" in ln for ln in list(self.logs))

    def presenter_lines(self):
        """The presenter's lines not collected yet. The log list keeps only
        its last 2000 lines and the profiler writes one per frame, so it is
        read as it goes rather than sliced afterwards."""
        fresh = [ln for ln in list(self.logs)
                 if "[phase] fg presenter" in ln and ln not in self._seen]
        self._seen.update(fresh)
        return fresh

    def run_for(self, seconds):
        """Frames answered per second, and the presenter's reports meanwhile."""
        self.presenter_lines()
        lines = []
        start = time.perf_counter()
        polled = start
        count = 0
        while time.perf_counter() - start < seconds:
            self.frame()
            count += 1
            if time.perf_counter() - polled > 0.5:
                lines += self.presenter_lines()
                polled = time.perf_counter()
        rate = count / (time.perf_counter() - start)
        # The presenter reports every two seconds, and only when it has a slot
        # to show: frames keep coming, as they were, until one more report
        # lands. Its first report in the window may cover frames from before.
        tail_end = time.perf_counter() + 3.0
        while time.perf_counter() < tail_end:
            self.frame()
            new = self.presenter_lines()
            lines += new
            if new:
                break
        return rate, lines[1:]

    def failed(self) -> bool:
        return any("presenter failed" in ln or "Evaluate failed" in ln
                   for ln in list(self.logs))

    def close(self):
        from pipeline import shutdown_worker
        shutdown_worker(self.worker, self.stop)


def warm_up(session, target):
    """FG up and running on a first few pictures."""
    target.animate = True
    for _ in range(60):
        session.frame()
    target.animate = False
    time.sleep(0.3)


def wgc_phases(params, failures) -> None:
    from protocol import send_motion_size, send_wgc, send_window
    target = Target(W, H, name="NsFgFreshFrames", ghost=True)
    session = Session(params)
    try:
        send_wgc(session.worker, target.hwnd)
        if session.reader.wait_wgak(15) != (W, H):
            failures.append("the capture target is not the expected size")
        send_motion_size(session.worker, GW, GH)
        session.reader.wait_mack(10)
        send_window(session.worker, W, H)
        session.reader.wait_wack(10)
        warm_up(session, target)
        if not session.started():
            failures.append("WGC: FG never started at 4x - the run tested nothing")
            return

        # 1. A window that does not redraw.
        rate, _lines = session.run_for(3.0)
        print(f"    WGC, still window: the worker answered {rate:.0f} frames/s "
              f"(client cap {CLIENT_CAP})")
        if rate > 30:
            failures.append(f"a window that does not redraw still runs the loop at "
                            f"{rate:.0f} frames/s - WGC does not wait for a frame")

        # 2. A window redrawn at 30 fps: a video.
        target.animate_interval = 1.0 / VIDEO_FPS
        target.animate = True
        rate, lines = session.run_for(8.0)
        target.animate = False
        shown, planned, late, superseded, skipped, flat, held = presenter_totals(lines)
        seconds = 2.0 * len(lines)
        slots = planned / 3 + flat + skipped
        print(f"    WGC, {VIDEO_FPS} fps window: {rate:.0f} frames/s answered; the "
              f"presenter got {slots / max(seconds, 1):.0f} slots/s; generated "
              f"shown {shown} of {planned} (late {late}, superseded {superseded}); "
              f"real skipped {skipped}, without generated {flat}; held {held}")
        # "Late" is left out on purpose: a source frame 21 ms after the last
        # asks 4x = 190 fps of a 144 Hz display, and dropping what it cannot
        # show is the presenter doing its job. What the stale slots did is
        # supersede generated frames and discard real ones unseen.
        if not lines or planned == 0:
            failures.append("no generated frames were planned - FG never interpolated")
            return
        if superseded > 0.2 * planned:
            failures.append(f"{superseded} of {planned} generated frames were "
                            f"superseded before they were shown: frames with "
                            f"nothing new still take FG's slots")
        if skipped > 0.1 * slots:
            failures.append(f"{skipped} of {slots:.0f} slots were discarded unseen "
                            f"- the loop queues more real frames than the source has")
        if slots / seconds > VIDEO_FPS * 1.5:
            failures.append(f"FG took {slots / seconds:.0f} slots/s from a "
                            f"{VIDEO_FPS} fps source")
    finally:
        if session.failed():
            failures.append("WGC: the FG presenter failed during the run")
        session.close()
        target.close()


def dda_phase(params, failures) -> None:
    """The pointer over a still picture, through Desktop Duplication."""
    from protocol import send_dda, send_motion_size, send_window
    user32 = ctypes.windll.user32
    cover = Target(W, H, name="NsFgFreshCover", cover=True)
    home = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(home))
    moving = threading.Event()
    done = threading.Event()

    def circle():
        # A small circle inside the cover, a new position every 2 ms: faster
        # than any display refreshes, so every composition has a pointer
        # update in it and no picture change.
        t0 = time.perf_counter()
        while not done.is_set():
            if moving.is_set():
                a = (time.perf_counter() - t0) * 2 * math.pi
                user32.SetCursorPos(int(W / 2 + 80 * math.cos(a)),
                                    int(H / 2 + 80 * math.sin(a)))
            time.sleep(0.002)

    mover = threading.Thread(target=circle, daemon=True)
    mover.start()
    session = Session(params)
    try:
        send_dda(session.worker, W, H)
        session.reader.wait_dack(15)
        send_motion_size(session.worker, GW, GH)
        session.reader.wait_mack(10)
        send_window(session.worker, W, H)
        session.reader.wait_wack(10)
        warm_up(session, cover)
        if not session.started():
            failures.append("DDA: FG never started at 4x - the run tested nothing")
            return
        # Desktop Duplication reports a change anywhere on the output, and
        # the rest of the desktop is whatever the machine is showing: the
        # rate without the pointer is the baseline, not a fixed number.
        def slots_per_s(lines):
            _s, planned, _l, _su, skipped, flat, held = presenter_totals(lines)
            return (planned / 3 + flat + skipped) / max(2.0 * len(lines), 1), held

        rate_still, lines = session.run_for(3.0)
        still, _held = slots_per_s(lines)
        moving.set()
        rate, lines = session.run_for(4.0)
        moving.clear()
        slots, held = slots_per_s(lines)
        print(f"    DDA, still picture: {rate_still:.0f} frames/s answered, "
              f"{still:.0f} slots/s; the pointer moving over it: {rate:.0f} "
              f"frames/s answered, {slots:.0f} slots/s, {held} held")
        if not lines:
            failures.append("DDA: the presenter reported nothing")
            return
        if rate < rate_still + 10:
            # Not a failure of the fix: the pointer added no updates of its
            # own here, so there was nothing to hold and this proves little.
            print("    (the pointer added few updates on this machine - weak check)")
        elif held == 0:
            failures.append("DDA: not one pointer-only frame was held - FG takes "
                            "a picture that did not change")
        # The reported symptom: the rate went up while the mouse moved.
        if slots > 1.25 * still + 10:
            failures.append(f"moving the pointer raised FG from {still:.0f} to "
                            f"{slots:.0f} slots/s over a picture that did not change")
    finally:
        done.set()
        mover.join(1)
        user32.SetCursorPos(home.x, home.y)
        if session.failed():
            failures.append("DDA: the FG presenter failed during the run")
        session.close()
        cover.close()


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    os.environ["NS_NR_SMALL"] = "0"
    os.environ["NS_PHASE"] = "1"
    os.environ["NS_HDR"] = "0"
    from settings_io import PROFILES
    params = dict(PROFILES["Natural"])

    failures: list = []
    for phase in (wgc_phases, dda_phase):
        try:
            phase(params, failures)
        except Exception as exc:
            failures.append(f"{phase.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: FG generates from new pictures only, a still window does not spin "
          "the loop, and pointer-only frames are held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
