import copy
import hashlib
import io
import json
import zipfile
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts.site import ASSETS, BASE_URL, REPO, build_site, editions_from_releases, load_releases, published_cover


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
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
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

    def test_rebuilds_on_same_day_have_distinct_labels(self):
        first = release()
        second = release("edition-2026-10-20-aaaa-2.1", "2026-10-20T19:00:00Z")
        editions = editions_from_releases([first, second], second["tag_name"], REPO)
        self.assertEqual(len({item["label"] for item in editions}), 2)
        self.assertIn("build 2", editions[0]["label"])

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


if __name__ == "__main__":
    unittest.main()
