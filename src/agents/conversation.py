"""A conversation that gathers requirements. It does not plan, and it cannot.

THE LINE THIS FILE HOLDS: the kickoff deck's whole framing is "an agent, not a
chatbot". So this asks about what is missing and fills the form; the pipeline
still plans, verifies and reports. It never answers with an itinerary, never
names a venue, and pressing Plan is still the user's decision.

Two things are deliberately NOT the model's job:

1. Deciding what is missing. That is a constraint check, and constraint checks
   live in code (project ground rule 2). The question list below is closed and
   `missing_information` decides which apply. A model asked to work out what it
   still needs will invent requirements, or forget the ones that matter.
2. Deciding whether a value is usable. Every field goes through
   intent.validate - the city back through classify_city, cuisines and allergens
   intersected with real vocabularies, numbers range-checked - and every
   rejection is reported.

What the model IS for: reading "make it one day instead" or "we're at the Hotel
Avenida" out of a sentence. That is the part code is bad at.
"""
from __future__ import annotations

from . import intent

# The complete set of things this agent may ask about. Closed on purpose: a
# question that is not here cannot be asked, so the conversation cannot wander
# into territory the planner has no field for.
QUESTION_IDS = ("describe", "city", "city_is_country", "origin", "budget_basis",
                "dates", "infeasible", "kids")

# At most this many per turn. A form disguised as an interrogation is worse than
# a form.
MAX_QUESTIONS_PER_TURN = 2


def _question(qid: str, text: str, why: str, field: str | None = None) -> dict:
    return {"id": qid, "text": text, "why": why, "field": field}


def missing_information(fields: dict, feasibility: dict | None = None,
                        asked: set[str] | None = None,
                        rejected: list[dict] | None = None) -> list[dict]:
    """What still needs answering, decided in code.

    `asked` stops the same question repeating when a user chooses not to answer
    it - silence is an answer, and nagging is not a feature.
    """
    asked = set(asked or ())
    fields = fields or {}
    rejected = rejected or []
    questions: list[dict] = []

    if not fields:
        questions.append(_question(
            "describe", "Tell me about the trip — city, how long, what you want "
            "to eat, and roughly what you want to spend.",
            "nothing has been picked up from the conversation yet"))
        return questions[:MAX_QUESTIONS_PER_TURN]

    # A country reached validate() and was refused there; ask rather than guess.
    country = next((item for item in rejected
                    if item.get("field") == "city"
                    and "not a city" in (item.get("reason") or "")), None)
    if country and "city_is_country" not in asked:
        questions.append(_question(
            "city_is_country",
            f"{country.get('value')} is a country, not a city — which city "
            "should I plan for? Every search is phrased \"…in <place>\", so a "
            "country pulls results from anywhere in it.",
            "a country in the city box poisons every search", "city"))
    elif not fields.get("city") and "city" not in asked:
        questions.append(_question(
            "city", "Which city is this trip in?",
            "the city decides every search", "city"))

    # Feasibility comes from the LAST plan, so this only fires once something
    # has actually been searched and priced - no extra API call to ask it.
    if (feasibility and feasibility.get("checked")
            and feasibility.get("feasible") is False
            and "infeasible" not in asked):
        options = "; ".join(item["text"] for item in
                            (feasibility.get("suggestions") or [])[:3])
        questions.append(_question(
            "infeasible",
            f"{feasibility.get('reason', '')} Which would you rather change? "
            f"{options}",
            "no combination of venues fits the budget as stated"))

    if fields.get("budget_amount") and not fields.get("budget_basis") \
            and "budget_basis" not in asked:
        questions.append(_question(
            "budget_basis",
            f"Is ${fields['budget_amount']:,.0f} per person, or for the whole "
            "party? It makes a very different trip for a party of four.",
            "the same number means two different budgets", "budget_basis"))

    if not fields.get("origin_text") and "origin" not in asked:
        questions.append(_question(
            "origin", "Where are you starting from each day — a hotel, an "
            "address, or a landmark? I plan the routes from there.",
            "without it the routes start from the city centre", "origin_text"))

    if not fields.get("start_date") and "dates" not in asked:
        questions.append(_question(
            "dates", "Which dates? It lets me check that each place is actually "
            "open when you would arrive.",
            "opening hours cannot be checked without a weekday", "start_date"))

    # Only worth asking when the party is big enough for children to be
    # plausible - "any kids?" to a solo traveller is a silly question, and the
    # answer only changes anything because Places actually carries the field.
    if (fields.get("family_friendly") is None
            and (fields.get("party_size") or 0) >= 3
            and "kids" not in asked):
        questions.append(_question(
            "kids", "Any children in the party? I can drop places Google marks "
            "as not good for children — though most places say nothing either "
            "way, so I can only exclude, not promise.",
            "a real Places field, but only present on some venues",
            "family_friendly"))

    return questions[:MAX_QUESTIONS_PER_TURN]


def _user_text(history: list[dict]) -> str:
    """Every user turn, joined.

    Re-reading the whole conversation each turn is what lets a later message
    correct an earlier one - "actually make it one day" has to be able to
    override "two days" without any diff logic here.
    """
    return "\n".join(str(turn.get("content") or "").strip()
                     for turn in (history or [])
                     if turn.get("role") == "user" and turn.get("content"))


def next_turn(client, history: list[dict], feasibility: dict | None = None,
              backend: str = "auto", asked: set[str] | None = None) -> dict:
    """Read the conversation so far, then ask about what is still missing.

    Returns {"fields", "questions", "ready", "rejected", "notes",
    "other_criteria"}. There is no itinerary key and there never will be.
    """
    text = _user_text(history)
    if not text:
        return {"fields": {}, "questions": missing_information({}), "ready": False,
                "rejected": [], "notes": [], "other_criteria": [], "ok": False}

    # The same extractor and the same validator as Phase B. A second, looser
    # path into the form would be a second place for an unchecked value to enter.
    draft = intent.extract(client, text, backend)
    questions = missing_information(draft.get("fields") or {}, feasibility,
                                    asked, draft.get("rejected"))
    return {**draft, "questions": questions, "ready": not questions}
