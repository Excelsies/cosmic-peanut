"""Drive the shell against the mock host in Playwright WebKit (dev-only check, not shipped).

    uv run python web/shell/dev/e2e_check.py [--shots DIR]

Asserts: connect + hello renders the library, first launch notice, selection with crossfade,
params panel sends `params`, keys, a fatal plugin error falls back, heartbeat is sent,
permission help, the file player (transport row, slider, keys, queue panel, 320x180 layout).
Also measures shell per-frame overhead in the page and saves screenshots.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_host import MockHost

BENCH_JS = """
async () => {
  const { createPluginHost } = await import('/shell/pluginHost.js');
  const box = document.createElement('div');
  document.body.append(box);
  const host = createPluginHost({
    container: box, onEvent() {}, visible: () => true,
    settings: { quality: 'auto', maxDpr: 2, fpsCap: 0, renderScaleMax: 1, reduceFlashing: true },
  });
  host.show({ key: 'bench/a', pageUrl: 'about:blank' }, {});
  await new Promise((r) => setTimeout(r, 300));
  const N = 5000;
  const bufs = Array.from({ length: N }, () => new ArrayBuffer(6496));
  let t0 = performance.now();
  for (let i = 0; i < N; i++) host.frame(bufs[i]);
  const single = (performance.now() - t0) / N;
  host.show({ key: 'bench/b', pageUrl: 'about:blank' }, {});
  await new Promise((r) => setTimeout(r, 300));
  const bufs2 = Array.from({ length: N }, () => new ArrayBuffer(6496));
  t0 = performance.now();
  for (let i = 0; i < N; i++) host.frame(bufs2[i]);
  const fade = (performance.now() - t0) / N;
  host.dispose();
  box.remove();
  return { single, fade };
}
"""


# Free pixels: between the top bar's controls and the player row, between the row and the bottom
# bar's own controls, and between the open queue drawer and the row (negative = overlap).
GAPS_JS = """
() => {
  const r = (e) => e.getBoundingClientRect();
  const row = r(document.querySelector('.player'));
  const kids = (sel, skip) => [...document.querySelector(sel).children].filter((c) => c !== skip && !c.hidden);
  const topBottom = Math.max(...kids('.topbar').map((c) => r(c).bottom));
  const mainTop = Math.min(...kids('.bottombar', document.querySelector('.player')).map((c) => r(c).top));
  const drawer = document.querySelector('#queue');
  return {
    aboveTop: Math.round(row.top - topBottom), belowRow: Math.round(mainTop - row.bottom),
    drawerAbove: drawer.hidden ? 0 : Math.round(row.top - r(drawer).bottom),
  };
}
"""


async def log(host: MockHost) -> list[dict[str, Any]]:
    return list(host.received)


def check(cond: bool, what: str) -> None:
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SystemExit(1)


async def wait_for(pred, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def wait_js(page: Page, expr: str, timeout: float = 5.0) -> None:
    """Poll a JS expression (wait_for_function needs eval, which the shell CSP forbids)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await page.evaluate(f"() => Boolean({expr})"):
            return
        await asyncio.sleep(0.05)
    raise SystemExit(f"FAIL timed out waiting for {expr}")


def sent(host: MockHost, type_: str) -> list[dict[str, Any]]:
    return [m for m in host.received if m.get("type") == type_]


async def run(shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    host = MockHost(0, 0)
    await host.start()
    try:
        async with async_playwright() as pw:
            browser = await pw.webkit.launch()
            page: Page = await browser.new_page(
                viewport={"width": 1280, "height": 760}, device_scale_factor=2
            )
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(host.url)

            # (the library renders its cards when opened, not at load: wait for hello instead)
            await page.wait_for_selector("#source option", state="attached")
            check(
                await page.locator("#notice").is_visible(), "first-launch photosensitivity notice"
            )
            await page.screenshot(path=str(shots / "01-notice.png"))
            await page.click("#notice button.primary")
            check(await wait_for(lambda: any(m.get("photosensitivityNoticeSeen") for m in sent(host, "settings"))),
                  "notice dismissal persisted via settings")  # fmt: skip
            await page.keyboard.press("l")
            await page.wait_for_selector("#library .card", state="attached")
            check(
                await page.locator("#library .card").count() == 2,
                "hello renders the library (2 cards, once opened)",
            )
            await page.keyboard.press("Escape")

            check(
                await wait_for(lambda: bool(sent(host, "select"))),
                "first visualizer selected and persisted",
            )
            check(
                await page.locator(".now-name").text_content() == "Pulse Bars",
                "now playing shows Pulse Bars",
            )
            check(
                await wait_for(lambda: len(sent(host, "heartbeat")) >= 2, 3),
                "heartbeat sent every 500 ms",
            )
            check(
                await wait_for(lambda: bool(sent(host, "perf")), 4), "plugin perf forwarded to host"
            )
            perf = sent(host, "perf")[-1]
            check(
                isinstance(perf.get("shellMs"), (int, float)),
                f"perf carries shellMs ({perf.get('shellMs')})",
            )
            check(await wait_for(lambda: bool(sent(host, "onsetSeen")), 4), "onsetSeen forwarded")
            iframe = page.locator("iframe.viz-frame")
            check(
                await iframe.get_attribute("sandbox") == "allow-scripts",
                "iframe sandbox is allow-scripts only",
            )
            await page.mouse.move(640, 380)
            await page.screenshot(path=str(shots / "02-player.png"))

            # --- switch with crossfade ---
            await page.keyboard.press("n")
            check(await wait_for(lambda: any(m.get("key") == "mock/orbit" for m in sent(host, "select"))),
                  "N selects the next visualizer")  # fmt: skip
            await wait_js(page, "document.querySelectorAll('iframe.viz-frame').length === 2")
            await wait_js(
                page,
                "[...document.querySelectorAll('iframe.viz-frame')].some(f => f.style.opacity === '1')"
                " && document.querySelectorAll('iframe.viz-frame').length === 2",
            )
            await page.wait_for_timeout(700)
            ops = await page.evaluate(
                "[...document.querySelectorAll('iframe.viz-frame')].map(f => getComputedStyle(f).opacity)"
            )
            check(
                len(ops) == 2 and all(0 < float(o) < 1 for o in ops),
                f"mid-crossfade both visible {ops}",
            )
            await wait_js(page, "document.querySelectorAll('iframe.viz-frame').length === 1", 4.0)
            check(True, "old iframe removed after 1.5 s crossfade")
            check(
                await page.locator(".now-name").text_content() == "Orbit", "now playing shows Orbit"
            )

            # --- params panel ---
            await page.mouse.move(600, 300)
            await page.click("button[title='Parameters']")
            await page.wait_for_selector("#params:not([hidden])")
            await page.locator("#param-hue").evaluate(
                "el => { el.value = '120'; el.dispatchEvent(new Event('input', { bubbles: true }));"
                " el.dispatchEvent(new Event('change', { bubbles: true })); }"
            )
            check(await wait_for(lambda: any(m.get("values", {}).get("hue") == 120 for m in sent(host, "params"))),
                  "params panel sends full params")  # fmt: skip
            p = [m for m in sent(host, "params") if m.get("values", {}).get("hue") == 120][-1]
            check(
                set(p["values"]) == {"hue", "shape", "tint", "crash"},
                f"params are the full set {p['values']}",
            )
            await page.screenshot(path=str(shots / "03-params.png"))
            await page.keyboard.press("Escape")
            check(await page.locator("#params").is_hidden(), "Esc closes the panel")

            # --- keys ---
            await page.keyboard.press("l")
            check(await page.locator("#library").is_visible(), "L opens library")
            await page.screenshot(path=str(shots / "04-library.png"))
            await page.keyboard.press("Escape")
            check(await page.locator("#library").is_hidden(), "Esc closes library")
            await page.keyboard.press("p")
            check(await page.locator("#hud").is_visible(), "P shows HUD")
            await host.broadcast({"type": "stats", "hostCpu": 4.2, "rssMb": 88, "analysisMsP50": 0.3,
                                  "captureToSendMsP95": 3.1, "droppedFrames": 0, "latencyMsP95": 28})  # fmt: skip
            await page.wait_for_timeout(1300)
            check(
                "4.2%" in (await page.locator("#hud").text_content() or ""), "HUD shows host stats"
            )
            await page.keyboard.press("f")
            await page.keyboard.press("t")
            acts = [m["action"] for m in sent(host, "window")]
            check(acts[-2:] == ["fullscreen", "floatOnTop"], f"F/T send window actions {acts}")
            await page.keyboard.press("Shift+N")
            check(
                await wait_for(lambda: sent(host, "select")[-1]["key"] == "mock/bars"),
                "Shift+N goes back",
            )
            await page.wait_for_timeout(1800)
            await page.keyboard.press("n")
            check(
                await wait_for(lambda: sent(host, "select")[-1]["key"] == "mock/orbit"),
                "N again to Orbit",
            )
            await wait_js(page, "document.querySelectorAll('iframe.viz-frame').length === 1", 5.0)

            # --- idle fade ---
            await page.wait_for_timeout(3300)
            check(await page.evaluate("document.getElementById('app').classList.contains('idle')"),
                  "overlays fade after 3 s idle")  # fmt: skip
            await page.screenshot(path=str(shots / "05-idle.png"))
            await page.mouse.move(100, 100)
            await page.mouse.move(120, 120)
            is_idle = "document.getElementById('app').classList.contains('idle')"
            if await page.evaluate(is_idle):
                # Container WebKit (Linux, headless) delivers no mouse events to the page once the
                # shell is idle (same on main), so the mouse can't be checked there: say so, and
                # check that a key brings the overlays back instead.
                print(
                    "SKIP mouse movement brings overlays back (no mouse events once idle)",
                    flush=True,
                )
                await page.keyboard.press("Shift")
            check(not await page.evaluate(is_idle), "input brings overlays back")

            # --- fatal error falls back ---
            await page.click("button[title='Parameters']")
            await page.click("#param-crash")  # panel re-renders on fallback
            check(await wait_for(lambda: any(m.get("fatal") for m in sent(host, "pluginError"))),
                  "fatal plugin error reported to host")  # fmt: skip
            check(await wait_for(lambda: sent(host, "select")[-1]["key"] == "mock/bars"),
                  "fell back to the previous visualizer")  # fmt: skip
            await page.wait_for_selector("#error:not([hidden])")
            txt = await page.locator("#error").text_content() or ""
            check(
                "fake_plugin.js:42: Crash requested" in txt,
                "error overlay shows file:line: message",
            )
            await page.keyboard.press("Escape")
            await page.screenshot(path=str(shots / "06-fatal.png"))

            # --- permission help ---
            await host.broadcast({"type": "silence", "silent": True, "seconds": 4})
            await page.wait_for_selector("#permission:not([hidden])")
            await page.click("#permission button")
            check(
                await wait_for(lambda: bool(sent(host, "openPermissions"))),
                "permission button sends openPermissions",
            )

            # --- install prompt ---
            await page.keyboard.press("l")
            await page.fill("#add-url", "https://github.com/x/y")
            await page.keyboard.press("Enter")
            await page.wait_for_selector("#install:not([hidden])")
            await page.screenshot(path=str(shots / "07-install.png"))
            await page.click("#install button.primary")
            check(await wait_for(lambda: any(m.get("accept") for m in sent(host, "installConfirm"))),
                  "trust prompt confirm sends installConfirm")  # fmt: skip

            # --- file player (the mock's pretend "Files" source) ---
            await page.keyboard.press("Escape")
            check(
                await page.locator(".player").count() == 0,
                "no player row while the source has no transport",
            )
            await page.select_option("#source", "player")
            await page.wait_for_selector(".player .player-title:not([hidden])")
            check(
                await page.locator(".player-title").text_content() == "Music Is Math",
                "Files source shows the now-playing title",
            )
            check(
                await page.locator(".player-band").text_content() == "Boards of Canada"
                and await page.locator(".player-album").text_content() == "Geogaddi",
                "band and album shown",
            )
            # Space is a focused button's own: take focus off whatever the earlier steps left it on
            await page.evaluate("document.activeElement && document.activeElement.blur()")
            await page.keyboard.press("Space")
            check(await wait_for(lambda: any(m.get("action") == "toggle" for m in sent(host, "transport"))),
                  "Space sends transport toggle")  # fmt: skip
            await wait_js(
                page,
                "document.querySelector('.player-play').getAttribute('aria-label') === 'Pause'",
            )
            first = await page.locator(".player-elapsed").text_content()
            await wait_js(
                page, f"document.querySelector('.player-elapsed').textContent !== '{first}'", 3.0
            )
            check(True, "elapsed time advances while playing")
            before = len(sent(host, "transport"))
            await page.keyboard.press("ArrowRight")
            check(await wait_for(lambda: any(m.get("action") == "seek" and m.get("value", 0) >= 5
                                             for m in sent(host, "transport")[before:])),
                  "Right sends a seek of +5 s")  # fmt: skip
            await page.keyboard.press(".")
            await wait_js(
                page,
                "document.querySelector('.player-title').textContent === '07 - field recording'",
            )
            check(
                await page.locator(".player-band").is_hidden(),
                "an untagged file shows its title only",
            )
            await page.screenshot(path=str(shots / "08-player.png"))

            # the slider: drag = display only, release = one seek
            before = len([m for m in sent(host, "transport") if m.get("action") == "seek"])
            await page.locator(".player-seek").evaluate(
                "el => { for (const v of [30, 60, 90]) { el.value = v;"
                " el.dispatchEvent(new Event('input', { bubbles: true })); } }"
            )
            await page.wait_for_timeout(300)
            seeks = [m for m in sent(host, "transport") if m.get("action") == "seek"]
            check(len(seeks) == before, "dragging the slider sends nothing")
            await page.locator(".player-seek").evaluate(
                "el => el.dispatchEvent(new Event('change', { bubbles: true }))"
            )
            check(await wait_for(lambda: len([m for m in sent(host, "transport") if m.get("action") == "seek"]) == before + 1),
                  "releasing sends exactly one seek")  # fmt: skip

            await wait_js(
                page, "document.querySelector('.player-elapsed').textContent.startsWith('1:3')"
            )
            check(True, "the host's nowPlaying after the seek confirms the position (1:3x)")

            # the queue panel
            await page.keyboard.press("q")
            await page.wait_for_selector("#queue:not([hidden]) .queue-list li")
            check(
                await page.locator("#queue .queue-list li").count() == 12,
                "queue panel lists the 12 entries",
            )
            check(
                await page.locator("#queue button[aria-current='true'] .q-title").text_content()
                == "07 - field recording",
                "current entry marked",
            )
            check(
                await page.locator("#queue li.is-unplayable button").get_attribute("aria-disabled")
                == "true",
                "unplayable entry is disabled",
            )
            check(
                await page.locator("#queue .queue-list img, #queue .queue-list b").count() == 0
                and "<img src=x" in (await page.locator("#queue .queue-list").text_content() or ""),
                "markup in tags is shown as text",
            )
            await page.screenshot(path=str(shots / "09-queue.png"))
            await page.locator("#queue .queue-list li").nth(5).locator("button").click()
            check(await wait_for(lambda: any(m.get("index") == 5 for m in sent(host, "playerSelect"))),
                  "clicking an entry sends playerSelect")  # fmt: skip
            await page.click("#queue .q-clear")
            check(
                await wait_for(lambda: bool(sent(host, "playerClear"))), "Clear sends playerClear"
            )
            await page.keyboard.press("Escape")
            await page.wait_for_selector(".player-open:not([hidden])")
            check(
                await page.locator(".player-controls").is_hidden()
                and await page.locator(".player-open-folder").is_visible(),
                "empty queue: just Open files… and Open folder…, no dead controls",
            )
            await page.click(".player-open")
            check(
                await wait_for(
                    lambda: any(
                        m.get("kind") == "files" and m.get("replace") is True
                        for m in sent(host, "playerOpen")
                    )
                ),
                "Open files… sends playerOpen",
            )
            await page.wait_for_selector(".player-controls:not([hidden])")

            # a 320x180 window (the minimum): look at 10-small.png for overlap
            for w, h in ((480, 270), (320, 180)):
                await page.set_viewport_size({"width": w, "height": h})
                await page.keyboard.press("Shift")  # keep the overlays up
                await page.wait_for_timeout(400)
                gap = await page.evaluate(GAPS_JS)
                check(
                    gap["aboveTop"] >= 0 and gap["belowRow"] >= 0,
                    f"{w}x{h}: the player row sits between the top controls and the bottom ones {gap}",
                )
                await page.keyboard.press("q")
                await page.wait_for_timeout(400)
                gap = await page.evaluate(GAPS_JS)
                check(gap["drawerAbove"] >= 0, f"{w}x{h}: the open queue ends above the row {gap}")
                await page.keyboard.press("Escape")
            # a bigger root font leaves no room at 320x180: the row goes away, the keys stay
            big_font = await page.add_style_tag(content="html{font-size:18px !important}")
            await page.keyboard.press("Shift")
            await page.wait_for_timeout(500)
            check(
                await page.evaluate("getComputedStyle(document.querySelector('.player')).display")
                == "none",
                "320x180 at 18 px: no room, so no row",
            )
            before = len(sent(host, "transport"))
            await page.evaluate("document.activeElement && document.activeElement.blur()")
            await page.keyboard.press("Space")
            check(await wait_for(lambda: len(sent(host, "transport")) > before),
                  "the keys still work without the row")  # fmt: skip
            await big_font.evaluate("el => el.remove()")
            await page.wait_for_timeout(300)
            await page.set_viewport_size({"width": 320, "height": 180})
            await page.keyboard.press("Shift")
            await page.wait_for_timeout(300)
            check(
                await page.locator(".player").get_attribute("data-collapse") == "3",
                "narrow window drops album, band and time labels",
            )
            await page.screenshot(path=str(shots / "10-small.png"))
            await page.set_viewport_size({"width": 1280, "height": 760})

            await page.select_option("#source", "system")
            await wait_js(page, "document.querySelectorAll('.player').length === 0")
            check(True, "switching to a source without a transport removes the player")

            # --- overhead ---
            bench = await page.evaluate(BENCH_JS)
            print(f"shell frame() cost: {bench['single'] * 1000:.1f} µs/frame, "
                  f"{bench['fade'] * 1000:.1f} µs/frame during crossfade", flush=True)  # fmt: skip
            check(bench["fade"] < 1.0, "shell overhead < 1 ms per frame")

            real_errors = [e for e in errors if "favicon" not in e]
            check(not real_errors, f"no page errors {real_errors}")
            await browser.close()
    finally:
        await host.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, default=Path("/tmp/tidalviz-shell-shots"))
    asyncio.run(run(ap.parse_args().shots))
