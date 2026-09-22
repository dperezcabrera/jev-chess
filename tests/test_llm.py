from system_one_chess.llm import parse_choice, render

LABELS = ["e4", "Nf3", "O-O", "exd5", "Qxf7#"]


def test_json_replies_are_read_first():
    assert parse_choice('{"choice": "Nf3"}', LABELS) == "Nf3"
    assert parse_choice('Sure! {"choice": "O-O"} is best.', LABELS) == "O-O"
    assert parse_choice('{"choice": "Kf9"}', LABELS) is None


def test_other_spellings_case_and_punctuation_do_not_count_as_illegal():
    aliases = {"e2e4": "e4", "Qxf7": "Qxf7#", "0-0": "O-O", "g1f3": "Nf3"}
    assert parse_choice('{"choice": "e2e4"}', LABELS, aliases) == "e4"
    assert parse_choice('{"choice": "nf3!"}', LABELS) == "Nf3"
    assert parse_choice('{"choice": "Qxf7"}', LABELS, aliases) == "Qxf7#"
    assert parse_choice("I castle: 0-0.", LABELS, aliases) == "O-O"
    assert parse_choice("Best is g1f3, developing.", LABELS, aliases) == "Nf3"
    assert parse_choice('{"choice": "Nf6"}', LABELS, aliases) is None, "a move that is not legal stays illegal"


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


def test_chess_aliases_cover_uci_check_signs_castling_and_captures():
    import chess

    from system_one_chess.jev import move_aliases

    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    options = {board.san(m): m for m in board.legal_moves}
    aliases = move_aliases(board, options)
    assert aliases["e1g1"] == "O-O" and aliases["0-0-0"] == "O-O-O" and aliases["e1-g1"] == "O-O"
    board = chess.Board("4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1")
    options = {board.san(m): m for m in board.legal_moves}
    aliases = move_aliases(board, options)
    assert aliases["Qe7"] == "Qe7+" and aliases["e2e7"] == "Qe7+"


def test_the_schema_only_admits_the_labels_and_a_model_that_rejects_it_is_asked_without():
    import asyncio

    import httpx

    from system_one_chess.llm import LLMApi, choice_schema
    from system_one_chess.provider import Gateway

    assert choice_schema(["e4", "Nf3"])["json_schema"]["schema"]["properties"]["choice"]["enum"] == ["e4", "Nf3"]
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "", "reasoning": "I play e4"}}], "usage": {}}
        )

    api = LLMApi()
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = Gateway("openrouter", "https://openrouter.ai/api", "key", "", 30.0, False)
    answer = asyncio.run(api.choose(gateway, "acme/plain", {"fen": "x"}, "Which move?", {"e4": None, "Nf3": None}))
    assert answer.choice == "e4" and answer.illegal == 0, "the move named in the reasoning counts"
    assert [("response_format" in b) for b in bodies] == [True, False]
    asyncio.run(api.choose(gateway, "acme/plain", {"fen": "x"}, "Which move?", {"e4": None, "Nf3": None}))
    assert len(bodies) == 3 and "response_format" not in bodies[2], "the rejection is remembered"


def test_an_empty_or_errored_reply_is_asked_again_and_is_not_an_illegal_move(monkeypatch):
    import asyncio
    import json

    import httpx

    from system_one_chess import llm as llm_module
    from system_one_chess.llm import LLMApi, LLMError
    from system_one_chess.provider import Gateway

    real_sleep = asyncio.sleep
    monkeypatch.setattr(llm_module.asyncio, "sleep", lambda _: real_sleep(0))
    replies = [
        {"message": {"content": "", "reasoning": "hmm"}, "finish_reason": "error"},
        {"message": {"content": None}, "finish_reason": "stop"},
        {"message": {"content": json.dumps({"choice": "Nf3"})}, "finish_reason": "stop"},
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        reply = replies.pop(0) if replies else {"message": {"content": ""}, "finish_reason": "error"}
        return httpx.Response(
            200, json={"choices": [reply], "usage": {"prompt_tokens": 10, "completion_tokens": 0, "cost": 0.0001}}
        )

    api = LLMApi()
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = Gateway("openrouter", "https://openrouter.ai/api", "key", "", 30.0, False)
    answer = asyncio.run(
        api.choose(gateway, "acme/flaky", {"fen": "x"}, "Which move?", {"e4": None, "Nf3": None}, attempts=1)
    )
    assert answer.choice == "Nf3" and answer.illegal == 0 and answer.illegal_answers == ()
    assert len(calls) == 3 and abs(answer.cost_usd - 0.0003) < 1e-9, (
        "the blank replies cost and were retried, not counted"
    )
    try:
        asyncio.run(api.choose(gateway, "acme/flaky", {"fen": "x"}, "Which move?", {"e4": None}, attempts=1))
    except LLMError as error:
        assert "no answer" in str(error)
    else:
        raise AssertionError("persistent blanks must fail as a gateway error, not as a forfeit")


def test_a_reply_cut_short_while_thinking_is_asked_again_with_a_bigger_budget_before_it_counts():
    import asyncio
    import json

    import httpx

    from system_one_chess.llm import MAX_TOKENS, TRUNCATED_TOKENS, LLMApi
    from system_one_chess.provider import Gateway

    bodies = []
    replies = [
        {
            "message": {"content": "", "reasoning": "Let me analyze this position carefully..."},
            "finish_reason": "length",
        },
        {"message": {"content": json.dumps({"choice": "e4"})}, "finish_reason": "stop"},
        {"message": {"content": "", "reasoning": "Analyzing..."}, "finish_reason": "length"},
        {"message": {"content": "", "reasoning": "Still analyzing..."}, "finish_reason": "length"},
        {"message": {"content": json.dumps({"choice": "Nf3"})}, "finish_reason": "stop"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [replies.pop(0)], "usage": {"prompt_tokens": 10, "completion_tokens": 100, "cost": 0.001}},
        )

    api = LLMApi()
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = Gateway("openrouter", "https://openrouter.ai/api", "key", "", 30.0, False)
    labels = {"e4": None, "Nf3": None}
    answer = asyncio.run(api.choose(gateway, "acme/thinker", {"fen": "x"}, "Which move?", labels, attempts=2))
    assert answer.choice == "e4" and answer.illegal == 0, "one truncated reply is not an illegal move"
    assert bodies[0]["max_tokens"] == MAX_TOKENS and "reasoning" not in bodies[0]
    assert bodies[1]["max_tokens"] == TRUNCATED_TOKENS and bodies[1]["reasoning"] == {"effort": "low"}
    answer = asyncio.run(api.choose(gateway, "acme/thinker", {"fen": "x"}, "Which move?", labels, attempts=2))
    assert answer.choice == "Nf3" and answer.illegal == 1, "a second truncated reply in the same decision counts"
    assert answer.illegal_answers[0].startswith("[ran out of tokens while thinking] Still analyzing")
