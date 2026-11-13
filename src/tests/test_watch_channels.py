"""Viewing-channel selection, safe URLs and page integration. No external I/O."""
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import watch_channels as watch

TODAY = date(2026, 11, 13)
ANIME = {"mal_id": 38000, "title_zh": "鬼灭之刃", "title": "Demon Slayer", "title_japanese": "鬼滅の刃"}


def entry(**overrides):
    return {"platform": "bilibili", "url": "https://www.bilibili.com/bangumi/play/ss26801",
            "verified_title": "鬼灭之刃", "verified_on": TODAY.isoformat(), **overrides}


class WatchChannelTests(unittest.TestCase):
    def test_01_unknown_title_has_four_searches(self):
        rows = watch.build_watch_channels(ANIME, catalogue={}, today=TODAY)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["kind"] == "search" and not row["direct_url"] for row in rows))

    def test_02_reviewed_player_precedes_search(self):
        rows = watch.build_watch_channels(ANIME, catalogue={"38000": [entry(platform="iqiyi", url="https://www.iqiyi.com/v_19rrlx96w0.html")]}, today=TODAY)
        self.assertEqual(rows[0]["id"], "iqiyi")
        self.assertEqual(rows[0]["label"], "官方播放页")
        self.assertTrue(rows[0]["search_url"])

    def test_03_exact_mal_match_does_not_leak_seasons(self):
        rows = watch.build_watch_channels({**ANIME, "mal_id": 42203}, catalogue={"39587": [entry()]}, today=TODAY)
        self.assertFalse(any(row["direct_url"] for row in rows))

    def test_04_search_preserves_chinese_and_special_characters(self):
        title = 'Re:0 / A&B + "中文" #?'
        rows = watch.build_watch_channels({**ANIME, "title_zh": title}, catalogue={}, today=TODAY)
        for row in rows:
            with self.subTest(platform=row["id"]):
                self.assertEqual(unquote(row["search_url"][len(row["search"]):]), title)
                self.assertNotIn("#", row["search_url"])

    def test_05_english_and_japanese_title_fallbacks(self):
        self.assertEqual(watch.build_watch_channels({"title": "English"}, catalogue={})[0]["title"], "English")
        self.assertEqual(watch.build_watch_channels({"title_japanese": "日本語"}, catalogue={})[0]["title"], "日本語")
        self.assertEqual(watch.build_watch_channels({"title_zh": "  ", "title": "English"}, catalogue={})[0]["title"], "English")

    def test_06_missing_or_invalid_id_remains_search(self):
        for value in [None, "", -1, "1 OR 1=1"]:
            with self.subTest(value=value):
                self.assertTrue(all(not row["direct_url"] for row in watch.build_watch_channels({**ANIME, "mal_id": value}, catalogue={})))

    def test_07_stale_future_disabled_and_missing_dates_fall_back(self):
        bad = [entry(verified_on=""), entry(verified_on="bad"), entry(verified_on=(TODAY+timedelta(days=1)).isoformat()),
               entry(verified_on=(TODAY-timedelta(days=181)).isoformat()), entry(enabled=False)]
        for item in bad:
            with self.subTest(item=item): self.assertIsNone(watch.reviewed_destination(item, TODAY))

    def test_08_unsafe_hosts_schemes_ports_and_paths_rejected(self):
        urls = ["javascript:alert(1)", "http://www.bilibili.com/bangumi/play/ss1", "//www.bilibili.com/bangumi/play/ss1",
                "https://www.bilibili.com.evil.invalid/bangumi/play/ss1", "https://user@www.bilibili.com/bangumi/play/ss1",
                "https://www.bilibili.com:443/bangumi/play/ss1", "https://www.bilibili.com/video/BV123",
                "https://www.bilibili.com/bangumi/play/ss1?redirect=evil", "https://www.bilibili.com/bangumi/play/ss1#foo",
                "https://www.bilibili.com/../bangumi/play/ss1", "https://www.bilibili.com/\nbangumi/play/ss1"]
        for url in urls:
            with self.subTest(url=url): self.assertIsNone(watch.reviewed_destination(entry(url=url), TODAY))

    def test_09_unknown_provider_and_missing_evidence_rejected(self):
        self.assertIsNone(watch.reviewed_destination(entry(platform="unknown"), TODAY))
        self.assertIsNone(watch.reviewed_destination(entry(platform=[]), TODAY))
        self.assertIsNone(watch.reviewed_destination(entry(verified_title=""), TODAY))
        self.assertIsNone(watch.reviewed_destination([], TODAY))

    def test_10_detail_is_distinguished_and_player_is_preferred(self):
        detail = entry(url="https://www.bilibili.com/bangumi/media/md22718131/")
        rows = watch.build_watch_channels(ANIME, catalogue={"38000": [detail]}, today=TODAY)
        self.assertEqual(rows[0]["label"], "官方番剧页")
        rows = watch.build_watch_channels(ANIME, catalogue={"38000": [detail, entry()]}, today=TODAY)
        self.assertEqual(rows[0]["kind"], "play")

    def test_11_bad_catalogue_or_entry_list_fails_closed(self):
        for catalogue in [[], None, {"38000": None}, {"38000": {}}, {"38000": [None, {}, "wrong"]}]:
            with self.subTest(catalogue=catalogue):
                if catalogue is None: continue
                self.assertFalse(any(row["direct_url"] for row in watch.build_watch_channels(ANIME, catalogue=catalogue, today=TODAY)))

    def test_12_missing_and_corrupt_catalogue_use_search(self):
        watch.load_catalogue.cache_clear()
        try:
            with patch.object(Path, "read_text", side_effect=OSError("test missing")):
                self.assertEqual(watch.load_catalogue(), {})
            watch.load_catalogue.cache_clear()
            with patch.object(Path, "read_text", return_value="{bad"):
                self.assertEqual(watch.load_catalogue(), {})
        finally: watch.load_catalogue.cache_clear()

    def test_13_config_records_are_reviewed_and_match_seed(self):
        data = json.loads(watch.CATALOGUE_PATH.read_text(encoding="utf-8"))
        seed = json.loads((watch.CATALOGUE_PATH.parent / "anime_seed.json").read_text(encoding="utf-8"))
        ids = {str(row["mal_id"]) for row in seed}
        self.assertEqual(len(data["entries"]), 12)
        self.assertTrue(set(data["entries"]).issubset(ids))
        for mal, entries in data["entries"].items():
            with self.subTest(mal=mal): self.assertTrue(all(watch.reviewed_destination(row, TODAY) for row in entries))

    def test_14_rendered_official_and_search_links_are_safe(self):
        from test_application import module
        catalogue = {"38000": [entry()]}
        with module.app.test_request_context('/anime/1'):
            page = module.render_template('watch_channels.html', watch_channels=watch.build_watch_channels(ANIME, catalogue=catalogue, today=TODAY))
        self.assertIn('官方播放页', page)
        self.assertIn('https://www.bilibili.com/bangumi/play/ss26801', page)
        self.assertIn('在平台搜索', page)
        self.assertEqual(page.count('target="_blank"'), 5)
        self.assertEqual(page.count('rel="noopener noreferrer external"'), 5)

    def test_15_unknown_work_page_has_explicit_search_labels(self):
        from test_application import module
        with module.app.test_request_context('/anime/1'):
            page = module.render_template('watch_channels.html', watch_channels=watch.build_watch_channels(ANIME, catalogue={}, today=TODAY))
        self.assertIn('暂未核验到官方播放页', page)
        self.assertEqual(page.count('class="watch-link-kind">平台搜索'), 4)
        self.assertNotIn('前往观看', page)

    def test_16_title_is_escaped_in_html(self):
        from test_application import module
        with module.app.test_request_context('/anime/1'):
            page = module.render_template('watch_channels.html', watch_channels=watch.build_watch_channels({**ANIME, 'title_zh':'<script>alert(1)</script>'}, catalogue={}))
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
