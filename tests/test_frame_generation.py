"""Opt-in RTX smoke test for DLSS-G output, bypass and clean shutdown.

Run with --run. Shows a small moving test image for several seconds.
The normal pixel export must still contain real frames, not generated ones.
"""
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import protocol as wire
from worker_reply import read_reply


def exact(pipe, count):
    result = bytearray()
    while len(result) < count:
        chunk = pipe.read(count - len(result))
        if not chunk:
            raise RuntimeError("worker exited before replying")
        result.extend(chunk)
    return result


def reply(pipe, count):
    """One reply, with the asynchronous CACK after the video header eaten.

    The worker emits a 24-byte CACK right after the D5V3 header is accepted,
    before any command reply. Reading stdout directly (as this test did) took
    that CACK for the WACK it was waiting on and every later read was four
    bytes out of phase. The shared helper is what the neighbouring direct
    tests use for exactly this reason; it also raises instead of hiding a
    failed CreateFeature.
    """
    return read_reply(pipe, count)


def run(hdr=False, dynamic=False, check_pixels=False, fg_only=False):
    w, h = 640, 360
    work_w, work_h = w, h
    if hdr:
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        import pygame
        pygame.init()
        screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
        screen.fill((20, 30, 40))
        pygame.display.flip()
        hwnd = pygame.display.get_wm_info()["window"]
    dumps = tempfile.TemporaryDirectory(prefix="fg-pixels-") if check_pixels else None
    env = dict(os.environ, NS_FRAMEGEN="1", NS_HDR="1" if hdr else "0",
               NS_NR_SMALL="0")
    if dumps:
        assert hdr, "pixel check requires --hdr"
        env["NS_FG_DUMP"] = dumps.name
    worker = subprocess.Popen([str(ROOT / "native/nvngx.dll"), "--live"],
        cwd=ROOT / "native", env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    logs = []
    drain = threading.Thread(target=lambda: logs.extend(iter(worker.stderr.readline, b"")), daemon=True)
    drain.start()
    timeout = threading.Timer(60, worker.kill)
    timeout.start()
    try:
        worker.stdin.write(struct.pack(wire.HEADER_FMT, wire.VIDEO_MAGIC,
            work_w, work_h, 1, 0, 0, 0, 1, 0, 0, 1., 1., 1., -1., w, h))
        worker.stdin.flush()
        if hdr:
            wire.send_wgc(worker, hwnd)
            ack = struct.unpack(wire.WGC_ACK_FMT, reply(worker.stdout, struct.calcsize(wire.WGC_ACK_FMT)))
            assert ack[0] == wire.WGC_ACK_MAGIC and ack[1] == 1, ack
        wire.send_window(worker, w, h)
        ack = struct.unpack(wire.WINDOW_ACK_FMT, reply(worker.stdout, struct.calcsize(wire.WINDOW_ACK_FMT)))
        assert ack[0] == wire.WINDOW_ACK_MAGIC and ack[1] == 1, ack
        motion = np.zeros((work_h, work_w, 2), dtype=np.float16)
        motion[:, :, 0] = -4 * work_w / w
        total = 420 if dynamic else 240
        for i in range(total):
            started = time.monotonic()
            frame = np.full((h, w, 4), (20, 30, 40, 255), dtype=np.uint8)
            x = 80 + (i % 80) * 4
            frame[80:280, x:x+120, :3] = (220, 100, 50)
            frame[15:65, 500:620, :3] = 255  # static HDR white patch
            bypass = True if fg_only else (125 <= i < 130)
            flags = wire.FRAME_FLAG_WANT_PIXELS if bypass else 0
            if bypass:
                flags |= wire.FRAME_FLAG_BYPASS
            if dynamic or fg_only:
                multiplier = 2 if (fg_only or i < 140) else 3 if i < 270 else 4
                enabled = True if fg_only else (15 <= i < 400 or i >= 410)
                flags |= 0x800 | (0x100 if enabled else 0) | ((multiplier - 2) << 9)
            if hdr:
                pygame.event.pump()
                screen.blit(pygame.image.frombuffer(frame.tobytes(), (w, h), "RGBA"), (0, 0))
                pygame.display.flip()
                worker.stdin.write(struct.pack(wire.FRAME_FMT, wire.CAPTURE_MAGIC, i, 0, 0, i))
                worker.stdin.flush()
                prepared = struct.unpack(wire.OUT_FMT, reply(worker.stdout, struct.calcsize(wire.OUT_FMT)))
                assert prepared[1] == i and prepared[2] == 1
                flags |= wire.FRAME_FLAG_NO_COLOR | wire.FRAME_FLAG_PREPARED
            worker.stdin.write(struct.pack(wire.FRAME_FMT, wire.FRAME_MAGIC, i,
                                           int(i % 80 == 0 or i == 130), flags, i))
            if not hdr:
                worker.stdin.write(frame.tobytes())
            worker.stdin.write(motion.tobytes())
            worker.stdin.flush()
            ack = struct.unpack(wire.OUT_FMT, reply(worker.stdout, struct.calcsize(wire.OUT_FMT)))
            assert ack[0] == wire.OUT_MAGIC and ack[2] == 1, ack
            if ack[3]:
                output = exact(worker.stdout, ack[3])
                if bypass and not hdr:
                    assert output == frame.tobytes(), "bypass export changed"
            time.sleep(max(0, 1/60 - (time.monotonic() - started)))
        if hdr:
            wire.send_wgc(worker, 0)
            ack = struct.unpack(wire.WGC_ACK_FMT, reply(worker.stdout, struct.calcsize(wire.WGC_ACK_FMT)))
            assert ack[1] == 1, ack
            worker.stdin.write(struct.pack(wire.FRAME_FMT, wire.FRAME_MAGIC, total, 1,
                wire.FRAME_FLAG_BYPASS | wire.FRAME_FLAG_WANT_PIXELS, total))
            worker.stdin.write(frame.tobytes())
            worker.stdin.write(motion.tobytes())
            worker.stdin.flush()
            ack = struct.unpack(wire.OUT_FMT, reply(worker.stdout, struct.calcsize(wire.OUT_FMT)))
            assert ack[2] == 1 and ack[3] == frame.nbytes, ack
            assert exact(worker.stdout, ack[3]) == frame.tobytes(), "HDR to SDR export changed"
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
        print(log)
        if hdr:
            pygame.quit()
    assert worker.returncode == 0, worker.returncode
    if env.get("NS_PHASE") == "1":
        timings = re.findall(r"boundary fg: submit [\d.]+/[\d.]+ wait ([\d.]+)/[\d.]+ GPU ([\d.]+)/[\d.]+", log)
        assert timings and any(float(gpu) > 0 for _, gpu in timings), "FG GPU timings missing"
        assert "video memory after phase sample:" in log, "periodic VRAM budget missing"
    if fg_only:
        # NR off from the very first frame and FG on: this is the case that had
        # no lifecycle lines at all in a user's log (#104). The presenter must
        # start here, on the bypass path, not only after NR comes back.
        assert "[fg] Init_Ext -> 0x00000001" in log, \
            "FG never initialised on the bypass path: no [fg] Init_Ext"
        assert re.search(r"\[fg\] \d+x enabled at", log), \
            "the presenter never started: no '[fg] Nx enabled at' on the bypass path"
        assert "[fg] displayed" in log, \
            "the presenter never reported a rate: no '[fg] displayed' on the bypass path"
        assert "presenter failed" not in log and "Evaluate failed" not in log, log[-600:]
        rates = [float(x) for x in re.findall(r"\[fg\] displayed ([\d.]+) FPS", log)]
        assert rates, "no presenter rate with NR off and FG on"
        print(f"PASS: NR off + FG on -> {len(rates)} presenter reports, "
              f"max {max(rates):.1f} FPS; FG starts on the bypass path")
        return
    assert log.count("[fg] 2x enabled") >= 1, "FG never started"
    # The presenter must SURVIVE the bypass window, not be rebuilt after it.
    # The old assert counted two '2x enabled' lines and passed on a build where
    # the bypass path joined the presenter and rebuilt it afterwards - it was
    # counting the symptom (#104). With the fix the presenter keeps running:
    # the feature is created once and the bypass frames flow through it.
    assert log.count("[fg] Init_Ext") == 1, \
        f"the presenter was rebuilt ({log.count('[fg] Init_Ext')} inits): the bypass path still tears it down"
    assert log.count("[fg] 2x enabled") == 1, \
        f"the presenter was restarted after bypass ({log.count('[fg] 2x enabled')} starts)"
    if dynamic:
        assert "[fg] 3x enabled" in log and "[fg] 4x enabled" in log
        assert log.count("[fg] UI: off") >= 2 and log.count("[fg] 4x enabled") >= 2
        assert log.count("direct feature 18 ready") == 1, "UI settings restarted NR"
    rates = [float(x) for x in re.findall(r"\[fg\] displayed ([\d.]+) FPS", log)]
    assert rates and max(rates) > 75, rates
    assert "[fg] presenter failed" not in log and "[fg] Evaluate failed" not in log
    if hdr:
        assert "capture=FP16 scRGB" in log and "format=24" in log, "HDR path not exercised"
        assert "presentation=8-bit SDR" in log
    if dumps:
        checked = 0
        def decode(path):
            packed = np.fromfile(path, np.uint32).reshape(h, w)
            pq = np.stack([(packed >> shift) & 1023 for shift in (0, 10, 20)], 2) / 1023.
            q = pq ** (32 / 2523)
            return (np.maximum(q - 3424/4096, 0) / (2413/128 - 2392/128*q)) ** (16384/2610) / .008
        for real_path in Path(dumps.name).glob("fg-*-0.raw"):
            real = decode(real_path)
            white = real[25:55, 525:595].mean()
            assert white > 1.2, ("test must exercise HDR values above scRGB 1", white)
            for generated in real_path.parent.glob(real_path.name.replace("-0.raw", "-*.raw")):
                if generated == real_path:
                    continue
                frame = decode(generated)
                assert np.isfinite(frame).all()
                ratio = frame[25:55, 525:595].mean() / white
                assert .9 < ratio < 1.1, (generated.name, "HDR brightness flicker", ratio)
                assert np.mean(frame.max(2) < .0001) < .01, (generated.name, "black pixels")
                checked += 1
        assert checked >= (6 if dynamic else 1), checked
        dumps.cleanup()
        print(f"PASS: {checked} generated HDR10 buffers preserve bright whites; no black frames")
    print("PASS: paced DLSS-G output, bypass export, resume and clean shutdown", rates)


if __name__ == "__main__":
    if "--run" in sys.argv or "--hdr" in sys.argv or "--dynamic" in sys.argv \
            or "--fg-only" in sys.argv:
        run("--hdr" in sys.argv, "--dynamic" in sys.argv,
            "--check-pixels" in sys.argv, "--fg-only" in sys.argv)
    else:
        print("SKIP: opt-in DLSS-G/GPU test; pass --run")
