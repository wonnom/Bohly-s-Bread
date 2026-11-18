"""Rule-based filtering, ordering and same-event dedupe. No LLM calls happen here."""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

FRESH_HOURS = 24
SAME_EVENT_THRESHOLD = 0.6
SAME_AMOUNT_THRESHOLD = 0.3
MIN_TOPIC_SCORE = 2

DENY_PATTERNS = re.compile(
    r"earnings call|conference call|investor call|webcast|transcript|to report .*results|"
    r"stocks? to (buy|watch|sell)|price target|share consolidation|ads ratio|"
    r"(buy|sell|hold) rating|shares? (jump|jumps|fall|falls|slide|slides|surge|surges|soar|soars)",
    re.IGNORECASE,
)

APAC_PATTERN = re.compile(
    r"\b(asia|asian|apac|asean|asia[- ]pacific|singapore|singaporean|malaysia|malaysian|indonesia|indonesian|"
    r"thailand|thai|vietnam|vietnamese|philippines|philippine|filipino|india|indian|china|chinese|hong kong|"
    r"japan|japanese|korea|korean|taiwan|taiwanese|australia|australian|new zealand|bangladesh|pakistan|"
    r"sri lanka|cambodia|laos|myanmar|brunei|nepal|mongolia|adb|aiib|temasek|sembcorp|keppel|adani|"
    r"petronas|pertamina|sinopec|cnooc|petrochina|jera|kogas|woodside|santos)\b",
    re.IGNORECASE,
)

COUNTRY_NAMES = {
    "singapore": "singapore", "singaporean": "singapore", "malaysia": "malaysia", "malaysian": "malaysia",
    "indonesia": "indonesia", "indonesian": "indonesia", "thailand": "thailand", "thai": "thailand",
    "vietnam": "vietnam", "vietnamese": "vietnam", "philippines": "philippines", "philippine": "philippines",
    "filipino": "philippines", "india": "india", "indian": "india", "china": "china", "chinese": "china",
    "hong kong": "hong kong", "japan": "japan", "japanese": "japan", "korea": "korea", "korean": "korea",
    "taiwan": "taiwan", "taiwanese": "taiwan", "australia": "australia", "australian": "australia",
    "new zealand": "new zealand", "bangladesh": "bangladesh", "pakistan": "pakistan", "sri lanka": "sri lanka",
    "cambodia": "cambodia", "laos": "laos", "myanmar": "myanmar", "brunei": "brunei", "nepal": "nepal",
    "mongolia": "mongolia",
}
COUNTRY_PATTERN = re.compile(r"\b(" + "|".join(COUNTRY_NAMES) + r")\b", re.IGNORECASE)

STRONG_TERMS = [
    r"project financ\w*", r"financial close", r"syndicated (loan|facility)", r"green (loan|bond)",
    r"refinanc\w*", r"public[- ]private partnership", r"\bppp\b", r"credit facilit\w*",
    r"final investment decision", r"\bfid\b", r"private credit", r"\bopec\+?", r"\bbrent\b",
    r"oil prices?", r"interest rates?", r"monetary policy", r"central bank", r"trade finance",
    r"supply chain finance", r"cross[- ]border payments?", r"transaction banking",
]
DEAL_TERMS = [
    r"\bfinancing\b", r"\bfinanced?\b", r"\bloans?\b", r"\bbonds?\b", r"\bdebt\b", r"\blenders?\b",
    r"\bacqui\w+", r"\bstakes?\b", r"\bdivest\w*", r"\bmergers?\b", r"\binvest(s|ing|ment|ments)?\b",
    r"\bdeals?\b", r"\bfunding\b", r"\bsanctions?\b",
]
SECTOR_TERMS = [
    r"\blng\b", r"\bgas\b", r"\boil\b", r"\bpower\b", r"\belectricity\b", r"\bsolar\b", r"\bwind\b",
    r"\bhydro\w*", r"\bbattery\b|\benergy storage\b", r"\brenewables?\b", r"\bgrid\b", r"\butilit(y|ies)\b",
    r"\bports?\b", r"\bairports?\b", r"\brail(way)?\b", r"\bdata cent(er|re)s?\b", r"\btelecoms?\b",
    r"\binfrastructure\b", r"\bpipelines?\b", r"\bbanks?\b", r"\bcommodit(y|ies)\b",
]
AMOUNT_PATTERN = re.compile(r"[$€£¥₹]\s?\d|\b\d[\d,.]*\s?(bn|billion|million|crore|mw|gw|mtpa)\b", re.IGNORECASE)

STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "in", "into", "on", "for", "by", "at", "with", "from", "as", "its",
    "is", "are", "be", "will", "after", "over", "says", "said", "new", "us",
}


def _text(article: Dict[str, Any]) -> str:
    return f"{article.get('title', '')} {article.get('description', '')}"


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def article_time(article: Dict[str, Any]) -> Optional[datetime]:
    return _parse_time(article.get("published_at")) or _parse_time(article.get("first_seen"))


def is_fresh(article: Dict[str, Any], now: datetime, hours: int = FRESH_HOURS) -> bool:
    moment = article_time(article)
    return moment is None or now - moment <= timedelta(hours=hours)


def _matched_terms(terms: List[str], text: str) -> List[str]:
    matches = (re.search(term, text, re.IGNORECASE) for term in terms)
    return [" ".join(match.group(0).lower().split()) for match in matches if match]


def score_breakdown(article: Dict[str, Any]) -> Dict[str, Any]:
    """Explain an article's ranking score so it can be reviewed after the trial."""
    text = _text(article)
    strong = _matched_terms(STRONG_TERMS, text)
    deal = _matched_terms(DEAL_TERMS, text)
    sector = _matched_terms(SECTOR_TERMS, text)
    amount = 1 if AMOUNT_PATTERN.search(text) else 0
    trust = 3 - int(article.get("trust", 3))
    topic = len(strong) * 3 + len(deal) * 2 + len(sector) + amount
    return {
        "total": topic + trust,
        "topic": topic,
        "strong": len(strong),
        "deal": len(deal),
        "sector": len(sector),
        "amount": amount,
        "trust": trust,
        "matched_terms": sorted(set(strong + deal + sector)),
    }


def topic_score(article: Dict[str, Any]) -> int:
    return score_breakdown(article)["topic"]


def rejection_reason(article: Dict[str, Any]) -> Optional[str]:
    """Return why the code filters remove an article, or None if it passes."""
    text = _text(article)
    if DENY_PATTERNS.search(text):
        return "deny_list"
    if article.get("region") != "APAC" and not APAC_PATTERN.search(text):
        return "not_apac"
    if topic_score(article) < MIN_TOPIC_SCORE:
        return "low_topic_score"
    return None


def passes_code_filter(article: Dict[str, Any]) -> bool:
    return rejection_reason(article) is None


def rank_key(article: Dict[str, Any]) -> Tuple[int, float]:
    moment = article_time(article)
    return score_breakdown(article)["total"], moment.timestamp() if moment else 0.0


def shortlist(articles: Iterable[Dict[str, Any]], limit: int = 40, per_source: int = 8) -> List[Dict[str, Any]]:
    """Return the strongest articles, limiting how many any one source can contribute."""
    chosen: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for article in sorted(articles, key=rank_key, reverse=True):
        source = str(article.get("source", "")).lower()
        if counts.get(source, 0) >= per_source:
            continue
        counts[source] = counts.get(source, 0) + 1
        chosen.append(article)
        if len(chosen) == limit:
            break
    return chosen


def _title_tokens(title: str) -> set:
    text = title.lower().replace("indian", "india")
    text = re.sub(r"(?:us|s|a|hk)?[$€£¥₹]", " ", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:bn|billion)\b", r"\1b", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:mn|million)\b", r"\1m", text)
    tokens = set()
    for token in re.findall(r"[a-z0-9.]*[a-z0-9]", text):
        if token in STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _countries(title: str) -> set:
    return {COUNTRY_NAMES[match.lower()] for match in COUNTRY_PATTERN.findall(title)}


def same_event(first_title: str, second_title: str) -> bool:
    """Match similar headlines, unless they name different countries."""
    first, second = _title_tokens(first_title), _title_tokens(second_title)
    if not first or not second:
        return False

    first_countries, second_countries = _countries(first_title), _countries(second_title)
    if first_countries and second_countries and not first_countries & second_countries:
        return False

    overlap = len(first & second) / len(first | second)
    shared_amount = any(re.fullmatch(r"\d+(\.\d+)?[bm]", token) for token in first & second)
    return overlap >= SAME_EVENT_THRESHOLD or (shared_amount and overlap >= SAME_AMOUNT_THRESHOLD)


def drop_same_event(
    articles: Iterable[Dict[str, Any]], recent_titles: Iterable[str] = ()
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Keep one article per event, preferring the most trusted source."""
    recent = list(recent_titles)
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    ordered = sorted(articles, key=lambda item: (int(item.get("trust", 3)), -rank_key(item)[0]))
    for article in ordered:
        title = article.get("title", "")
        duplicate = any(same_event(title, other) for other in recent) or any(
            same_event(title, other.get("title", "")) for other in kept
        )
        (dropped if duplicate else kept).append(article)
    return kept, dropped
