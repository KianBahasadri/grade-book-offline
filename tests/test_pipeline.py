import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.build import conversion_html, localize_links, validate_html
from scripts.releases import baseline, release_notes
from scripts.snapshot import (
    changed,
    clean_fragment,
    digest,
    semantic_html,
    validate_catalog,
    validate_chapter,
)


class ChangeDetectionTests(unittest.TestCase):
    def manifest(self, text):
        return {"schema": 1, "content_sha256": digest(semantic_html(text)),
                "chapters": [{"id": "example"}]}

    def test_timestamps_and_editor_noise_do_not_publish(self):
        before = '<div id="old"><p class="a">Text <span>with a citation (1).</span></p>' \
                 '<div data-upstream-timestamp>Yesterday</div></div>'
        after = '<div id="new"><p class="b" style="color:red">Text <span>with a citation (1).</span></p>' \
                '<div data-upstream-timestamp>Today</div></div>'
        self.assertFalse(changed(self.manifest(after), self.manifest(before)))

    def test_content_changes_and_manual_rebuild_publish(self):
        old = self.manifest("<p>Original</p>")
        self.assertTrue(changed(self.manifest("<p>Revised</p>"), old))
        self.assertTrue(changed(old, None))
        self.assertTrue(changed(old, old, force=True))

    def test_image_reference_and_link_changes_publish(self):
        for before, after in [
            ('<img src="assets/old.png">', '<img src="assets/new.png">'),
            ("<p>Reference A</p>", "<p>Reference B</p>"),
            ('<a href="https://example.org/a">source</a>',
             '<a href="https://example.org/b">source</a>'),
            ('<td colspan="2">value</td>', '<td colspan="3">value</td>'),
        ]:
            with self.subTest(before=before):
                self.assertTrue(changed(self.manifest(after), self.manifest(before)))

    def test_broken_baseline_is_an_error_even_with_force(self):
        for previous in [{}, {"schema": 1, "content_sha256": "bad", "chapters": [1]}]:
            with self.assertRaises(ValueError):
                changed(self.manifest("current"), previous, force=True)

    def test_replaced_image_at_same_url_changes_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "assets").mkdir()
            html = '<p>Figure</p><img src="/figure.png">'
            with patch("scripts.snapshot.download_image", return_value=(b"first", "png")):
                before = clean_fragment(html, "https://book.gradepro.org/", directory, {})
            with patch("scripts.snapshot.download_image", return_value=(b"second", "png")):
                after = clean_fragment(html, "https://book.gradepro.org/", directory, {})
            self.assertTrue(changed(self.manifest(after), self.manifest(before)))


class CompletenessTests(unittest.TestCase):
    def test_catalog_must_match_published_entries(self):
        catalog = {"guidelines": [{"id": "one", "version": "final"},
                                  {"id": "draft", "version": "prePrint"}]}
        validate_catalog(catalog, [{"id": "one"}])
        for actual in [[], [{"id": "one"}, {"id": "one"}], [{"id": "draft"}]]:
            with self.assertRaises(ValueError):
                validate_catalog(catalog, actual)

    def test_missing_body_fails_but_unpublished_api_orphans_are_ignored(self):
        body = "Published section content. " * 12
        data = {
            "guideline": {"version": "final", "chapters": ["one"]},
            "chapters": [{"id": "one", "title": "One", "subchapters": [], "sections": ["s"]},
                         {"id": "orphan", "title": "Hidden draft", "subchapters": [], "sections": []}],
            "sections": [{"id": "s", "htmlContent": f"<p>{body}</p>"}],
        }
        html = f'<div class="chapter-full"><h2 class="chapter-full__title">One</h2><p>{body}</p></div>'
        validate_chapter(data, html)
        with self.assertRaises(ValueError):
            validate_chapter(data, html.replace(body, "Unrelated body. " * 20))

    def test_anchors_are_unique_and_cross_chapter_links_work(self):
        fragments = localize_links({
            "a": '<h1 id="intro">A</h1><a href="https://book.gradepro.org/guideline/b#intro">B</a>',
            "b": '<h1 id="intro">B</h1><a href="https://book.gradepro.org/guideline/a">A</a>',
        })
        with tempfile.TemporaryDirectory() as temp:
            document = "".join(f'<section id="{key}">{value}</section>'
                               for key, value in fragments.items())
            validate_html(document, Path(temp))
            self.assertIn('href="#b-', fragments["a"])
            self.assertIn('href="#a"', fragments["b"])

    def test_remote_images_and_broken_anchors_fail_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            for html in ['<img src="https://example.org/figure.png">',
                         '<a href="#missing">Broken</a>', '<script>alert(1)</script>']:
                with self.assertRaises(ValueError):
                    validate_html(html, Path(temp))

    def test_conversion_preserves_anchors_without_auto_heading_collisions(self):
        document = '<nav>Contents</nav><section id="one"><div id="sub">' \
                   '<h1>One</h1><h2>One</h2><a href="#sub">Link</a></div></section>'
        conversion = conversion_html(document)
        self.assertNotEqual(conversion.h1["id"], conversion.h2["id"])
        self.assertEqual(conversion.a["href"], "#" + conversion.h1["id"])
        self.assertFalse(conversion.h1.find("span"))
        self.assertFalse(conversion.select("div[id], section[id], nav"))
        with tempfile.TemporaryDirectory() as temp:
            validate_html(str(conversion), Path(temp))

    def test_editor_images_removed_but_missing_content_image_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fragment = clean_fragment('<p>Text<img class="ProseMirror-separator"></p>',
                                      "https://book.gradepro.org", directory, {})
            self.assertNotIn("<img", fragment)
            with self.assertRaises(ValueError):
                clean_fragment('<img alt="Important figure">', "https://book.gradepro.org",
                               directory, {})


class ReleaseTests(unittest.TestCase):
    def test_api_failure_is_not_treated_as_first_release(self):
        for status in [403, 429, 500]:
            error = HTTPError("url", status, "Failure", {}, None)
            with patch("scripts.releases.api", side_effect=[{}, error]):
                with self.assertRaises(HTTPError):
                    baseline("owner/repo", Path("/unused"))

    def test_first_release_is_only_a_verified_404(self):
        error = HTTPError("url", 404, "Not found", {}, None)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "previous.json"
            destination.write_text("stale")
            with patch("scripts.releases.api", side_effect=[{}, error]):
                baseline("owner/repo", destination)
            self.assertFalse(destination.exists())

    def test_release_without_manifest_fails(self):
        with patch("scripts.releases.api", side_effect=[{}, {"assets": []}]):
            with self.assertRaises(ValueError):
                baseline("owner/repo", Path("/unused"))

    def test_notes_distinguish_content_update_and_forced_rebuild(self):
        current = {
            "source": "https://book.gradepro.org", "captured_at": "2026-10-20T00:00:00Z",
            "content_sha256": "a" * 64,
            "chapters": [{"id": "one", "title": "One", "content_sha256": "x"}],
        }
        self.assertIn("Manual rebuild", release_notes(current, current))
        previous = json.loads(json.dumps(current))
        previous["content_sha256"] = "b" * 64
        previous["chapters"][0]["content_sha256"] = "y"
        self.assertIn("**Updated:**", release_notes(current, previous))


if __name__ == "__main__":
    unittest.main()
