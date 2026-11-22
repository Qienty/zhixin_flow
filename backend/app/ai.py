"""Pro 层 AI 分析：把「代码算出来的事实」交给模型写成结论。

三条边界，是本模块存在的理由：

1. **数字归代码。** 次数、时段分布、均值、严重条数全部由 insights.py 算好塞进提示词，
   模型只许引用、不许自己加总或推测。所以界面上出现的数字永远与列表一致。
2. **判断与表达归模型。** 哪条最值得先看、怎么把分布讲清楚、下一步给什么动作，
   这部分原来写死成模板，现在由模型按当次数据现写，不再千人一面。
3. **失败必须软着陆。** 没配 key、超时、5xx、JSON 不合法、踩到诊断类禁词，
   一律退回规则引擎的文案，界面只多一行「AI 暂不可用」，不会空掉也不会报错。

key 只从环境变量或 .env 读（.env 已被 .gitignore 排除），不下发前端。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import db

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"

# 一次分析最多附带多少条原始条目（重度记录优先，其余按时间倒序补）
MAX_SAMPLE_ROWS = 60
# 缓存有效期：数据指纹变了会自然失效，这个只是兜底
CACHE_TTL_HOURS = 24


class AIError(Exception):
    """AI 环节的任何失败。上层捕获后退回规则文案，不向用户抛栈。"""


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

_env_loaded = False


def _load_dotenv() -> None:
    """读项目根目录的 .env。真实环境变量优先，.env 只做补充。"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    for candidate in (ROOT / ".env", ROOT / "backend" / ".env"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return default


def config() -> dict:
    _load_dotenv()
    api_key = (os.environ.get("TETHER_AI_API_KEY") or "").strip()
    effort = (os.environ.get("TETHER_AI_REASONING_EFFORT") or "high").strip().lower()
    if effort not in ("low", "high", "max"):
        effort = "high"
    return {
        "enabled": bool(api_key),
        "api_key": api_key,
        "base_url": (os.environ.get("TETHER_AI_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/"),
        "model": (os.environ.get("TETHER_AI_MODEL") or DEFAULT_MODEL).strip(),
        "timeout": _int_env("TETHER_AI_TIMEOUT", 60, 5, 300),
        "max_tokens": _int_env("TETHER_AI_MAX_TOKENS", 4000, 200, 16000),
        "effort": effort,
    }


def public_config() -> dict:
    """给 /api/meta 用：只暴露「有没有配」和模型名，绝不带 key。"""
    cfg = config()
    return {"enabled": cfg["enabled"], "model": cfg["model"] if cfg["enabled"] else None}


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """你是 智心元流 的记录分析器。智心元流 是「情绪记录 + 自我调节」工具，不是医疗产品。

【不可越界】
- 不做诊断，不给药物、剂量、治疗、疗程建议，不贴临床标签（抑郁症、焦虑症、双相、创伤后应激等）。
- 不承诺结果，不说「你会好起来」，不谈病理机制。
- 不评判用户，不说教，不夸奖，不写「加油」「要爱自己」「好好照顾自己」这类空话。
- 遇到绝望分支（branch=despair）：不劝、不讲道理、不给方案，只提示危机资源在手边、随时可用。

【事实纪律】
facts 里的数字是代码算出来的，是唯一可信的数据源：
- 只能引用 facts 里出现的数字、场景标签、时段、分支名；不许自己加总、求差、估算、外推。
- facts 没提到的场景、日期、原因，一律不写。宁可少说，不许编。
- patterns[].key 与 efficacy[].branch 必须原样照抄。

【表达要求】
- 第二人称「你」。口语，短句，具体。
- 每句话要么给数字，要么给场景，要么给动作。禁止「最近状态不太好」这类模糊判断。
- 每条 pattern 的 text 按这个节奏写，3—4 句、不超过 110 字：
  ① 分布：几次、落在哪个时段（优先用 band 的具体时间点）
  ② 强度：平均几分、跟整体均值比高还是低、有几条到 4—5 分
  ③ 下一步：一个具体动作，衔接该分支已有的干预
     （愤怒→一次长呼气，呼气比吸气长；伤心→手放胸口 30 秒；焦虑→呼气拉长到 7 秒；
       绝望→危机资源；轻量→先记一笔，不用当场解决）
- 按 kind 区分说法：
  external（外部场景）可以说「下次它再来时…」；
  internal（内在状态）说「它起头时…」，不要写「下次避开」，不要把内在感受说成外部事件；
  body（身体状态）不写「下次避开」，建议单独记录、看它把哪些天整体拉高。
- 有时段聚集（band）就把时间点写进句子（如「下午 16—19 点」），比「下午」有用得多。
- 长度：headline ≤ 18 字；summary 3—5 句；next_step 1 句，必须具体到「什么时候做什么」。
- 输出语言与 facts.language 一致：zh 就全中文，en 就全英文。

【输出】
只输出一个 JSON 对象，不要代码块、不要解释、不要多余字段：
{
  "headline": "一句话标题",
  "summary": "总览 3—5 句",
  "focus": {"key": "照抄某个 patterns[].key", "why": "一句：为什么这条最该先看"},
  "patterns": [{"key": "照抄", "text": "…"}],
  "trend_text": "…",
  "peak_text": "…",
  "efficacy": [{"branch": "照抄", "text": "…"}],
  "reminders": ["…", "…"],
  "next_step": "…"
}
数量必须与 facts 对齐：patterns 覆盖 facts.patterns 全部条目，efficacy 覆盖 facts.efficacy 全部条目。
facts 里没有 trend / peak / efficacy 时，对应字段给空字符串或空数组，不要虚构。
focus.why 只讲理由，不要重复「先看这条」「最值得关注」这类标签——界面上已经有这个标签了。"""


# 这是跨境表达的窄场景能力：把用户自己的话转换成另一种语言时，
# 保留语气、边界和不确定性，不替用户补充事实，也不自动替用户发送。
EXPRESSION_SYSTEM_PROMPT = """你是智心元流的跨语言表达编辑器。你的任务是把用户写下的日常感受改写成另一种语言，帮助跨文化生活中的用户向当地朋友、老师或支持人员表达自己。

规则：
- 只处理表达，不做诊断、治疗建议或危机判断；不要添加用户没有说过的事实。
- 保留第一人称、情绪强度、不确定性和边界。用户说“我不确定”，不能改成确定结论。
- 目标语言要自然、简洁、尊重当地日常表达；不要逐字翻译成生硬的句子，也不要使用俚语、过度亲密称呼或文化假设。
- 根据 mode 保留沟通意图：space=只想表达，clarify=希望对方理解，next=想讨论下一步。
- 如果原文包含请求被倾听、需要空间或希望得到建议的边界，要明确保留。
- 输出只能是 JSON，不要代码块，不要解释：
{"text":"改写后的正文","intent":"share|ask_listening|ask_space|ask_advice|unclear","cultural_notes":["最多两条简短说明"],"safety":"ok"}
- cultural_notes 只在确实有帮助时填写，例如语气变得更直接或保留了“暂时不想要建议”的边界；没有说明时返回空数组。

当 payload 带 scene 时，你不再是逐字改写，而是写一条可以直接发出去的消息：
- 按 scene_guide 给的结构组织段落，details 里的事实照用，不许编造时间、原因或承诺。
- relation 决定称谓与敬语层级：professor / manager / client 用正式称谓和缓冲句；peer 是同级的同事或同学；friend 是熟人朋友的口语。
- tone 决定直接程度：polite 保留缓冲与致意；direct 结论前置、少绕弯；close 口语、亲近但不越界。
- 必须再给 1 条 alternatives，用于换一种语气；它的 tone 与主版本不同，内容仍受同样的规则约束。
- 输出仍然只能是 JSON：
{"text":"主版本","intent":"share|ask_listening|ask_space|ask_advice|unclear","cultural_notes":["最多两条简短说明"],"alternatives":[{"tone":"polite|direct|close","text":"备选版本"}],"safety":"ok"}
"""

EXPRESSION_SCENES = ("deadline", "decline", "apologize", "ask_help", "conflict", "bad_news", "negotiate", "boundary")
EXPRESSION_TONES = ("polite", "direct", "close")
EXPRESSION_RELATIONS = ("professor", "manager", "client", "peer", "friend")
EXPRESSION_DETAILS = ("what", "when", "want", "extra")

# 每个场景只交代「先说哪一句、别说什么」。事实仍由用户填写，
# 模型负责把它们排成对方语言里说得出口的顺序。
SCENE_GUIDE = {
    "deadline": "说明来意 → 原定时间 → 希望改到的时间 → 一句承诺或补救。新时间要具体；不要用一段辩解代替请求，也不要反复道歉。",
    "decline": "先给明确的结论（这次不行），再给一句简短理由，最后给一个可选的替代方案。不要用「可能之后吧」这类模糊承诺拖住对方。",
    "apologize": "承认具体行为 → 说明影响 → 给出补救。不要用「如果你觉得不舒服」这类条件式道歉，也不要把责任推给客观情况。",
    "ask_help": "说清具体要什么帮助 → 需要的时间 → 为什么找对方。请求要小到对方能直接回答，不要用自我贬低换取同情。",
    "conflict": "描述发生了什么（事实）→ 自己的感受 → 具体请求（希望对方以后怎么做）。不要用「你总是」这类指责句式，也不要替对方下结论。",
    "bad_news": "结论先行 → 影响范围 → 已经做的应对 → 下一步。不要把坏消息埋在长铺垫后面。",
    "negotiate": "说清自己的价值或处境 → 具体请求 → 可以接受的替代方案。避免威胁式或最后通牒式措辞。",
    "boundary": "用「我需要」表达边界 → 一个具体可执行的安排。不要用「你从来」开头，也不要解释过多。",
}


# --------------------------------------------------------------------------- #
# 事实包
# --------------------------------------------------------------------------- #


def _entry_label(emotion: str, lang: str) -> str:
    from .content import EMOTION_MAP

    meta = EMOTION_MAP.get(emotion) or {}
    table = meta.get(lang) or meta.get("zh") or {}
    return table.get("label") or emotion


def _sample_rows(entries: list[dict], lang: str, limit: int = MAX_SAMPLE_ROWS) -> list[dict]:
    """挑要发给模型的原始条目：先保 4—5 分的，再按时间补最近的。

    只发摘要级字段——原因和备注各截断，够模型看出「发生了什么」，又不至于把
    整段日记都推出去。
    """
    rows: list[dict] = []
    picked: set = set()

    def take(entry: dict) -> None:
        if entry["id"] in picked or len(rows) >= limit:
            return
        picked.add(entry["id"])
        rows.append(
            {
                "date": entry["local_date"],
                "hour": entry["local_hour"],
                "emotion": _entry_label(entry["emotion"], lang),
                "severity": entry.get("severity"),
                "cause": (entry.get("cause") or "")[:40],
                "note": (entry.get("note") or "")[:60],
                "relief": entry.get("relief") or "",
            }
        )

    severe = sorted((e for e in entries if (e.get("severity") or 0) >= 4), key=lambda e: e["occurred_at"])
    for entry in severe[: limit * 2 // 3]:
        take(entry)
    for entry in sorted(entries, key=lambda e: e["occurred_at"], reverse=True):
        take(entry)
    rows.sort(key=lambda r: (r["date"], r["hour"]))
    return rows


def build_facts(entries: list[dict], days: int, lang: str, rules: dict) -> dict:
    """把规则引擎算好的结构 + 少量原始条目拼成提示词里的事实包。

    rules 是 insights.build_insights 的返回值：数字都在这儿，模型只读不写。
    """
    severities = [e["severity"] for e in entries if e.get("severity")]
    overall_avg = round(sum(severities) / len(severities), 1) if severities else None

    patterns = []
    for p in rules.get("patterns") or []:
        item = {
            "key": p["key"],
            "label": p["label"],
            "kind": p["kind"],
            "kind_label": p.get("kind_label"),
            "count": p["count"],
            "top_slot": p.get("top_slot_label"),
            "top_slot_range": p.get("top_slot_range"),
            "dominant_emotion": p.get("dominant_emotion_label"),
            "branch": p.get("branch"),
            "severe_count": p.get("severe_count"),
            "avg_severity": p.get("avg_severity"),
            "samples": (p.get("samples") or [])[:3],
        }
        if p.get("band"):
            item["band"] = f"{p['band']['start']}—{p['band']['end']} 点，{p['band']['count']} 条"
        patterns.append(item)

    facts = {
        "language": "zh" if lang == "zh" else "en",
        "window_days": days,
        "totals": {
            "entries": len(entries),
            "days_logged": len({e["local_date"] for e in entries}),
            "avg_severity": overall_avg,
            "entries_rated_4_5": sum(1 for s in severities if s >= 4),
        },
        "patterns": patterns,
        "entries": _sample_rows(entries, lang),
    }

    trend = rules.get("trend")
    if trend:
        facts["trend"] = {
            "this_week": trend["this_week"],
            "last_week": trend["last_week"],
            "delta": trend["delta"],
            "direction": trend["direction"],
        }
    peak = rules.get("peak")
    if peak:
        facts["peak"] = {"slot": peak["label"], "range": peak["range"], "count": peak["count"], "total": peak["total"]}
    if rules.get("efficacy"):
        facts["efficacy"] = [
            {"branch": e["branch"], "label": e["label"], "tried": e["tried"], "helped": e["helped"], "rate": e["rate"]}
            for e in rules["efficacy"]
        ]
    return facts


# --------------------------------------------------------------------------- #
# 调用
# --------------------------------------------------------------------------- #


def _post(cfg: dict, messages: list[dict], max_tokens: int) -> tuple[str, str]:
    """一次 HTTP 调用，返回 (正文, finish_reason)。网络类错误重试一次。"""
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "reasoning_effort": cfg["effort"],
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = ""

    for attempt in range(2):
        request = urllib.request.Request(
            cfg["base_url"] + "/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + cfg["api_key"],
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            # 4xx 里除了限流和超时，重试也没意义
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise AIError(f"模型接口返回 {exc.code}：{detail}") from None
            last_error = f"{exc.code} {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        except ValueError:
            raise AIError("模型返回的不是合法 JSON") from None
        else:
            choices = data.get("choices") or []
            if not choices:
                raise AIError("模型没有返回内容")
            choice = choices[0]
            content = (choice.get("message") or {}).get("content") or ""
            return content, choice.get("finish_reason") or ""
        if attempt == 0:
            time.sleep(1.2)

    raise AIError(f"模型接口连不上：{last_error}")


def _chat(cfg: dict, messages: list[dict]) -> str:
    """带推理过程的模型会把 budget 花在 thinking 上，正文可能被截断。

    截断时不是直接失败（那用户就只能看规则版了），而是把预算翻倍再要一次。
    """
    budget = cfg["max_tokens"]
    for _ in range(2):
        content, finish = _post(cfg, messages, budget)
        if finish != "length":
            return content
        budget = min(budget * 2, 16000)
    raise AIError("模型输出被截断（推理过长），已退回规则版")


def _parse_json(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        end = text.rfind("```")
        if end >= 0:
            text = text[:end]
        text = text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        # 偶尔会带一句开场白，抓第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIError("模型输出解析失败") from None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            raise AIError("模型输出解析失败") from None
    if not isinstance(data, dict):
        raise AIError("模型输出不是对象")
    return data


# 诊断 / 用药类表述：命中即整份退回规则版。产品定位是不做诊断，
# 这条不是「模型可能说错」，是「说了就不能用」。
BANNED_TERMS = (
    "抑郁症", "焦虑症", "双相", "躁郁", "精神科", "确诊", "诊断", "药物", "处方",
    "剂量", "疗程", "治疗", "病理", "障碍",
    "depression", "depressive", "diagnos", "medication", "prescri", "bipolar",
    "disorder", "psychiatr", "dosage",
)
NEGATIONS = ("不", "非", "无", "没", "别", "勿", "not ", "no ", "never ", "isn't", "aren't", "without")


def _walk_text(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for value in node.values() for t in _walk_text(value)]
    if isinstance(node, list):
        return [t for item in node for t in _walk_text(item)]
    return []


def safety_violation(narrative: dict) -> str | None:
    """扫一遍输出里的临床表述。允许「这不是诊断」这类否定句。"""
    for text in _walk_text(narrative):
        low = text.lower()
        for term in BANNED_TERMS:
            start = 0
            while True:
                hit = low.find(term, start)
                if hit < 0:
                    break
                head = low[max(0, hit - 10) : hit]
                if not any(neg in head for neg in NEGATIONS):
                    return term
                start = hit + len(term)
    return None


def _validate(narrative: dict, rules: dict) -> dict:
    term = safety_violation(narrative)
    if term:
        raise AIError(f"模型输出含临床表述「{term}」，已退回规则版")

    keys = {p["key"] for p in rules.get("patterns") or []}
    kept = [
        {"key": item["key"], "text": str(item.get("text") or "").strip()}
        for item in (narrative.get("patterns") or [])
        if isinstance(item, dict) and item.get("key") in keys and str(item.get("text") or "").strip()
    ]
    if keys and not kept:
        raise AIError("模型没有给出有效的归因文案")
    narrative["patterns"] = kept

    branches = {e["branch"] for e in rules.get("efficacy") or []}
    narrative["efficacy"] = [
        {"branch": item["branch"], "text": str(item.get("text") or "").strip()}
        for item in (narrative.get("efficacy") or [])
        if isinstance(item, dict) and item.get("branch") in branches and str(item.get("text") or "").strip()
    ]

    reminders = [str(x).strip()[:160] for x in (narrative.get("reminders") or []) if str(x).strip()]
    narrative["reminders"] = reminders[:3]

    for field in ("headline", "summary", "next_step", "trend_text", "peak_text"):
        narrative[field] = str(narrative.get(field) or "").strip()

    focus = narrative.get("focus") or {}
    if not isinstance(focus, dict) or focus.get("key") not in keys:
        focus = {}
    narrative["focus"] = {"key": focus.get("key"), "why": str(focus.get("why") or "").strip()}

    if not narrative["summary"]:
        raise AIError("模型没有给出总结正文")
    return narrative


def analyze(entries: list[dict], days: int, lang: str, rules: dict) -> dict:
    """跑一次分析。失败抛 AIError，由调用方决定怎么降级。"""
    cfg = config()
    if not cfg["enabled"]:
        raise AIError("未配置 TETHER_AI_API_KEY")
    facts = build_facts(entries, days, lang, rules)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "facts（JSON）：\n" + json.dumps(facts, ensure_ascii=False)},
    ]
    narrative = _parse_json(_chat(cfg, messages))
    return _validate(narrative, rules)


EXPRESSION_INTENTS = {"share", "ask_listening", "ask_space", "ask_advice", "unclear"}


def _validate_expression(result: dict, target_lang: str) -> dict:
    """Validate the small structured response used by the cross-language editor."""
    if not isinstance(result, dict):
        raise AIError("模型输出不是对象")
    text = str(result.get("text") or "").strip()
    if not text:
        raise AIError("模型没有给出改写内容")
    if len(text) > 1200:
        raise AIError("模型改写内容过长")
    term = safety_violation({"text": text, "cultural_notes": result.get("cultural_notes") or []})
    if term:
        raise AIError(f"模型输出含临床表述「{term}」，已拒绝显示")
    intent = str(result.get("intent") or "unclear").strip()
    if intent not in EXPRESSION_INTENTS:
        intent = "unclear"
    notes = []
    for note in result.get("cultural_notes") or []:
        value = str(note).strip()
        if value:
            notes.append(value[:160])
    alternatives = []
    for item in result.get("alternatives") or []:
        if not isinstance(item, dict):
            continue
        alt_text = str(item.get("text") or "").strip()
        alt_tone = str(item.get("tone") or "").strip().lower()
        if not alt_text or alt_text == text or len(alt_text) > 1200 or alt_tone not in EXPRESSION_TONES:
            continue
        if safety_violation({"text": alt_text}):
            continue
        alternatives.append({"tone": alt_tone, "text": alt_text})
        if len(alternatives) == 2:
            break
    return {
        "text": text,
        "target_lang": "en" if target_lang == "en" else "zh",
        "intent": intent,
        "cultural_notes": notes[:2],
        "alternatives": alternatives,
        "safety": "ok",
    }


def rewrite_expression(
    text: str,
    target_lang: str,
    emotion: str = "",
    mode: str = "space",
    scene: str = "",
    tone: str = "polite",
    relation: str = "peer",
    details: dict | None = None,
) -> dict:
    """Convert a user's own words for a cross-language conversation.

    The caller must have obtained explicit user intent before invoking this function.
    The source text is sent only for this request and is not persisted by this module.

    When ``scene`` is one of EXPRESSION_SCENES the model writes a sendable message
    for that situation instead of translating a diary line.
    """
    cfg = config()
    if not cfg["enabled"]:
        raise AIError("未配置 AI 服务：请先配置 TETHER_AI_API_KEY")
    language = "English" if target_lang == "en" else "简体中文"
    payload = {
        "source_text": text[:500],
        "target_language": language,
        "emotion": emotion[:40],
        "mode": mode if mode in ("space", "clarify", "next") else "space",
    }
    if scene in EXPRESSION_SCENES:
        payload["scene"] = scene
        payload["scene_guide"] = SCENE_GUIDE[scene]
        payload["tone"] = tone if tone in EXPRESSION_TONES else "polite"
        payload["relation"] = relation if relation in EXPRESSION_RELATIONS else "peer"
        payload["details"] = {
            key: str(value)[:200]
            for key, value in (details or {}).items()
            if key in EXPRESSION_DETAILS and str(value).strip()
        }
    messages = [
        {"role": "system", "content": EXPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": "请处理以下 JSON，不要添加事实：\n" + json.dumps(payload, ensure_ascii=False)},
    ]
    return _validate_expression(_parse_json(_chat(cfg, messages)), target_lang)


# --------------------------------------------------------------------------- #
# 合并与缓存
# --------------------------------------------------------------------------- #


def merge(payload: dict, narrative: dict) -> dict:
    """把模型写的文案填回规则引擎搭好的骨架里。

    骨架不动：卡片顺序、次数、颜色、时段都来自代码，模型只替换句子的措辞。
    """
    by_key = {p["key"]: p for p in payload.get("patterns") or []}
    for item in narrative.get("patterns") or []:
        target = by_key.get(item["key"])
        if target is not None:
            target["text"] = item["text"]

    # Trend values and text are computed together; model prose cannot replace them.
    if payload.get("peak") and narrative.get("peak_text"):
        payload["peak"]["text"] = narrative["peak_text"]

    by_branch = {e["branch"]: e for e in payload.get("efficacy") or []}
    for item in narrative.get("efficacy") or []:
        target = by_branch.get(item["branch"])
        if target is not None:
            target["text"] = item["text"]

    # Keep reminders in rule-pattern order so each expanded card gets its own action.
    return payload


def rules_day(entries: list[dict]) -> str:
    # The API adds the viewer's calendar day to every row for daily trend invalidation.
    return entries[0].get("_window_today", "") if entries else ""


def fingerprint(entries: list[dict], days: int, lang: str, model: str) -> str:
    """数据指纹：记录、窗口或语言任一变化就换一份缓存。"""
    digest = hashlib.sha256(f"local-window-v3|{lang}|{model}|{rules_day(entries)}".encode())
    for entry in sorted(entries, key=lambda e: e["id"]):
        digest.update(
            json.dumps(
                [entry.get(field) for field in ("id", "user_id", "occurred_at", "local_date", "emotion", "severity", "local_hour", "cause", "note", "relief", "intervention_completed")],
                ensure_ascii=False, separators=(",", ":"),
            ).encode()
        )
        digest.update(b";")
    return digest.hexdigest()


def cache_prune(connection=None) -> None:
    """Physically remove expired summaries, including cache entries never read again."""
    conn = connection or db.connect()
    try:
        cutoff = (db.now_utc() - timedelta(hours=CACHE_TTL_HOURS)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM ai_cache WHERE julianday(created_at) IS NULL")
        conn.execute("DELETE FROM ai_cache WHERE julianday(created_at) <= julianday(?)", (cutoff,))
        if connection is None:
            conn.commit()
    finally:
        if connection is None:
            conn.close()


def data_revision(user_id: int) -> int:
    conn = db.connect()
    try:
        row = conn.execute("SELECT data_revision FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["data_revision"] if row else -1
    finally:
        conn.close()


def cache_get(key: str, user_id: int) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT payload, created_at FROM ai_cache WHERE key = ? AND user_id = ?", (key, user_id)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = db.now_utc()
        if not now - timedelta(hours=CACHE_TTL_HOURS) < created <= now:
            return None
        return json.loads(row["payload"])
    except (ValueError, TypeError, AttributeError):
        return None


def cache_put(key: str, narrative: dict, model: str, user_id: int, revision: int) -> bool:
    conn = db.connect()
    try:
        # Deletion/edit and cache insertion serialize on the same SQLite write lock.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data_revision FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or row["data_revision"] != revision:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO ai_cache(key, payload, model, created_at, user_id) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
            "model = excluded.model, created_at = excluded.created_at, user_id = excluded.user_id",
            (key, json.dumps(narrative, ensure_ascii=False), model, db.now_iso(), user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def cache_clear(user_id: int, connection=None) -> None:
    conn = connection or db.connect()
    try:
        conn.execute("DELETE FROM ai_cache WHERE user_id = ?", (user_id,))
        if connection is None:
            conn.commit()
    finally:
        if connection is None:
            conn.close()
