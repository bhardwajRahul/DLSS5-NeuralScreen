# Unused neural passes: implementation and validation

Baseline: `1400ededd74c92d35e994a27acb79d62241c79e4` (v2.0.1).

The worker retains the requested pass count but allocates only passes that can
execute. Boost off therefore keeps one feature even when the saved count is
2–4. Both parameter updates and resource rebuilds use this rule. Reducing the
count retires GPU work before releasing surplus features. Active handles and
their temporal history stay intact. A failed retirement stops processing and
leaves resources alive for process teardown. Re-enabling released passes creates
them again.

`NS_PHASE=1` now reports `boundary fg` GPU timestamps and CPU fence-wait time
separately, plus periodic local-memory usage and DXGI budget. It reuses existing
waits and the existing two-second reporting cadence. GPU timestamps surround FG
evaluation and its disable-status copy. The CPU wait covers completion of the
whole submission, including the later real-frame and export copies.

## Measured memory

Hardware: RTX 4080 SUPER, driver 616.92, 16,376 MiB reported by `nvidia-smi`.
Baseline and patch were built with the same existing MSVC build script and ran
sequentially with the same NVIDIA runtime DLLs. Each used 2560×1440, Boost off,
saved pass count 2, four warm-up evaluations, 80 identical input frames, zero
motion vectors, and request pacing capped at 60 FPS. Image parameters were
style 1, auto-mask 1, intensity 1, local tone 0.5, local structure 1, skin structure
−1. The FG comparison kept ×2 enabled at 2560×1440.

Windows GPU Process Memory counters sampled each test worker three times after
warm-up, while its resources remained allocated. All three samples agreed.

| Configuration | Baseline dedicated | Patched dedicated | Reduction |
|---|---:|---:|---:|
| Neural pass isolated, FG off | 1,225.34 MiB | 729.11 MiB | 496.23 MiB |
| Neural pass with FG ×2 | 1,861.07 MiB | 1,364.84 MiB | 496.23 MiB |

Shared GPU memory was 360.84 → 219.96 MiB without FG and 383.64 → 242.77 MiB
with FG. These counters are separate from dedicated VRAM.

All 80 enhanced real-frame outputs were byte-identical between baseline and
patch in **each** comparison. Generated frames were exercised by the FG smoke
test but were not compared byte-for-byte. Both comparison workers completed 84
direct evaluations, including warm-up. No GPU resource failures were reported.

At 1440p with FG ×2, the patched profiler reported interval means of
3.917–3.940 ms FG GPU time and 4.953–5.257 ms CPU fence-wait time. Its DXGI local
usage was 1,356 MiB against budgets of 9,815–9,830 MiB at those report times.
DXGI budgets varied during the run. The baseline lacks periodic budget reports.

These synthetic comparisons demonstrate lower resource use and unchanged real
output for the tested inputs. Pipe transfers, readbacks, foreground changes,
and other active GPU applications prevent a controlled throughput comparison.
The 60 FPS setting was preserved, not established as achieved performance.
There is no claim of reduced total GPU utilization, improved game FPS, or fixed
stutter. Game frame-time and visual gameplay comparisons remain unverified.

## Live Control session

A subsequent 20-second sample with Control DX12 running measured NeuralScreen
at 1,458.01 MiB dedicated GPU memory in all 20 samples. The earlier stock-worker
reading was 1,954.23 MiB, a difference of 496.22 MiB (25.4% of the worker's
dedicated allocation). The active patched worker was verified by its hash and
`asked=2 effective=1 allocated=1` log. Resolution remained 2560×1440, Boost off,
60 FPS cap, and FG ×2. The configuration file hash was unchanged after installation.

Control's own dedicated allocation averaged 10,967.48 MiB during this sample,
with a range of 10,864.73–11,093.77 MiB. The before/after readings were not taken
at a matched scene or time. They corroborate the controlled VRAM measurement,
but do not establish a reduction in game memory, GPU utilization, or frame times.

## Checks completed

- Baseline and patched native release builds succeeded.
- New `test_nr_pass_resources.py` runs the production reconciler with controlled
  creation and fence failures. It covers shrink, re-enable, active-handle
  preservation, partial creation, and failed retirement without unsafe release.
- Its opt-in `--gpu` check passed Boost-off saved counts 1–4, Boost-on multiple
  passes, 4→1, re-enable, both resize paths, and unchanged full-size pixels.
- Pass-wire, command-resource reuse, runner reporting (10 cases), and frame-pacing
  checks passed.
- `test_frame_generation.py --run` passed with profiling enabled and disabled,
  covering ×2, bypass, resume, and clean shutdown. The enabled run asserted
  nonzero FG GPU timings and periodic memory/budget reports.
- `git diff --check` passed. Final review kept the patch to pass ownership and
  existing diagnostics, without new production dependencies or settings.

Local comparison harness and raw JSON/log evidence are under
`_work/measure_pass_resources.py` and `_work/validation/` in this worktree.
The separate FG smoke logs are `_work/fg-profile-on.log` and
`_work/fg-profile-off.log`. These local artifacts are ignored by Git.

Patched worker SHA-256:
`7467FC5A4A6096D2371513F2E25B083120F735457AC72D8C7314DE4BFDC17C65`

Rebuilt baseline worker SHA-256:
`C93E8D430F071B546710B1511952B133EFB728D9CBFBB831D923F08D33C87637`

Stock release worker SHA-256:
`C53FC21DF21FFDDE0200F17A017134F189D53753146E6A79ACDD414091377638`
