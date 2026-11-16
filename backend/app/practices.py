"""Versioned, original self-help prompts; no diagnosis or treatment claims.

Conceptual reference: WHO, Doing What Matters in Times of Stress (2020).
These short product exercises are not a reproduction of, or substitute for,
the WHO programme. Clinical review has not been completed. See docs/practices.md.
"""
from copy import deepcopy


def step(key, title, body, seconds=0, prompt=None):
    return {"id": key, "title": title, "body": body, "seconds": seconds, "prompt": prompt}


PRACTICES = {
    "grounding": {
        "id": "grounding", "version": 1, "minutes": 2, "icon": "grounding",
        "zh": {
            "title": "回到当下", "subtitle": "留意身体与周围，给自己一点停顿。",
            "steps": [
                step("contact", "找一个舒服的位置", "坐着或站着都可以。留意双脚与地面、身体与椅子的接触。保持自己舒服的呼吸，不必改变节奏。", 30),
                step("surroundings", "看看身边", "保持睁眼，慢慢找出周围三样你看得到的东西，再留意一种声音。无需找对答案，只是注意此刻。", 30),
                step("return", "把注意力带回来", "如果注意力跑远了，可以再次看看周围。选择眼前一件你愿意继续做的事，按自己的节奏回去。", 20),
            ],
        },
        "en": {
            "title": "Back to the present", "subtitle": "Notice your body and surroundings. Take a small pause.",
            "steps": [
                step("contact", "Find a comfortable position", "Sit or stand. Notice your feet touching the floor or your body meeting the chair. Let your breathing follow its own comfortable rhythm.", 30),
                step("surroundings", "Look around you", "With your eyes open, notice three things you can see and one sound you can hear. There is no right answer. Just notice this moment.", 30),
                step("return", "Bring your attention back", "If your attention wanders, gently look around again. Choose one thing in front of you that you want to return to, at your own pace.", 20),
            ],
        },
    },
    "notice-thoughts": {
        "id": "notice-thoughts", "version": 1, "minutes": 3, "icon": "thoughts",
        "zh": {
            "title": "观察一个念头", "subtitle": "先看见脑海里的话，不急着下结论。",
            "steps": [
                step("thought", "此刻在想什么？", "选一个此刻容易留意到的念头，不必回忆让你痛苦的经历。愿意的话，可以用一句话写下来，也可以留空。", prompt="此刻浮现的一句话（可跳过）"),
                step("notice", "试着加上一个开头", "在心里试着说：我注意到自己在想……然后接上那句话。无需争辩、证明或强迫自己积极起来。", 30),
                step("choose", "留意眼前", "看看周围，感受脚下的支撑。此刻你愿意把注意力放在哪件小事上？不需要现在解决所有问题。", 20),
            ],
        },
        "en": {
            "title": "Notice a thought", "subtitle": "Notice the words in your mind before drawing conclusions.",
            "steps": [
                step("thought", "What is on your mind?", "Choose a thought that is easy to notice right now. You do not need to revisit painful experiences. Write one sentence if you want, or leave it blank.", prompt="A thought that is here now (optional)"),
                step("notice", "Try a different beginning", "Try saying to yourself: I notice that I am having the thought that… followed by those words. There is no need to argue, prove anything, or force a positive thought.", 30),
                step("choose", "Notice what is here", "Look around and notice the support beneath your feet. What small thing would you like to give your attention to? You do not have to solve everything now.", 20),
            ],
        },
    },
    "make-room": {
        "id": "make-room", "version": 1, "minutes": 2, "icon": "room",
        "zh": {
            "title": "给情绪一点空间", "subtitle": "用自己的方式，留意此刻的感受。",
            "steps": [
                step("name", "给感受一个名字", "如果愿意，试着在心里说：我现在感到……。说不清楚也没关系，不必评判这种感受。", 25),
                step("allow", "暂时不急着改变", "保持你觉得舒服的姿势，看看能否让这份感受暂时待一会儿。你可以随时停下，把注意力放回周围的物品或声音。", 30),
                step("care", "给自己一句话", "试着对自己说一句你愿意听到的话，例如：此刻不容易，我可以慢一点。也可以只安静地坐一会儿。", 20),
            ],
        },
        "en": {
            "title": "Make room for feelings", "subtitle": "Notice this moment in a way that feels manageable.",
            "steps": [
                step("name", "Give the feeling a name", "If you want, try saying: Right now, I feel… It is okay if you cannot find a name. You do not need to judge the feeling.", 25),
                step("allow", "No need to change it now", "Stay in a comfortable position and see whether you can let the feeling be here for a moment. You can stop at any time and return your attention to things or sounds around you.", 30),
                step("care", "Offer yourself a few words", "Try words you would want to hear, such as: This is a difficult moment. I can take my time. You can also simply sit quietly for a moment.", 20),
            ],
        },
    },
    "small-action": {
        "id": "small-action", "version": 1, "minutes": 3, "icon": "action",
        "zh": {
            "title": "做一件小事", "subtitle": "选一件你愿意、也方便去做的事。",
            "steps": [
                step("action", "只选一件就好", "想一件此刻方便、对你有一点意义的小事，比如整理眼前的一样物品，或给自己倒杯水。选择由你决定，也可以今天先不做。", prompt="我愿意尝试的小事（可跳过）"),
                step("try", "按自己的节奏试试", "如果方便，可以现在尝试一下。需要离开屏幕时点暂停；回来后继续。没有完成也可以结束，不需要追赶计时。", 60),
                step("reflect", "留下一点观察", "刚才做了什么，或有什么让你停下？只描述你注意到的事，不需要给自己打分。", prompt="我注意到……（可跳过）"),
            ],
        },
        "en": {
            "title": "One small action", "subtitle": "Choose something you want and are able to do.",
            "steps": [
                step("action", "Choose just one thing", "Think of a manageable action that matters a little to you, such as putting one item away or getting yourself some water. The choice is yours. It is also okay not to do it today.", prompt="A small action I want to try (optional)"),
                step("try", "Try it at your own pace", "If it is convenient, try it now. Pause if you leave the screen, then continue when you return. You can finish even if the action is incomplete. There is no need to race the timer.", 60),
                step("reflect", "Leave a small observation", "What did you do, or what made you stop? Describe what you noticed without grading yourself.", prompt="I noticed… (optional)"),
            ],
        },
    },
}


def localize(practice, lang):
    lang = lang if lang in ("zh", "en") else "zh"
    return {key: deepcopy(practice[key]) for key in ("id", "version", "minutes", "icon")} | deepcopy(practice[lang])


def catalog(lang):
    return [localize(practice, lang) for practice in PRACTICES.values()]
