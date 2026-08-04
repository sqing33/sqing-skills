from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_ai_hotspots.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("collect_ai_hotspots", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


NOW = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)


def source(**overrides):
    values = {
        "id": "fixture",
        "name": "Fixture",
        "url": "https://example.com/feed",
        "format": "rss",
        "language": "en",
        "categories": ("industry",),
        "tier": "secondary",
        "weight": 10,
        "enabled": True,
    }
    values.update(overrides)
    return collector.Source(**values)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    def test_rss_parses_missing_date_and_skips_missing_link(self):
        rows = collector.xml_entries(fixture("rss.xml"), source(), NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].title, "Acme launches Model One")
        self.assertEqual(rows[0].summary, "A primary model launch.")
        self.assertIsNone(rows[1].published_at)
        self.assertTrue(rows[1].signals["missing_published_at"])

    def test_atom_namespace(self):
        rows = collector.xml_entries(
            fixture("atom.xml"), source(format="atom", categories=("papers",)), NOW
        )
        self.assertEqual(rows[0].url, "https://arxiv.org/abs/2607.00001")
        self.assertEqual(rows[0].category, "papers")

    def test_huggingface_papers_json(self):
        rows = collector.json_entries(
            fixture("huggingface_papers.json"),
            source(format="json", parser="huggingface_papers", categories=("papers",)),
            NOW,
        )
        self.assertEqual(rows[0].title, "Agent Paper Three")
        self.assertEqual(rows[0].signals["upvotes"], 42.0)

    def test_huggingface_model_uses_publication_for_freshness(self):
        rows = collector.json_entries(
            fixture("huggingface_models.json"),
            source(
                format="json",
                parser="huggingface_models",
                categories=("models",),
            ),
            NOW,
        )
        self.assertEqual(rows[0].published_at, "2026-06-01T10:00:00Z")
        self.assertEqual(rows[0].freshness_at, "2026-06-01T10:00:00Z")
        self.assertTrue(rows[0].signals["trending_snapshot"])

    def test_github_json(self):
        rows = collector.json_entries(
            fixture("github.json"),
            source(format="json", parser="github_repositories", categories=("opensource",)),
            NOW,
        )
        self.assertEqual(rows[0].title, "acme/agent-five")
        self.assertEqual(rows[0].signals["stars"], 250.0)

    def test_media_category_uses_action_not_model_brand(self):
        media = source(
            id="media",
            categories=("industry", "models", "products"),
            tier="secondary",
        )
        self.assertEqual(
            collector.infer_category(media, "Claude Code now has a built-in browser", ""),
            "products",
        )
        self.assertEqual(
            collector.infer_category(media, "LinkedIn AI slop according to a study", ""),
            "industry",
        )
        self.assertEqual(
            collector.infer_category(media, "Acme releases a new language model", ""),
            "models",
        )
        self.assertEqual(
            collector.infer_category(media, "Acme raises funding for a large language model appliance", ""),
            "industry",
        )
        self.assertEqual(
            collector.infer_category(media, "SpaceXAI releases Grok 4.5", ""),
            "models",
        )
        self.assertEqual(
            collector.infer_category(media, "Open source drives the next AI paradigm", ""),
            "industry",
        )
        self.assertEqual(
            collector.infer_category(media, "大模型公司智谱与 MiniMax 解禁期股价走势分化", ""),
            "industry",
        )
        self.assertEqual(
            collector.infer_category(media, "大模型公司创始人内部信与薪酬动态", ""),
            "industry",
        )
        self.assertEqual(
            collector.infer_category(media, "Apple sues OpenAI over trade secrets", ""),
            "industry",
        )

    def test_malformed_xml(self):
        with self.assertRaises(collector.CollectorError):
            collector.xml_entries(fixture("malformed.xml"), source(), NOW)


class RankingTests(unittest.TestCase):
    def candidate(self, title: str, url: str, source_name: str = "Fixture", weight: int = 10):
        item = collector.make_candidate(
            source(name=source_name, weight=weight),
            title,
            url,
            NOW - timedelta(hours=1),
            "",
            {},
            NOW - timedelta(hours=1),
        )
        return item

    def test_canonical_url_removes_tracking(self):
        self.assertEqual(
            collector.canonical_url("https://EXAMPLE.com/news/?utm_source=x&keep=1#top"),
            "https://example.com/news?keep=1",
        )

    def test_duplicate_url_merges_sources(self):
        left = self.candidate("Acme Model", "https://example.com/news?utm_source=x", "Official", 30)
        right = self.candidate("Acme Model launch", "https://example.com/news", "Media", 10)
        merged = collector.merge_candidates([right, left])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_name, "Official")
        self.assertEqual(merged[0].duplicate_sources, ["Media", "Official"])

    def test_topic_dedupe_merges_lawsuit_headline_variants(self):
        left = self.candidate(
            "Apple Suing OpenAI, Two Former Employees for Trade Secrets Theft",
            "https://example.com/one",
            "Media One",
        )
        right = self.candidate(
            "Apple sues OpenAI over alleged theft of trade secrets",
            "https://example.com/two",
            "Media Two",
        )
        right.source_id = "media-two"
        merged = collector.merge_candidates([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].duplicate_sources, ["Media One", "Media Two"])

    def test_topic_dedupe_merges_chinese_entity_action_variants(self):
        left = self.candidate(
            "商汤开源SenseNova-Vision统一视觉大模型，单模型横扫四大视觉任务",
            "https://example.com/one-cn",
            "Official",
        )
        right = self.candidate(
            "商汤开源统一视觉大模型SenseNova-Vision",
            "https://example.com/two-cn",
            "Media",
        )
        right.source_id = "media-cn"
        merged = collector.merge_candidates([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].duplicate_sources, ["Media", "Official"])

    def test_score_is_deterministic_and_cross_source_increases_it(self):
        item = self.candidate("Acme Model", "https://example.com/news")
        first = collector.score_candidate(item, NOW)
        item.duplicate_sources.append("Second")
        second = collector.score_candidate(item, NOW)
        self.assertEqual(first, collector.score_candidate(self.candidate("Acme Model", "https://example.com/news"), NOW))
        self.assertGreater(second, first)

    def test_window_filters_old_published_trend(self):
        old = self.candidate("Old", "https://example.com/old")
        old.freshness_at = "2026-06-01T00:00:00Z"
        cutoff = NOW - timedelta(hours=48)
        self.assertFalse(collector.candidate_in_window(old, cutoff, NOW))

    def test_diverse_selection_caps_one_source_when_alternatives_exist(self):
        rows = []
        for index in range(6):
            item = self.candidate(f"Model {index}", f"https://example.com/model-{index}")
            item.score = 100 - index
            item.category = "models"
            rows.append(item)
        for index in range(4):
            item = self.candidate(
                f"News {index}", f"https://news.example.com/{index}", source_name=f"News {index}"
            )
            item.source_id = f"news-{index}"
            item.category = "industry"
            item.score = 80 - index
            rows.append(item)
        selected = collector.select_diverse(rows, 6)
        self.assertLessEqual(sum(item.source_id == "fixture" for item in selected), 2)
        self.assertTrue(any(item.category == "industry" for item in selected))
        self.assertEqual([item.score for item in selected], sorted((item.score for item in selected), reverse=True))

    def test_shortlist_filters_noise_and_enforces_source_group_caps(self):
        rows = []
        for index, category in enumerate(("industry", "products", "models", "industry", "products", "models")):
            item = self.candidate(f"Google event {index}", f"https://news.example.com/{index}")
            item.source_id = "google-news-en" if index % 2 == 0 else "google-news-zh"
            item.source_name = "Google News"
            item.category = category
            item.score = 100 - index
            rows.append(item)
        for index in range(4):
            item = self.candidate(f"HN project {index}", f"https://hn.example.com/{index}")
            item.source_id = "hnrss-ai"
            item.source_name = "Hacker News AI"
            item.category = "opensource"
            item.score = 90 - index
            rows.append(item)
        noise = self.candidate("人工智能ETF融资融券余额1亿元", "https://noise.example.com")
        noise.source_id = "google-news-zh"
        noise.category = "industry"
        noise.score = 110
        rows.insert(0, noise)
        rows.sort(key=lambda item: -item.score)
        selected = collector.select_shortlist(rows, 12)
        self.assertFalse(any(collector.is_noise_candidate(item) for item in selected))
        self.assertLessEqual(sum(item.source_id.startswith("google-news-") for item in selected), 4)
        self.assertLessEqual(sum(item.source_id == "hnrss-ai" for item in selected), 2)
        self.assertLessEqual(len(selected), 12)

    def test_shortlist_rejects_promotional_and_low_signal_hn_items(self):
        promotional = self.candidate(
            "Global Careers Hub – Free AI Tools, Scholarships and Career Resources",
            "https://example.com/careers",
        )
        promotional.source_id = "hnrss-ai"
        promotional.category = "products"
        generic = self.candidate("The Winners of the AI Era", "https://example.com/winners")
        generic.source_id = "hnrss-ai"
        generic.category = "industry"
        project = self.candidate(
            "Show HN: Self-hosted voice AI agent",
            "https://github.com/example/voice-agent",
        )
        project.source_id = "hnrss-ai"
        project.category = "opensource"
        selected = collector.select_shortlist([promotional, generic, project], 12)
        self.assertEqual([item.title for item in selected], [project.title])


class CollectionTests(unittest.TestCase):
    def test_markdown_renders_shortlist_not_full_pool(self):
        item = {
            "title": "Shortlisted event",
            "url": "https://example.com/short",
            "published_at": "2026-07-12T23:00:00Z",
            "category": "products",
            "source_name": "Official",
            "duplicate_sources": ["Official"],
            "score": 50,
        }
        payload = {
            "collected_at": "2026-07-13T00:00:00Z",
            "hours": 24,
            "hotspots": [item, {**item, "title": "Pool only"}],
            "shortlist": [item],
            "warnings": [],
            "needs_window_expansion": False,
            "error": None,
        }
        markdown = collector.render_markdown(payload)
        self.assertIn("Shortlisted event", markdown)
        self.assertNotIn("Pool only", markdown)
        self.assertIn("完整候选池：2 条；待核验短名单：1 条", markdown)

    def test_partial_failure_returns_success(self):
        good = source(id="good", name="Good")
        bad = source(id="bad", name="Bad", url="https://bad.example/feed")

        def fake_fetch(item, url, timeout=20):
            if item.id == "bad":
                raise collector.CollectorError("timeout")
            return fixture("rss.xml")

        with patch.object(collector, "fetch_text", side_effect=fake_fetch):
            payload, code = collector.collect([good, bad], 24, 30, observed_at=NOW)
        self.assertEqual(code, 0)
        self.assertTrue(payload["hotspots"])
        self.assertIn("shortlist", payload)
        self.assertTrue(payload["needs_window_expansion"])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_all_sources_fail(self):
        only = source(id="bad", name="Bad")
        with patch.object(collector, "fetch_text", side_effect=collector.CollectorError("timeout")):
            payload, code = collector.collect([only], 48, 30, observed_at=NOW)
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "all selected sources failed")

    def test_source_filters_and_explicit_disabled_source(self):
        enabled = source(id="enabled")
        disabled = source(id="disabled", enabled=False, categories=("models",), language="zh")
        selected = collector.select_sources([enabled, disabled], ["disabled"], ["models"], ["zh"])
        self.assertEqual([item.id for item in selected], ["disabled"])

    def test_candidate_category_filter_is_strict(self):
        mixed = source(
            id="mixed",
            categories=("models", "industry"),
            tier="secondary",
        )
        xml = """<rss><channel><item><title>Acme raises funding</title>
        <link>https://example.com/funding</link>
        <pubDate>Sun, 12 Jul 2026 23:00:00 GMT</pubDate></item></channel></rss>"""
        with patch.object(collector, "fetch_text", return_value=xml):
            payload, code = collector.collect(
                [mixed], 24, 10, observed_at=NOW, candidate_categories=["models"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(payload["hotspots"], [])

    def test_source_catalog_is_valid(self):
        rows = collector.load_sources(ROOT / "references" / "sources.json")
        ids = {item.id for item in rows}
        self.assertIn("openai", ids)
        self.assertIn("github-new-ai", ids)
        self.assertIn("models", next(item for item in rows if item.id == "google-news-en").categories)
        self.assertIn("opensource", next(item for item in rows if item.id == "techcrunch-ai").categories)
        self.assertEqual(next(item for item in rows if item.id == "github-new-ai").minimum_stars, 20)
        self.assertFalse(next(item for item in rows if item.id == "microsoft-ai").enabled)


if __name__ == "__main__":
    unittest.main()
