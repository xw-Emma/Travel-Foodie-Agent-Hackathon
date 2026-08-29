"""Phase H verification: the conversation gathers requirements and cannot plan."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config
from src.agents import conversation, intent

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")


def section(title):
    print(f"\n=== {title} ===")


def ids(questions):
    return [question["id"] for question in questions]


class _Echo:
    """Stands in for the LLM so the guards can be tested without a network."""

    def __init__(self, payload):
        self.payload = payload

    def chat(self, **_):
        return {"content": self.payload}


COMPLETE = {"city": "Lisbon", "budget_amount": 100.0,
            "budget_basis": "per_person", "origin_text": "Hotel Avenida",
            "start_date": "2026-09-05"}
token = config.set_backend_override("local")

section("H2 code decides what is missing, not the model")
check("nothing extracted asks for a description",
      ids(conversation.missing_information({})), ["describe"])
check("a missing city is asked for",
      "city" in ids(conversation.missing_information({"budget_amount": 100.0})), True)
check("a stated budget with no basis is asked about",
      "budget_basis" in ids(conversation.missing_information(
          {**COMPLETE, "budget_basis": None})), True)
check("a missing origin is asked for",
      "origin" in ids(conversation.missing_information(
          {**COMPLETE, "origin_text": None})), True)
check("missing dates are asked for",
      "dates" in ids(conversation.missing_information(
          {**COMPLETE, "start_date": None})), True)
check("a complete draft asks nothing",
      conversation.missing_information(COMPLETE), [])
check("every question id is in the closed list",
      all(question["id"] in conversation.QUESTION_IDS
          for question in conversation.missing_information({"city": "Lisbon"})), True)
check("no more than two per turn, so it is not an interrogation",
      len(conversation.missing_information({"city": "Lisbon"}))
      <= conversation.MAX_QUESTIONS_PER_TURN, True)
check("an answered question is not asked again",
      "origin" in ids(conversation.missing_information(
          {**COMPLETE, "origin_text": None}, asked={"origin"})), False)
check("and neither is one the user ignored",
      conversation.missing_information({**COMPLETE, "start_date": None},
                                       asked={"dates"}), [])

section("H2 a country asks instead of guessing")
country_rejection = [{"field": "city", "value": "Portugal",
                      "reason": "that is a country, not a city - name the city"}]
questions = conversation.missing_information({"budget_amount": 100.0}, None, None,
                                             country_rejection)
check("the country triggers its own question",
      "city_is_country" in ids(questions), True)
check("and names the country back", "Portugal" in questions[0]["text"], True)
check("the plain missing-city question is not also asked",
      "city" in ids(questions), False)

section("H2 F's infeasibility becomes a question")
feasibility = {"checked": True, "feasible": False,
               "reason": "The cheapest plan costs $240.00, $40.00 over the "
                         "$200.00 budget.",
               "suggestions": [{"text": "Raise the budget to $240.00"},
                               {"text": "Plan 1 day instead of 2"}]}
questions = conversation.missing_information(COMPLETE, feasibility)
check("an unsatisfiable plan is raised", ids(questions), ["infeasible"])
check("with F's own numbers", "$240.00" in questions[0]["text"], True)
check("and the options it computed",
      "Plan 1 day instead of 2" in questions[0]["text"], True)
check("a feasible plan raises nothing",
      conversation.missing_information(
          COMPLETE, {"checked": True, "feasible": True}), [])
check("an unchecked feasibility raises nothing",
      conversation.missing_information(COMPLETE, {"checked": False}), [])

section("H1/H3 it gathers - it cannot plan, and cannot name a venue")
history = [{"role": "user", "content": "Lisbon, lunch and dinner, "
                                       "starting from Hotel Avenida"}]
turn = conversation.next_turn(
    _Echo('{"city":"Lisbon","meals":["lunch","dinner"],'
          '"origin_text":"Hotel Avenida","budget_amount":100,'
          '"budget_basis":"per_person","start_date":"2026-09-05",'
          '"restaurants":["Belcanto"],"itinerary":[{"lunch":"A Cevicheria"}]}'),
    history, None, "local")
check("there is no itinerary key in the reply", "itinerary" in turn, False)
check("venue keys never reach the fields",
      set(turn["fields"]) & intent.VENUE_LIKE_KEYS, set())
check("and are reported as refused",
      {"restaurants", "itinerary"} <= {item["field"] for item in turn["rejected"]},
      True)
check("the legitimate fields do come through",
      turn["fields"].get("origin_text"), "Hotel Avenida")
check("a complete turn is ready", turn["ready"], True)

section("H1 later turns can correct earlier ones")
two_turns = [{"role": "user", "content": "Two days in Lisbon"},
             {"role": "assistant", "content": "Which dates?"},
             {"role": "user", "content": "Actually make it one day"}]
check("the whole conversation is re-read, so a correction can win",
      "Actually make it one day" in conversation._user_text(two_turns), True)
check("assistant turns are not fed back as user input",
      "Which dates?" in conversation._user_text(two_turns), False)

section("H5 it degrades honestly")
check("an empty history asks for a description",
      ids(conversation.next_turn(None, [], None, "local")["questions"]),
      ["describe"])
check("and is not marked ready",
      conversation.next_turn(None, [], None, "local")["ready"], False)
no_llm = conversation.next_turn(None, history, None, "local")
check("no LLM yields no invented fields", no_llm["fields"], {})
check("and says so rather than pretending",
      "No LLM" in (no_llm["notes"][0] if no_llm["notes"] else ""), True)


class _Boom:
    def chat(self, **_):
        raise RuntimeError("gateway down")


broken = conversation.next_turn(_Boom(), history, None, "local")
check("a gateway failure still returns a usable turn", broken["fields"], {})
check("and never raises", broken["ready"], False)
check("garbage from the model yields nothing, not a guess",
      conversation.next_turn(_Echo("not json"), history, None, "local")["fields"],
      {})

section("H the origin lands in the field the Route Agent reads")
check("origin_text is an accepted field", "origin_text" in intent.ALLOWED_FIELDS, True)
check("a landmark is kept",
      intent.validate({"origin_text": "Hotel Avenida Palace"},
                      "local")["fields"].get("origin_text"),
      "Hotel Avenida Palace")
check("something absurdly long is refused, not truncated silently",
      [item["field"] for item in
       intent.validate({"origin_text": "x" * 300}, "local")["rejected"]],
      ["origin_text"])
config._backend_override.reset(token)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("ALL PHASE H CHECKS PASSED")
