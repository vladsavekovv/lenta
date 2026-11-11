"""Typing into a Settings field on a phone (1.26.1).

Vlad, 9 Nov 2026: "when I go Media pages in Settings > Playback on the phone and click the field to
change the name keyboard appears and immediately disappears I cannot change the name … It works on
the tablet."

The cause was not the field. On a phone, Settings shows its blocks in one column, and `layout.js`
redrew that column on every resize by taking every block out and putting it back (`cols.append`).
Taking a block out takes the focus out of any field inside it — and a phone's keyboard opening is
itself a resize. So the keyboard came up, the page got shorter, the block holding the field was
re-appended, the field lost focus and the keyboard went down again. At tablet width the blocks are
placed by position instead, which is why the tablet worked. It hit every text field in every Settings
block on a phone; the device name (1.25.0) was simply the first one he typed into there.

This test does what a person does: at phone size, tap a field, shrink the page as the keyboard does,
and type. It walks every Settings page and every visible text field in a block, as administrator,
in two themes. On 1.26.0 every such field loses its focus.
"""
import asyncio
import os
import subprocess
import sys

BASE = os.environ.get("LENTA_TEST_BASE", "http://127.0.0.1:8597")
PAGES = ["appearance", "home", "audio", "intros", "media-pages", "app", "account", "general", "users",
         "assistants", "network", "libraries", "library-sections", "scanning", "fix-matches",
         "metadata-sources", "artwork", "transcoding", "trailers", "subtitles", "previews", "livetv",
         "streaming", "logs"]
PHONE = {"width": 412, "height": 915}
KEYBOARD = 0.55            # the share of the screen left once a phone's keyboard is up

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


if BASE.endswith(":8597"):
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

FIELDS = """[...document.querySelectorAll('.panel input, .panel textarea')]
  .filter(i => ['text', 'search', 'password', 'number', 'url', 'email', ''].includes(i.type || '') || i.tagName === 'TEXTAREA')
  .filter(i => !i.disabled && !i.readOnly && i.offsetParent && i.getBoundingClientRect().height > 0)
  .map((i, n) => { i.dataset.typingProbe = String(n); return n; })"""


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for theme in ("hbo", "default"):
            print(f"\n{theme} theme, at phone size")
            ctx = await b.new_context(viewport=PHONE, is_mobile=True, has_touch=True, device_scale_factor=3)
            pg = await ctx.new_page()
            await pg.goto(BASE + "/", wait_until="load")
            await pg.click('[data-user="admin"]')
            if await pg.query_selector("#lp"):
                await pg.fill("#lp", "secret123")
                await pg.click("#login button.primary")
            await pg.wait_for_selector(".card", timeout=45000)
            # The theme the way the app sets it, so the phone look follows exactly as it would
            await pg.evaluate(f"""(() => {{ document.documentElement.dataset.theme = '{theme}';
                                 window.dispatchEvent(new Event('resize')); }})()""")
            tried = kept = 0
            lost = []
            for page in PAGES:
                await pg.set_viewport_size(PHONE)
                await pg.goto(f"{BASE}/#/settings/{page}", wait_until="load")
                try:
                    await pg.wait_for_selector(".panel", timeout=8000)
                except Exception:                       # noqa: BLE001 — a page with no blocks has no fields here
                    continue
                await pg.wait_for_timeout(400)
                probes = await pg.evaluate(FIELDS)
                for n in probes:
                    sel = f'[data-typing-probe="{n}"]'
                    await pg.set_viewport_size(PHONE)
                    await pg.wait_for_timeout(150)
                    if not await pg.query_selector(sel):
                        continue
                    await pg.locator(sel).scroll_into_view_if_needed()
                    await pg.tap(sel)
                    await pg.wait_for_timeout(150)
                    if not await pg.evaluate(f"document.activeElement === document.querySelector('{sel}')"):
                        continue                         # a field that opens a picker instead of taking text
                    tried += 1
                    await pg.set_viewport_size({"width": PHONE["width"], "height": int(PHONE["height"] * KEYBOARD)})
                    await pg.wait_for_timeout(500)
                    still = await pg.evaluate(f"document.activeElement === document.querySelector('{sel}')")
                    if still:
                        kept += 1
                    else:
                        name = await pg.evaluate(f"(e => e.id || e.name || e.placeholder || 'a field')(document.querySelector('{sel}'))")
                        lost.append(f"{page}: {name}")
            check(f"every text field keeps its focus when the keyboard comes up ({kept} of {tried})",
                  tried > 0 and kept == tried)
            if lost:
                print("        lost:", ", ".join(lost[:12]))
            # The field he reported, end to end: tap, keyboard, type, and the name is what was typed.
            await pg.set_viewport_size(PHONE)
            await pg.goto(f"{BASE}/#/settings/media-pages", wait_until="load")
            await pg.wait_for_selector("#p-devname", timeout=10000)
            await pg.evaluate("localStorage.removeItem('lenta.deviceName')")
            await pg.locator("#p-devname").scroll_into_view_if_needed()
            await pg.tap("#p-devname")
            await pg.set_viewport_size({"width": PHONE["width"], "height": int(PHONE["height"] * KEYBOARD)})
            await pg.wait_for_timeout(500)
            await pg.keyboard.press("Control+A")
            await pg.keyboard.type("Vlad's phone", delay=40)
            got = await pg.evaluate("localStorage.getItem('lenta.deviceName')")
            check(f"the device name can be typed on a phone ({got!r})", got == "Vlad's phone")
            await pg.evaluate("localStorage.removeItem('lenta.deviceName')")
            # And nothing else about the phone column changed: on every page the blocks are one column, in order.
            bad, blocks, most = [], 0, 0
            for page in PAGES:
                await pg.set_viewport_size(PHONE)
                await pg.goto(f"{BASE}/#/settings/{page}", wait_until="load")
                try:
                    await pg.wait_for_selector(".panel", timeout=8000)
                except Exception:                       # noqa: BLE001
                    continue
                await pg.wait_for_timeout(300)
                await pg.set_viewport_size({"width": PHONE["width"], "height": int(PHONE["height"] * KEYBOARD)})
                await pg.wait_for_timeout(300)
                cols = await pg.evaluate("""(() => { const ps = [...document.querySelectorAll('.panel[data-panel]')].filter(p => p.getBoundingClientRect().width > 0);
                    const xs = new Set(ps.map(p => Math.round(p.getBoundingClientRect().left)));
                    const ys = ps.map(p => p.getBoundingClientRect().top);
                    return {n: ps.length, columns: xs.size, ordered: ys.every((y, i) => i === 0 || y > ys[i - 1])}; })()""")
                blocks += cols["n"]
                most = max(most, cols["n"])
                if cols["n"] and (cols["columns"] != 1 or not cols["ordered"]):
                    bad.append(f"{page} {cols}")
            check(f"on every page the blocks are one column, top to bottom ({blocks} blocks, up to {most} on a page"
                  f"{', ' + bad[0] if bad else ''})", most >= 2 and not bad)
            await ctx.close()
        await b.close()


asyncio.run(main())
print(f"\n{len(fails)} failed" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
