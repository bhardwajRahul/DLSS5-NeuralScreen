"""Regression: NR OFF must stop continuous capture and presentation.

This is a control-plane test; no display or NVIDIA hardware is required.

Run:  runtime\\python.exe tests\\test_low_cost_off.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock


def _repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "main.py").is_file():
            return p
    return start


BASE = _repo_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import channels  # noqa: E402
import pipeline  # noqa: E402


class _Display:
    def __init__(self):
        self.hud_only = None
        self.visible = None

    def set_hud_only(self, value):
        self.hud_only = bool(value)

    def set_visible(self, value):
        self.visible = bool(value)


class _Worker:
    def poll(self):
        return None


class _Reader:
    def __init__(self, calls):
        self.calls = calls

    def wait_wack(self, timeout):
        self.calls.append(("wack", timeout))

    def wait_dack(self, timeout):
        self.calls.append(("dack", timeout))

    def wait_wgak(self, timeout):
        self.calls.append(("wgak", timeout))
        return 0, 0


def _state(*, window=False):
    calls = []
    return types.SimpleNamespace(
        paused=True,
        cfg={"frame_generation": False},
        recorder=None,
        pending_shot=None,
        off_suspended=False,
        work_frame=object(),
        output_rgba=object(),
        guides=types.SimpleNamespace(previous_gray=object()),
        worker=_Worker(),
        worker_stop=None,
        worker_failed=False,
        next_auto_revive=0.0,
        reader=_Reader(calls),
        present_mode=True,
        present_attempted=False,
        dda_mode=True,
        dda_attempted=False,
        gray_active=True,
        out_shm=False,
        out_attempted=False,
        window_hwnd=123 if window else None,
        display=_Display(),
        calls=calls,
    )


def main() -> int:
    failures = []

    st = _state()
    with (mock.patch.object(channels, "send_window",
                            side_effect=lambda *a: st.calls.append(("window", a[1:]))),
          mock.patch.object(channels, "send_dda",
                            side_effect=lambda *a: st.calls.append(("dda", a[1:])))):
        if not pipeline.sync_low_cost_off(st):
            failures.append("OFF did not enter the suspended state")
        first_calls = list(st.calls)
        pipeline.sync_low_cost_off(st)

    names = [c[0] for c in first_calls]
    if names != ["window", "wack", "dda", "dack"]:
        failures.append(f"fullscreen suspend handshake order is wrong: {names}")
    if st.calls != first_calls:
        failures.append("repeated OFF renegotiated channels instead of being idempotent")
    if st.present_mode or st.dda_mode or st.gray_active:
        failures.append("active worker channel flags survived OFF")
    if st.work_frame is not None or st.output_rgba is not None:
        failures.append("OFF retained a stale frame")
    if st.display.hud_only is not True:
        failures.append("OFF did not leave a transparent menu-capable HUD layer")

    # One-window capture has its own stop command; DDA1 would not close WGCW.
    stw = _state(window=True)
    with (mock.patch.object(channels, "send_window"),
          mock.patch.object(channels, "send_wgc",
                            side_effect=lambda *a: stw.calls.append(("wgc", a[1:])))):
        pipeline.sync_low_cost_off(stw)
    if "wgc" not in [c[0] for c in stw.calls] or "dack" in [c[0] for c in stw.calls]:
        failures.append(f"window capture did not stop through WGCW: {stw.calls}")

    # Explicit consumers wake bypass without changing the user's NR toggle.
    for field, value in (("recorder", object()), ("pending_shot", object())):
        active = _state()
        setattr(active, field, value)
        if pipeline.wants_low_cost_off(active):
            failures.append(f"{field} did not wake bypass")
    active = _state()
    active.cfg["frame_generation"] = True
    if pipeline.wants_low_cost_off(active):
        failures.append("Frame Generation did not wake bypass")

    # Resume re-arms negotiation and starts from a clean capture/history.
    st.paused = False
    if pipeline.sync_low_cost_off(st):
        failures.append("NR ON stayed suspended")
    if st.present_attempted or st.dda_attempted:
        failures.append("resume did not re-arm capture/presentation negotiation")
    if st.guides.previous_gray is not None:
        failures.append("resume retained temporal history from before OFF")
    if st.display.visible is not True:
        failures.append("resume did not reveal the output layer")

    # Structural guarantee: the low-cost branch itself leaves before the
    # grab/send path. The old check scanned from `if low_cost_off:` to the
    # grab and looked for any `continue` - and that window contains an
    # UNRELATED one (the follow-window branch's), so removing the low-cost
    # branch's own exit left the test green (audit: WEAK). The branch's body
    # is now isolated by its own indentation, and the exit has to be in it.
    lines = (BASE / "main.py").read_text(encoding="utf-8").splitlines()
    at = next((i for i, l in enumerate(lines)
               if l.strip() == "if low_cost_off:"), None)
    if at is None:
        failures.append("main has no `if low_cost_off:` branch at all")
    else:
        indent = len(lines[at]) - len(lines[at].lstrip())
        body = []
        for l in lines[at + 1:]:
            if not l.strip():
                body.append(l)
                continue
            cur = len(l) - len(l.lstrip())
            if cur <= indent:
                break
            body.append(l)
        if not body:
            failures.append("the low-cost branch is empty")
        elif not any(l.strip().startswith("continue") for l in body):
            failures.append(
                "the low-cost branch does not leave the frame loop - it falls "
                "into the capture path with NR off, which is the cost the "
                "branch exists to avoid")
        else:
            print(f"    low-cost branch: lines {at + 1}..{at + len(body)}, "
                  f"own exit present")
        # And the grab must still be outside it.
        grab_at = next((i for i, l in enumerate(lines)
                        if "_safe_grab()" in l and i > at), None)
        if grab_at is not None and grab_at < at + len(body):
            failures.append("the capture call sits inside the low-cost branch")

    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print("OK: NR OFF closes capture/presentation and the frame loop stays idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
