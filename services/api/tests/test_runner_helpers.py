"""Unit tests for the e2e runner's pure helpers."""
from evals.run_evals import _gap_ridealong

GAP = [{
    "key": "spouse_full_name",
    "detect": r"spouse or partner'?s name",
    "answer": "My wife's full legal name is Priya Sharma.",
}]
REPLY = ("Thanks — I've recorded 6 values. One more thing from earlier: "
         "What is your spouse or partner's name?")


def test_gap_answer_rides_along_when_asked_and_unanswered():
    used: set[str] = set()
    text, keys = _gap_ridealong(REPLY, GAP, {}, used)
    assert "Priya Sharma" in text and keys == ["spouse_full_name"]
    assert used == {"spouse_full_name"}


def test_gap_answer_silent_when_already_answered():
    text, keys = _gap_ridealong(REPLY, GAP, {"spouse_full_name": "Priya Sharma"}, set())
    assert text == "" and keys == []


def test_gap_answer_fires_once_and_only_on_match():
    used: set[str] = set()
    assert _gap_ridealong("What city do you live in?", GAP, {}, used) == ("", [])
    _gap_ridealong(REPLY, GAP, {}, used)
    assert _gap_ridealong(REPLY, GAP, {}, used) == ("", [])
