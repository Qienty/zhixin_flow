"""四维语用配置：直接性 / 敬语层级 / 面子成本 / 结论位置。

设计目标是「加一门语言 = 加一条配置」，而不是改提示词、接口或前端：

- 每种语言（文化）给一组基础维度，写成数据；
- 语气与对象只做加减调整，再夹到 1—3 之间；
- 生成时把这组维度作为结构化事实传给模型，模型只负责措辞，不负责猜文化。

这样同一句「拒绝」，对英语同事、对日本客户、对德国上级，差异来自配置，
而不是来自提示词里写死的一段「英美职场习惯」。
"""
from __future__ import annotations

import os

# 1 = 低，2 = 中，3 = 高
LOW, MID, HIGH = 1, 2, 3

CONCLUSION_ORDER = ("early", "after_context", "late")
CONCLUSION_LABEL = {
    "early": {"zh": "结论放第一句", "en": "conclusion first"},
    "after_context": {"zh": "先交代背景再说请求", "en": "context before request"},
    "late": {"zh": "请求放在最后", "en": "request at the end"},
}

LANGUAGES = {
    "en": {
        "label": {"zh": "英语", "en": "English"},
        "model_name": "English",
        "directness": HIGH,
        "honorific": LOW,
        "face_cost": LOW,
        "conclusion": "early",
        "guide": "结论放第一句；缓冲句最多一层；不要连续道歉；避免过度自谦（会显得不自信）。",
    },
    "zh": {
        "label": {"zh": "中文", "en": "Chinese"},
        "model_name": "Simplified Chinese",
        "directness": MID,
        "honorific": MID,
        "face_cost": MID,
        "conclusion": "after_context",
        "guide": "先交代背景再说请求；保留一层面子缓冲；对上级与客户用完整敬语，对平级少客套。",
    },
    "ja": {
        "label": {"zh": "日语", "en": "Japanese"},
        "model_name": "Japanese",
        "directness": LOW,
        "honorific": HIGH,
        "face_cost": HIGH,
        "conclusion": "late",
        "guide": "以缓冲句开头；请求放在最后；少用直接否定，用「検討します」式的余地表达，把拒绝留给对方解读。",
    },
    "ko": {
        "label": {"zh": "韩语", "en": "Korean"},
        "model_name": "Korean",
        "directness": MID,
        "honorific": HIGH,
        "face_cost": HIGH,
        "conclusion": "late",
        "guide": "先确认关系与职级称谓；敬语层级必须一致；否定请求时给一个替代时间，不要只说不。",
    },
    "de": {
        "label": {"zh": "德语", "en": "German"},
        "model_name": "German",
        "directness": HIGH,
        "honorific": LOW,
        "face_cost": LOW,
        "conclusion": "early",
        "guide": "直接说明事实与请求，不要用缓冲句堆砌客气；直接性本身不等于失礼。",
    },
}

TONES = {
    "polite": {"directness": -1, "face_cost": +1, "conclusion": -1, "note": "更稳妥：减少断言、留一处缓冲。"},
    "direct": {"directness": +1, "face_cost": -1, "conclusion": +1, "note": "更直接：结论前置、去掉多余铺垫。"},
    "close": {"directness": +1, "honorific": -1, "face_cost": -1, "note": "更亲近：口语化，但不越界、不替对方做决定。"},
}

RELATIONS = {
    "professor": {"honorific": +1, "face_cost": +1, "directness": -1, "note": "对象是教授/老师：完整称谓、完整句式。"},
    "manager": {"honorific": +1, "face_cost": +1, "note": "对象是上级：先说结论，但保留一处致意。"},
    "client": {"honorific": +1, "face_cost": +1, "directness": -1, "note": "对象是客户：先确认诉求，再给方案与时间。"},
    "peer": {"note": "对象是同事/同学：平级语气，不必堆敬语。"},
    "friend": {"honorific": -1, "face_cost": -1, "directness": +1, "note": "对象是朋友：短句口语，避免公文腔。"},
}

DEFAULT_LANGUAGES = ("zh", "en")


def _clamp(value: int, low: int = 1, high: int = 3) -> int:
    return max(low, min(high, value))


def _level(value: int) -> str:
    return {1: "low", 2: "medium", 3: "high"}[value]


def enabled_languages() -> list[str]:
    """TETHER_LANGUAGES 决定对外可选的语言；默认中英，加语言只改这一行配置。"""
    raw = (os.environ.get("TETHER_LANGUAGES") or "").strip()
    codes = [code.strip().lower() for code in raw.split(",") if code.strip()] if raw else []
    picked = [code for code in codes if code in LANGUAGES]
    if not picked:
        picked = list(DEFAULT_LANGUAGES)
    return picked


def is_enabled(code: str) -> bool:
    return code in enabled_languages()


def model_name(code: str) -> str:
    """给模型的「目标语言」名称；必须来自配置，不能写死 en/else 中文。"""
    profile = LANGUAGES.get(code)
    return profile["model_name"] if profile else "English"


def options(ui_lang: str = "zh") -> list[dict]:
    """给 /api/meta 用：前端据此渲染语言下拉框，不用改代码。"""
    table = LANGUAGES
    return [
        {"code": code, "label": table[code]["label"].get(ui_lang) or table[code]["label"]["zh"]}
        for code in enabled_languages()
    ]


def plan(language: str, tone: str = "", relation: str = "") -> dict:
    """把语言基础维度 + 语气 + 对象折算成一组可传给模型的语用事实。"""
    profile = LANGUAGES.get(language)
    if profile is None:
        return {}
    dimensions = {
        "directness": profile["directness"],
        "honorific": profile["honorific"],
        "face_cost": profile["face_cost"],
    }
    conclusion = profile["conclusion"]
    notes = [profile["guide"]]
    if tone in TONES:
        for key, delta in TONES[tone].items():
            if key == "note":
                continue
            if key == "conclusion":
                index = _clamp(CONCLUSION_ORDER.index(conclusion) + delta, 0, len(CONCLUSION_ORDER) - 1)
                conclusion = CONCLUSION_ORDER[index]
            else:
                dimensions[key] = _clamp(dimensions[key] + delta)
        notes.append(TONES[tone]["note"])
    if relation in RELATIONS:
        for key, delta in RELATIONS[relation].items():
            if key == "note":
                continue
            if key in dimensions:
                dimensions[key] = _clamp(dimensions[key] + delta)
        notes.append(RELATIONS[relation]["note"])
    return {
        "language": language,
        "label": profile["label"]["zh"],
        "dimensions": {**dimensions, "conclusion_position": conclusion},
        "levels": {
            "directness": _level(dimensions["directness"]),
            "honorific": _level(dimensions["honorific"]),
            "face_cost": _level(dimensions["face_cost"]),
            "conclusion_position": conclusion,
        },
        "conclusion_label": CONCLUSION_LABEL[conclusion]["zh"],
        "guidance": notes,
    }