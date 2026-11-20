# Shell dev tools (not shipped)

- `mock_host.py` — fake host: serves the shell + `/config.json` + control WebSocket, a fake
  registry of two visualizers whose pages live on a second port (`fake_plugin.js` answers the
  §5 handshake, draws the bands, reports `ready`/`perf`/`onsetSeen`), and 94 Hz synthetic
  binary v1 frames. `uv run python web/shell/dev/mock_host.py` and open the printed URL.
  Test hooks: `GET /_log` (messages the shell sent), `POST /_send` (broadcast a host message).
  The Orbit visualizer's "Crash (test)" param makes the fake plugin report a fatal error.
- `e2e_check.py` — drives the shell against the mock in Playwright WebKit and asserts the main
  flows; measures `pluginHost.frame()` cost. `uv run python web/shell/dev/e2e_check.py --shots DIR`.
- `mock_player.py` — the mock's pretend "Files" source (a queue, a clock, the transport rules of
  protocols.md §4; no audio). Pick **Files** in the source menu, or start with
  `mock_host.py --source player [--tracks 2000]`. Tags "arrive" a few per 250 ms after an open, so the
  queue is re-sent while the panel is open. `tests/test_shell_mock_player.py` holds it to protocols.md §4
  (it runs with the normal suite).
- `queue_bench.mjs` — times parsing and applying a 2000-entry `queue` message
  (`node web/shell/dev/queue_bench.mjs`, run from `web/` so happy-dom resolves).
  When the window is too short for the player row, the row is hidden and files are opened from the queue panel (`q`).
