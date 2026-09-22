r"""The conversion queue: order, progress, stop, retry, and what a row says.

The converter used to be one button and one silent thread: pick a file, wait,
read an alert. No progress, no way to stop it, one file at a time - and a
second click while it ran was refused. The queue is what a person needs
around media_convert, and its rules are checked here without a GPU: the
converter is a stand-in that reports progress the way the real one does.

WHAT THIS LOCKS

1. Adding: media is queued in order; a missing path, a format the converter
   will not open and a file already waiting are refused, each with its
   reason; a folder adds the media directly inside it.
2. One file at a time, in order, each with the settings it was ADDED with -
   a slider moved later does not reach a file already waiting.
3. Progress: the row carries the fraction, a rate measured from the first
   frame (the worker's warm-up is not the file's speed) and the time left.
4. Stop: the running file stops through the cancel event and is marked
   stopped; stop_all stops the waiting ones as well.
5. A failure is a short reason on the row ("denied" for a folder that cannot
   be written), and Retry runs the file again with the settings of now.
6. Remove, clear finished, and the finished list the main loop announces.
7. The row text: every state reads from the strings, and a finished file
   names its codec - the CPU encoder says so.

Run:  runtime\python.exe tests\test_convert_queue.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import convert_jobs  # noqa: E402
import media_convert  # noqa: E402
from convert_jobs import (CANCELLED, DONE, FAILED, QUEUED, RUNNING,  # noqa: E402
                          ConvertQueue, ConvertSettings)
from i18n import STRINGS  # noqa: E402


class FakeConverter:
    """Reports progress like media_convert, and can be held, failed or stopped."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.gate: dict[str, threading.Event] = {}
        self.fail: dict[str, BaseException] = {}
        self.frames = 40

    def __call__(self, source, output, params, **kw):
        name = Path(source).name
        self.calls.append((name, dict(kw, params=params)))
        progress, cancel = kw["progress"], kw["cancel"]
        progress(media_convert.Progress("starting", 0, self.frames))
        if name in self.fail:
            raise self.fail.pop(name)
        for i in range(1, self.frames + 1):
            gate = self.gate.get(name)
            if gate is not None:
                gate.wait(5.0)
            if cancel.is_set():
                raise media_convert.ConversionCancelled("cancelled")
            progress(media_convert.Progress("processing", i, self.frames))
            time.sleep(0.02)
        out = Path(source).with_name(Path(source).stem + "-nr.mp4")
        return media_convert.ConversionResult(
            source=Path(source), output=out, kind="video", frames=self.frames,
            codec="libx264" if "tiny" in name else "av1_nvenc",
            audio="aac" if "pcm" in name else "copied")


def wait_for(predicate, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def statuses(queue: ConvertQueue) -> dict:
    return {r["name"]: r["status"] for r in queue.rows()}


def main() -> int:
    failures: list[str] = []
    s = STRINGS["en"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("a.mp4", "b.mkv", "c.png", "tiny.mp4", "pcm.avi",
                     "notes.txt"):
            (root / name).write_bytes(b"x" * 2048)
        folder = root / "batch"
        folder.mkdir()
        for name in ("z.mov", "y.jpg", "readme.md"):
            (folder / name).write_bytes(b"x" * 10)

        fake = FakeConverter()
        queue = ConvertQueue(convert=fake)
        first = ConvertSettings(params={"intensity": 1.0}, codec="hevc")

        # 1. Adding and refusing - the runner is held on the first file so
        #    the list can be read before anything finishes.
        fake.gate["a.mp4"] = threading.Event()
        added, refused = queue.add(
            [root / "a.mp4", root / "b.mkv", root / "notes.txt",
             root / "gone.mp4", root / "a.mp4"], first)
        reasons = dict(refused)
        if len(added) != 2:
            failures.append(f"two media files were offered, {len(added)} added")
        if reasons.get("notes.txt") != "format":
            failures.append(f"a .txt was not refused as a format: {refused}")
        if reasons.get("gone.mp4") != "missing":
            failures.append(f"a missing file was not refused: {refused}")
        if reasons.get("a.mp4") != "queued":
            failures.append(f"a file already queued was queued again: {refused}")
        added_dir, _ = queue.add([folder], first)
        names = [r["name"] for r in queue.rows()]
        if len(added_dir) != 2 or names[-2:] != ["y.jpg", "z.mov"]:
            failures.append(f"a folder did not add its two media files in "
                            f"name order: {names}")

        # 2. One at a time, in order.
        if not wait_for(lambda: statuses(queue).get("a.mp4") == RUNNING):
            failures.append("the first file never started")
        if statuses(queue).get("b.mkv") != QUEUED:
            failures.append("the second file started while the first ran")

        # A slider moves while a.mp4 converts: b.mkv keeps the look it was
        # added with.
        queue.add([root / "c.png"],
                  ConvertSettings(params={"intensity": 0.2}, codec="h264"))
        fake.gate["a.mp4"].set()

        # 3. Progress on the running row.
        fake.gate["b.mkv"] = threading.Event()
        if not wait_for(lambda: statuses(queue).get("b.mkv") == RUNNING):
            failures.append("the second file never started")
        # Let some frames through, then hold it and read the row.
        released = fake.gate["b.mkv"]
        released.set()
        time.sleep(0.6)
        row = next(r for r in queue.rows() if r["name"] == "b.mkv")
        if not 0.0 < row["fraction"] <= 1.0:
            failures.append(f"the running row has no fraction: {row}")
        if row["status"] == RUNNING and row["fps"] <= 0:
            failures.append(f"the running row has no rate after 0.6 s: {row}")
        if row["status"] == RUNNING and row["eta"] is None:
            failures.append(f"the running row has no time left: {row}")

        if not wait_for(lambda: not queue.busy, 20.0):
            failures.append("the queue never drained")
        by_name = {name: kw for name, kw in fake.calls}
        if by_name.get("b.mkv", {}).get("codec") != "hevc" or \
                by_name.get("b.mkv", {}).get("params") != {"intensity": 1.0}:
            failures.append("b.mkv was converted with settings from after it "
                            "was added")
        if by_name.get("c.png", {}).get("codec") != "h264":
            failures.append("c.png did not get the settings it was added with")
        if [n for n, _ in fake.calls][:3] != ["a.mp4", "b.mkv", "z.mov"] and \
                [n for n, _ in fake.calls][:2] != ["a.mp4", "b.mkv"]:
            failures.append(f"the files did not run in order: "
                            f"{[n for n, _ in fake.calls]}")
        finished = queue.pop_finished()
        if len(finished) != 5 or any(j.status != DONE for j in finished):
            failures.append(f"five files should have finished DONE: "
                            f"{[(j.source.name, j.status) for j in finished]}")
        if queue.pop_finished():
            failures.append("pop_finished announced the same jobs twice")

        # 4. Stop one, then stop everything.
        fake.gate["tiny.mp4"] = threading.Event()
        queue.add([root / "tiny.mp4", root / "pcm.avi"], first)
        if not wait_for(lambda: statuses(queue).get("tiny.mp4") == RUNNING):
            failures.append("tiny.mp4 never started")
        tiny_id = next(r["id"] for r in queue.rows() if r["name"] == "tiny.mp4")
        if not queue.act(tiny_id, "stop"):
            failures.append("stopping the running file was refused")
        fake.gate["tiny.mp4"].set()
        if not wait_for(lambda: statuses(queue).get("tiny.mp4") == CANCELLED):
            failures.append(f"the stopped file is not marked stopped: "
                            f"{statuses(queue)}")
        if not wait_for(lambda: not queue.busy, 20.0):
            failures.append("the queue did not move on after a stop")
        fake.gate["a.mp4"] = threading.Event()
        queue.clear_finished()
        queue.add([root / "a.mp4", root / "b.mkv"], first)
        if not wait_for(lambda: statuses(queue).get("a.mp4") == RUNNING):
            failures.append("a.mp4 did not start again")
        if queue.stop_all() != 2:
            failures.append("stop_all did not count the running and the "
                            "waiting file")
        fake.gate["a.mp4"].set()
        if not wait_for(lambda: statuses(queue) == {"a.mp4": CANCELLED,
                                                    "b.mkv": CANCELLED}):
            failures.append(f"stop_all left something running: "
                            f"{statuses(queue)}")

        # 5. A failure, and Retry with the settings of now.
        queue.clear_finished()
        queue.pop_finished()
        fake.fail["a.mp4"] = media_convert.ConversionError(
            "encode", PermissionError(13, "Permission denied"))
        queue.add([root / "a.mp4"], first)
        if not wait_for(lambda: statuses(queue).get("a.mp4") == FAILED):
            failures.append("a failing conversion was not marked failed")
        row = queue.rows()[0]
        if row["error"] != "denied":
            failures.append(f"a folder that cannot be written should read "
                            f"'denied', got {row['error']!r}")
        text, tone = convert_jobs.status_line(row, s)
        if tone != "danger" or text != s["convert_err_denied"]:
            failures.append(f"the failed row reads {text!r} ({tone})")
        now = ConvertSettings(params={"intensity": 0.5}, codec="av1",
                              out_dir=str(root))
        if not queue.act(row["id"], "retry", now):
            failures.append("Retry was refused on a failed file")
        if not wait_for(lambda: statuses(queue).get("a.mp4") == DONE):
            failures.append("the retried file did not finish")
        if fake.calls[-1][1].get("codec") != "av1" or \
                fake.calls[-1][1].get("out_dir") != str(root):
            failures.append("Retry used the old settings, not the new ones")

        # 6. Remove a waiting file; clear finished.
        fake.gate["b.mkv"] = threading.Event()
        queue.add([root / "b.mkv", root / "c.png"], first)
        wait_for(lambda: statuses(queue).get("b.mkv") == RUNNING)
        c_id = next(r["id"] for r in queue.rows() if r["name"] == "c.png")
        if not queue.act(c_id, "remove"):
            failures.append("removing a waiting file was refused")
        b_id = next(r["id"] for r in queue.rows() if r["name"] == "b.mkv")
        if queue.act(b_id, "remove"):
            failures.append("the RUNNING file could be removed - it has to be "
                            "stopped first")
        fake.gate["b.mkv"].set()
        wait_for(lambda: not queue.busy, 20.0)
        if queue.clear_finished() != 2 or queue.rows():
            failures.append(f"clear finished left rows: {queue.rows()}")

    # 7. The row text for every state.
    def line(**row):
        base = {"status": QUEUED, "kind": "video", "size": 5 * 1024 * 1024,
                "stage": "", "done": 0, "total": 0, "fraction": 0.0,
                "fps": 0.0, "eta": None, "seconds": 0.0, "codec": "",
                "audio": "", "error": ""}
        base.update(row)
        return convert_jobs.status_line(base, s)

    text, tone = line()
    if "5.0 MB" not in text or s["convert_queued"] not in text:
        failures.append(f"a waiting row reads {text!r}")
    text, _ = line(status=RUNNING, stage="processing", done=30, total=120,
                   fraction=0.25, fps=24.4, eta=62.0)
    for part in ("25%", "24.4 fps", "1:02"):
        if part not in text:
            failures.append(f"a running row lacks {part!r}: {text!r}")
    text, _ = line(status=RUNNING, stage="processing", done=30, total=0)
    if "30" not in text:
        failures.append(f"a running row without a total lacks its frame "
                        f"count: {text!r}")
    text, _ = line(status=RUNNING, kind="image", stage="processing")
    if text != s["convert_processing"]:
        failures.append(f"a running still reads {text!r}")
    text, tone = line(status=DONE, seconds=75.0, codec="libx264", audio="aac")
    if tone != "ok" or "1:15" not in text or "CPU" not in text \
            or s["convert_audio_aac"] not in text:
        failures.append(f"a finished row reads {text!r} ({tone})")
    text, tone = line(status=CANCELLED)
    if text != s["convert_cancelled"]:
        failures.append(f"a stopped row reads {text!r}")
    for status, action in ((QUEUED, "remove"), (RUNNING, "stop"),
                           (DONE, "show"), (FAILED, "retry"),
                           (CANCELLED, "retry")):
        if convert_jobs.row_action({"status": status}) != action:
            failures.append(f"a {status} row should offer {action}")
    for seconds, want in ((0.4, "0:00"), (59.6, "1:00"), (3725, "1:02:05")):
        if convert_jobs.format_eta(seconds) != want:
            failures.append(f"format_eta({seconds}) = "
                            f"{convert_jobs.format_eta(seconds)!r}, not {want!r}")

    # 8. A failed row names the right thing. The stage alone is not enough for
    #    "process": a muxer that refuses the write and a worker that dies are
    #    both that stage, and sending the user to restart a healthy worker for
    #    a file it could not write is the same class of lie as a silent
    #    "unknown". libav (av.error.*) is the file's fault, anything else at
    #    that stage is the worker's.
    import av  # noqa: E402

    cases = [
        (media_convert.ConversionError(
            "process", av.error.ArgumentError(22, "Invalid argument",
                                              "clip-nr.mp4.partial")), "encode"),
        (media_convert.ConversionError("process", RuntimeError("pipe closed")),
         "worker"),
        (media_convert.ConversionError("encode", OSError("disk full")), "encode"),
        (media_convert.ConversionError("decode", ValueError("bad header")),
         "unreadable"),
        (media_convert.ConversionError(
            "encode", PermissionError(13, "Permission denied")), "denied"),
    ]
    for exc, want in cases:
        got = convert_jobs.friendly_error(exc)
        if got != want:
            failures.append(f"friendly_error({exc.stage}/"
                            f"{type(exc.cause).__name__}) = {got!r}, not {want!r}")
        if f"convert_err_{want}" not in s:
            failures.append(f"the queue names {want!r} but the strings have no "
                            f"convert_err_{want}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the queue runs in order with the settings each file was added "
          "with, reports progress, stops, retries, and every row reads from "
          "the strings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
