"""Editing templates and content plans for GTA VI Shorts.

A template turns a verified fact set into a render plan (hook, caption segments, style).
Templates never add facts of their own. Each plan carries the source list that goes into
the description, so attribution is always present.
"""
import json
from pathlib import Path

FACTS_PATH = Path(__file__).resolve().parent / "content" / "facts.json"


def load_facts(path=FACTS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))["facts"]


def countdown_plan(days_left, facts, style="vice_pink"):
    """1-2 day countdown Short built from fact entries (caller checks the fact gate)."""
    if days_left == 0:
        hook = ["LAUNCH", "DAY IS HERE"]
    elif days_left == 1:
        hook = ["1 DAY", "TO GTA VI"]
    else:
        hook = [f"{days_left} DAYS", "TO GTA VI"]
    segments = []
    t = 3.5
    step = 6.0
    for f in facts:
        segments.append({"text": f["text"], "start": t, "end": t + step, "keywords": f.get("keywords", [])})
        t += step
    segments.append({
        "text": "Follow for daily GTA VI updates.",
        "start": t, "end": t + 5.0, "keywords": ["Follow"],
    })
    duration = round(t + 5.5, 1)
    return {
        "template": "countdown_v1",
        "style": style,
        "hook": hook,
        "segments": segments,
        "duration_s": duration,
        "title": f"{hook[0]} {hook[1]} | GTA VI launch countdown",
        "sources": [s for f in facts for s in f["sources"]],
        "fact_ids": [f["id"] for f in facts],
        "creator_id": "self:bounce-rumble",
    }


def description_for(plan, disclaimer):
    lines = [
        "GTA VI launch countdown. Facts from the sources below.",
        "",
        "Sources:",
    ]
    for s in plan["sources"]:
        lines.append(f"- {s['title']}: {s['url']}")
    lines += ["", disclaimer, "", "#GTA6 #GTAVI #GTA"]
    return "\n".join(lines)


def question_plan(key, hook, segments, style="midnight_neon", duration_s=24.0, title="", creator_id="self:bounce-rumble"):
    """Fact-free question Short: no claims about the game, no third-party footage."""
    return {
        "template": "question_v1",
        "style": style,
        "hook": hook,
        "segments": segments,
        "duration_s": duration_s,
        "title": title,
        "sources": [],
        "fact_ids": [],
        "creator_id": creator_id,
        "key": key,
    }


def question_description(plan, disclaimer):
    return "\n".join([
        "A question for you: comment your answer.",
        "",
        "This video makes no factual claims about the game and uses no third-party footage.",
        "",
        disclaimer,
        "",
        "#GTA6 #GTAVI #shorts",
    ])
