# Changelog

Release history of NeuralScreen, reconstructed from the repository itself: the
Git tags of `perseval-BLR/NeuralScreen`, the published release bodies
(`gh release view <tag> --json body`) and the assets each release carries
(`gh release view <tag> --json assets`). Dates are the GitHub **publish** dates
in UTC, not the tag timestamps.

Scope and honesty notes:

* The list starts at `v1.0.0` and ends at `v1.15.1`; every tag in that range has a
  published GitHub release (35 releases in total, `gh release list`). The tag
  `v0.1.0-alpha` (2026-09-06) is **not** part of this history: it is a tag from the
  other project line that shares this repository's history (the AMD fork), not a
  NeuralScreen version. It is excluded rather than guessed at. The tag
  `pre-state-refactor` (2026-09-11) is an internal marker commit with no release
  and is not listed either.
* `v1.3.1-win10-diag` is a diagnostic-only tag, not a version. It is listed at
  the bottom for completeness.
* Each entry is a summary of what the published release notes said. Where the
  notes were written for a single audience or a single machine, the entry keeps
  that focus instead of inventing broader claims.
* v1.5.1's body also documents v1.5.2's contents (the UX package was first
  attached to that tag and then given its own release); v1.5.2's own entry is
  taken from its own body.
* No entry is invented: every bullet is traceable to the release body of that
  tag, and the asset list per release is the same eight-file set unless noted
  (the early releases carried a different set - see the individual entries).

---

## v1.15.1 - 2026-09-19 - the readings on the status line, and the guard that tells us why

* The status line dropped the numbers people watch: it laid values out from the
  right edge in reverse and silently discarded whatever ran out of room, so on 4K
  with a real card name it showed `SKIP 0 3840x2160` and no rates at all. One row
  again - state and card left, readings anchored right, FG at the edge. The
  resolution, the skip count and the frame counter left the line by decision.
* The first hotkey press in a game did nothing: the polling fallback took its
  baseline on the poller's first sample, so the first press after launch was
  recorded as "already down" and swallowed (6 of 6 runs). The baseline now comes
  from registration.
* The z-order guard now logs what it saw - class, title, pid and rect of the
  window that took the top, and the branch it took (`hud-on-top`,
  `picture-above-hud`, `foreign-above-hud`, `nothing-covers`), including the
  healthy case - which is what made #96 and #89 undiagnosable.
* Build scripts no longer hardcode the author's `BuildTools` path: six scripts
  resolve the toolchain through `vswhere`, in one shared `vcvars.bat` that checks
  its result (contributed by @HyperRamzey, with a clang-cl path as well).
* Suite: 173 checks, 170 PASS / 0 FAIL / 3 SKIP, GUI-E2E 9/9.

## v1.15.0 - 2026-09-18 - washed-out colours, the taskbar menu, and the FG readout

* Washed-out colours with HDR off on a 10-bit display (#99): the FP16 capture
  format was treated as scRGB by itself, so white landed at 187 instead of 255.
  `isFloat` and `hdr` are separate facts now; only a real scRGB capture is
  tone-mapped. Covered on WARP: 255 with the flag, ~187 without.
* The panel showed a Frame Generation multiplier that was not running (#100):
  after a refused x4 the worker steps down to x2 while the buttons kept showing
  the pick. The panel now reports the step the presenter really runs.
* A minimised foreign window opened the menu (#96): Windows activates the 1x1
  taskbar window as a fallback and it arrives as the same message as a click.
  Now distinguished by the state before the event plus the minimised window.
* The z-order guard saw no window on a left-hand monitor (#89): the rect was
  tested against the primary screen, where a window on a left monitor has a
  negative x. The rule intersects the virtual desktop now.
* Every NGX failure code is named in the log - `0xBAD0000C` is `FAIL_OutOfDate`
  (an older driver), which had been reaching user logs as `?`.
* Suite: 171 checks, 168 PASS / 0 FAIL / 3 SKIP, GUI-E2E 9/9.

## v1.14.0 - 2026-09-17 - sixteen post-release audit fixes

* The capture stops rebuilding itself on a high-colour display: a v1.13.1 log
  showed the duplicated desktop alternating FP16/BGRA8 1955 times in 133 s
  (`grab 43.0ms` against `NR 18.3 fps`). A high-colour display now asks for FP16
  first through `DuplicateOutput1`, BGRA8 stays the fallback, and it drops to
  `grab 3.0ms` / `NR 48.6 fps`.
* A failed resize no longer answers "ok": the handler fell through into
  `CreateFeature` and wrote a success reply after a failure, leaving a half-built
  state the client believed was applied.
* The mode-switch veil always comes down now (#89, #96 - reported twice as "the
  window is invisible").
* Four ways the panel fought the user: the keyboard stayed captured after the
  menu closed, a drag outlived the menu, the drop-down opened out of the
  viewport, and a click on a hint hit the row.
* A hand-edited `config.json` no longer aborts the launch (26 hostile values are
  normalised or refused by field name); the runtime is hashed once (184 ms -> 1 ms);
  the save dialog opens in an existing folder; eleven strings were translated.
* Suite: 166 checks, 163 PASS / 0 FAIL / 3 SKIP, GUI-E2E 9/9. This release also
  found checks that could not fail - four tests that always printed SKIP.

## v1.13.1 - 2026-09-16 - the hotfix release

* Four fixes reported in the first hours of v1.13.0.
* The menu no longer hides behind the picture (#94): a restarted worker re-asserts
  its picture window as topmost, and the recovery path used `SWP_NOZORDER`, a flag
  that made `SetWindowPos` ignore the position. The menu is re-asserted as topmost
  itself, every frame while it is open.
* Only the control reacts, not the whole row: every toggle, slider and choice row
  reacted anywhere on the panel, and clicking a caption restarted the worker. Each
  row carries an explicit hit zone that is the control.
* The focus outline follows the keyboard rather than the mouse, and the taskbar
  activation path stopped hiding an already visible menu.
* NVOFA became the default motion backend.

## v1.13.0 - 2026-09-16 - a real OFF, the compatibility gate, and a verified release

* A real OFF: with NR and FG off and nothing recording, capture and presentation
  close, no frames are sent and the overlay hides (#75). NR back on rearms every
  channel from a fresh frame; FG, recording and screenshots keep their own bypass.
* A frame cap on the active loop: 30 / 60 / custom 15-240 / unlimited, on a
  monotonic deadline, and the panel stops conflating the NR rate, the FG presenter
  cadence and the skip counter (#89).
* The compatibility gate runs first: one short-lived worker, three 640x360 frames,
  and only a clean pass unlocks the overlay - a failure blocks it with a named
  reason and a timed quarantine instead of a restart loop (#71, #83). The verdict
  is cached per exact configuration; `unknown`, `not run` and an unexpected SKIP
  can never become a PASS.
* A verified release: `config.default.json` is tracked and the user's
  `config.json` is generated and ignored; `build_release_zip.py` refuses a dirty
  tree, a wrong tag or a mismatched manifest and builds byte-identical archives
  per tag; the set ships `SHA256SUMS`, `runtime-manifest.json` and
  `THIRD-PARTY-NOTICES.md`. The manifest pinned 1352 payload and 1299 runtime files.
* Full keyboard navigation, and the README labels each GPU family validated /
  reported / unverified / known failure. 148 checks on HEAD, 0 FAIL, GUI-E2E 9/9.

## v1.12.0 - 2026-09-15 - capture reliability and safe recovery

* SDR flicker on 10-bit displays fixed: Desktop Duplication requests one stable
  BGRA8 format instead of alternating BGRA8/FP16 (#86).
* Multi-GPU monitor routing fixed: every menu display resolves to its real
  `(adapter, output)` pair, and an incompatible pair falls back to Python frame
  transfer instead of silently showing adapter output 0 (#88).
* Bounded capture recovery: three consecutive failures, then a 30-second cooldown
  instead of an endless restart loop. WGC initialises WinRT before its support
  query and keeps resize recovery on the frame-pool path.
* GPU failures are distinct diagnostic stages with one terminal state, and a
  resize honours fence failures instead of freeing feature resources after a
  failed wait.
* Recording and screenshots: 192 kHz WASAPI is resampled to AAC 48 kHz, AAC is
  probed before the MP4 is opened, and screenshots freeze the frame before Save As
  so the dialog cannot appear in the image (#89). Taskbar activation is idempotent
  (#87).
* 132 of 133 checks passed; the remaining one exposed a WGC resize race that was
  fixed before the tag.

## v1.11.1 - 2026-09-15 - the shipped config default, and the multiplier unlocks

* The 1.11.0 archive carried a maintainer's working config: `frame_multiplier` 3
  (an RTX 40 card caps at x2), SR-merge leftovers in the sliders and two dead
  `dlss_sr` keys. The defaults are the product defaults again - multiplier 2, FG
  off, CPU motion, Natural's four sliders.
* A new static check ("config: the shipped defaults") compares the committed
  `config.default.json` against the profile-derived defaults, which the
  zip-vs-HEAD comparison can never catch; it flagged the old values immediately.
* The multiplier group is selectable while Frame Generation is off, so the way out
  of a refused multiplier is no longer locked behind the switch that caused it.
* 128 checks green on the release commit.

## v1.11.0 - 2026-09-15 - FG truth, wider sliders

* The blink over the picture in window mode is fixed: the z-order guard took the
  top of the stack at face value and re-asserted the pair for invisible IME /
  MSCTFIME / ForegroundStaging / DWM helper windows - 10,339 pairs in one session.
  The guard walks to the first visible window that can cover the layer, and
  "foreign" is decided by HWND, not class.
* The Frame Generation switch follows reality: when FG cannot start it now flips
  back off with an 8-second notice instead of staying ON with nothing
  interpolating; Num7 toggles FG from anywhere.
* Sliders reach further (tone and structure to 2.0, skin structure to 2.5 -
  measured on the live runtime first); intensity stays 1.0 because the runtime
  clamps above it. A built-in profile moves the four sliders only.
* The drag stutter ends: the `dragging` gate now covers the title-bar drag, edge
  scale and grip resize.

## v1.10.0 - 2026-09-14 - DLSS 4.5 FG, NVOFA, BYO libraries, perf series

* DLSS 4.5 Frame Generation after neural rendering, opt-in, with an x2/x3/x4
  multiplier. The depth is flat and the motion is estimated, so UI and text can
  distort - stated openly as the cost of the approach.
* NVIDIA Optical Flow (NVOFA) as an opt-in motion backend for the driver's
  optical flow instead of CPU DIS, with an automatic fallback to CPU and one alert.
* The HUD pairs both rates ("network / presenter") instead of showing the
  network-only counter that read as broken with FG on.
* The library auto-updater is removed and the app no longer downloads anything:
  both NVIDIA runtimes ship in the archive, and a `native/libraries/` DLL wins over
  the bundled copy. DLSS Super Resolution from the 1.9.x cycle was removed after
  the live test (it reads as smear on desktop captures).
* Performance: the recorded-frame ring ends a 33 MB-per-frame allocation
  (13.2 -> 2.4 ms), bypass no longer computes an unread motion field (2.9 ms/frame).

## v1.9.0 - 2026-09-14 - models switch, the overlay reworked, a week of window-mode bugs closed

* Model switch: three networks with three different outputs rather than three
  strengths. Measured fine detail against the untouched frame on a desktop
  capture: Default +18.7%, Natural -11.4%, Cinematic -23.4%.
* The menu redesigned: four tabs instead of one long scroll, one status line
  instead of a readings grid, every parameter slider shows its scale, and About
  says what an issue report has to carry to be solvable.
* The parameter ranges are measured, not guessed: intensity used to be offered to
  2.5 while the DLL clamps at 1.0, and a test now sweeps every knob and names the
  dead ones.
* The one-window overlay, five bugs closed as one series: a magenta fallback
  screen, the desktop cut-out dropped twice a second while the menu was open, a
  stale surround, a trail of copies when the window shrank, and a shimmer whenever
  the program's UI sat above the picture.
* A colour-format flip no longer tears the capture down; an Infinity in a preset
  is reported as a broken file instead of being silently clamped.

## v1.8.2 - 2026-09-13 - Boost on by default, and three silent failures named

* Boost is on out of the box: on a 5070 Ti at 4K it is 45.7 -> 72.6 frames for a
  picture indistinguishable at 1:1, because the network's result is composited
  onto the native frame.
* Every profile's Local tone is half a point lower - the slider was lifting
  shadows more than the picture wanted, most of all on dark scenes.
* The overlay invisible with the effect firing for a second at a time (#61):
  something else in the worker's process printed into its stdout, which carries
  the binary protocol. The protocol now has a private handle.
* A black screen at 10-bit colour depth (#58): one FP16 frame was read as "HDR"
  off the format alone, putting the picture on the scRGB path with HDR off.
* Also: alerts appear at the top of the screen instead of inside the captured
  window, "the window cannot be captured" stopped repeating every half second, a
  180° flipped display is turned back over (#47), the Spout bridge builds its
  device on the card the worker runs on, and a window resize no longer leaves a
  hole in a recording.
* A window resize now reconfigures the worker instead of replacing it:
  1.845 s -> 0.112 s, with no veil, because nothing dies.

## v1.8.1 - 2026-09-13 - faster pipeline + fixes

* The screen flickered on every cursor move (#58): 1.8.0 put a `SetColorSpace1`
  call into the ordinary present path; it runs only with HDR compatibility on now.
  Reported on an ASUS ROG STRIX G16 with an external monitor.
* The processed picture could sit behind a focused fullscreen game while the HUD
  stayed on top - the picture is raised first, the HUD last (thanks to @tzachbon, #52).
* Dead links in `TECHNICAL.md` / `TECHNICAL.ru.md` and the HDR links in both
  READMEs, broken in every unpacked copy, fixed.
* Moving a slider no longer rebuilds the neural feature (113-148 ms of frozen
  picture, measured at 0 ms after), a mode switch is half as long (1.845 -> 0.972 s),
  and there is one fence wait per frame instead of three.

## v1.8.0 - 2026-09-12 - experimental HDR support, off until you turn it on

* HDR compatibility, off by default (Num2 -> the gear -> CAPTURE): the screen is
  duplicated in FP16 scRGB, the network is handed a tone-mapped SDR copy, and its
  edit returns as a bounded linear residual, so highlights above white survive.
* This is not native HDR inference - the runtime still works on an SDR proxy, and
  a zero edit reproduces the original exactly. The arithmetic and limits are in
  `docs/HDR.md`.
* Contributed by @saffd96 (#36): capture, shaders, presentation and a WARP test
  for all of it.
* Known limits: screenshots, recording and Spout output stay tone-mapped 8-bit
  SDR, and Windows HDR has to be on as well.

## v1.7.1 - 2026-09-12 - hotfix: a video-memory leak and two ways the picture could never appear

* The card filled up: every slider step released the neural feature against the
  NGX core while it had been created by `nvngx_dlssnr.dll`, leaking video memory -
  on a 16 GB card, half a minute of sliding, and it took other applications down
  with it (#48). The worker now reports its own video memory after every build.
* The network and the capture could run on different cards: `NS_GPU` is a DXGI
  adapter index, the neural side treated it as a wish and the capture took it
  literally - on a hybrid laptop adapter 0 is the iGPU, and nothing appeared.
* A second monitor could stay blank until you switched away and back: one
  auto-reset event served every GPU wait, so a wait could be woken by somebody
  else's completion.
* Also: a hand-edited config can no longer take the program down silently, a crash
  brings its traceback to the log, HDR is reported once per session, a card that
  cannot run the pass is marked in the picker, autostart's registry entry is
  documented, and the README came down to 158 lines.

## v1.7.0 - 2026-09-12 - Boost: up to 80% more frames for the same picture

* Boost: the network is capped at 2560x1440 and until now it ran at the full frame
  size wherever the slider stood - the output was bit-identical at every position.
  The network now runs at the work resolution and its edit is composited onto the
  untouched native frame, so text and edges stay 1:1. Off by default at that point.
* The neural calls move to a module of their own, `nvngx.dll_ns-forwarder.dll`:
  `nvngx_dlssnr.dll` decides by looking at the module a call returns to, so the
  worker no longer has to *be* an executable named `nvngx.dll`.
* Fixed: saved presets never reached the disk; a card that cannot run the pass
  stopped being retried forever; a revive brought the long warm-up back; a manual
  revive left the automatic one armed; the GPU alert only fired with the menu open.
* The test suite grew from 67 checks to 92.

## v1.6.1 - 2026-09-11 - the second monitor fixes, idle screens stop burning the GPU

* The overlay was created at (0, 0) of the virtual desktop while the capture
  followed the chosen monitor, so on a second monitor the program ran, logged
  frames, answered hotkeys and showed nothing (#28, #33, #35).
* An idle screen no longer costs a card: when nothing changed, the network ran
  over the previous frame anyway; it now answers "nothing changed" and the menu
  shows `idle`.
* Fixed: the Spout2 switch looked off while working; a GPU that cannot run the
  network could lock the program on it; the resolution slider promised 3840x2160
  at its top step though the network caps at 2560x1440; the theme fell back to
  light after a monitor switch; minimised windows are listed again.
* Typography split by role (IBM Plex Sans for language, IBM Plex Mono for
  readings), with the faces shipped so it looks the same on any Windows install.

## v1.6.0 - 2026-09-11 - Win 10 confirmed, OBS output, 1 or 2 GPU picker, Save As fixed

* OBS output as a switch (settings, RECORDING): Spout2 on or off, no more
  `NS_SPOUT=1` before launch; toggling restarts the worker.
* An experimental GPU picker for two-NVIDIA-card machines, moving the network and
  the capture together (#29); `NS_GPU=<index>` does the same without the menu.
* Windows 10 confirmed by a user on an RTX 4060 (#30), with the one-window rebuild
  loop they hit fixed.
* "Save as" never opened for anyone - an error was caught and swallowed and the
  dialog fell back to the screenshots folder.
* Changing the desktop resolution left the program on the old one; the archive
  shipped without its icons in 1.5.6/1.5.7 - both fixed.

## v1.5.7 - 2026-09-11 - the icons ship, screenshots on any path

* Screenshots were silently lost on non-ASCII paths: OpenCV's writer returns True
  and writes nothing outside ASCII, so Python writes the file now.
* The archive shipped without its icons (issue #28) - both are back and the
  release check fails the build if they go missing again.
* A redrawn icon: the logo alone, round, with more contrast and a white rim, every
  size rendered on its own.
* Adaptive exposure could produce NaN when `NS_PW_DARK` and `NS_PW_LIT` were
  equal, returning a black frame - guarded.
* `main.py` was taken apart: 3788 lines to 940, the state went into one object
  with `__slots__`, and a test now forbids importing `main` back. 54 checks green.

## v1.5.6 - 2026-09-10 - adaptive exposure, GDI fallback, recording fix

* Adaptive exposure: dark scenes are brightened for the network automatically (the
  PaperWhite principle), on by default, `NS_PW=0` disables.
* GDI capture fallback on Optimus laptops where the display is wired to the iGPU
  (`DXGI_ERROR_UNSUPPORTED`), confirmed on an Acer Nitro with an RTX 4050 (#26).
* Recording dropped every second frame - the slot is reserved by `needs_frame()`
  now, taking a 5-second recording from ~75 to ~150 frames.
* A crash on a monitor switch (issues #24/#26): a stale dxcam output index raised
  `IndexError`; the capture validates and falls back to output 0 with an alert.
* The smoke and GUI cycle checks never ran in the full suite; 52 tests green.

## v1.5.5 - 2026-09-10 - user presets, recording indicator, screenshot folder

* User presets: save a snapshot of the four sliders under a name, delete it, and
  it applies like a built-in profile - with broken entries dropped and a config
  pointing at a deleted preset falling back to Natural.
* A recording indicator: a red dot with a timer in the screen corner, hidden from
  the recorded file, toggleable in settings.
* A remembered screenshot folder, so Save As opens there every time (#20).
* UI: the resolution slider got its own section, expanded lists became a pop-up
  layer with hover highlights, and silent failure points gained alerts.
* Under the hood: a swappable runtime (`nr_dll` / `NS_NR_DLL`), noise-floor motion
  vectors zeroed, and an environment header in the log. 49 tests green.

## v1.5.4 - 2026-09-10 - stability: 8 code-review fixes, remap fixes, auto-recovery

* Remapping no longer auto-executes the key you press, and Num4/Num6 became
  remappable - all eight hotkeys are listed, in all 12 languages.
* NVENC codec fallback AV1 -> HEVC -> H.264, probed at open time, so recording
  works on RTX 30 (no AV1 encoder); frame demand is gated to one frame per 30 fps.
* Atomic config writes (temp + fsync + atomic replace), and the menu layout save
  also persists the profile, the NR parameters and the monitor.
* The saved monitor is remembered by DXGI DeviceName instead of a positional
  index, so a cable unplug or dock does not move the capture to another screen.
* NGX result 0x00000000 ("no frame this call") is no longer treated as a crash -
  it used to restart the worker three times and turn NR off (issue #11). Fence
  hardening, and one automatic revive after a 30 s backoff for transient failures.

## v1.5.3 - 2026-09-10 - UI hotfix: text clipping, language list scroll, CJK names

* Long localised strings clip with an ellipsis instead of painting over the
  neighbours; the French UI was the worst offender. French wording was shortened
  and the record button shows a short "Stop" while recording (all 12 languages).
* The language list scrolls, is bounded by the panel and opens upward when there
  is no room below - the last languages were unreachable before.
* CJK names (中文, 日本語, 한국어) render with per-script fonts instead of boxes.
* Menu position is fixed: the saved offset is honoured across mode switches, and
  the restore applies only to a recreated window.

## v1.5.2 - 2026-09-10 - UX package, 12 languages

* 12 languages (EN, RU, FR, DE, ES, IT, PT, PL, UK, ZH, JA, KO) behind a drop-down
  switcher, with CJK fonts shipped so they do not render as tofu boxes.
* A recording audio limiter: the system mix can peak above 0 dBFS (measured up to
  +7.9 dB) and AAC clipped it - a soft tanh limiter above 0.9 folds peaks toward
  1.0, verified as +7.9 dBFS -> 0.0 dBFS and 2103 clipped samples -> 0.
* Menu rework: live indicators on the main page only, WORK became MODE, the View
  section became Actions, and the footer holds only Quit.
* Default profile is Natural, and new tests cover every control, the limiter and
  i18n parity.

## v1.5.1 - 2026-09-09 - Hotfix: RTX 30/40 restored (and the UX package, now v1.5.2)

* The v1.4.1+ builds regressed every non-Blackwell card: the architecture spoof
  target moved from `0x1B0` to `0x1A0`, and the leaked runtimes refuse feature 18
  below `0x1B0`. Back to the v1.3.0 stack - universal runtime (310.8.0,
  sm_75/86/89/120) with spoof `0x1B0` - so RTX 30 and RTX 40 work again.
* `[arch]` lines now always reach the log and print the real spoof value, and the
  archive carries a `VERSION.txt` with the commit, the runtime SHA-256 and the
  kernel architectures.
* v1.5.0 was released twice under the same tag (first a broken community patch,
  then a Blackwell-only runtime): this release is a single verifiable archive,
  stated in the notes.
* The tag also carried the v1.5.2 UX package while it was being tested, which then
  got its own release.

## v1.5.0 - 2026-09-09 - RTX 40 support

* RTX 40-series support: the bundled `nvngx_dlssnr.dll` is a community
  re-targeted 310.8.0 build carrying sm_89 and sm_120 kernels, so it runs on Ada
  and Blackwell. Previous builds died on Ada with `0xBAD00001` on frame 0
  (issues #8, #10, #11, #12).
* A window picker page that outlines the real window in amber on hover and lists
  only real taskbar windows.
* A visible window-mode exit in the footer, and the capture mode shown under the
  GPU line.
* Spout2 bridge behind `NS_SPOUT=1` (OBS records the NR picture through the Spout2
  Capture plugin), off by default; DRED diagnostics for device removal; recording
  at 30 fps instead of 60; safe passthrough when feature 18 cannot be created.
* Known limitations: no HDR, no hybrid-graphics laptops without a MUX switch.

## v1.4.2 - 2026-09-09 - Window picker page (testing build)

* Marked a pre-release for testing only: experimental changes needing real-world
  validation.
* The window picker page: hover outlines the real window on screen, clicking
  switches the capture; only real taskbar windows are listed.
* A visible window-mode exit from the footer ("already active" alert in fullscreen
  mode), and the capture mode shown under the GPU line.
* Screenshots in the README: light/dark main page, the window list, the settings
  page.

## v1.4.1 - 2026-09-09 - Spout2 bridge (testing build)

* Marked a pre-release for testing only.
* The Spout2 bridge behind `NS_SPOUT=1`, so OBS with the Spout2 Capture plugin can
  record the NR picture directly (off by default).
* DRED device-removed diagnostics in the log instead of a bare "code 6".
* Recording at 30 fps instead of 60 (about 36% -> 18% of the frame cost).
* Correct GPU architecture IDs (Turing 0x160, Ampere 0x170), a pre-Blackwell warm-up
  clamp, a hardened hotkey parser, safe passthrough, and picking a window raises it.

## v1.4.0 - 2026-09-09 - No flash, mode-switch overlay

* No flash on startup or mode switches: both overlay windows are created hidden and
  revealed with the first real frame.
* A mode-switch overlay: a brief semi-transparent blur with a spinner covers a
  Num5 or monitor change, and the picture comes back sharp.
* The menu is no longer clipped across a one-window-mode switch.
* Release-archive hygiene: the zip carries exactly what the program needs, with no
  tests or dev tools, and includes NVIDIA's runtime.
* Worker fixes (residual descriptor cache on resize, a GRAY resource leak, a stale
  sized picture, a 4 s DWM flicker from a topmost re-assert on IME windows), and a
  22-scenario suite. The body also lists four post-release fixes added under the
  same tag, ending with DRED diagnostics.

## v1.3.1-win10-diag - 2026-09-08 - diagnostic-only tag (not a version)

* Not a NeuralScreen version: a diagnostic build for issue #1 (the Windows 10
  "code 6" death). The worker logs every upload sub-step with a `[pure]` prefix,
  and this build exists to be run, reproduce the crash and have
  `NeuralScreen.log` attached. Release body is 594 characters, the shortest of any
  release.

## v1.3.0 - 2026-09-08 - One-window mode, WGC capture, numpad keys

* One-window mode (Num5): the network runs on the focused window and the overlay
  sits on it, following moves and resizes.
* The NVIDIA App and OBS can record the result: in one-window mode the overlay
  stops hiding from capture; full-screen mode keeps hiding it.
* Hotkeys moved to the numpad (Num1 NR, Num2 menu, Num3 screenshot, Num0 record,
  Num5 one-window, Num4/Num6 resolution, Ctrl+Alt+Q quit), Num Lock on.
* Odd-height and fast windows (140+ FPS) record correctly and the capture bridge
  cleans up on failure.
* Post-release updates under the same tag: the matched residual composite
  (47.9 -> 71.9 FPS at work_scale 0.65 with the native anchor intact), a sharp
  resolution slider, the menu re-placed across a window-mode switch, the version in
  the header, and the ~4 s flicker removed.

## v1.2.1 - 2026-09-08 - Fixes: resolution slider, hotkey polling

* One control for the processing resolution instead of a button and a slider that
  could disagree; its top step is the whole screen, and it still switches live.
* Hotkeys work in games that take the keyboard: a polling fallback runs alongside
  `RegisterHotKey`, and defaults moved off keys games use (F10 NR, F11 menu, Home
  screenshot).
* Recording went back onto shared memory - a size check was rejecting 4K frames and
  they quietly went down the pipe; a 5-second recording went 105 -> 118 frames.
* Stability: a monitor resolution change rebuilds the capture chain, the
  architecture hook no longer answers for a second card or an iGPU, and stopping a
  recording is clean.

## v1.2.0 - 2026-09-07 - Sound in recordings, reduced resolution

* Sound in recordings: `Insert` writes system audio as a second AAC track (WASAPI
  loopback, 192 kbit/s stereo, no virtual cable), padded from the same clock as the
  video so pauses do not shorten the track.
* Processing at reduced resolution: a slider whose top step is the full screen
  (default, best picture) and whose lower steps feed the network a smaller frame -
  42.9 -> 65.3 FPS on a 4K desktop, `Evaluate` 16.05 -> 7.25 ms. Off by default.
* Correction to v1.1.0: the network is same-resolution, and its cost tracks the
  pixels it is handed - `eval = 1.50 ms + 1.51 ms/MPix`.
* `NeuralScreen.exe`: an unsigned launcher with the program's icon (Windows warns
  about an unknown publisher once); `NeuralScreen.vbs` still works.

## v1.1.0 - 2026-09-07 - GPU status, wipe slider, arch hook

* A GPU status line in the menu: green means feature 18 was actually created, by
  the worker's answer rather than the architecture.
* A before/after wipe slider (raw vs NR, composed on the GPU, lands in recordings).
* The architecture hook is on by default (`NS_ARCH_SPOOF=0` disables): feature 18
  on 20/30/40-series, confirmed working on a 40-series card by a user, with a
  likely conflict with NVIDIA's licence terms stated openly.
* NGX evaluation scales with the screen and not with `work_scale` - 15.7 ms on a
  4K desktop, 47 FPS at 4K with NR on (RTX 5070 Ti).
* Native Save As for screenshots, monitor selection, configurable hotkeys, autostart
  with Windows, and a reworked menu.

## v1.0.0 - 2026-09-06 - First release

* Silent launch: `NeuralScreen.vbs` plus a bundled portable Python, no console
  windows.
* Built-in recording (Insert): MP4 AV1 NVENC, 60 fps, quality-targeted VBR, with a
  HUD and the @perseval_BLR watermark burned in and sRGB/BT.709 colour tags.
* A full GPU pipeline (DDA capture + NGX + the worker's window), 102 FPS on
  1440p-class desktops.
* Automatic monitor resolution detection and automatic recovery after display mode
  switches.
* Assets differ from later releases: the archive plus `nvngx.dll`,
  `nvngx_dlssnr.dll`, `README.md` and `README.ru.md`.
