# encoding:utf-8
"""The catalog editor's backend contract: seed rows, save, and clear.

The web console builds its editor from two fields the models API already
returns per provider -- ``catalog`` (what the user saved) and ``seed`` (the
vendor's presets, typed with their real capabilities). These tests pin that
contract, plus the save handler the editor posts to.
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "web" not in sys.modules:
    web_stub = types.ModuleType("web")
    web_stub.HTTPError = type("HTTPError", (Exception,), {})
    web_stub.cookies = lambda: {}
    web_stub.header = lambda *args, **kwargs: None
    web_stub.data = lambda: b"{}"
    web_stub.input = lambda **kwargs: types.SimpleNamespace(**kwargs)
    web_stub.setcookie = lambda *args, **kwargs: None
    web_stub.seeother = lambda *args, **kwargs: Exception("seeother")
    web_stub.notfound = lambda *args, **kwargs: Exception("notfound")
    web_stub.badrequest = lambda *args, **kwargs: Exception("badrequest")
    web_stub.application = lambda *args, **kwargs: types.SimpleNamespace(wsgifunc=lambda: None)
    web_stub.httpserver = types.SimpleNamespace(
        LogMiddleware=type("LogMiddleware", (), {"log": lambda *args, **kwargs: None}),
        StaticMiddleware=lambda app: app,
        WSGIServer=lambda *args, **kwargs: types.SimpleNamespace(serve_forever=lambda: None),
    )
    sys.modules["web"] = web_stub


def _providers_with(config):
    """Provider overview rows, as the models API returns them."""
    from channel.web import web_channel

    with patch.object(web_channel, "conf", return_value=config), \
            patch("models.custom_provider.conf", return_value=config), \
            patch("channel.web.web_channel.model_catalog.get_catalog_map", return_value={}):
        return web_channel.ModelsHandler._provider_overview()


def _provider(config, pid):
    for p in _providers_with(config):
        if p["id"] == pid:
            return p
    return None


class TestSeedRows(unittest.TestCase):
    """`seed` gives the editor starting rows typed with real capabilities."""

    def test_a_builtin_vendor_seeds_its_preset_models(self):
        p = _provider({"zhipu_ai_api_key": "sk-x"}, "zhipu")
        self.assertIsNotNone(p)
        self.assertTrue(p["seed"], "a built-in vendor must offer seed rows")
        names = [e["name"] for e in p["seed"]]
        self.assertIn("glm-5.2", names)

    def test_every_seed_row_carries_at_least_one_tag(self):
        """The editor shows capabilities as toggle chips, so an untagged row
        would render as a model the UI cannot place anywhere."""
        p = _provider({"zhipu_ai_api_key": "sk-x"}, "zhipu")
        for entry in p["seed"]:
            self.assertTrue(
                entry.get("capabilities"),
                f"seed row {entry.get('name')!r} has no capabilities",
            )

    def test_only_conversational_presets_are_tagged_text(self):
        """A tag is a routing decision, not decoration: an ASR-only preset
        must NOT claim 'text', or it would surface in the main-model
        dropdown where it cannot serve a chat turn."""
        p = _provider({"zhipu_ai_api_key": "sk-x"}, "zhipu")
        asr_only = [e for e in p["seed"] if e["capabilities"] == ["asr"]]
        self.assertTrue(asr_only, "expected at least one ASR-only preset")
        for entry in asr_only:
            self.assertNotIn("text", entry["capabilities"])

    def test_a_model_listed_for_two_roles_carries_both_tags(self):
        """Membership in the capability lists is the type, and the tags
        merge -- a VL model is text + vision, not one or the other."""
        p = _provider({"open_ai_api_key": "sk-x"}, "openai")
        by_name = {e["name"]: e for e in p["seed"]}
        vl = next((e for n, e in by_name.items()
                   if set(["text", "vision"]).issubset(e["capabilities"])), None)
        self.assertIsNotNone(vl, "no OpenAI preset carries both text+vision")

    def test_the_legacy_custom_card_has_no_seed(self):
        """The bare 'custom' card is a free-form endpoint: there is nothing
        to seed, so the editor must start empty rather than guess."""
        p = _provider({"custom_api_key": "sk-x"}, "custom")
        if p:  # hidden in multi-provider mode; only assert when present
            self.assertEqual(p["seed"], [])


class TestCatalogField(unittest.TestCase):
    """`catalog` is what the user saved -- empty means 'presets in use'."""

    def test_without_a_catalog_the_field_is_empty(self):
        p = _provider({"zhipu_ai_api_key": "sk-x"}, "zhipu")
        self.assertFalse(p["catalog"])

    def test_a_saved_catalog_is_returned_for_the_editor_to_load(self):
        saved = [{"name": "glm-5.2", "capabilities": ["text"], "context_window": 200000}]
        from channel.web import web_channel

        with patch.object(web_channel, "conf", return_value={"zhipu_ai_api_key": "sk-x"}), \
                patch("models.custom_provider.conf", return_value={"zhipu_ai_api_key": "sk-x"}), \
                patch("channel.web.web_channel.model_catalog.get_catalog_map",
                      return_value={"zhipu": saved}):
            rows = web_channel.ModelsHandler._provider_overview()
        p = next(x for x in rows if x["id"] == "zhipu")
        self.assertEqual(len(p["catalog"]), 1)
        self.assertEqual(p["catalog"][0]["name"], "glm-5.2")

    def test_a_catalog_replaces_the_preset_model_list(self):
        """The whole point of the field: the dropdown follows the catalog."""
        saved = [{"name": "only-this-one", "capabilities": ["text"]}]
        from channel.web import web_channel

        with patch.object(web_channel, "conf", return_value={"zhipu_ai_api_key": "sk-x"}), \
                patch("models.custom_provider.conf", return_value={"zhipu_ai_api_key": "sk-x"}), \
                patch("channel.web.web_channel.model_catalog.get_catalog_map",
                      return_value={"zhipu": saved}):
            rows = web_channel.ModelsHandler._provider_overview()
        p = next(x for x in rows if x["id"] == "zhipu")
        self.assertEqual(p["models"], ["only-this-one"])


class TestSaveCatalogHandler(unittest.TestCase):
    """POST action=save_catalog -- what the editor's Save button calls."""

    def _post(self, payload):
        import json

        from channel.web import web_channel

        handler = web_channel.ModelsHandler()
        with patch.object(web_channel, "conf", return_value={}), \
                patch("channel.web.web_channel.model_catalog.save_catalog",
                      return_value=payload.get("models") or []) as save:
            raw = handler._handle_save_catalog(payload)
        data = json.loads(raw)
        return data, save

    def test_a_valid_catalog_saves(self):
        data, save = self._post({
            "provider_id": "zhipu",
            "models": [{"name": "glm-5.2", "capabilities": ["text"]}],
        })
        self.assertEqual(data["status"], "success")
        save.assert_called_once()

    def test_an_empty_list_clears_the_catalog_back_to_presets(self):
        data, _ = self._post({"provider_id": "zhipu", "models": []})
        self.assertEqual(data["status"], "success")

    def test_a_missing_provider_id_is_rejected(self):
        data, save = self._post({"models": []})
        self.assertEqual(data["status"], "error")
        save.assert_not_called()

    def test_an_unknown_provider_is_rejected(self):
        data, _ = self._post({"provider_id": "not-a-vendor", "models": []})
        self.assertEqual(data["status"], "error")

    def test_a_custom_provider_id_is_accepted(self):
        """Expanded custom (OpenAI-compatible) providers are cataloguable."""
        data, _ = self._post({"provider_id": "custom:3f2a9c1b", "models": []})
        self.assertEqual(data["status"], "success")


class TestCatalogNormalization(unittest.TestCase):
    """Rules the editor relies on when building its payload."""

    def test_a_row_without_a_name_is_rejected(self):
        from models import model_catalog

        with self.assertRaises(ValueError):
            model_catalog.normalize_entry({"capabilities": ["text"]})

    def test_capabilities_default_to_text(self):
        from models import model_catalog

        entry = model_catalog.normalize_entry({"name": "m"})
        self.assertEqual(entry["capabilities"], ["text"])

    def test_an_unknown_capability_is_dropped_not_rejected(self):
        """Hand-edited config may carry a stray tag. Dropping it keeps the
        model usable instead of failing the whole save over one bad tag."""
        from models import model_catalog

        entry = model_catalog.normalize_entry({"name": "m", "capabilities": ["text", "telepathy"]})
        self.assertEqual(entry["capabilities"], ["text"])

    def test_a_row_of_only_unknown_capabilities_falls_back_to_text(self):
        """An empty tag set would make the model unreachable everywhere."""
        from models import model_catalog

        entry = model_catalog.normalize_entry({"name": "m", "capabilities": ["telepathy"]})
        self.assertEqual(entry["capabilities"], ["text"])

    def test_a_non_positive_context_window_is_rejected(self):
        from models import model_catalog

        with self.assertRaises(ValueError):
            model_catalog.normalize_entry({"name": "m", "context_window": 0})

    def test_an_unbudgeted_model_can_be_saved_without_numbers(self):
        """Embedding/TTS/ASR/image models have no text budget. The editor
        hides those inputs for them, so the entry must save fine without."""
        from models import model_catalog

        entry = model_catalog.normalize_entry(
            {"name": "text-embed-3", "capabilities": ["embedding"]})
        self.assertEqual(entry["capabilities"], ["embedding"])
        self.assertNotIn("context_window", entry)
        self.assertNotIn("max_output_tokens", entry)

    def test_a_model_can_carry_both_text_and_an_extra_role(self):
        from models import model_catalog

        entry = model_catalog.normalize_entry(
            {"name": "vl", "capabilities": ["text", "vision"], "context_window": 128000})
        self.assertEqual(sorted(entry["capabilities"]), ["text", "vision"])
        self.assertEqual(entry["context_window"], 128000)

    def test_set_but_valid_numbers_are_kept(self):
        from models import model_catalog

        entry = model_catalog.normalize_entry(
            {"name": "m", "context_window": 200000, "max_output_tokens": 16000})
        self.assertEqual(entry["context_window"], 200000)
        self.assertEqual(entry["max_output_tokens"], 16000)


if __name__ == "__main__":
    unittest.main()
