from api.services.decision_answer import decision_answer_prompt


def test_the_answer_carries_the_question_and_the_choice() -> None:
    prompt = decision_answer_prompt("Which repo?", ["a/one"], None)

    assert 'My answer to your question "Which repo?": a/one' in prompt
    assert prompt.endswith("Please continue.")


def test_a_choice_and_a_note_are_both_kept() -> None:
    prompt = decision_answer_prompt("Which repo?", ["a/one", "b/two"], "  the private one ")

    assert "a/one, b/two - the private one" in prompt


def test_no_answer_at_all_is_still_a_usable_instruction() -> None:
    assert "use your best judgement" in decision_answer_prompt("Which?", [], "  ")
