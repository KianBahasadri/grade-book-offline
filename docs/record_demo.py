"""Record a 60fps captioned walkthrough of the public download site."""

import argparse
import base64
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path(__file__).resolve().parent
WORK = ROOT / ".work/demo"
SITE_BUILD = WORK / "site"
RAW_FRAMES = WORK / "raw_frames"
VIEWPORT = {"width": 1600, "height": 900}
LAUNCH_ARGS = ["--use-angle=gl-egl", "--enable-gpu", "--ignore-gpu-blocklist"]

SETUP = """
(() => {
  const stage = document.querySelector(".book-stage");
  stage.setPointerCapture = () => {};
  stage.hasPointerCapture = () => false;
  stage.releasePointerCapture = () => {};

  const cursor = document.createElement("div");
  cursor.id = "demo-cursor";
  cursor.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.2 3.2 10.6 19l2.1-6.1L19 10.7 5.2 3.2z" fill="#172027" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>';
  cursor.style.cssText = "position:fixed;z-index:80;pointer-events:none;left:0;top:0;margin:-2px 0 0 -3px;filter:drop-shadow(0 1px 1px rgba(10,13,15,.35));will-change:transform;";
  const caption = document.createElement("p");
  caption.id = "demo-caption";
  caption.style.cssText = [
    "position:fixed", "z-index:70", "left:50%", "top:18px", "transform:translateX(-50%)",
    "margin:0", "padding:10px 20px", "border-radius:999px", "pointer-events:none",
    "background:var(--surface)", "color:var(--ink-strong)", "border:1px solid var(--line)",
    "font:500 34px/1.15 'IBM Plex Sans', sans-serif", "letter-spacing:-.03em",
    "box-shadow:0 8px 24px rgba(10,13,15,.12)", "white-space:nowrap", "opacity:0",
    "transition:opacity 140ms ease"
  ].join(";");
  document.body.append(cursor, caption);

  window.__cursor = { x: 800, y: 450 };
  window.setCursorPos = (x, y) => {
    window.__cursor.x = x;
    window.__cursor.y = y;
    cursor.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  };
  window.setCursorPos(800, 450);

  addEventListener("mousemove", event => {
    window.setCursorPos(event.clientX, event.clientY);
  }, true);

  window.setCaption = text => {
    caption.textContent = text || "";
    caption.style.opacity = text ? "1" : "0";
  };

  const ease = t => t * t * (3 - 2 * t);

  window.demoGlide = (toX, toY, durationMs) => new Promise(resolve => {
    const startX = window.__cursor.x;
    const startY = window.__cursor.y;
    const start = performance.now();
    function step(now) {
      const t = Math.min(1, (now - start) / durationMs);
      const k = ease(t);
      window.setCursorPos(startX + (toX - startX) * k, startY + (toY - startY) * k);
      if (t < 1) requestAnimationFrame(step);
      else resolve();
    }
    requestAnimationFrame(step);
  });

  window.demoDrag = segments => new Promise(resolve => {
    let sx = window.__cursor.x;
    let sy = window.__cursor.y;
    stage.dispatchEvent(new PointerEvent("pointerdown", {
      clientX: sx, clientY: sy, button: 0, buttons: 1,
      pointerId: 1, isPrimary: true, pointerType: "mouse", bubbles: true
    }));
    let idx = 0;
    let segStart = performance.now();
    function step(now) {
      const seg = segments[idx];
      const t = Math.min(1, (now - segStart) / seg.ms);
      const k = ease(t);
      const x = sx + (seg.x - sx) * k;
      const y = sy + (seg.y - sy) * k;
      window.setCursorPos(x, y);
      window.dispatchEvent(new PointerEvent("pointermove", {
        clientX: x, clientY: y, button: 0, buttons: 1,
        pointerId: 1, isPrimary: true, pointerType: "mouse", bubbles: true
      }));
      if (t < 1) {
        requestAnimationFrame(step);
      } else {
        sx = seg.x;
        sy = seg.y;
        idx++;
        if (idx < segments.length) {
          segStart = now;
          requestAnimationFrame(step);
        } else {
          window.dispatchEvent(new PointerEvent("pointerup", {
            clientX: sx, clientY: sy, button: 0, buttons: 0,
            pointerId: 1, isPrimary: true, pointerType: "mouse", bubbles: true
          }));
          resolve();
        }
      }
    }
    requestAnimationFrame(step);
  });
})();
"""


def launch_browser(playwright, executable_path=None):
    candidates = [executable_path] if executable_path else [None]
    cache = Path.home() / ".cache/ms-playwright"
    if cache.is_dir():
        candidates.extend(sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True))
    system_chromium = shutil.which("chromium") or shutil.which("google-chrome")
    if system_chromium:
        candidates.append(system_chromium)
    last_error = None
    for candidate in candidates:
        options = {"executable_path": str(candidate)} if candidate else {}
        try:
            return playwright.chromium.launch(headless=True, args=LAUNCH_ARGS, **options)
        except PlaywrightError as error:
            last_error = error
    raise last_error


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def target_url(url=None):
    if url:
        yield url
        return
    if SITE_BUILD.exists():
        shutil.rmtree(SITE_BUILD)
    subprocess.run([sys.executable, "-m", "scripts.site", "--output", str(SITE_BUILD)],
                   cwd=ROOT, check=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(SITE_BUILD)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Path to an installed Chromium executable")
    parser.add_argument("--url", help="Website URL to record (defaults to a fresh local build)")
    args = parser.parse_args()

    if RAW_FRAMES.exists():
        for frame in RAW_FRAMES.glob("*"):
            frame.unlink()
    RAW_FRAMES.mkdir(parents=True, exist_ok=True)

    with target_url(args.url) as url, sync_playwright() as playwright:
        browser = launch_browser(playwright, args.browser)
        context = browser.new_context(
            viewport=VIEWPORT,
            color_scheme="light",
            reduced_motion="no-preference",
            locale="en-US",
            device_scale_factor=1,
        )
        page = context.new_page()
        started = time.perf_counter()

        def log(message):
            print(f"{time.perf_counter() - started:6.2f}  {message}", flush=True)

        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("html.is-enhanced")
        page.wait_for_function("() => document.querySelector('.cover-opener img')?.complete")
        page.wait_for_timeout(400)
        page.evaluate(SETUP)

        book = page.locator("#book").bounding_box()
        origin_x = book["x"] + book["width"] / 2
        origin_y = book["y"] + book["height"] / 2
        page.mouse.move(origin_x, origin_y)
        page.wait_for_timeout(150)

        cdp = context.new_cdp_session(page)
        frames = []

        def on_frame(params):
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            frames.append((params["data"], params["metadata"]["timestamp"]))

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 94, "maxWidth": 1600, "maxHeight": 900, "everyNthFrame": 1},
        )

        def pause(ms):
            page.wait_for_timeout(int(ms))

        def glide(x, y, ms=380):
            page.evaluate("([x, y, ms]) => window.demoGlide(x, y, ms)", [x, y, ms])
            page.mouse.move(x, y)

        log("title")
        pause(900)

        page.evaluate("setCaption('Drag to look around')")
        log("drag-light")
        page.evaluate(
            "segments => window.demoDrag(segments)",
            [
                {"x": origin_x - 210, "y": origin_y + 26, "ms": 900},
                {"x": origin_x + 45, "y": origin_y - 10, "ms": 1000},
                {"x": origin_x - 35, "y": origin_y + 6, "ms": 500},
            ],
        )
        pause(300)

        opener = page.locator(".open-label")
        opener_box = opener.bounding_box()
        page.evaluate("setCaption('Choose how you like to read')")
        log("open-light")
        glide(opener_box["x"] + opener_box["width"] / 2, opener_box["y"] + opener_box["height"] / 2, 360)
        opener.click()
        pause(1150)

        log("formats")
        for link in page.locator(".download-link").all():
            box = link.bounding_box()
            glide(box["x"] + 120, box["y"] + box["height"] / 2, 210)
            pause(120)
        pause(200)

        close = page.locator(".close-book")
        close_box = close.bounding_box()
        page.evaluate("setCaption('Earlier editions are available')")
        log("close-light")
        glide(close_box["x"] + close_box["width"] / 2, close_box["y"] + close_box["height"] / 2, 320)
        close.click()
        pause(1150)

        editions = page.locator("#editions-trigger")
        editions_box = editions.bounding_box()
        log("editions")
        glide(editions_box["x"] + editions_box["width"] / 2, editions_box["y"] + editions_box["height"] / 2, 380)
        editions.click()
        pause(420)

        log("search")
        search = page.locator("#edition-query")
        search_box = search.bounding_box()
        glide(search_box["x"] + 80, search_box["y"] + search_box["height"] / 2, 260)
        search.click()
        search.press_sequentially("latest", delay=65)
        pause(300)
        visible = page.locator(".edition-list li:not([hidden]) .edition-link").first
        visible_box = visible.bounding_box()
        glide(visible_box["x"] + 70, visible_box["y"] + visible_box["height"] / 2, 250)
        pause(550)
        page.keyboard.press("Escape")
        page.evaluate("() => document.activeElement.blur()")
        pause(220)

        about = page.locator("#about-trigger")
        about_box = about.bounding_box()
        log("about")
        glide(about_box["x"] + about_box["width"] / 2, about_box["y"] + about_box["height"] / 2, 280)
        about.click()
        pause(450)
        page.evaluate("""() => new Promise(resolve => {
          const panel = document.getElementById("about-panel");
          const target = Math.max(0, panel.scrollHeight - panel.clientHeight);
          const start = performance.now();
          function step(now) {
            const t = Math.min(1, (now - start) / 520);
            panel.scrollTop = target * (t * t * (3 - 2 * t));
            if (t < 1) requestAnimationFrame(step);
            else resolve();
          }
          requestAnimationFrame(step);
        })""")
        pause(550)
        page.keyboard.press("Escape")
        page.evaluate("() => document.activeElement.blur()")
        pause(220)

        theme = page.locator(".theme-toggle")
        theme_box = theme.bounding_box()
        page.evaluate("setCaption('Light or dark')")
        log("theme")
        glide(theme_box["x"] + theme_box["width"] / 2, theme_box["y"] + theme_box["height"] / 2, 420)
        pause(120)
        theme.click()
        pause(400)

        book = page.locator("#book").bounding_box()
        cx = book["x"] + book["width"] * 0.55
        cy = book["y"] + book["height"] * 0.45
        glide(cx, cy, 340)
        log("drag-dark")
        page.evaluate(
            "segments => window.demoDrag(segments)",
            [
                {"x": cx - 165, "y": cy + 20, "ms": 750},
                {"x": cx + 20, "y": cy - 6, "ms": 750},
            ],
        )
        pause(250)

        opener_box = opener.bounding_box()
        page.evaluate("setCaption('kianbahasadri.github.io/grade-book-offline')")
        log("open-dark")
        glide(opener_box["x"] + opener_box["width"] / 2, opener_box["y"] + opener_box["height"] / 2, 340)
        opener.click()
        pause(1150)

        links = page.locator(".download-link").all()
        for link in links[:2]:
            box = link.bounding_box()
            glide(box["x"] + 120, box["y"] + box["height"] / 2, 220)
            pause(140)
        pause(350)

        close_box = close.bounding_box()
        log("close-dark")
        glide(close_box["x"] + close_box["width"] / 2, close_box["y"] + close_box["height"] / 2, 320)
        close.click()
        pause(1150)
        pause(1000)
        log(f"DONE frames={len(frames)}")

        cdp.send("Page.stopScreencast")
        context.close()
        browser.close()

    concat_file = WORK / "frames.ffconcat"
    lines = ["ffconcat version 1.0"]
    written = []
    for i, (b64_data, ts) in enumerate(frames):
        path = RAW_FRAMES / f"f_{i:05d}.jpg"
        path.write_bytes(base64.b64decode(b64_data))
        written.append(path)
        lines.append(f"file '{path.resolve()}'")
        if i + 1 < len(frames):
            dur = max(0.008, frames[i + 1][1] - ts)
        else:
            dur = 0.8
        lines.append(f"duration {dur:.6f}")
    lines.append(f"file '{written[-1].resolve()}'")
    concat_file.write_text("\n".join(lines) + "\n")

    video_out = DOCS / "demo.mp4"
    poster_out = DOCS / "demo-poster.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-vf", "fps=60", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.2", "-crf", "18", "-preset", "slow",
            "-movflags", "+faststart", str(video_out),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "5.8", "-i", str(video_out),
            "-frames:v", "1", "-update", "1", "-q:v", "2", str(poster_out),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
