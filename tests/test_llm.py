from system_one_chess.llm import parse_choice, render

LABELS = ["e4", "Nf3", "O-O", "exd5", "Qxf7#"]


def test_json_replies_are_read_first():
    assert parse_choice('{"choice": "Nf3"}', LABELS) == "Nf3"
    assert parse_choice('Sure! {"choice": "O-O"} is best.', LABELS) == "O-O"
    assert parse_choice('{"choice": "Kf9"}', LABELS) is None


def test_a_single_label_in_plain_text_is_accepted_but_ambiguity_is_not():
    assert parse_choice("I would play Qxf7# here.", LABELS) == "Qxf7#"
    assert parse_choice("Either e4 or Nf3 works.", LABELS) is None
    assert parse_choice("Pawn to exd5.", LABELS) == "exd5"
    assert parse_choice("Nothing to see.", LABELS) is None


def test_the_prompt_lists_every_option_with_its_description():
    text = render({"fen": "x"}, "Which move?", {"e4": "pawn e2 to e4", "Nf3": None})
    assert text.startswith("Which move?") and '"fen": "x"' in text
    assert "- e4: pawn e2 to e4" in text and "- Nf3\n" in text + "\n"
    assert 'Legal labels:\n["e4", "Nf3"]' in text
