from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_hotspots.py"
SPEC = importlib.util.spec_from_file_location("verify_hotspots", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class HtmlExtractionTests(unittest.TestCase):
    def test_extracts_metadata_text_and_image(self):
        raw = """<html><head><title>Official launch</title>
        <meta name="description" content="Launch details">
        <meta property="og:image" content="/hero.png"></head>
        <body><main><h1>Official launch</h1><p>Confirmed product details.</p></main></body></html>"""
        evidence = verifier.evidence_from_html(
            "https://openai.com/test", "https://openai.com/test", raw, "http"
        )
        self.assertEqual(evidence.title, "Official launch")
        self.assertEqual(evidence.description, "Launch details")
        self.assertEqual(evidence.source_level, "primary_candidate")
        self.assertIn("Confirmed product details", evidence.text_excerpt)
        self.assertEqual(evidence.image_suggestions[0]["url"], "https://openai.com/hero.png")

    def test_source_level_classification(self):
        self.assertEqual(verifier.source_level("https://github.com/acme/repo"), "primary_candidate")
        self.assertEqual(verifier.source_level("https://www.reuters.com/technology/x"), "strong_secondary")
        self.assertEqual(verifier.source_level("https://news.google.com/articles/x"), "aggregator")

    def test_primary_search_result_scores_higher(self):
        primary = verifier.search_result_score(
            "https://anthropic.com/news/acme", "Acme launch", "Acme launch"
        )
        media = verifier.search_result_score(
            "https://the-decoder.com/acme", "Acme launch", "Acme launch"
        )
        self.assertGreater(primary, media)

    def test_entity_search_targets_likely_official_domain(self):
        queries = verifier.build_search_queries("商汤发布并开源SenseNova-Vision统一视觉大模型")
        self.assertTrue(queries[0].startswith("site:sensetime.com"))
        self.assertIn("sensenova-vision", queries[0])

    def test_rank_search_rows_drops_aggregators(self):
        rows = [
            {"url": "https://news.google.com/articles/x", "title": "Acme"},
            {"url": "https://github.com/acme/model", "title": "Acme model"},
        ]
        ranked = verifier.rank_search_rows(rows, "Acme model", 5)
        self.assertEqual([row["url"] for row in ranked], ["https://github.com/acme/model"])


class InputTests(unittest.TestCase):
    def test_missing_shortlist_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"hotspots": []}', encoding="utf-8")
            with self.assertRaises(verifier.VerifyError):
                verifier.load_payload(str(path))


if __name__ == "__main__":
    unittest.main()
