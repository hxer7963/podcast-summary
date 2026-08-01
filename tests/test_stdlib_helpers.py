import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


volc_asr = load_script("volc_asr.py")
xiaoyuzhou = load_script("xiaoyuzhou_download.py")


class VolcAsrTests(unittest.TestCase):
    def test_submit_uses_api_key_and_resource_id(self):
        captured = {}

        def fake_post(url, headers, payload):
            captured.update(url=url, headers=headers, payload=payload)
            return {"x-api-status-code": "20000000"}, b"{}"

        with patch.object(volc_asr, "_post_json", side_effect=fake_post):
            request_id = volc_asr.submit_task(
                "secret", "https://example.com/audio.mp3", request_id="request-id"
            )
        self.assertEqual(request_id, "request-id")
        self.assertEqual(captured["headers"]["X-Api-Key"], "secret")
        self.assertEqual(captured["headers"]["X-Api-Resource-Id"], "volc.seedasr.auc")

    def test_direct_audio_creates_stable_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PODCAST_OUTPUT_DIR": directory}):
                first = volc_asr.default_episode_dir("https://example.com/episode.mp3")
                second = volc_asr.default_episode_dir("https://example.com/episode.mp3")
                self.assertEqual(first, second)
                first.mkdir(parents=True)
                volc_asr.ensure_readme(first, "https://example.com/episode.mp3", "Episode")
                self.assertIn("Audio URL:", first.joinpath("README.md").read_text())


class XiaoyuzhouTests(unittest.TestCase):
    def test_shownotes_conversion_has_no_third_party_dependency(self):
        parser = xiaoyuzhou.HTMLTextParser()
        parser.feed("<p>Hello <b>world</b></p><p>Next</p>")
        self.assertIn("Hello world", parser.text())
        self.assertIn("Next", parser.text())


if __name__ == "__main__":
    unittest.main()
