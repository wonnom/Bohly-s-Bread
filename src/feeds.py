"""Search queries and RSS feeds. Region "APAC" means the whole source is APAC-focused."""
from dataclasses import dataclass
from typing import List
from urllib.parse import urlencode

NEWS_QUERIES: List[str] = [
    '"project finance" OR "financial close" OR "syndicated loan" OR "green loan" OR refinancing',
    '"transaction banking" OR "infrastructure financing" OR LNG OR commodities',
]

FINLIGHT_COUNTRIES = ["SG", "MY", "ID", "TH", "VN", "PH", "IN", "CN", "HK", "JP", "KR", "TW", "AU", "NZ"]
FINLIGHT_CATEGORIES = ["economy", "business", "regulation", "energy", "commodities", "climate"]
FINLIGHT_EXCLUDED_SOURCES = ["finance.yahoo.com", "seekingalpha.com", "www.benzinga.com"]

APAC_TERMS = "(Asia OR Singapore OR India OR Indonesia OR Vietnam OR Philippines OR Malaysia OR Thailand OR Australia OR Japan OR Korea OR China)"
TIER_ONE_SITES = "(site:reuters.com OR site:bloomberg.com OR site:ft.com OR site:cnbc.com OR site:spglobal.com)"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: str
    region: str
    trust: int  # 1 = top-tier press, 2 = specialist trade press, 3 = general


def _google_news(query: str) -> str:
    params = urlencode({"q": f"{query} when:1d", "hl": "en-SG", "gl": "SG", "ceid": "SG:en"})
    return f"https://news.google.com/rss/search?{params}"


RSS_FEEDS: List[Feed] = [
    Feed("LNG Prime", "https://lngprime.com/feed/", "energy", "GLOBAL", 2),
    Feed("Power Technology", "https://www.power-technology.com/feed/", "energy", "GLOBAL", 2),
    Feed("PV Tech", "https://www.pv-tech.org/feed/", "energy", "GLOBAL", 2),
    Feed("Energy-Storage.news", "https://www.energy-storage.news/feed/", "energy", "GLOBAL", 2),
    Feed("Offshore Energy", "https://www.offshore-energy.biz/feed/", "energy", "GLOBAL", 2),
    Feed("OilPrice", "https://oilprice.com/rss/main", "commodities", "GLOBAL", 3),
    Feed("DatacenterDynamics", "https://www.datacenterdynamics.com/en/rss/", "infrastructure", "GLOBAL", 2),
    Feed("Seatrade Maritime", "https://www.seatrade-maritime.com/rss.xml", "infrastructure", "GLOBAL", 2),
    Feed("Trade Finance Global", "https://www.tradefinanceglobal.com/feed/", "banking", "GLOBAL", 2),
    Feed("pv magazine India", "https://www.pv-magazine-india.com/feed/", "energy", "APAC", 2),
    Feed("pv magazine Australia", "https://www.pv-magazine-australia.com/feed/", "energy", "APAC", 2),
    Feed("ET EnergyWorld", "https://energy.economictimes.indiatimes.com/rss/topstories", "energy", "APAC", 2),
    Feed("ET Infra", "https://infra.economictimes.indiatimes.com/rss/topstories", "infrastructure", "APAC", 2),
    Feed("ET Banking", "https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms", "banking", "APAC", 2),
    Feed("ADB", "https://feeds.feedburner.com/adb_news", "infrastructure", "APAC", 1),
    Feed("Business Times", "https://www.businesstimes.com.sg/rss/companies-markets", "banking", "APAC", 2),
    Feed("Google News: project finance", _google_news(f'("project finance" OR "financial close" OR "syndicated loan" OR "green loan" OR refinancing) {APAC_TERMS}'), "deals", "APAC", 3),
    Feed("Google News: infrastructure M&A", _google_news('(acquire OR acquisition OR stake OR divest) (infrastructure OR "data centre" OR "data center" OR renewable OR port OR airport OR utility) (Asia OR Singapore OR India OR Indonesia OR Australia)'), "deals", "APAC", 3),
    Feed("Google News: top-tier press", _google_news(f"{TIER_ONE_SITES} (financing OR loan OR LNG OR OPEC OR infrastructure OR refinancing)"), "deals", "GLOBAL", 3),
    Feed("Google News: energy markets", _google_news(f'{TIER_ONE_SITES} (OPEC+ OR LNG OR Brent OR "power prices")'), "commodities", "GLOBAL", 3),
    Feed("Google News: Southeast Asia", _google_news("(financing OR loan OR investment) (infrastructure OR power OR energy OR port) (Indonesia OR Vietnam OR Philippines OR Malaysia OR Thailand)"), "deals", "APAC", 3),
]
