"""A screenshot is frozen before Save As can enter Desktop Duplication (#89).

The old sequence opened the native dialog first and asked DDA for pixels only
after it closed.  Desktop Duplication can return that dialog's buffered frame,
so the saved image contained the file picker.  This is a state-machine test:
the worker frame is copied, then the dialog starts, and its chosen path writes
that frozen copy rather than a later output slot.
"""
from __future__ import annotations

import queue
import sys
import types
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import commands  # noqa: E402


def main() -> int:
    failures: list[str] = []
    st = types.SimpleNamespace(
        shot_dialog_open=False,
        pending_shot=None,
        shot_rgba=None,
        shot_paths=queue.Queue(),
        cfg={},
        display=types.SimpleNamespace(
            get_hwnd=lambda: 0,
            alert=lambda _message: None,
            menu=types.SimpleNamespace(set_state=lambda _state: None),
        ),
    )
    opened: list[np.ndarray] = []
    real_open = commands.open_save_dialog
    real_save = commands.save_screenshot

    def fake_open(state):
        # The dialog starts only after the immutable frame has been placed on
        # state. Keep a copy to prove later source/output reuse cannot change it.
        opened.append(state.shot_rgba.copy())
        state.shot_dialog_open = True

    commands.open_save_dialog = fake_open
    try:
        commands.request_screenshot(st)
        if st.pending_shot is not commands.SHOT_FRAME_PENDING:
            failures.append("screenshot request did not ask the next worker frame")
        if opened:
            failures.append("Save As opened before a worker frame was frozen")

        worker_rgba = np.zeros((3, 4, 4), dtype=np.uint8)
        worker_rgba[..., 0] = 17
        if not commands.freeze_screenshot_frame(st, worker_rgba):
            failures.append("the requested worker frame was not frozen")
        if st.pending_shot is not None:
            failures.append("the pending request survived the frozen frame")
        if len(opened) != 1:
            failures.append(f"Save As opened {len(opened)} times, want once")
        worker_rgba[..., 0] = 99  # simulate the next shared-output reuse
        if st.shot_rgba is None or int(st.shot_rgba[..., 0].max()) != 17:
            failures.append("the frozen screenshot aliases the reusable output frame")
        if opened and int(opened[0][..., 0].max()) != 17:
            failures.append("Save As saw a frame after its output slot was reused")

        saved: list[tuple[Path, np.ndarray]] = []
        commands.save_screenshot = lambda _st, path, rgba: saved.append((path, rgba.copy()))
        path = Path("screenshot-before-dialog.jpg")
        st.shot_paths.put(path)
        commands.drain_save_dialog(st)
        if len(saved) != 1 or saved[0][0] != path:
            failures.append(f"dialog result saved {saved!r}, want one frozen frame at {path}")
        elif int(saved[0][1][..., 0].max()) != 17:
            failures.append("the dialog result did not use the frozen pre-dialog frame")
        if st.shot_rgba is not None:
            failures.append("the frozen frame was retained after saving")

        st.shot_rgba = np.ones((2, 2, 4), dtype=np.uint8)
        st.shot_dialog_open = True
        st.shot_paths.put(None)
        commands.drain_save_dialog(st)
        if st.shot_rgba is not None:
            failures.append("cancelled Save As retained a full-resolution frame")
    finally:
        commands.open_save_dialog = real_open
        commands.save_screenshot = real_save

    source = (BASE / "main.py").read_text(encoding="utf-8")
    frozen = source.find("commands.freeze_screenshot_frame(st, st.output_rgba)")
    shown = source.find("st.display.show(st.output_rgba)")
    if frozen < 0 or shown < 0 or frozen > shown:
        failures.append("main opens/uses a display result before freezing the screenshot")

    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print("OK: screenshot pixels are frozen before Save As and survive output reuse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
