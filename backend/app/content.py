"""
内容层：情绪定义、三套峰值干预路径、危机资源、文案模板。

设计原则（来自产品计划书）：
1. 愤怒走高唤醒 —— 降唤醒优先，长呼气 → 30 秒呼吸 → 一句断念；
2. 伤心走退缩孤独 —— 陪伴性语言 + 极小身体动作，不推「断念」；
3. 绝望不走「断念」路径 —— 温和托底 + 本地危机资源，免费、显眼、不设付费墙。
文案严格限定在「情绪记录与自我调节」，不出现治疗 / 诊断 / 临床表述。
"""

EMOTIONS = [
    {
        "key": "anger",
        "zh": {"label": "愤怒", "hint": "心跳快、肌肉紧"},
        "en": {"label": "Angry", "hint": "hot, tight, fast"},
        # 记下之后系统先说的那一句。愤怒先降唤醒，所以指向「听息」。
        "say": {
            "zh": "先听息。三口气之后再说话。",
            "en": "Listen to your breath first. Three breaths before you speak.",
        },
        "say_note": {
            "zh": "把注意力放到鼻子下面，听自己呼气的声音，不用数得准。",
            "en": "Put your attention under your nose and listen to your own exhale. No need to count right.",
        },
        "color": "#B4523F",
        "branch": "anger",
    },
    {
        "key": "sadness",
        "zh": {"label": "伤心", "hint": "想缩起来"},
        "en": {"label": "Sad", "hint": "low and withdrawn"},
        "say": {
            "zh": "不用解释。手放这儿，待一会儿。",
            "en": "No need to explain. Hand here. Stay a while.",
        },
        "say_note": {
            "zh": "伤心不需要被说服，也不需要马上好起来。先有人陪着就够了。",
            "en": "Sadness doesn't need convincing, or fixing on the spot. Company is enough to start.",
        },
        "color": "#4E6E96",
        "branch": "sadness",
    },
    {
        "key": "despair",
        "zh": {"label": "绝望", "hint": "没有出路感"},
        "en": {"label": "Hopeless", "hint": "no way out"},
        "say": {
            "zh": "先别一个人扛。这些电话现在就能打。",
            "en": "Don't carry this alone. These lines are open right now.",
        },
        "say_note": {
            "zh": "绝望不问「怎么办」，它先要有人接住。打过去不用组织语言。",
            "en": "Hopelessness isn't asking for a plan. It needs someone to catch you. You don't need the right words.",
        },
        "color": "#5B5878",
        "branch": "despair",
    },
    {
        "key": "anxiety",
        "zh": {"label": "焦虑", "hint": "停不下来地想"},
        "en": {"label": "Anxious", "hint": "can't stop thinking"},
        "say": {
            "zh": "把这口气呼完。剩下的先不管。",
            "en": "Finish this one exhale. The rest can wait.",
        },
        "say_note": {
            "zh": "焦虑在往前跑，先把它拉回到身体上：呼气比吸气长就好。",
            "en": "Anxiety runs ahead. Pull it back into the body: exhale longer than you inhale.",
        },
        "color": "#C08A3E",
        "branch": "anxiety",
    },
    {
        "key": "drained",
        "zh": {"label": "疲惫", "hint": "空了、没电"},
        "en": {"label": "Drained", "hint": "empty, flat"},
        "say": {
            "zh": "今天只做一件事。先把这一笔记下来。",
            "en": "One thing today. Start by noting this one.",
        },
        "say_note": {
            "zh": "疲惫不是懒。它只是告诉你额度用完了，今天不需要再证明什么。",
            "en": "Drained isn't lazy. It means the budget is spent — nothing left to prove today.",
        },
        "color": "#6E7A6B",
        "branch": "none",
    },
    {
        "key": "calm",
        "zh": {"label": "平静", "hint": "还行"},
        "en": {"label": "Calm", "hint": "okay, settled"},
        "say": {
            "zh": "那就这样待着。什么都不用做。",
            "en": "Stay as you are. Nothing to do.",
        },
        "say_note": {
            "zh": "平静也算一条记录。它让你以后知道，日子不全是难的那几天。",
            "en": "Calm counts as an entry too. It reminds you later that not every day was a hard one.",
        },
        "color": "#4F7A66",
        "branch": "none",
    },
    {
        "key": "good",
        "zh": {"label": "挺好", "hint": "想说一声"},
        "en": {"label": "Good", "hint": "worth noting"},
        "say": {
            "zh": "愿你多遇到这样的时刻。记下来。",
            "en": "May more of these find you. Note it down.",
        },
        "say_note": {
            "zh": "好时刻也值得留一笔。以后难的时候翻到它，比任何道理都有用。",
            "en": "Good moments are worth keeping too. On a hard day, finding this beats any advice.",
        },
        "color": "#C9971F",
        "branch": "none",
    },
]

EMOTION_KEYS = [e["key"] for e in EMOTIONS]
EMOTION_MAP = {e["key"]: e for e in EMOTIONS}

SEVERITY_LABELS = {
    "zh": {1: "很轻", 2: "有点", 3: "中间", 4: "很强", 5: "到顶了"},
    "en": {1: "faint", 2: "mild", 3: "mid", 4: "strong", 5: "peak"},
}

INTERVENTIONS = {
    "anger": {
        "zh": {
            "title": "先把身体降下来",
            "lede": "愤怒是高唤醒。这一分钟不做决定，也不用讲道理。",
            "steps": [
                {
                    "kind": "prompt",
                    "seconds": 8,
                    "anim": "exhale",
                    "title": "一次长呼气",
                    "body": "用嘴长长地吐一口气，像吹凉一碗汤。呼气要比吸气长。",
                    "foot": "只做这一件事",
                },
                {
                    "kind": "breath",
                    "seconds": 30,
                    "anim": "breath",
                    "pattern": {"inhale": 4, "exhale": 6},
                    "cycles": 3,
                    "title": "30 秒呼吸引导",
                    "body": "吸气 4 秒，呼气 6 秒。跟着圆圈走，不用数太准。",
                    "foot": "3 轮",
                },
                {
                    "kind": "prompt",
                    "seconds": 10,
                    "anim": "still",
                    "title": "一句断念",
                    "body": "这个念头来了。看着它走，不用赶它。",
                    "foot": "念头是事件，不是你",
                },
            ],
            "closing": "这一分钟你什么都没解决，但你把唤醒降了一点。等下要处理的事，现在会比刚才好处理。",
        },
        "en": {
            "title": "Bring the body down first",
            "lede": "Anger is high arousal. One minute, no decisions, no arguments.",
            "steps": [
                {
                    "kind": "prompt",
                    "seconds": 8,
                    "anim": "exhale",
                    "title": "One long exhale",
                    "body": "Let the breath out slowly through your mouth, like cooling soup. Longer out than in.",
                    "foot": "only this",
                },
                {
                    "kind": "breath",
                    "seconds": 30,
                    "anim": "breath",
                    "pattern": {"inhale": 4, "exhale": 6},
                    "cycles": 3,
                    "title": "30 seconds of breathing",
                    "body": "In for 4, out for 6. Follow the circle — no need to be exact.",
                    "foot": "3 rounds",
                },
                {
                    "kind": "prompt",
                    "seconds": 10,
                    "anim": "still",
                    "title": "One line to hold",
                    "body": "This thought arrived. Watch it leave. You don't have to push it.",
                    "foot": "a thought is an event, not you",
                },
            ],
            "closing": "You didn't solve anything in that minute, but you lowered the arousal. Whatever comes next is easier to handle than it was a minute ago.",
        },
    },
    "sadness": {
        "zh": {
            "title": "先别一个人待着",
            "lede": "伤心不是要修好的问题。这一分钟只是陪一下自己。",
            "steps": [
                {
                    "kind": "prompt",
                    "seconds": 30,
                    "anim": "still",
                    "title": "把手放在胸口",
                    "body": "感受手的温度，和胸口起伏的那一点点。不用深呼吸，正常呼吸就行。",
                    "foot": "30 秒，什么都不用想",
                },
                {
                    "kind": "prompt",
                    "seconds": 20,
                    "anim": "warm",
                    "title": "允许它在这儿",
                    "body": "现在难受是真的。它不需要马上变好，也不需要有个理由。",
                    "foot": "不评价，不分析",
                },
            ],
            "closing": "伤心的时候，「看着念头走」是句风凉话。手上还有温度，就先握着这个。",
        },
        "en": {
            "title": "Don't sit in it alone",
            "lede": "Sadness isn't a problem to fix. This minute is just company.",
            "steps": [
                {
                    "kind": "prompt",
                    "seconds": 30,
                    "anim": "still",
                    "title": "Hand on your chest",
                    "body": "Notice the warmth of your hand, and the small rise and fall underneath. No deep breathing needed.",
                    "foot": "30 seconds, nothing to figure out",
                },
                {
                    "kind": "prompt",
                    "seconds": 20,
                    "anim": "warm",
                    "title": "Let it be here",
                    "body": "This hurts, and that's real. It doesn't have to improve right now, or have a reason.",
                    "foot": "no judging, no analysis",
                },
            ],
            "closing": "\"Watch the thought leave\" is a cold thing to say to sadness. Your hand is warm. Start there.",
        },
    },
    "despair": {
        "zh": {
            "title": "这一分钟不用你解决任何事",
            "lede": "你标记的是绝望。这里不推「断念」——绝望的时候，那句话是轻视，不是帮助。",
            "steps": [
                {
                    "kind": "safety",
                    "seconds": 0,
                    "anim": "still",
                    "title": "先说清楚一件事",
                    "body": "你现在感觉到的，是真实的，但它不是全部，也不一定是结论。这一分钟你不需要扛住所有事。",
                    "foot": "如果你有立刻的危险，请联系当地急救",
                },
                {
                    "kind": "safety",
                    "seconds": 0,
                    "anim": "still",
                    "title": "一个很小的动作",
                    "body": "喝一口水，或者把窗户打开一下。只做一件，做完就行。",
                    "foot": "越小越好",
                },
            ],
            "closing": "这条路上的资源全部免费，不需要订阅。如果你愿意，把现在这一条记下来——它会留在这里，等你想说的时候。",
            "show_crisis": True,
        },
        "en": {
            "title": "Nothing needs solving this minute",
            "lede": "You marked hopeless. There's no \"watch the thought leave\" here — said to despair, that line is dismissive, not help.",
            "steps": [
                {
                    "kind": "safety",
                    "seconds": 0,
                    "anim": "still",
                    "title": "One thing, plainly",
                    "body": "What you feel right now is real. It is also not the whole picture, and not necessarily the conclusion. You don't have to hold all of it this minute.",
                    "foot": "if you are in immediate danger, contact local emergency services",
                },
                {
                    "kind": "safety",
                    "seconds": 0,
                    "anim": "still",
                    "title": "One very small action",
                    "body": "Drink a glass of water, or open a window. Just one, then stop.",
                    "foot": "smaller is better",
                },
            ],
            "closing": "Every resource on this path is free and never behind a paywall. If you're willing, save this entry — it stays here until you want to talk.",
            "show_crisis": True,
        },
    },
    "anxiety": {
        "zh": {
            "title": "把呼气拉长",
            "lede": "焦虑是刹车和油门同时踩着。先把呼气放长，身体才会收到「现在安全」的信号。",
            "steps": [
                {
                    "kind": "breath",
                    "seconds": 40,
                    "anim": "breath",
                    "pattern": {"inhale": 4, "exhale": 7},
                    "cycles": 3,
                    "title": "40 秒延长呼气",
                    "body": "吸气 4 秒，呼气 7 秒。呼气的时候，肩膀往下掉一点。",
                    "foot": "3 轮",
                },
                {
                    "kind": "prompt",
                    "seconds": 15,
                    "anim": "still",
                    "title": "落地一句",
                    "body": "把脚踩实地面，说出你眼前三样东西的名字。",
                    "foot": "5-4-3 里的第一层",
                },
            ],
            "closing": "焦虑不会因为一次呼吸消失，但身体的警报可以先降一格。",
        },
        "en": {
            "title": "Stretch the exhale",
            "lede": "Anxiety is brake and accelerator at once. Lengthen the out-breath first — that's the signal the body listens to.",
            "steps": [
                {
                    "kind": "breath",
                    "seconds": 40,
                    "anim": "breath",
                    "pattern": {"inhale": 4, "exhale": 7},
                    "cycles": 3,
                    "title": "40 seconds, long exhale",
                    "body": "In for 4, out for 7. Let the shoulders drop a little on the way out.",
                    "foot": "3 rounds",
                },
                {
                    "kind": "prompt",
                    "seconds": 15,
                    "anim": "still",
                    "title": "Land somewhere",
                    "body": "Feet flat on the floor. Name three things you can see.",
                    "foot": "the first layer of 5-4-3",
                },
            ],
            "closing": "One breath won't remove anxiety, but the alarm can come down a notch.",
        },
    },
    "none": {
        "zh": {
            "title": "记下来就行",
            "lede": "这个情绪不需要干预。写一句发生了什么，比什么都管用。",
            "steps": [],
            "closing": "记录本身就是价值：以后你能看到它多久来一次。",
        },
        "en": {
            "title": "Noting it is enough",
            "lede": "No intervention needed here. One line about what happened is worth more.",
            "steps": [],
            "closing": "Logging is the value: later you'll see how often it comes.",
        },
    },
}

BRANCH_HINT = {
    "anger": {
        "zh": "愤怒分支 · 30 秒降唤醒",
        "en": "Anger · pause and breathe",
    },
    "sadness": {
        "zh": "伤心分支 · 陪伴 + 身体动作",
        "en": "Sadness · a moment of comfort",
    },
    "despair": {
        "zh": "托底分支 · 危机资源免费",
        "en": "safety branch · free crisis resources",
    },
    "anxiety": {
        "zh": "焦虑分支 · 延长呼气",
        "en": "anxiety branch · long exhale",
    },
    "none": {
        "zh": "轻量记录 · 无干预",
        "en": "light log · no intervention",
    },
}

CRISIS = {
    "CN": {
        "label": {"zh": "中国大陆", "en": "China mainland"},
        "emergency": {"zh": "110（报警）/ 120（急救）", "en": "110 (police) / 120 (ambulance)"},
        "lines": [
            {
                # 国家卫健委 2024 年 12 月发文设置，2025 年 5 月起全国接通
                "name": "全国心理援助热线",
                "contact": {"zh": "12356 · 24 小时 · 免费", "en": "12356 · 24h · free"},
                "number": "12356",
            },
            {
                "name": "北京心理危机研究与干预中心",
                "contact": {
                    "zh": "手机 010-82951332 · 座机 800-810-1117 · 24 小时",
                    "en": "mobile 010-82951332 · landline 800-810-1117 · 24h",
                },
                "number": "01082951332",
                "url": "https://www.crisis.org.cn",
            },
            {
                "name": "青少年服务台",
                "contact": {"zh": "12355", "en": "12355"},
                "number": "12355",
            },
        ],
    },
    "US": {
        "label": {"zh": "美国", "en": "United States"},
        "emergency": {"zh": "911", "en": "911"},
        "lines": [
            {
                "name": "988 Suicide & Crisis Lifeline",
                "contact": {"zh": "拨打或短信 988", "en": "Call or text 988"},
                "number": "988",
                "url": "https://988lifeline.org",
            }
        ],
    },
    "CA": {
        "label": {"zh": "加拿大", "en": "Canada"},
        "emergency": {"zh": "911", "en": "911"},
        "lines": [
            {
                "name": "988 Suicide Crisis Helpline",
                "contact": {"zh": "拨打或短信 988", "en": "Call or text 988"},
                "number": "988",
                "url": "https://988.ca",
            }
        ],
    },
    "UK": {
        "label": {"zh": "英国", "en": "United Kingdom"},
        "emergency": {"zh": "999", "en": "999"},
        "lines": [
            {
                "name": "Samaritans",
                "contact": {"zh": "拨打 116 123（24 小时免费）", "en": "Call 116 123, free, 24/7"},
                "number": "116123",
                "url": "https://www.samaritans.org",
            }
        ],
    },
    "AU": {
        "label": {"zh": "澳大利亚", "en": "Australia"},
        "emergency": {"zh": "000", "en": "000"},
        "lines": [
            {
                "name": "Lifeline Australia",
                "contact": {"zh": "拨打 13 11 14", "en": "Call 13 11 14"},
                "number": "131114",
                "url": "https://www.lifeline.org.au",
            }
        ],
    },
    "EU": {
        "label": {"zh": "欧盟 / 欧洲", "en": "EU / Europe"},
        "emergency": {"zh": "112", "en": "112"},
        "lines": [
            {
                "name": "Find A Helpline",
                "contact": {"zh": "按国家查询本地热线", "en": "Find your country's line"},
                "url": "https://findahelpline.com",
            }
        ],
    },
    "OTHER": {
        "label": {"zh": "其他地区", "en": "Elsewhere"},
        "emergency": {"zh": "当地急救号码", "en": "local emergency number"},
        "lines": [
            {
                "name": "Find A Helpline",
                "contact": {"zh": "按国家查询本地热线", "en": "Find your country's line"},
                "url": "https://findahelpline.com",
            }
        ],
    },
}

REGION_KEYS = list(CRISIS.keys())

# 危机资源是合规红线：每条号码都要有可核对的来源与核对日期。
# 核对节奏见 docs/crisis-resources.md（至少每 6 个月一次，号码变更立即更新）。
CRISIS_VERIFIED_AT = "2026-11-22"
CRISIS_SOURCES = {
    "CN": "https://www.nhc.gov.cn/",
    "US": "https://988lifeline.org/",
    "CA": "https://988.ca/",
    "UK": "https://www.samaritans.org/",
    "AU": "https://www.lifeline.org.au/",
    "EU": "https://findahelpline.com/",
    "OTHER": "https://findahelpline.com/",
}

DISCLAIMER = {
    "zh": "本产品是情绪记录与自我调节工具，不提供医疗建议、诊断或治疗，也不能替代专业帮助。如果你处于危险中，请联系当地急救服务或危机热线。",
    "en": "This is an emotion-logging and self-regulation tool. It does not provide medical advice, diagnosis, or treatment, and is not a substitute for professional care. If you are in danger, contact local emergency services or a crisis line.",
}

HELPFUL_PROMPTS = {
    "zh": [
        "这一下有没有让你稍微缓一点？",
        "刚才那 30 秒，身体有没有松一点点？",
        "不用回答得漂亮，就说有没有变化。",
    ],
    "en": [
        "Did that take the edge off at all?",
        "Did anything loosen in those 30 seconds?",
        "No right answer — just whether it moved.",
    ],
}


def intervention_for(emotion: str, lang: str) -> dict:
    meta = EMOTION_MAP.get(emotion) or EMOTION_MAP["calm"]
    branch = meta["branch"]
    body = INTERVENTIONS.get(branch, INTERVENTIONS["none"])
    payload = body.get(lang) or body["zh"]
    say = meta.get("say") or {}
    note = meta.get("say_note") or {}
    return {
        "emotion": meta["key"],
        "branch": branch,
        # 记下之后系统先说的那一句，浮层第一屏
        "say": say.get(lang) or say.get("zh") or "",
        "say_note": note.get(lang) or note.get("zh") or "",
        "branch_hint": BRANCH_HINT.get(branch, BRANCH_HINT["none"]).get(lang, ""),
        "relief_prompt": (HELPFUL_PROMPTS.get(lang) or HELPFUL_PROMPTS["zh"])[0],
        **payload,
    }


def crisis_for(region: str, lang: str) -> dict:
    region = region if region in CRISIS else "OTHER"
    data = CRISIS[region]
    lines = []
    for line in data["lines"]:
        lines.append(
            {
                "name": line["name"],
                "contact": line["contact"].get(lang) or line["contact"]["zh"],
                "number": line.get("number"),
                "url": line.get("url"),
            }
        )
    return {
        "region": region,
        "label": data["label"].get(lang) or data["label"]["zh"],
        "emergency": data["emergency"].get(lang) or data["emergency"]["zh"],
        "lines": lines,
        "verified_at": CRISIS_VERIFIED_AT,
        "source": CRISIS_SOURCES.get(region, ""),
        "free_note": {
            "zh": "这条路径上的资源永久免费，不设付费墙。",
            "en": "Resources on this path are free forever, never behind a paywall.",
        }.get(lang, ""),
    }


def meta_payload(lang: str) -> dict:
    emotions = []
    for e in EMOTIONS:
        say = e.get("say") or {}
        note = e.get("say_note") or {}
        emotions.append(
            {
                "key": e["key"],
                "label": e["zh"]["label"] if lang == "zh" else e["en"]["label"],
                "hint": e["zh"]["hint"] if lang == "zh" else e["en"]["hint"],
                "say": say.get(lang) or say.get("zh") or "",
                "say_note": note.get(lang) or note.get("zh") or "",
                "color": e["color"],
                "branch": e["branch"],
            }
        )
    return {
        "emotions": emotions,
        "severity_labels": {str(k): v for k, v in (SEVERITY_LABELS.get(lang) or SEVERITY_LABELS["zh"]).items()},
        "regions": [{"key": k, "label": CRISIS[k]["label"].get(lang) or CRISIS[k]["label"]["zh"]} for k in REGION_KEYS],
        "disclaimer": DISCLAIMER.get(lang) or DISCLAIMER["zh"],
    }


# --------------------------------------------------------------------------- #
# 缓解转盘
# --------------------------------------------------------------------------- #

# 不知道能做什么的时候，转盘负责替你做决定。
# 默认给的都是「五分钟内能做完、不需要意志力」的小动作 —— 情绪上来时做不了大事。
WHEEL_DEFAULTS = {
    "zh": ["听一首歌", "走五分钟", "喝口水", "洗把脸", "深呼吸三次", "给朋友发条消息"],
    "en": [
        "Listen to one song",
        "Walk for five minutes",
        "Drink some water",
        "Wash your face",
        "Three slow breaths",
        "Text a friend",
    ],
}

WHEEL_LIMITS = {"min": 2, "max": 12, "label": 24}


def wheel_defaults(lang: str) -> list[str]:
    return list(WHEEL_DEFAULTS.get(lang) or WHEEL_DEFAULTS["zh"])
