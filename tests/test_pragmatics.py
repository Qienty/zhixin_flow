"""四维语用配置：加一门语言只改配置，不改代码。"""
import json
import os
import unittest
from unittest.mock import patch

import test_regressions as fixtures
from app import ai, api, pragmatics


class PragmaticsTests(unittest.TestCase):
    setUp = fixtures.Regressions.setUp
    request = fixtures.Regressions.request

    def test_language_profiles_carry_the_four_dimensions(self):
        english = pragmatics.plan("en")
        japanese = pragmatics.plan("ja")
        self.assertEqual(set(english["dimensions"]),
                         {"directness", "honorific", "face_cost", "conclusion_position"})
        self.assertEqual(english["levels"]["directness"], "high")
        self.assertEqual(english["levels"]["honorific"], "low")
        self.assertEqual(english["dimensions"]["conclusion_position"], "early")
        self.assertEqual(japanese["levels"]["honorific"], "high")
        self.assertEqual(japanese["levels"]["face_cost"], "high")
        self.assertEqual(japanese["dimensions"]["conclusion_position"], "late")
        self.assertEqual(japanese["label"], "日语")

    def test_tone_and_relation_adjust_dimensions_with_clamping(self):
        base = pragmatics.plan("zh")
        polite = pragmatics.plan("zh", tone="polite")
        direct = pragmatics.plan("zh", tone="direct")
        self.assertLess(polite["dimensions"]["directness"], base["dimensions"]["directness"])
        self.assertGreater(direct["dimensions"]["directness"], base["dimensions"]["directness"])
        self.assertEqual(direct["dimensions"]["directness"], 3)  # 到顶后不再越界
        with_professor = pragmatics.plan("zh", relation="professor")
        self.assertGreater(with_professor["dimensions"]["honorific"], base["dimensions"]["honorific"])
        # 已经到顶的维度不会越界
        topped = pragmatics.plan("ja", tone="polite", relation="professor")
        self.assertEqual(topped["levels"]["honorific"], "high")
        self.assertIn(topped["levels"]["face_cost"], ("high",))

    def test_language_list_comes_from_configuration_only(self):
        with patch.dict(os.environ, {"TETHER_LANGUAGES": "zh,en,ja"}, clear=False):
            self.assertEqual(pragmatics.enabled_languages(), ["zh", "en", "ja"])
            self.assertTrue(pragmatics.is_enabled("ja"))
            self.assertFalse(pragmatics.is_enabled("ko"))
            options = pragmatics.options("en")
        self.assertEqual([item["code"] for item in options], ["zh", "en", "ja"])
        self.assertEqual(options[2]["label"], "Japanese")
        with patch.dict(os.environ, {"TETHER_LANGUAGES": "ja,ko,fr"}, clear=False):
            self.assertEqual(pragmatics.enabled_languages(), ["ja", "ko"])  # 未知语言被忽略
        with patch.dict(os.environ, {"TETHER_LANGUAGES": ""}, clear=False):
            self.assertEqual(pragmatics.enabled_languages(), ["zh", "en"])  # 回落到默认

    def test_meta_exposes_the_configured_languages(self):
        with patch.dict(os.environ, {"TETHER_LANGUAGES": "zh,en,ja"}, clear=False):
            response = self.request("GET", "/api/meta")
        self.assertEqual(response.status, 200)
        self.assertEqual([item["code"] for item in response.payload["languages"]], ["zh", "en", "ja"])

    def test_translate_rejects_languages_that_are_not_enabled(self):
        body = {"scene": "decline", "target_lang": "ja", "details": {"what": "加班"}}
        self.assertEqual(self.request("POST", "/api/expression/translate", body).status, 400)
        result = {"text": "x", "target_lang": "ja", "intent": "share", "cultural_notes": [], "safety": "ok"}
        with patch.dict(os.environ, {"TETHER_LANGUAGES": "zh,en,ja"}, clear=False), \
                patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "rewrite_expression", return_value=result) as rewrite:
            response = self.request("POST", "/api/expression/translate", body)
        self.assertEqual(response.status, 200, response.payload)
        self.assertEqual(rewrite.call_args.args[1], "ja")

    def test_model_payload_receives_the_pragmatics_plan(self):
        captured = {}

        def fake_chat(cfg, messages):
            captured["payload"] = json.loads(messages[-1]["content"].split("\n", 1)[1])
            return '{"text":"x","intent":"share","cultural_notes":[]}'

        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "_chat", side_effect=fake_chat):
            ai.rewrite_expression("加班到很晚", "ja", scene="decline", tone="polite", relation="client")
        plan = captured["payload"]["pragmatics"]
        self.assertEqual(plan["language"], "ja")
        self.assertEqual(plan["levels"]["honorific"], "high")
        self.assertIn("conclusion_position", plan["dimensions"])
        self.assertTrue(plan["guidance"])

    def test_target_language_name_comes_from_config_not_from_an_else_branch(self):
        captured = {}

        def fake_chat(cfg, messages):
            captured["payload"] = json.loads(messages[-1]["content"].split("\n", 1)[1])
            return '{"text":"x","intent":"share","cultural_notes":[]}'

        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "_chat", side_effect=fake_chat):
            japanese = ai.rewrite_expression("追加の修正", "ja", scene="decline")
            chinese = ai.rewrite_expression("追加修改", "zh", scene="decline")
        self.assertEqual(captured["payload"]["target_language"], "Simplified Chinese")
        self.assertEqual(chinese["target_lang"], "zh")
        self.assertEqual(pragmatics.model_name("ja"), "Japanese")
        self.assertEqual(pragmatics.model_name("ko"), "Korean")
        self.assertEqual(pragmatics.model_name("unknown"), "English")  # 未知回落到英语，不会静默变中文
        self.assertEqual(japanese["target_lang"], "ja")

    def test_interpretation_payload_uses_both_cultures(self):
        captured = {}

        def fake_chat(cfg, messages):
            captured["payload"] = json.loads(messages[-1]["content"].split("\n", 1)[1])
            return json.dumps({
                "meaning": "对方在提一个请求", "tone": "tense", "subtext": "可能不太满意",
                "replies": [{"tone": "polite", "text": "明白，我调整一下。"}], "cultural_notes": [],
            })

        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "_chat", side_effect=fake_chat):
            ai.interpret_message("I was surprised you didn't loop me in.", source_lang="en", target_lang="ja")
        self.assertEqual(captured["payload"]["source_pragmatics"]["language"], "en")
        self.assertEqual(captured["payload"]["target_pragmatics"]["language"], "ja")
        self.assertEqual(captured["payload"]["source_pragmatics"]["levels"]["directness"], "high")


if __name__ == "__main__":
    unittest.main()