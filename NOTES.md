# Notes: decisions and measurements

Newest first within each milestone. Numbers are from the dev machine unless stated
(Apple M4 Pro, macOS 26.6) — the PRD's reference machine is an M1 MacBook Air.

## Performance pass (2026-10-11)

Fork only. Fewer machine resources at the same picture: each change is pixel-identical in the
render A/B (Playwright WebKit on Mesa llvmpipe, 2560×1440 stills, against the tree before the pass)
or moves at most 1/255 on a few pixels, as stated. Same Linux machine as above; "audit" marks an
estimate from the audit, not a measurement here.

**Host**
- **The control WebSocket declines permessage-deflate.** WebKitGTK offered it and aiohttp
  accepted, so every 29 KB frame was zlib-compressed in an executor thread (audit: ~2–3 points of
  host CPU, ~350–450 wakeups/s). Tests and `tools.cpu_budget` now make the same offer, at the real
  29,024-byte frame size.
- **Capture: `stdbuf -o0 pw-record --latency 512`.** `pw-record` writes with `fwrite()` to glibc's
  fully buffered stdout, which is why 512 delivered two blocks at once (above); at 256 every block
  arrived one 5.3 ms cycle late. Unbuffered, each cycle is one `write()`: half the graph rate forced
  on every client of the sink (187.5 → 93.75 Hz) and ~5.3 ms less audio-to-screen latency. Without
  `stdbuf`: 256 and one warning. Emulated with a ctypes `fwrite` writer, not measured live.
- **Digital silence.** A frame equal to the last one apart from `frameIndex`/`hostTime` goes out
  at most once a second (`KEEPALIVE_S`): 5 s of synthetic silence, 5 frames for 377 hops.
  `resume()` forces the next frame out, so a reloaded shell gets one. The analyzer skips window,
  FFT and sums once the window and the two before it are all zero (bit-identical; ~40 µs/hop at
  fft 2048, ~45–50 at 4096), and released bands below 1e-9 snap to 0 (exact zeros 6 s in, not
  2e-44 subnormals).
- **Analysis allocates nothing per hop** (~10 KB → ~1.1 KB transient; the tracemalloc bound goes
  from 16 KB to 2 KB). About −15 to −20 µs/hop paced; scalars move ≤ 3.8e-6 (summation order),
  bands and spectrum are bit-identical.
- `OPENBLAS_NUM_THREADS` defaults to 1 before numpy loads (`app.py`, `tools/`): 24 → 1 threads
  (audit: ~1.7 CPU-s per launch). dulwich and urllib3 load on the first git fetch:
  `tidalviz.plugins.git` import 25.5 → 3.8 ms (audit: ~6–7 MB RSS).
- Paused, the analysis thread sleeps on an Event: 48 → ≤ 2 voluntary switches over 40 blocks. The
  demo source renders its 8 s loop once: 78 → 4 µs per block (first loop bit-identical; after it the
  bass and pad partials restart their phase on the bar line).
- Dev-folder watcher step 10 → 50 ms, only while a dev folder is registered: idle wakeups ~100/s →
  20/s; save-to-callback p50 11 / max 19 ms → p50 50 / max 100 ms (budget 300).
- Static files carry ETag/Last-Modified and answer 304 (`FileResponse`). `/lib/three/<version>/`
  (now in the import map) and, outside `--dev`, `/sdk` are `immutable`; the rest `no-cache`; dev
  folders stay `no-store`.
- `pw-dump` runs off the loop, once per `hello` and `setSource`; the latest `setSource` wins. A
  saved `app:<pid>` source whose app has quit falls back to system audio with a warning. The hub
  carries a dropped frame's onset flag onto the frame that replaces it.
- `tools/thread_profile.py` (Linux): per-thread CPU and wakeups for tidalviz, its WebKit processes,
  Hyprland and PipeWire, plus main-thread UI runs/s (synthetic 60 Hz / 4 ms load: 60.0 runs/s,
  p50 4.01 ms). The HUD now says host CPU includes WebKit UI work.

**Shell and SDK**
- **Throttle click: the shell decides.** Active iframe only, 15–35 fps and
  < 0.8 × min(60, `fpsCap`), sent as `perf.throttled`; the host clicks only on that. WebKit holds a
  never-clicked iframe near 20 fps on macOS but ~30 on Linux (Playwright's WebKit: ~31), so the
  first band, 15–25, never clicked here. Battery's 30 fps cap or a 30 Hz monitor no longer takes the
  pointer; a plugin GPU-bound below 15 fps is deliberately not clicked.
- Hidden page: the 10 s ready timeout counts only while visible (fresh 10 s on return); auto-cycle
  stops. The GTK host sends the monitor's `refreshHz` (macOS: 0, unchanged); under 55 Hz the fps cap
  follows it. Auto/High send `maxDpr` 0 and the SDK re-sizes on a resolution change. Params go live
  on input and are saved on change.
- The HUD renders on data, not at 4 Hz; no devlog DOM outside dev mode; non-fatal plugin errors
  capped at 5/s per plugin; closed panels render on open; the pointer path allocates nothing.
- **`depth: false`** (manifest, webgl2/three, default true) on Stargate, Radar, Dark Sun, Eclipse,
  Marbling, Tetraballs, Cosmic Peanut and the template. Undertow, Blaze, Cascade, Tie-Dye, Skull
  Trip and Laminar keep the depth buffer: llvmpipe shades their final pass up to 1 LSB differently
  when the target has no depth attachment, even with the depth test off. VRAM saving not measured.

**Visualizers** (bit-identical unless a delta is given)
- Orbit, Tentacube: `OutputPass.needsSwap = false`, so the composer never allocates its second
  full-res HalfFloat target (~44 MB at 1440p); bloom off at Bloom 0; Orbit's ring in one pass and
  pillar matrices built once. Tentacube draws its background after the creature (early-Z).
- Blaze: RG16F/R16F sim targets on the half-float path and uniforms set once (238 → 148 GL
  calls/frame, by the commit). Cascade: R16F mist. Radar: one-channel phosphor (R16F, ~12.6 MB less
  traffic per frame and VRAM). Laminar: RG16F velocity, 262 → 199 calls/frame. Eclipse: 41 → 36.
  Cosmic Peanut: 37 → 24.
- ALU skipped where the result can't change: Eclipse corona trees and cloud mask, Marbling drag
  cull, Dark Sun range, Tie-Dye knot noise, Skull Trip jaw (exact `min()` bound) and the stripes
  under the opaque skull, Laminar's reflection outside the ball, Tetraballs' volume pass scissored
  to the sphere (proven by a float32 brute-force test). Bars and Marbling skip frames that would
  redraw the same image (Marbling: ~80% of frames in silence); `plugin-api.md` now says an undrawn
  frame keeps the last image, which the fps cap already relied on.
- Cosmic Peanut: Fine lines hold a 1×1 soft texture (−14.7 MB at DPR 1); plain Soft lines at 1:1
  draw straight to the canvas (no 14.7 MB texture, no copy); instanced `LINE_STRIP`s replace the
  vertex and index buffers (−1 to −1.6 MB). The README gives GPU memory per DPR.
- ≤ 1/255: Undertow's hue matrix on the CPU (≤ 0.006% of pixels), Stargate `pow` → multiplies
  (≤ 0.012%), Tie-Dye CPU absorbance (≤ 0.002%), Skull Trip CPU uniforms (≤ 0.014%), Eclipse and
  Marbling `pow` skips (≤ 6 px), Cosmic Peanut's removed varying (1–2 px).
- **Tried and dropped (changed pixels):** Blaze analytic curl noise (the sim diverges, 7–21% of
  pixels); Undertow's atan-free swirl (1/255 on up to 0.73%); Tentacube's fold sign test (1–2
  mirror-line pixels per still); Pulsar's cached row paths (~128 AA edge pixels); Radar's decay
  clock (trails up to 5/255 brighter, cause not found); Skull Trip's teeth skip (up to 5/255); the
  MSAA → canvas resolve in Cosmic Peanut (no safe probe). three 0.186.1 ships no min build, so
  three.js stays unminified.

**Render A/B of the merged tree** (all 18 visualizers, drums and music, stills 30/90/150): Bars,
Orbit, Pulsar, Blaze, Cascade, Radar, Tentacube, Dark Sun, Tetraballs, Halo and Cosmic Peanut are
bit-identical; Laminar shares a bit-identical outcome on every still (RUNS=3). The rest move at
most 1/255: Undertow ≤ 0.006% of pixels, Stargate ≤ 0.012%, Tie-Dye ≤ 0.002%, Skull Trip ≤ 0.014%,
Eclipse ≤ 6 px, Marbling 1 px.

Known gap: no live per-process CPU/GPU measurement of the merged app yet; everything above is unit
tests, emulation and llvmpipe renders, not the RTX 4090 or macOS. Soft lines with antialias at
DPR 1 (Cosmic Peanut) is still ~44.7 MB, over the PRD's 10 MB.

## Background and visual opacity (2026-10-02)

Two persisted settings, `backgroundOpacity` (0–1, default 0) and `visualOpacity` (0.1–1, default
1), live only while *Transparent background* is on.

- **Two sliders, and the visual's goes on a `.viz-layer` wrapper.** *Transparent background* only
  removes the black backdrop, but most visualizers paint nearly every pixel, so the window stays
  opaque. The backdrop alpha sits on the stage background. The visual opacity sits on a new
  `.viz-layer` around the plugin iframes, not on `.stage` (that would fade the backdrop too) and not
  on each iframe (the plugin host animates their inline `opacity` for the crossfade).
- **Fade live on `input`, save on `change`.** Each `settings` message rewrites settings.json.
- Visual stops at 0.1 so the visual can fade but never vanish; Background has no floor.

Known gap: not run on macOS, and the GPU cost of the extra compositing layer below opacity 1 is
unmeasured.

## Linux support (2026-10-02)

**Machine (not the M4 Pro above):** Linux 7.2 (CachyOS), i9-12900KF, RTX 4090 (driver 615.71),
PipeWire 1.6, WebKitGTK 2.52, Hyprland 0.56 on Wayland, 2560×1440 @ 60 Hz.

**Capture** (`capture/pipewire_source.py`): `pw-record --raw` on a pipe, 512-frame blocks.
`--latency 256` gives one block per 10.7 ms; 512 delivers two at once (a dropped hop). Per-app pids
come from `application.process.id`, else `pipewire.sec.pid`. A per-app target is pinned with
`node.dont-reconnect`: otherwise WirePlumber re-links `pw-record` to the microphone when the app's
stream ends. The watchdog marks the source `failed` when `pw-record` delivers no data for 1 s (4 s
before the first block, for a sink waking from suspend); silent audio still streams zero blocks.

| Measure (pink noise via `speaker-test`) | Result | Budget / macOS note |
| --- | --- | --- |
| Block interval, 5 s | p1/p50/p99 10.61 / 10.67 / 10.72 ms | 10.67 ms ideal |
| Headless host CPU, 60 s, `tools.cpu_budget` | **2.4%** (analysis 1.4, main 0.6, reader 0.2) | < 10% (macOS 6.6%) |
| Orbit bench, fullscreen 2560×1440 | 58.5–59.5 fps, p99 33 ms, 0.9–2.9% dropped | 60 fps, p99 20 ms |
| Audio-to-screen p95 (same runs) | 31.4–31.7 ms | 50 ms |
| App with window, Orbit, 1440p (% of one core) | `tidalviz` **30–33%**, WebProcess 10% | **over the budget** (macOS 13.8%) |

- **Windowed host CPU is 30–33%, over the 10% budget, and not fixed here.** The GTK main thread is
  25% of it. `py-spy record --native` shows no Tidalviz Python on the hot path, only GTK/GDK and
  NVIDIA's EGL-Wayland libraries; what wakes it ~92 times a second isn't established.
- **Crash on NVIDIA + native Wayland, and the fix.** The app died ~1 s after launch (`Gdk-Message:
  Error 71 (Protocol error)`). One variable at a time, 12 s launches (exit 124 = survived):

  | Variant | Exit | Error 71 |
  | --- | --- | --- |
  | baseline | 1, dead in ~1 s | yes |
  | `WEBKIT_DISABLE_DMABUF_RENDERER=1` | 124 | 0 |
  | `__NV_DISABLE_EXPLICIT_SYNC=1` | 124 | 0 |
  | `GDK_BACKEND=x11` | 124 | 0 |

  `window_gtk.configure_gl_environment()` sets `__NV_DISABLE_EXPLICIT_SYNC=1` before GTK starts when
  `/proc/driver/nvidia/version` exists; a value the user set wins (`=0` brings the crash back).
- **Known gap: WebKitWebProcess segfaults on exit with the NVIDIA driver** (a core dump each
  time): `libwebkit2gtk` destructors call into `libnvidia-eglcore` after the window has gone. The
  app itself exits normally. Not fixed here.
- Not measured: switching the default output mid-stream, Flatpak apps.
- **Tried and dropped:** `WEBKIT_DISABLE_DMABUF_RENDERER=1` copies every frame through shared
  memory (Orbit 51.6 fps, WebKitWebProcess at 94.5% CPU, against 59.5 fps and 10.3% CPU with DMA-BUF
  kept); a Qt backend (WebEngine is Chromium, 524 MB); PyGObject as a default dependency (a bare
  `uv sync` would fail without headers), hence the `gtk` extra.

## Visualizer batch: Tentacube, Tie-Dye, Skull Trip, Dark Sun, Eclipse, Marbling, Laminar, Tetraballs (2026-09-28)

Built in parallel worktrees and merged one at a time. GPU times were measured on the M4 Pro at
2560×1440 while up to seven agents were rendering, so treat them as rough upper bounds (the
reference M1 Air is roughly 3–4× slower).

| Visualizer | Technique | GPU (M4 Pro, loaded) |
| --- | --- | --- |
| Tentacube | three.js; verlet tentacle chains (CPU, ~0.05 ms); material morph; Poincaré-disk tiling | ≤ 2.2 ms |
| Tie-Dye | half-res dye pass + full-res weave composite | 1.6–1.9 ms |
| Skull Trip | raymarched SDF skull (half res) over AA op-art stripes | 2.0–2.2 ms |
| Dark Sun | quarter-res watercolour washes + full-res sun/horizon; mirrored spectrum range | ~0.9 ms |
| Eclipse | procedural branching corona trees (noise gave worms/specks); third-res clouds | ~2.3 ms |
| Marbling | Jaffer drop/tine displacement, baked feedback with a 48-event queue | 1.4–2 ms |
| Laminar | stable-fluids sim (MacCormack, obstacle, confinement in the wake cone) | 3–3.6 ms |
| Tetraballs | raymarched metaballs; 9 materials (thin-film, Charlie sheen, dispersion…) | chrome ≤ 2.5; fire 3.5–10; smoke 4–9 |

- **Fire/smoke in Tetraballs likely miss 60 fps on an M1 Air at medium quality**; the low
  quality setting and auto render scale reduce them. To verify on the reference machine.
- **Photosensitivity:** every visualizer routes global brightness swings through the flash
  limiter. Skull Trip's check counts 4 changes/s at a 2% luminance threshold but 0 at 5%; WCAG's
  general-flash threshold is ~10% opposing changes, so it isn't a flash by that definition.
- **Harness:** the melody signal produced NaNs before t = 0 (negative modulo) and the drums kick
  peaked at ~30 Hz; both fixed. Headless WebKit paints a transparent page's unstyled iframe
  white, so backdrop colour in harness screenshots isn't reliable.
- Merging: parallel agents each appended a manifest entry and a test; a conflict can split a
  function in two, so the manifest test file is rebuilt from both sides rather than
  "keep both".

## Host CPU (2026-09-27)

Measured with `uv run python -m tools.cpu_budget` (headless host, WebSocket client in a separate
process so its CPU isn't counted) and an in-process sampler for the app with its window.

| Setup | Host process CPU |
| --- | --- |
| Headless, live TIDAL, before this work | ~7–8.6% (the earlier "~15%" probe counted its in-process client) |
| Headless, live TIDAL, after | **6.6%** — analysis 2.8, main/asyncio 1.5, catap drain 1.3, catap worker 0.8 |
| App with window, live TIDAL, after | 13.8% |
| App with window, live TIDAL, before (old code) | 23.3% |

- **Our code meets the < 10% budget.** The remaining ~7 points in the app are WKWebView's UI
  process, which lives in the host process and handles WebKit's per-frame layer commits and IPC
  at 60 fps. Getting the combined number under 10% would mean running the host in a separate
  process from the window — not done; it would meet the budget's letter, not its intent.
- **catap drain pacing** (`capture/catap_drain.py`): catap 0.6 polls its native ring every 1 ms
  (~1,000 wakeups/s for ~94 chunks). A wrapper around the private
  `AudioRecorder._drain_native_recorder` sleeps until just before the next chunk is due. It's
  guarded (no method ⇒ warning, catap untouched) and catap is pinned `~=0.6.0` so an upgrade is a
  deliberate step. This was most of the window-app improvement.
- Analysis: fewer, fused numpy calls per hop; planar mirrored ring with a zero-copy view of the
  newest frames; float32 spectrum after the float64 FFT. Golden-value tests pinned the outputs
  first. Analysis p50 ≈ 0.20–0.23 ms live.
- Tried and dropped: a lock-as-doorbell instead of the ring's Condition — no measurable gain.

## Integration — app, bench, e2e (2026-09-26)

- **Plugins were stuck at 20 fps in the app.** The shell's iframes ignore the pointer (so the
  shell sees the mouse), so the native click that lifts WebKit's cross-origin rAF throttle never
  reached the plugin. Now, while the active plugin reports < 40 fps, its iframe accepts the
  pointer; the shell then takes keyboard focus back. The fix is occasionally flaky in the real
  window (one bench run stayed at 22 fps); the host re-clicks every 2 s.
- **No 120 Hz WebKit preference.** Turning off `PreferPageRenderingUpdatesNear60FPSEnabled` made
  WKWebView pace frames irregularly: Orbit 48 fps / 26% dropped vs 59.9 fps / 0.56% at the default.
- **Bench, Orbit, live TIDAL audio, 2560×1440 canvas (M4 Pro, window alone):** 59.9 fps, 0.7%
  dropped, frame p99 37 ms (budget 20), audio-to-screen p95 ~33–57 ms (budget 50), analysis p50
  0.19 ms, capture→send p95 0.7 ms, **host CPU ~20% (budget 10%)**. Two app windows at once push
  latency to ~500 ms — measure alone.
- **Host CPU breakdown (headless probe):** analysis thread ~7% (Analyzer 0.46–0.54 ms thread CPU
  per hop live vs 44 µs in a hot loop: per-call numpy overhead on cold caches; `AnalysisContext.load`
  alone is 0.18 ms), catap capture workers ~6%, event loop ~2%. QoS user-interactive doesn't help.
  Reaching 10% needs far fewer numpy calls per hop (batching extractors) — open decision.
- **Frame contract:** waveform is now 2,048 samples per channel (Cosmic Peanut): mono 12,640 B,
  stereo 29,024 B.

## M1/M2 components — parallel lanes (2026-09-25)

Built in parallel worktrees (audio, host, plugins, sdk, shell, viz, spike) against the M0
contracts, then merged. The app entry point that wires them together is the next step.

**M1 spike: WKWebView (tools/spike/REPORT.md).** The architecture holds, with two changes:
- WebKit throttles rAF in a cross-origin iframe to **20 Hz until the user interacts with it**
  (no preference turns this off). One synthetic NSEvent click into the window after each plugin
  is ready lifts it. Host send → first iframe rAF: 47 ms p95 without, **17 ms p95 at 60 Hz and
  9 ms at 120 Hz** with it. Estimated audio-to-screen ≈ 33–49 ms, inside the 50 ms budget.
- The plugin iframe **shares the shell's WebContent process and main thread**: `while(true){}`
  freezes the shell. Heartbeat detection works; recovery needs
  `_killWebContentProcessAndResetState()` + `reload()` (plain reload does nothing).
- WebGL2 and WebGPU both work in the sandboxed iframe. rAF is 60 Hz on ProMotion unless
  `PreferPageRenderingUpdatesNear60FPSEnabled` is turned off. `performance.now()` has 1 ms
  resolution in WKWebView. On-top windows ignore the first fullscreen toggle.
- Site isolation (private `SiteIsolationEnabled` + plugin served from `localhost`) gives the
  plugin its own process so a hang doesn't freeze the shell — an M2 experiment, flag is unstable.

**Audio.** Analysis p50 44 µs / p99 124 µs in the benchmark; live on the real pipeline with TIDAL
p50 ≈ 0.5 ms, p99 0.77–0.91 ms (cores idle down between hops; the analysis thread uses Mach
real-time scheduling, without it p99 ≈ 1.8 ms). Capture → publish p95 0.9 ms. Zero per-frame
allocation (tracemalloc test). Onsets: every click detected once, 0.67 ms error. Tempo locks in
2.9 s at 120 ± 0.2 BPM. Reserved scalar 13 is now **`onsetAge`** (sub-hop onset timing).
Known gap: tempo can land an octave off on ambiguous material.

**Host.** Loopback p95 0.5 ms for 10.6 KB frames at 94 Hz, 100% delivered. The bootstrap page's
CSP carries a per-response nonce (the §3 CSP as first written blocked its own inline scripts —
the spike found the same). Paths reject hidden segments; dev harnesses and `*.test.js` are not
served.

**Plugins.** Dev-folder save → callback ≈ 30 ms. Real HTTPS fetch of a small GitHub repo ≈ 5.5 s.
Keys: built-ins use their folder name, dev folders `dev-<slug>-<hash8>`.

**SDK / shell.** SDK overhead is below WKWebView's 1 ms timer (µs-level in micro-benchmarks);
shell forwarding ≈ 3–20 µs per frame. Frames are forwarded by transfer.

**Viz.** At 2560×1440 in WebKit: Bars 0.1/0.2 ms, Undertow ≤0.05/0.1 ms (+~0.7 ms GPU), Orbit
0.1/0.5 ms (+~0.8 ms GPU), Template ≤0.05/0.1 ms CPU p50/p99. Vendored three 0.186.1 is 6.6 MB.

## M0 — contracts (2026-09-25)

- **Frame v1 channels are planar** (mono, left, right) instead of interleaved, so every waveform
  view is zero-copy. Mono 6,496 B, stereo 10,592 B. See docs/protocols.md §1.
- **Shell and SDK are plain ES modules with JSDoc types**, type-checked by `tsc --checkJs`. The PRD
  asks for TypeScript built at build time *and* a checkout that runs with only uv; shipping source
  that needs no build satisfies both, with no committed build output to drift. Node is dev-only.
- **aiohttp serves HTTP and the WebSocket** on one event loop (the PRD allowed aiohttp for HTTP and
  named `websockets` for WS); one dependency instead of two.
- **Manifest `fallback`** is the `id` of another entry in the same manifest.
- **catap live check:** system capture delivers 48 kHz stereo float32, 512-frame buffers
  (~94 callbacks/s) with real signal from TIDAL — the hop matches catap's buffer size.
