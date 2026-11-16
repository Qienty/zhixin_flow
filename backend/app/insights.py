"""
AI 总结（规则 + 模板实现，不依赖大模型）。

产品计划书要求总结的颗粒度必须落到「场景 + 时间模式」：
  ❌ "你最近容易焦虑。"
  ✅ "过去两周有 5 次记录指向「被临时加需求」，其中 4 次发生在下午 4 点后。
      其中 3 次你标为严重程度 4—5。下次这个场景出现时，可以优先使用愤怒分支的 30 秒降唤醒。"
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

TAG_RULES = [
    {
        "key": "workload",
        "zh": "被临时加需求 / 工作量",
        "en": "unplanned workload",
        "keywords": [
            "加需求", "加班", "老板", "领导", "会议", "同事", "汇报", "周报", "改稿",
            "需求变更", "工作量", "ddl", "deadline", "work", "overtime", "boss",
            "manager", "meeting", "extra work", "backlog", "client", "review",
        ],
    },
    {
        "key": "relationship",
        "zh": "亲密关系 / 家人",
        "en": "partner & family",
        "keywords": [
            "吵架", "伴侣", "老公", "老婆", "男朋友", "女朋友", "对象", "父母", "妈",
            "爸", "家人", "朋友", "冷战", "误会", "partner", "boyfriend", "girlfriend",
            "husband", "wife", "argument", "fight", "mom", "dad", "family", "friend",
        ],
    },
    {
        "key": "self_worth",
        "zh": "对自己的评价",
        "en": "self-worth",
        "keywords": [
            "自责", "没用", "失败", "不够好", "比不上", "内疚", "羞耻", "否定自己",
            "做不好", "做不到", "没意义", "没有意义", "看不到出路", "worthless", "failure",
            "not good enough", "guilt", "shame", "compare", "self-doubt", "pointless",
            "no way out",
        ],
    },
    {
        "key": "sleep",
        "zh": "睡眠不足",
        "en": "poor sleep",
        "keywords": [
            "失眠", "没睡好", "熬夜", "困", "睡不着", "睡眠", "sleep", "insomnia",
            "awake", "exhausted", "no sleep", "tired",
        ],
    },
    {
        "key": "commute",
        "zh": "通勤 / 交通",
        "en": "commute & transit",
        "keywords": [
            "堵车", "地铁", "公交", "通勤", "迟到", "航班", "赶不上", "traffic",
            "commute", "subway", "bus", "late", "flight", "delay",
        ],
    },
    {
        "key": "money",
        "zh": "钱 / 账单",
        "en": "money & bills",
        "keywords": [
            "房租", "账单", "工资", "还款", "花销", "没钱", "money", "rent", "bill",
            "salary", "debt", "expense", "broke",
        ],
    },
    {
        "key": "health",
        "zh": "身体状态",
        "en": "physical health",
        "keywords": [
            "头疼", "生病", "医院", "疼", "体检", "身体", "sick", "pain", "headache",
            "doctor", "hospital", "ill", "body",
        ],
    },
    {
        "key": "study",
        "zh": "学业",
        "en": "study",
        "keywords": [
            "考试", "作业", "论文", "绩点", "答辩", "申请", "exam", "homework",
            "thesis", "grade", "school", "assignment", "application",
        ],
    },
    {
        "key": "social_media",
        "zh": "社交媒体",
        "en": "social media",
        "keywords": [
            "社交媒体", "刷手机", "朋友圈", "微博", "小红书", "instagram", "tiktok",
            "social media", "scrolling", "twitter", "feed",
        ],
    },
    {
        "key": "uncertainty",
        "zh": "不确定 / 失控感",
        "en": "uncertainty",
        "keywords": [
            "不确定", "未来", "选择", "决定", "迷茫", "计划变了", "uncertain",
            "future", "decision", "unknown", "control", "change of plans",
        ],
    },
    {
        "key": "environment",
        "zh": "环境 / 噪音",
        "en": "environment & noise",
        "keywords": [
            "噪音", "吵", "装修", "拥挤", "天气", "noise", "loud", "crowded",
            "construction", "weather",
        ],
    },
]

TAG_LABEL = {r["key"]: {"zh": r["zh"], "en": r["en"]} for r in TAG_RULES}

SLOTS = [
    ("night", 0, 5, {"zh": "凌晨", "en": "late night"}, {"zh": "0—5 点", "en": "12—5am"}),
    ("morning", 5, 12, {"zh": "上午", "en": "morning"}, {"zh": "5—12 点", "en": "5—12am"}),
    ("afternoon", 12, 18, {"zh": "下午", "en": "afternoon"}, {"zh": "12—18 点", "en": "12—6pm"}),
    ("evening", 18, 24, {"zh": "晚上", "en": "evening"}, {"zh": "18—24 点", "en": "6pm—12am"}),
]

# 归因要区分「指向什么」：外部场景能说「下次再来」，内在状态和身体状态不能
TAG_KIND = {
    "workload": "external",
    "relationship": "external",
    "commute": "external",
    "money": "external",
    "study": "external",
    "environment": "external",
    "social_media": "external",
    "self_worth": "internal",
    "uncertainty": "internal",
    "sleep": "body",
    "health": "body",
}

KIND_LABEL = {
    "external": {"zh": "外部场景", "en": "external scene"},
    "internal": {"zh": "内在状态", "en": "internal state"},
    "body": {"zh": "身体状态", "en": "body state"},
}

MIN_SAMPLE = 20

# 「挺好 / 平静」不参与归因聚合：它们不是消耗，混进去会把均值拉低、结论说歪
POSITIVE_EMOTIONS = {"calm", "good"}


def kind_of(key: str) -> str:
    if key.startswith("raw:"):
        return "external"
    return TAG_KIND.get(key, "external")


def advice_for(kind: str, branch: str, lang: str) -> str:
    """按「指向什么 × 主导情绪」给出下一步。不是所有模式都适合「下次这个场景出现时」。"""
    zh = lang == "zh"

    if branch == "despair":
        return (
            "这一类不靠「再想办法」解决。它再出现时，直接进托底分支：危机资源在那儿，免费，也不用等到最糟的时候才用。"
            if zh
            else "This one isn't asking you to figure something out. When it returns, go straight to the safety branch — the crisis lines are free, and not only for the worst days."
        )

    if kind == "body":
        return (
            "身体状态没法「下次避开」。这类记录先单独放着，两周后你会看到它把哪些天的强度整体拉高了。"
            if zh
            else "Body state can't be avoided next time. Keep these entries separate — in two weeks you'll see which days it pushed up."
        )

    if kind == "internal":
        if branch == "sadness":
            return (
                "这类不是外面发生的事，是往回缩的感觉。它起头时，先把手放胸口 30 秒，别让它一个人待着。"
                if zh
                else "This isn't something happening out there — it's a pull inward. When it starts, hand on your chest for 30 seconds so it isn't sitting alone."
            )
        if branch == "anger":
            return (
                "这一组的火多半是冲自己来的。它起头时，先做一次长呼气，再决定要不要继续想这件事。"
                if zh
                else "The heat here is mostly aimed inward. When it starts, one long exhale before deciding whether to keep going over it."
            )
        if branch == "anxiety":
            return (
                "这不是外面的场景，是你自己在反复过同一遍。它起头时，先把呼气拉长到 7 秒，再决定要不要继续想。"
                if zh
                else "This isn't a scene outside — it's you re-running the same loop. When it starts, stretch the exhale to 7 seconds before deciding whether to continue."
            )
        return (
            "它起头时，先记一笔就够，不用当场解释自己。"
            if zh
            else "When it starts, just note it — no need to explain yourself on the spot."
        )

    # 外部场景
    if branch == "anger":
        return (
            "下次它再来，先做一次长呼气（呼气比吸气长），再回消息、再开口。"
            if zh
            else "Next time it shows up, one long exhale — longer out than in — before you reply or speak."
        )
    if branch == "sadness":
        return (
            "下次它再来，别急着回应。先把手放在胸口 30 秒。"
            if zh
            else "Next time it shows up, don't answer yet. Hand on your chest for 30 seconds first."
        )
    if branch == "anxiety":
        return (
            "下次它再来，先把呼气拉到 7 秒，再接着想。"
            if zh
            else "Next time it starts, stretch the exhale to 7 seconds before you keep thinking."
        )
    return (
        "下次它再来，先记一笔就够，不用当场解决。"
        if zh
        else "Next time, just note it. Nothing needs solving on the spot."
    )


def slot_of(hour: int) -> str:
    for key, start, end, _, _ in SLOTS:
        if start <= hour < end:
            return key
    return "night"


def slot_text(key: str, lang: str, field: int = 0) -> str:
    for k, _, _, short, rng in SLOTS:
        if k == key:
            table = short if field == 0 else rng
            return table.get(lang) or table["zh"]
    return ""


def tag_cause(cause: str) -> list[str]:
    """把自由文本原因归到场景标签。中文按子串、英文按词匹配。"""
    text = (cause or "").strip().lower()
    if not text:
        return []
    hits = []
    for rule in TAG_RULES:
        for kw in rule["keywords"]:
            if kw in text:
                hits.append(rule["key"])
                break
    return hits


def _label_for(tag_key: str, raw: str, lang: str) -> str:
    if tag_key in TAG_LABEL:
        return TAG_LABEL[tag_key].get(lang) or TAG_LABEL[tag_key]["zh"]
    return raw[:28]


def _group_by_scene(entries: list[dict]) -> dict[str, dict]:
    """按场景标签聚合，用于「什么在消耗你」。

    刻意排除 calm / good：归因是把同一类消耗放在一起看，
    把「和朋友吃饭，挺好」这种 1 分的好记录并进「亲密关系」，
    会同时拉低均值、也让结论读起来不合理。
    """
    groups: dict[str, dict] = {}
    for e in entries:
        if e["emotion"] in POSITIVE_EMOTIONS:
            continue
        cause = (e.get("cause") or "").strip()
        keys = tag_cause(cause)
        if not keys:
            if not cause:
                continue
            keys = [f"raw:{cause[:28]}"]
        for key in keys:
            g = groups.setdefault(
                key,
                {
                    "key": key,
                    "raw": cause,
                    "count": 0,
                    "slots": Counter(),
                    "severities": [],
                    "emotions": Counter(),
                    "samples": Counter(),
                    "hours": [],
                },
            )
            g["count"] += 1
            g["slots"][slot_of(e["local_hour"])] += 1
            g["hours"].append(e["local_hour"])
            if e.get("severity"):
                g["severities"].append(e["severity"])
            g["emotions"][e["emotion"]] += 1
            if cause:
                g["samples"][cause[:40]] += 1
    return groups


def _hour_band(hours: list[int], slot: str) -> dict | None:
    """在主导时段里找最密的 3 小时窗口——「下午 4 点后」比「下午」有用得多。"""
    if len(hours) < 3:
        return None
    slot_bounds = {k: (s, e) for k, s, e, _, _ in SLOTS}
    low, high = slot_bounds.get(slot, (0, 24))
    best = None
    best_count = 0
    for start in range(low, max(low, high - 2)):
        end = start + 3
        if end > high:
            break
        cnt = sum(1 for h in hours if start <= h < end)
        if cnt > best_count:
            best_count, best = cnt, (start, end)
    if not best or best_count < max(3, (len(hours) + 1) // 2):
        return None
    return {"start": best[0], "end": best[1], "count": best_count}


def _emotion_label(key: str, lang: str) -> str:
    from .content import EMOTION_MAP

    meta = EMOTION_MAP.get(key)
    if not meta:
        return key
    return meta["zh"]["label"] if lang == "zh" else meta["en"]["label"]


def _branch_of_emotion(emotion: str) -> str:
    from .content import EMOTION_MAP

    return (EMOTION_MAP.get(emotion) or {}).get("branch", "none")


def _pattern_text(p: dict, days: int, lang: str, overall_avg: float | None, overall_n: int) -> str:
    """三段：分布（几次 + 落在哪个时段）→ 强度（和整体比）→ 下一步（按指向 × 分支）。"""
    label = p["label"]
    count = p["count"]
    slot_short = p["top_slot_label"]
    slot_range = p["top_slot_range"]
    slot_count = p["top_slot_count"]
    severe = p["severe_count"]
    avg = p["avg_severity"]
    advice = advice_for(p["kind"], p["branch"], lang)

    band = p.get("band")

    if lang == "zh":
        if band and band["count"] >= count:
            where = f"，全部集中在{slot_short} {band['start']}—{band['end']} 点"
        elif band:
            where = f"，其中 {band['count']} 条集中在{slot_short} {band['start']}—{band['end']} 点"
        elif slot_count and slot_count >= count:
            where = f"，全部落在{slot_short}（{slot_range}）"
        elif slot_count and slot_count >= 2:
            where = f"，其中 {slot_count} 条落在{slot_short}（{slot_range}）"
        else:
            where = ""
        head = f"过去 {days} 天，{count} 条记录指向「{label}」{where}。"
        if avg:
            piece = f"这一组平均 {avg} 分"
            if overall_avg and count < overall_n * 0.9:
                delta = round(avg - overall_avg, 1)
                if delta >= 0.4:
                    piece += f"，比你这段时间的整体均值（{overall_avg}）高 {delta} 分"
                elif delta <= -0.4:
                    piece += f"，比整体均值（{overall_avg}）低 {abs(delta)} 分"
                else:
                    piece += f"，和整体均值（{overall_avg}）差不多"
            head += piece + (f"；{severe} 条到了 4—5 分。" if severe else "。")
        return head + advice

    if band and band["count"] >= count:
        where = f" — all of them between {band['start']}:00 and {band['end']}:00"
    elif band:
        where = f", {band['count']} of them between {band['start']}:00 and {band['end']}:00"
    elif slot_count and slot_count >= count:
        where = f" — all of them in the {slot_short} ({slot_range})"
    elif slot_count and slot_count >= 2:
        where = f", {slot_count} of them in the {slot_short} ({slot_range})"
    else:
        where = ""
    head = f'{count} entries in the last {days} days point to "{label}"{where}. '
    if avg:
        piece = f"This group averages {avg}"
        if overall_avg and count < overall_n * 0.9:
            delta = round(avg - overall_avg, 1)
            if delta >= 0.4:
                piece += f", {delta} above your overall {overall_avg}"
            elif delta <= -0.4:
                piece += f", {abs(delta)} below your overall {overall_avg}"
            else:
                piece += f", about your overall {overall_avg}"
        head += piece + (f"; {severe} hit 4—5." if severe else ".")
    return head + " " + advice


def build_basic(entries: list[dict], lang: str) -> dict:
    total = len(entries)
    severities = [e["severity"] for e in entries if e.get("severity")]
    days = sorted({e["local_date"] for e in entries})
    emotion_counts = Counter(e["emotion"] for e in entries)

    cause_counter: Counter = Counter()
    for e in entries:
        cause = (e.get("cause") or "").strip()
        if not cause:
            continue
        keys = tag_cause(cause)
        if keys:
            for k in keys:
                cause_counter[(_label_for(k, cause, lang), k)] += 1
        else:
            cause_counter[(cause[:28], f"raw:{cause[:28]}")] += 1

    from .content import EMOTIONS

    distribution = []
    for meta in EMOTIONS:
        c = emotion_counts.get(meta["key"], 0)
        if c == 0:
            continue
        distribution.append(
            {
                "key": meta["key"],
                "label": meta["zh"]["label"] if lang == "zh" else meta["en"]["label"],
                "color": meta["color"],
                "count": c,
                "pct": round(c / total * 100, 1) if total else 0,
            }
        )
    distribution.sort(key=lambda x: -x["count"])

    return {
        "entries": total,
        "days_logged": len(days),
        "avg_severity": round(sum(severities) / len(severities), 1) if severities else None,
        "severe_count": sum(1 for s in severities if s >= 4),
        "emotion_distribution": distribution,
        "top_causes": [
            {"label": label, "count": count} for (label, _), count in cause_counter.most_common(6)
        ],
        "first_date": days[0] if days else None,
        "last_date": days[-1] if days else None,
    }


def build_insights(entries: list[dict], days: int, lang: str, locked: bool, today: str | None = None) -> dict:
    basic = build_basic(entries, lang)
    now = datetime.now(timezone.utc)

    groups = _group_by_scene(entries)
    patterns = []
    for key, g in groups.items():
        if g["count"] < 3:
            continue
        top_slot, top_slot_count = g["slots"].most_common(1)[0]
        dominant_emotion = g["emotions"].most_common(1)[0][0]
        avg = round(sum(g["severities"]) / len(g["severities"]), 1) if g["severities"] else None
        p = {
            "key": key,
            "label": _label_for(key, g["raw"], lang),
            "kind": kind_of(key),
            "kind_label": KIND_LABEL[kind_of(key)][lang],
            "count": g["count"],
            "top_slot": top_slot,
            "top_slot_label": slot_text(top_slot, lang),
            "top_slot_range": slot_text(top_slot, lang, field=1),
            "top_slot_count": top_slot_count,
            "band": _hour_band(g["hours"], top_slot),
            "severe_count": sum(1 for s in g["severities"] if s >= 4),
            "avg_severity": avg,
            "dominant_emotion": dominant_emotion,
            "dominant_emotion_label": _emotion_label(dominant_emotion, lang),
            "branch": _branch_of_emotion(dominant_emotion),
            "samples": [s for s, _ in g["samples"].most_common(2)],
        }
        p["score"] = p["severe_count"] * 2 + p["count"]
        patterns.append(p)
    patterns.sort(key=lambda x: -x["score"])
    patterns = patterns[:3]
    for p in patterns:
        p["text"] = _pattern_text(p, days, lang, basic["avg_severity"], basic["entries"])
        del p["score"]

    # 趋势：近 7 天 vs 前 7 天
    trend = None
    local_today = datetime.fromisoformat(today).date() if today else now.date()
    cutoff = (local_today - timedelta(days=6)).isoformat()
    since = (local_today - timedelta(days=13)).isoformat()
    recent = [e["severity"] for e in entries if e.get("severity") and cutoff <= e["local_date"] <= local_today.isoformat()]
    earlier = [e["severity"] for e in entries if e.get("severity") and since <= e["local_date"] < cutoff]
    if len(recent) >= 2 and len(earlier) >= 2:
        a = round(sum(recent) / len(recent), 1)
        b = round(sum(earlier) / len(earlier), 1)
        delta = round(a - b, 1)
        if delta <= -0.3:
            direction = "down"
        elif delta >= 0.3:
            direction = "up"
        else:
            direction = "flat"
        trend = {
            "this_week": round(a, 1),
            "last_week": round(b, 1),
            "delta": delta,
            "direction": direction,
            "text": {
                "down": f"这周平均严重程度 {round(a, 1)}，上周 {round(b, 1)}——在往下走。"
                if lang == "zh"
                else f"This week averages {round(a, 1)} against {round(b, 1)} last week — coming down.",
                "up": f"这周平均 {round(a, 1)}，上周 {round(b, 1)}——比上周高 {abs(delta)}。"
                if lang == "zh"
                else f"This week averages {round(a, 1)} against {round(b, 1)} last week — {abs(delta)} higher.",
                "flat": f"这周 {round(a, 1)}，上周 {round(b, 1)}——基本持平。"
                if lang == "zh"
                else f"This week {round(a, 1)}, last week {round(b, 1)} — roughly flat.",
            }[direction],
        }

    # 严重记录的时段聚集
    peak = None
    severe_entries = [e for e in entries if (e.get("severity") or 0) >= 4]
    if len(severe_entries) >= 4:
        slots = Counter(slot_of(e["local_hour"]) for e in severe_entries)
        top, cnt = slots.most_common(1)[0]
        if cnt >= 3:
            peak = {
                "slot": top,
                "label": slot_text(top, lang),
                "range": slot_text(top, lang, field=1),
                "count": cnt,
                "total": len(severe_entries),
                "text": f"严重程度 4—5 的 {len(severe_entries)} 条记录里，{cnt} 条集中在{slot_text(top, lang)}（{slot_text(top, lang, field=1)}）。"
                if lang == "zh"
                else f"Of {len(severe_entries)} entries rated 4—5, {cnt} landed in the {slot_text(top, lang)} ({slot_text(top, lang, field=1)}).",
            }

    # 干预有效率：用户自己按的「有没有缓下来」
    eff_groups: dict[str, dict] = defaultdict(lambda: {"tried": 0, "helped": 0, "worse": 0})
    for e in entries:
        if e.get("relief") and e.get("intervention_branch"):
            g = eff_groups[e["intervention_branch"]]
            g["tried"] += 1
            if e["relief"] == "better":
                g["helped"] += 1
            elif e["relief"] == "worse":
                g["worse"] += 1
    efficacy = []
    for branch, g in eff_groups.items():
        if g["tried"] < 3:
            continue
        rate = round(g["helped"] / g["tried"] * 100)
        name = {
            "anger": ("愤怒分支", "the anger branch"),
            "sadness": ("伤心分支", "the sadness branch"),
            "despair": ("托底分支", "the safety branch"),
            "anxiety": ("焦虑分支", "the anxiety branch"),
            "none": ("轻量记录", "light logging"),
        }.get(branch, (branch, branch))
        label = name[0] if lang == "zh" else name[1]
        text = (
            f"{label}你用了 {g['tried']} 次，其中 {g['helped']} 次你反馈「缓下来一点」（{rate}%）。"
            if lang == "zh"
            else f"You used {label} {g['tried']} times and said it helped {g['helped']} of them ({rate}%)."
        )
        efficacy.append({"branch": branch, "label": label, "tried": g["tried"], "helped": g["helped"], "rate": rate, "text": text})

    # 场景提醒：不复述归因结论，只给「什么时候需要提前准备 + 准备什么」
    SHORT_ACTION = {
        "anger": {"zh": "提前留出一次长呼气的空档", "en": "leave room for one long exhale"},
        "sadness": {"zh": "提前想好一个可以发消息的人", "en": "line up one person you can message"},
        "anxiety": {"zh": "先把呼气拉到 7 秒再开始想", "en": "stretch the exhale to 7 before thinking"},
        "despair": {"zh": "提前把危机资源放到手边", "en": "keep the crisis lines within reach"},
        "none": {"zh": "先记一笔，别等它攒起来", "en": "note it early, before it stacks up"},
    }
    reminders = []
    for p in patterns[:2]:
        if p["kind"] == "body":
            short = "先把它单独记出来" if lang == "zh" else "keep these entries separate"
        else:
            short = SHORT_ACTION.get(p["branch"], SHORT_ACTION["none"])[lang]
        band = p.get("band")
        if band:
            where_zh = f"{p['top_slot_label']} {band['start']}—{band['end']} 点"
            where_en = f"between {band['start']}:00 and {band['end']}:00"
            hit = band["count"]
        else:
            where_zh = f"{p['top_slot_label']}（{p['top_slot_range']}）"
            where_en = f"the {p['top_slot_label']} ({p['top_slot_range']})"
            hit = p["top_slot_count"]
        lead_zh = (
            f"「{p['label']}」的 {p['count']} 条全部落在{where_zh}"
            if hit >= p["count"]
            else f"「{p['label']}」{p['count']} 条里有 {hit} 条落在{where_zh}"
        )
        lead_en = (
            f'All {p["count"]} "{p["label"]}" entries sit {where_en}'
            if hit >= p["count"]
            else f'{hit} of the {p["count"]} "{p["label"]}" entries sit {where_en}'
        )
        if lang == "zh":
            reminders.append(f"{lead_zh}。把这个时段当成需要提前准备的一段：{short}。")
        else:
            reminders.append(f"{lead_en}. Treat that window as something to prepare for — {short}.")

    efficacy.sort(key=lambda x: -x["tried"])

    enough = basic["entries"] >= MIN_SAMPLE
    if locked:
        for i, p in enumerate(patterns):
            if i > 0:
                p["text"] = None
        if trend:
            trend["text"] = None
        if peak:
            peak["text"] = None
        reminders = []
        for e in efficacy:
            e["text"] = None

    return {
        "window_days": days,
        "sample": {
            "entries": basic["entries"],
            "days_logged": basic["days_logged"],
            "enough": enough,
            "needed": MIN_SAMPLE,
            "missing": max(0, MIN_SAMPLE - basic["entries"]),
        },
        "patterns": patterns,
        "trend": trend,
        "peak": peak,
        "efficacy": efficacy,
        "reminders": reminders,
        "locked": locked,
        "generated_at": now.isoformat(timespec="seconds"),
    }


def _parse(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
