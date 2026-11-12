"""演示数据：一次性写入 28 条跨两周的记录，让「AI 总结」一打开就有东西可看。

数据刻意做成计划书里的那种模式：一个高频场景（被临时加需求）集中在下午、
严重程度偏高；伤心集中在晚上；等等。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# (days_ago, hour, minute, emotion, severity, cause, relief, intervention_completed)
DATA = [
    (13, 16, 12, "anger", 4, "下午四点又被临时加需求，晚上要赶出来", "better", True),
    (13, 10, 30, "anxiety", 3, "对未来的不确定，一直在想", "same", True),
    (12, 8, 5, "anger", 2, "地铁堵了，迟到", "better", True),
    (12, 21, 40, "sadness", 4, "和他吵架，说了难听的话", "better", True),
    (11, 8, 20, "drained", 2, "没睡好，四点就醒了", None, False),
    (11, 14, 10, "anxiety", 3, "论文没进展", "same", True),
    (10, 23, 15, "despair", 4, "觉得自己很没用，看不到出路", None, True),
    (10, 22, 5, "sadness", 2, "刷社交媒体刷到难受", None, False),
    (9, 17, 3, "anger", 5, "被临时加需求，改了三版", "better", True),
    (9, 19, 30, "calm", 1, "出去走了二十分钟，还行", None, False),
    (8, 22, 10, "sadness", 3, "冷战第二天，没人说话", "same", True),
    (8, 11, 45, "anxiety", 4, "不知道这个决定对不对", "better", True),
    (7, 9, 15, "drained", 3, "熬夜到两点，头疼", None, False),
    (7, 15, 20, "anxiety", 2, "房租和账单一起到了", "same", True),
    (6, 16, 48, "anger", 4, "临时加需求，还要今天给", "better", True),
    (6, 18, 40, "good", 1, "和朋友吃饭，挺好", None, False),
    (6, 0, 25, "despair", 5, "什么都做不好，没有意义", None, True),
    (5, 20, 15, "sadness", 4, "和家人通电话，被否定", "same", True),
    (5, 9, 35, "anger", 3, "堵车四十分钟", "better", True),
    (4, 8, 10, "drained", 2, "失眠，翻到三点", None, False),
    (4, 13, 50, "drained", 2, "头疼，身体很沉", None, False),
    (3, 17, 20, "anger", 5, "临时加需求 + 会议连轴", "better", True),
    (3, 9, 5, "anxiety", 3, "计划变了，很慌", "same", True),
    (2, 21, 30, "sadness", 3, "一个人待着，谁都不想找", "same", True),
    (2, 23, 45, "sadness", 3, "觉得自己不够好", None, False),
    (1, 10, 25, "anger", 3, "又被加需求了", "same", True),
    (1, 8, 15, "drained", 3, "又没睡好", None, False),
    (1, 20, 10, "calm", 2, "今天还撑得住", None, False),
]


def build_rows(tz_offset: int) -> list[dict]:
    tz = timezone(timedelta(minutes=tz_offset))
    now_local = datetime.now(tz)
    rows = []
    for i, (days_ago, hour, minute, emotion, severity, cause, relief, completed) in enumerate(DATA):
        local = (now_local - timedelta(days=days_ago)).replace(
            hour=hour, minute=minute, second=(i * 7) % 60, microsecond=0
        )
        utc = local.astimezone(timezone.utc)
        rows.append(
            {
                "emotion": emotion,
                "severity": severity,
                "cause": cause,
                "note": "",
                "occurred_at": utc.isoformat(timespec="seconds"),
                "local_date": local.strftime("%Y-%m-%d"),
                "local_hour": local.hour,
                "tz_offset": tz_offset,
                "relief": relief,
                "intervention_completed": 1 if completed else 0,
            }
        )
    return rows
