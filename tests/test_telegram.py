from src import telegram


def item(**overrides):
    article = {
        "title": "AT&T <Asia> loan_deal *closes*",
        "url": "https://example.com/a?x=1&y=2",
        "source": "Reuters",
        "published_at": "2026-11-18T02:00:00+00:00",
        "decision": {"country": "Singapore", "news_type": "Infrastructure & Project Finance"},
        "summary": {"summary": "Banks <agreed> terms.", "key_figures": ["$500m"], "key_parties": ["DBS & OCBC"]},
    }
    article.update(overrides)
    return article


def test_special_characters_are_escaped_for_telegram():
    text = telegram.format_article(item())

    assert "AT&amp;T &lt;Asia&gt; loan_deal *closes*" in text
    assert "Banks &lt;agreed&gt; terms." in text
    assert "DBS &amp; OCBC" in text
    assert 'href="https://example.com/a?x=1&amp;y=2"' in text


def test_message_shows_date_source_country_and_type():
    text = telegram.format_article(item())
    for expected in ("18 Nov 2026", "Reuters", "Singapore", "Infrastructure &amp; Project Finance"):
        assert expected in text


def test_figures_and_parties_are_hidden_when_not_stated():
    article = item(summary={"summary": "Short note.", "key_figures": [], "key_parties": []})
    text = telegram.format_article(article)
    assert "Key figures" not in text
    assert "Key parties" not in text


def test_long_briefs_are_split_under_the_telegram_limit():
    articles = [item(url=f"https://example.com/{index}", summary={"summary": "x" * 1400}) for index in range(6)]

    messages = telegram.build_brief_messages(articles, "Morning brief")

    assert len(messages) > 1
    assert all(len(text) <= telegram.MAX_MESSAGE_LENGTH for text, _ in messages)
    assert sum(len(group) for _, group in messages) == 6


def test_send_reports_failure_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(telegram.urllib.request, "urlopen", boom)
    assert telegram.send_telegram_message("hello") is False
