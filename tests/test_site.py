import copy
import hashlib
import io
import json
import struct
import zipfile
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts.site import ASSETS, BASE_URL, REPO, ROOT, brief_notes, build_site, editions_from_releases, load_releases, published_cover


def release(tag="edition-2026-10-20-aaaa-1.1", published="2026-10-20T18:00:00Z"):
    names = [*ASSETS.values(), "manifest.json", "SHA256SUMS"]
    return {
        "tag_name": tag, "published_at": published, "draft": False, "prerelease": False,
        "assets": [
            {"name": name, "state": "uploaded", "size": 3_000_000,
             "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}"}
            for name in names
        ],
    }


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.downloads = {}
        self.local_paths = []
        self.ids = []
        self.search_entries = []
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if "data-search" in attrs:
            self.search_entries.append(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if "data-format" in attrs:
            self.downloads[attrs["data-format"]] = attrs["href"]
        for key in ["href", "src", "value" if tag == "option" else "unused"]:
            value = attrs.get(key)
            if value and value.startswith(("./", "../../")):
                self.local_paths.append(urlsplit(value).path)


class WebsiteTests(unittest.TestCase):
    def test_latest_is_selected_by_github_not_date_or_list_order(self):
        current = release()
        newer_but_not_latest = release("edition-2026-10-21-bbbb-2.1", "2026-10-21T18:00:00Z")
        editions = editions_from_releases([newer_but_not_latest, current], current["tag_name"], REPO)
        self.assertEqual(editions[0]["tag"], current["tag_name"])
        self.assertTrue(editions[0]["latest"])

    def test_drafts_previews_and_incomplete_editions_are_not_offered(self):
        current = release()
        draft, preview, incomplete = [copy.deepcopy(current) for _ in range(3)]
        draft["draft"] = True
        preview["prerelease"] = True
        incomplete["tag_name"] = "edition-2026-10-19-old-2.1"
        incomplete["assets"] = []
        editions = editions_from_releases([draft, preview, incomplete, current], current["tag_name"], REPO)
        self.assertEqual(len(editions), 1)

    def test_missing_or_invalid_latest_aborts_deployment(self):
        for failure in ["absent", "missing-file", "empty-file", "external-link"]:
            current = release()
            if failure == "missing-file":
                current["assets"].pop()
            if failure == "empty-file":
                current["assets"][0]["size"] = 0
            if failure == "external-link":
                current["assets"][0]["browser_download_url"] = "https://example.com/download"
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                editions_from_releases([] if failure == "absent" else [current],
                                       current["tag_name"], REPO)

    def test_rebuilds_on_same_day_show_only_dates_and_keep_separate_downloads(self):
        first = release()
        second = release("edition-2026-10-20-aaaa-2.1", "2026-10-20T19:00:00Z")
        editions = editions_from_releases([first, second], second["tag_name"], REPO)
        self.assertEqual(len(editions), 2)
        self.assertEqual({item["label"] for item in editions}, {"20 October 2026"})
        self.assertEqual(len({item["assets"]["pdf"]["url"] for item in editions}), 2)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "website"
            build_site(editions, output, BASE_URL, REPO)
            pages = list(output.rglob("*.html"))
            self.assertEqual(len(pages), 3)
            for page in pages:
                self.assertNotRegex(page.read_text(), r"\bbuild \d+\b")

    def test_static_downloads_archives_and_relative_paths_work_without_javascript(self):
        current = release()
        older = release("edition-2026-10-13-bbbb-2.1", "2026-10-13T18:00:00Z")
        editions = editions_from_releases([older, current], current["tag_name"], REPO)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "website"
            build_site(editions, output, BASE_URL, REPO)
            pages = list(output.rglob("*.html"))
            self.assertEqual(len(pages), 3)
            for page in pages:
                text = page.read_text()
                parsed = Links(text)
                self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
                self.assertEqual(set(parsed.downloads), set(ASSETS))
                expected_tag = older["tag_name"] if older["tag_name"] in str(page) else current["tag_name"]
                for kind, url in parsed.downloads.items():
                    self.assertEqual(url, f"https://github.com/{REPO}/releases/download/{expected_tag}/{ASSETS[kind]}")
                for link in parsed.local_paths:
                    self.assertTrue((page.parent / link).resolve().is_file(), (page, link))
                self.assertNotIn("{{", text)
            earlier = (output / "editions" / older["tag_name"] / "index.html").read_text()
            self.assertIn("You’re viewing an earlier edition", earlier)
            self.assertIn("Get the latest edition", earlier)
            self.assertNotIn("You’re viewing an earlier edition", (output / "index.html").read_text())

    def test_site_uses_verified_cover_from_published_book(self):
        current = release()
        edition = editions_from_releases([current], current["tag_name"], REPO)[0]
        image = b"original cover bytes"
        for corrupted in [False, True]:
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w") as archive:
                archive.writestr("grade-book/assets/cover.png", image)
                archive.writestr("grade-book/manifest.json", json.dumps({"cover": {
                    "file": "assets/cover.png",
                    "content_sha256": "0" * 64 if corrupted else hashlib.sha256(image).hexdigest(),
                }}))
            with patch("scripts.site.urlopen", return_value=io.BytesIO(content.getvalue())):
                if corrupted:
                    with self.assertRaises(ValueError):
                        published_cover(edition)
                else:
                    self.assertEqual(published_cover(edition), ("official-cover.png", image))

    def test_edition_details_keep_only_what_changed(self):
        long_form = """Unofficial offline edition of The GRADE Book.
Captured 2026-10-20T19:30:07+00:00; includes 16 published English chapters.

Download grade-book.epub for an e-reader, grade-book.pdf for printing,
grade-book-html.zip for a browser (extract it and open index.html),
grade-book.docx for Word, or grade-book.txt for text only.
The official cover and figures are embedded in EPUB, PDF, DOCX and the HTML archive.

**Updated:**

- Certainty
- Risk of bias

The official book cover was added or updated in every illustrated format.

Book content remains CC BY-NC-ND 4.0.
Content SHA-256: `abc`.
See manifest.json for provenance and SHA256SUMS for download checksums.
"""
        self.assertEqual(brief_notes(long_form), "Updated: Certainty; Risk of bias\nCover updated.")
        first = long_form.replace("**Updated:**\n\n- Certainty\n- Risk of bias\n\nThe official book cover was added or updated in every illustrated format.\n\n",
                                   "First published edition.\n\n")
        self.assertEqual(brief_notes(first), "First published edition. 16 English chapters.")
        self.assertEqual(brief_notes("No template here.\nA custom correction."), "No template here.\nA custom correction.")

    def test_release_search_preserves_metadata_without_injecting_markup(self):
        current = release()
        current["name"] = 'Updates to "certainty" & evidence'
        current["body"] = ('## Révision\nImproved **risk of bias** guidance. '
                           '[Original](https://book.gradepro.org/)\n'
                           '<img src=x onerror="alert(1)">')
        editions = editions_from_releases([current], current["tag_name"], REPO)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "website"
            build_site(editions, output, BASE_URL, REPO)
            text = (output / "index.html").read_text()
            parsed = Links(text)
            self.assertEqual(len(parsed.search_entries), 1)
            entry = parsed.search_entries[0]
            for term in [current["name"], current["tag_name"], "Révision",
                         "risk of bias", "2026-10-20", "10/20/2026", "20/10/2026"]:
                self.assertIn(term, entry["data-search"])
            self.assertEqual(entry["data-date"], "2026-10-20")
            self.assertEqual(entry["data-published"], "2026-10-20T18:00:00+00:00")
            self.assertEqual(parsed.tags.count("img"), 1)  # Only the official cover.
            self.assertIn('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;', text)

    def test_high_resolution_cover_matches_the_published_artwork(self):
        current = release()
        editions = editions_from_releases([current], current["tag_name"], REPO)
        rendered = (ROOT / "site/official-cover-3x.png").read_bytes()
        metadata = json.loads((ROOT / "site/cover-render.json").read_text())
        size = struct.unpack(">II", rendered[16:24])
        self.assertEqual(size, (metadata["width"], metadata["height"]))
        self.assertGreaterEqual(size[0], 2300)
        self.assertGreaterEqual(size[1], 3300)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            build_site(editions, output, BASE_URL, REPO)
            image = next(output.glob("official-cover-*.png"))
            self.assertEqual(image.read_bytes(), rendered)
            for page in output.rglob("*.html"):
                text = page.read_text()
                self.assertIn('class="cover-flat"', text)
                self.assertIn(f'width="{size[0]}" height="{size[1]}"', text)

    def test_new_publisher_cover_is_not_replaced_by_a_stale_render(self):
        current = release()
        editions = editions_from_releases([current], current["tag_name"], REPO)
        replacement = b"a newly published cover"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            build_site(editions, output, BASE_URL, REPO, ("official-cover.webp", replacement))
            self.assertEqual(next(output.glob("official-cover-*.webp")).read_bytes(), replacement)
            self.assertIn('class="cover-preview"', (output / "index.html").read_text())

    def test_corrupt_high_resolution_cover_aborts_the_build(self):
        current = release()
        editions = editions_from_releases([current], current["tag_name"], REPO)
        read_bytes = Path.read_bytes

        def corrupted(path):
            return b"corrupted render" if path.name == "official-cover-3x.png" else read_bytes(path)

        with tempfile.TemporaryDirectory() as temp, patch.object(Path, "read_bytes", corrupted):
            with self.assertRaisesRegex(ValueError, "High-resolution cover checksum mismatch"):
                build_site(editions, Path(temp), BASE_URL, REPO)

    def test_releases_are_paginated_and_api_errors_are_not_swallowed(self):
        current = release()
        with patch("scripts.site.github_json", side_effect=[[current] * 100, [current], current]) as api:
            releases, latest = load_releases(REPO)
            self.assertEqual(len(releases), 101)
            self.assertEqual(latest, current["tag_name"])
            self.assertIn("page=2", api.call_args_list[1].args[0])
        with patch("scripts.site.github_json", side_effect=RuntimeError("API unavailable")):
            with self.assertRaises(RuntimeError):
                load_releases(REPO)

    def test_book_always_stays_behind_header_and_caption_controls(self):
        css = (ROOT / "site/style.css").read_text()
        self.assertNotRegex(css, r"\.is-book-zoomed\s+\.book-stage\s*\{[^}]*z-index")
        self.assertRegex(css, r"\.book-stage\s*\{[^}]*z-index:\s*0\b")
        self.assertRegex(css, r"\.site-header\s*\{[^}]*z-index:\s*10\b")
        self.assertRegex(css, r"\.book-caption\s*\{[^}]*z-index:\s*10\b")

    def test_book_opening_and_closing_pose_matches_settled_position(self):
        css = (ROOT / "site/style.css").read_text()
        js = (ROOT / "site/site.js").read_text()
        renderer = (ROOT / "site/book-renderer.js").read_text()
        self.assertRegex(css, r"\.is-enhanced\s+\.book\s*\{[^}]*position:\s*absolute\b")
        self.assertNotIn(".is-enhanced .book:not(.is-open) { position: absolute; }", css)
        self.assertNotIn(".is-enhanced .book.is-open:not(.is-open-flat) { zoom: 1; }", css)
        self.assertNotRegex(
            css,
            r"\.is-enhanced \.book\.is-open:not\(\.is-open-flat\) \{[^}]*transform-style:\s*flat")
        self.assertIn("--book-scale 1100ms cubic-bezier(.42,0,.58,1)", js)
        self.assertRegex(css, r"\.is-enhanced\s+\.book\.is-open\s*\{[^}]*margin-top:\s*var\(--book-lift,\s*0px\)")
        self.assertNotIn("centerOffset", renderer)
        self.assertIn('book.classList.contains("is-closing")', js)

    def test_seo_metadata_structured_data_and_sitemap(self):
        current = release()
        older = release("edition-2026-10-13-bbbb-2.1", "2026-10-13T18:00:00Z")
        editions = editions_from_releases([older, current], current["tag_name"], REPO)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "website"
            build_site(editions, output, BASE_URL, REPO)
            home = (output / "index.html").read_text()
            earlier = (output / "editions" / older["tag_name"] / "index.html").read_text()
            self.assertIn('<meta name="robots" content="index, follow">', home)
            self.assertIn('<meta name="robots" content="noindex, follow">', earlier)
            self.assertIn("Grading of Recommendations, Assessment, Development and Evaluation", home)
            self.assertIn('<meta property="og:image" content="https://kianbahasadri.github.io/grade-book-offline/official-cover-', home)
            self.assertIn('<meta name="twitter:card" content="summary_large_image">', home)
            ld_start = home.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
            ld_end = home.index("</script>", ld_start)
            schema = json.loads(home[ld_start:ld_end])
            self.assertEqual(schema["@type"], "Book")
            self.assertEqual(schema["name"], "The GRADE Book")
            self.assertEqual(len(schema["encoding"]), len(ASSETS))
            sitemap = (output / "sitemap.xml").read_text()
            self.assertIn(f"<loc>{BASE_URL}</loc>", sitemap)
            self.assertIn("<lastmod>2026-10-20</lastmod>", sitemap)
            robots = (output / "robots.txt").read_text()
            self.assertIn(f"Sitemap: {BASE_URL}sitemap.xml", robots)


if __name__ == "__main__":
    unittest.main()
