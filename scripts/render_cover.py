"""Render the publisher's original HTML cover at 3x resolution for the website."""

import argparse
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://book.gradepro.org/about"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Path to an installed Chromium executable")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        options = {"executable_path": args.browser} if args.browser else {}
        with playwright.chromium.launch(headless=True, **options) as browser:
            page = browser.new_page(viewport={"width": 1440, "height": 1500},
                                    device_scale_factor=3)
            page.goto(SOURCE, wait_until="networkidle")
            preview_url = page.locator(".about-cover-trigger img").evaluate("el => el.src")
            preview = page.request.get(preview_url)
            if not preview.ok:
                raise ValueError("Could not download the publisher's cover preview")
            preview_bytes = preview.body()
            page.locator(".about-cover-trigger").click()
            page.locator(".grade-book-cover").wait_for(state="visible")
            page.evaluate("document.fonts.ready")
            page.locator(".grade-book-cover img").evaluate_all(
                "images => Promise.all(images.map(image => image.decode()))")
            # Keep the publisher's typography, graphics and styles. Isolate the
            # original artwork from the surrounding modal and its backdrop.
            page.evaluate("""() => {
                const artwork = document.querySelector('.cover-page').cloneNode(true);
                document.body.replaceChildren(artwork);
                document.body.className = '';
            }""")
            page.add_style_tag(content="""
                html, body { margin: 0; padding: 0; background: white; }
                .cover-page { position: absolute; left: 0; top: 0; margin: 0;
                    width: 866px; min-height: 0; padding: 8px 35px 43px 37px;
                    background: white; display: block; box-sizing: border-box; }
            """)
            bounds = page.locator(".grade-book-cover").bounding_box()
            if not (793 <= bounds["width"] <= 795 and 1122 <= bounds["height"] <= 1125):
                raise ValueError(f"Publisher cover layout changed; inspect before capturing: {bounds}")
            # Capture just the artwork; the 3D model supplies its own edges.
            image = page.locator(".grade-book-cover").screenshot(
                animations="disabled", scale="device")
    (ROOT / "site/official-cover-3x.png").write_bytes(image)
    metadata = {
        "source_url": SOURCE,
        "preview_url": preview_url,
        "preview_sha256": hashlib.sha256(preview_bytes).hexdigest(),
        "render_sha256": hashlib.sha256(image).hexdigest(),
        "width": struct.unpack(">I", image[16:20])[0],
        "height": struct.unpack(">I", image[20:24])[0],
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    (ROOT / "site/cover-render.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Rendered the original cover at {metadata['width']} × {metadata['height']}; "
          "review it before publishing.")


if __name__ == "__main__":
    main()
