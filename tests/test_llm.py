from src import llm


def articles():
    return [
        {"id": 1, "title": "Original headline one", "source": "Reuters", "description": "Loan in Vietnam."},
        {"id": 2, "title": "Original headline two", "source": "CNA", "description": "Hotel opens."},
    ]


def test_reworded_titles_cannot_lose_an_article(monkeypatch):
    monkeypatch.setattr(llm, "_generate_gemini_json", lambda prompt, schema=None: {"results": [
        {"id": 1, "evidence": "Vietnam loan.", "should_send": True, "country": "Vietnam", "news_type": "Infrastructure"},
    ]})

    decisions = llm.classify_news_articles(articles())

    assert decisions[0]["id"] == 1
    assert decisions[0]["should_send"] is True
    assert decisions[0]["evidence"] == "Vietnam loan."


def test_unknown_ids_and_text_booleans_are_not_sent(monkeypatch):
    monkeypatch.setattr(llm, "_generate_gemini_json", lambda prompt, schema=None: {"results": [
        {"id": 99, "evidence": "Invented.", "should_send": True},
        {"id": 2, "evidence": "Not relevant.", "should_send": "false"},
    ]})

    decisions = llm.classify_news_articles(articles())

    assert [decision["id"] for decision in decisions] == [2]
    assert decisions[0]["should_send"] is False


def test_evidence_is_generated_before_the_decision():
    order = llm.FILTER_SCHEMA.items.property_ordering if llm.FILTER_SCHEMA.items else None
    order = order or llm.FILTER_SCHEMA.properties["results"].items.property_ordering
    assert order == ["id", "evidence", "should_send", "country", "news_type"]


def test_filter_prompt_stays_apac_only_and_covers_the_brief(monkeypatch):
    captured = {}

    def fake(prompt, schema=None):
        captured["prompt"] = prompt
        return {"results": []}

    monkeypatch.setattr(llm, "_generate_gemini_json", fake)
    llm.classify_news_articles(articles())

    for phrase in ("Asia-Pacific", "project finance", "banking", "energy", "commodit"):
        assert phrase.lower() in captured["prompt"].lower()


def test_summaries_are_matched_by_id(monkeypatch):
    monkeypatch.setattr(llm, "_generate_gemini_json", lambda prompt, schema=None: {"results": [
        {"id": 1, "summary": "Short note.", "key_figures": ["$500m"], "key_parties": ["ADB"]},
    ]})

    summaries = llm.summarize_articles(articles())

    assert summaries[1]["key_figures"] == ["$500m"]
