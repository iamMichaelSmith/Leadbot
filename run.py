import os
import re
import time
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode

import boto3
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
LEADS_TABLE = os.getenv("LEADS_TABLE", "MusicLibraryLeads")
PAGES_TABLE = os.getenv("PAGES_TABLE", "MusicLibraryPages")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL")

USER_AGENT = os.getenv("USER_AGENT", "MusicLibraryLeadFinder/1.0")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SLEEP_BETWEEN_REQUESTS = float(os.getenv("SLEEP_BETWEEN_REQUESTS", "2.0"))
MAX_PAGES_PER_RUN = int(os.getenv("MAX_PAGES_PER_RUN", "60"))
MAX_LEADS_PER_RUN = int(os.getenv("MAX_LEADS_PER_RUN", "0"))
MAX_PAGES_PER_DOMAIN = int(os.getenv("MAX_PAGES_PER_DOMAIN", "10"))
MAX_LINKS_PER_PAGE = int(os.getenv("MAX_LINKS_PER_PAGE", "40"))
ALLOW_EXTERNAL_DOMAINS = os.getenv("ALLOW_EXTERNAL_DOMAINS", "false").lower() in ("1", "true", "yes")
EXPORT_LEADS_FILE = os.getenv("EXPORT_LEADS_FILE", "").strip()
REQUIRE_SAME_DOMAIN_FORM = os.getenv("REQUIRE_SAME_DOMAIN_FORM", "1").strip() == "1"
MIN_ROLE_CONFIDENCE = int(os.getenv("MIN_ROLE_CONFIDENCE", "0"))
LIBRARIES_ONLY = os.getenv("LIBRARIES_ONLY", "0").strip() == "1"
MIN_LIBRARY_CONFIDENCE = int(os.getenv("MIN_LIBRARY_CONFIDENCE", "60"))

VISITED_CACHE_ENABLED = os.getenv("VISITED_CACHE_ENABLED", "1").strip() == "1"
VISITED_CACHE_TABLE = os.getenv("VISITED_CACHE_TABLE", PAGES_TABLE)
VISITED_CACHE_TTL_HOURS = float(os.getenv("VISITED_CACHE_TTL_HOURS", "0"))

DISCOVERY_ENABLED = os.getenv("DISCOVERY_ENABLED", "0").strip() == "1"
DISCOVERY_PROVIDER = os.getenv("DISCOVERY_PROVIDER", "brave").strip().lower()
DISCOVERY_PROVIDERS = os.getenv("DISCOVERY_PROVIDERS", "").strip().lower()
DISCOVERY_QUERIES_FILE = os.getenv("DISCOVERY_QUERIES_FILE", "queries.txt")
DISCOVERY_MAX_URLS = int(os.getenv("DISCOVERY_MAX_URLS", "50"))
DISCOVERY_PER_QUERY = int(os.getenv("DISCOVERY_PER_QUERY", "10"))
DISCOVERY_BATCH_SIZE = int(os.getenv("DISCOVERY_BATCH_SIZE", "50"))
DISCOVERY_STATE_FILE = os.getenv("DISCOVERY_STATE_FILE", "discovery_state.json")
DISCOVERY_DAILY_QUERY_LIMIT_BRAVE = int(os.getenv("DISCOVERY_DAILY_QUERY_LIMIT_BRAVE", "2000"))
DISCOVERY_DAILY_QUERY_LIMIT_SERPER = int(os.getenv("DISCOVERY_DAILY_QUERY_LIMIT_SERPER", "2500"))
DISCOVERY_DAILY_QUERY_LIMIT_OPENAI = int(os.getenv("DISCOVERY_DAILY_QUERY_LIMIT_OPENAI", "500"))

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_SEARCH_MODEL = os.getenv("OPENAI_SEARCH_MODEL", "gpt-4o-mini").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
DISCO_PORTFOLIO_LINK = os.getenv("DISCO_PORTFOLIO_LINK", "").strip()

QUEUE_ENABLED = os.getenv("QUEUE_ENABLED", "0").strip() == "1"
QUEUE_MODE = os.getenv("QUEUE_MODE", "local").strip().lower()
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "").strip()
SQS_MAX_MESSAGES = int(os.getenv("SQS_MAX_MESSAGES", "5"))
SQS_WAIT_SECONDS = int(os.getenv("SQS_WAIT_SECONDS", "10"))
SQS_VISIBILITY_TIMEOUT = int(os.getenv("SQS_VISIBILITY_TIMEOUT", "30"))
SQS_MESSAGE_GROUP_ID = os.getenv("SQS_MESSAGE_GROUP_ID", "leadbot").strip()
SKIP_CONTACTED_DOMAINS = os.getenv("SKIP_CONTACTED_DOMAINS", "1").strip() == "1"
DEDUPE_BY_DOMAIN = os.getenv("DEDUPE_BY_DOMAIN", "0").strip() == "1"
DEDUPE_FOR_FORMS = os.getenv("DEDUPE_FOR_FORMS", "1").strip() == "1"

dynamodb = boto3.resource(
    "dynamodb",
    region_name=AWS_REGION,
    endpoint_url=DYNAMODB_ENDPOINT_URL or None,
)
leads_table = dynamodb.Table(LEADS_TABLE)
pages_table = dynamodb.Table(PAGES_TABLE)
visited_table = dynamodb.Table(VISITED_CACHE_TABLE)

def queue_enabled() -> bool:
    return QUEUE_ENABLED and bool(SQS_QUEUE_URL)

class SqsQueue:
    def __init__(self, queue_url: str):
        self.queue_url = queue_url
        self.client = boto3.client("sqs", region_name=AWS_REGION)
        self.is_fifo = queue_url.endswith(".fifo")

    def send(self, url: str, seed_url: str):
        body = json.dumps({"url": url, "seed_url": seed_url})
        params = {"QueueUrl": self.queue_url, "MessageBody": body}
        if self.is_fifo:
            params["MessageGroupId"] = SQS_MESSAGE_GROUP_ID or "leadbot"
            params["MessageDeduplicationId"] = sha_id(body)
        self.client.send_message(**params)

    def receive(self) -> list[dict]:
        resp = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=SQS_MAX_MESSAGES,
            WaitTimeSeconds=SQS_WAIT_SECONDS,
            VisibilityTimeout=SQS_VISIBILITY_TIMEOUT,
        )
        return resp.get("Messages", [])

    def delete(self, receipt_handle: str):
        self.client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

DOMAIN_LAST_REQUEST = {}
DOMAIN_PAGES = {}

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def normalize_netloc(netloc: str) -> str:
    netloc = (netloc or "").lower()
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc

def now_iso() -> str:
    return utc_now().isoformat()

def sha_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

# Email filtering
BLOCKED_EMAIL_DOMAINS = {
    "sentry.io",
    "ingest.sentry.io",
    "cloudflare.com",
    "wix.com",
    "wixpress.com",
    "sentry.wixpress.com",
    "sentry-next.wixpress.com",
    "squarespace.com",
    "wordpress.com",
    "privacy-protection.com",
    "domainsbyproxy.com",
    "whoisprivacyservice.com",
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "reddit.com",
    "error-tracking.reddit.com",
}

BLOCKED_DISCOVERY_DOMAINS = {
    "linkedin.com",
    "wikipedia.org",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "reddit.com",
    "pitchfork.com",
    "newyorker.com",
    "rollingstone.com",
    "billboard.com",
    "variety.com",
    "hollywoodreporter.com",
    "complex.com",
    "tmz.com",
    "genius.com",
    "thefader.com",
}

BLOCKED_LIBRARY_DOMAINS = {
    "unitedmasters.com",
    "stage32.com",
    "rostr.cc",
    "reverbnation.com",
    "hotnewhiphop.com",
    "hypem.com",
    "musicconnection.com",
    "youtube.com",
    "millennialmind.co",
    "omarimc.com",
}

PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
}

PLACEHOLDER_EMAIL_LOCALPARTS = {
    "user",
    "email",
    "emails",
    "filler",
    "test",
    "testing",
}

BLOCKED_EMAIL_DOMAIN_SUFFIXES = (
    ".before",
    ".having",
    ".facilitates",
    ".once",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".pdf",
    ".zip",
)

BLOG_PATH_HINTS = (
    "/blog",
    "/news",
    "/press",
    "/article",
    "/interview",
    "/review",
    "/podcast",
    "/magazine",
)

LIBRARY_KEYWORDS = [
    "music library",
    "library music",
    "production music",
    "production library",
    "sync licensing",
    "music licensing",
    "royalty-free",
    "royalty free",
    "catalog",
    "music catalog",
    "library catalog",
    "license music",
]

BLOCKED_EMAIL_SUBSTRINGS = [
    ".ingest.",
    "noreply@",
    "no-reply@",
    "donotreply@",
    "do-not-reply@",
]

def is_candidate_email(email: str) -> bool:
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    if any(ext in e for ext in (".wav", ".aif", ".mp3")):
        return False
    for s in BLOCKED_EMAIL_SUBSTRINGS:
        if s in e:
            return False
    domain = e.split("@", 1)[1]
    local = e.split("@", 1)[0]
    if host_in_set(domain, BLOCKED_EMAIL_DOMAINS):
        return False
    if local in PLACEHOLDER_EMAIL_LOCALPARTS:
        return False
    if domain in PLACEHOLDER_EMAIL_DOMAINS:
        return False
    if any(domain.endswith(sfx) for sfx in BLOCKED_EMAIL_DOMAIN_SUFFIXES):
        return False
    if domain.endswith(".sentry.io"):
        return False
    return True

def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if e.startswith("mailto:"):
        e = e[len("mailto:"):]
    return e

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

OBFUSCATED_EMAIL_PATTERNS = [
    re.compile(
        r"([A-Z0-9._%+-]+)\s*(?:@|\(at\)|\[at\]|\sat\s)\s*([A-Z0-9.-]+)\s*(?:\.|\(dot\)|\[dot\]|\sdot\s)\s*([A-Z]{2,})",
        re.I,
    ),
]

ROLE_KEYWORDS = {
    "music_supervisor": ["music supervisor", "music supervision", "supervisor of music"],
    "publisher": ["publisher", "publishing", "licensing", "sync"],
    "producer": ["producer", "music producer", "beat maker", "composer"],
    "songwriter": ["songwriter"],
    "artist": ["artist", "rapper", "singer", "vocalist"],
}

PRIORITY_HINTS = [
    "contact", "about", "team", "staff", "directory", "people", "roster",
    "management", "agency", "bio", "speaker", "panel", "press", "epk",
    "booking", "licensing", "sync", "music-supervision", "submit", "submissions"
]

CONTACT_HINTS = (
    "contact",
    "about",
    "team",
    "staff",
    "directory",
    "people",
    "roster",
    "management",
    "agency",
    "licensing",
    "sync",
    "submit",
    "inquiry",
    "booking",
    "press",
    "epk",
)

COMMON_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/about",
    "/team",
    "/roster",
    "/artists",
    "/submit",
    "/submissions",
    "/licensing",
    "/sync",
    "/booking",
    "/management",
)

def safe_put_pages(item: dict):
    try:
        pages_table.put_item(Item=item)
    except Exception as e:
        print(f"DynamoDB pages_table write failed: {e}")

def safe_upsert_lead(item: dict):
    """
    Uses update_item so first_seen does not get overwritten.
    lead_id must exist.
    """
    item = {k: v for k, v in item.items() if v is not None}
    lead_id = item["lead_id"]
    expr_names = {}
    expr_values = {":now": now_iso()}

    update_parts = []
    update_parts.append("last_seen = :now")
    update_parts.append("first_seen = if_not_exists(first_seen, :now)")

    for k, v in item.items():
        if k in ("lead_id", "first_seen", "last_seen"):
            continue
        name_key = f"#{k}"
        val_key = f":{k}"
        expr_names[name_key] = k
        expr_values[val_key] = v
        update_parts.append(f"{name_key} = {val_key}")

    try:
        leads_table.update_item(
            Key={"lead_id": lead_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=expr_names if expr_names else None,
            ExpressionAttributeValues=expr_values,
        )
    except Exception as e:
        print(f"DynamoDB leads_table upsert failed: {e}")

def append_lead_export(item: dict):
    if not EXPORT_LEADS_FILE:
        return
    try:
        import json
        with open(EXPORT_LEADS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Lead export failed: {e}")

def is_lead_skipped(lead_id: str, lead_domain: str | None = None) -> bool:
    try:
        resp = leads_table.get_item(
            Key={"lead_id": lead_id},
            ProjectionExpression="lead_id,#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = resp.get("Item")
        if item and item.get("status") in ("skipped", "contacted"):
            return True
    except Exception:
        return False
    if SKIP_CONTACTED_DOMAINS and lead_domain:
        try:
            domain_id = sha_id(f"domain:{lead_domain}")
            resp = leads_table.get_item(
                Key={"lead_id": domain_id},
                ProjectionExpression="lead_id,#s",
                ExpressionAttributeNames={"#s": "status"},
            )
            item = resp.get("Item")
            return bool(item and item.get("status") == "contacted")
        except Exception:
            return False
    return False

def normalize_url(u: str) -> str:
    try:
        p = urlparse(u)
        query = strip_tracking_params(p.query)
        u2 = p._replace(fragment="", query=query).geturl()
        return u2.strip()
    except Exception:
        return (u or "").strip()

def strip_tracking_params(query: str) -> str:
    if not query:
        return ""
    tracking = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "gclid", "fbclid", "igshid", "ref", "ref_src", "mc_cid", "mc_eid",
        "mkt_tok", "_hsenc", "_hsmi", "ga_source", "ga_medium", "ga_campaign",
    }
    filtered = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in tracking]
    return urlencode(filtered, doseq=True)

def parse_iso(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    try:
        s = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None

def should_skip_cached(url: str) -> bool:
    if not VISITED_CACHE_ENABLED:
        return False
    try:
        resp = visited_table.get_item(
            Key={"page_url": url},
            ProjectionExpression="page_url,last_crawled",
        )
        item = resp.get("Item")
        if not item:
            return False
        if VISITED_CACHE_TTL_HOURS <= 0:
            return True
        last = parse_iso(item.get("last_crawled", ""))
        if not last:
            return False
        age = (utc_now() - last).total_seconds() / 3600.0
        return age < VISITED_CACHE_TTL_HOURS
    except Exception:
        return False

def is_http_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https")
    except Exception:
        return False

def score_link(u: str) -> int:
    u_low = u.lower()
    score = 0
    for hint in PRIORITY_HINTS:
        if hint in u_low:
            score += 10
    if u_low.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp3", ".wav", ".mov", ".mp4")):
        score -= 50
    return score

def detect_role(title: str, headings: str, body: str, url: str) -> tuple[str | None, int]:
    t = (title or "").lower()
    h = (headings or "").lower()
    b = (body or "").lower()
    u = (url or "").lower()

    def score_for_keywords(kws: list[str]) -> int:
        score = 0
        if any(k in t for k in kws):
            score += 40
        if any(k in h for k in kws):
            score += 25
        if any(k in u for k in kws):
            score += 15
        body_hits = sum(1 for k in kws if k in b)
        score += min(body_hits, 3) * 8
        if any(p in u for p in ("/team", "/staff", "/about", "/contact", "/roster", "/management")):
            score += 5
        if any(p in u for p in ("/blog", "/news", "/press", "/article")):
            score -= 10
        if score < 0:
            score = 0
        if score > 100:
            score = 100
        return score

    best = None
    best_score = 0
    for role, kws in ROLE_KEYWORDS.items():
        score = score_for_keywords(kws)
        if score > best_score:
            best_score = score
            best = role
    if best_score == 0:
        return None, 0
    return best, best_score

def fetch(url: str) -> str | None:
    url = normalize_url(url)
    try:
        if should_skip_cached(url):
            return None
        netloc = normalize_netloc(urlparse(url).netloc)
        if MAX_PAGES_PER_DOMAIN > 0 and netloc:
            count = DOMAIN_PAGES.get(netloc, 0)
            if count >= MAX_PAGES_PER_DOMAIN:
                return None
            DOMAIN_PAGES[netloc] = count + 1

        last = DOMAIN_LAST_REQUEST.get(netloc, 0.0)
        elapsed = time.time() - last
        if elapsed < SLEEP_BETWEEN_REQUESTS:
            time.sleep(SLEEP_BETWEEN_REQUESTS - elapsed)
        DOMAIN_LAST_REQUEST[netloc] = time.time()

        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        safe_put_pages({
            "page_url": url,
            "last_crawled": now_iso(),
            "status_code": int(r.status_code),
        })
        if r.status_code != 200:
            return None
        return r.text
    except Exception as e:
        safe_put_pages({
            "page_url": url,
            "last_crawled": now_iso(),
            "status_code": -1,
            "error": str(e)[:300],
        })
        print(f"Fetch failed: {url} -> {e}")
        return None

def extract_links(base_url: str, soup: BeautifulSoup, seed_netloc: str) -> list[str]:
    links = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        u = normalize_url(urljoin(base_url, href))
        if not is_http_url(u):
            continue
        if LIBRARIES_ONLY and is_blog_url(u):
            continue

        try:
            netloc = normalize_netloc(urlparse(u).netloc)
        except Exception:
            netloc = ""

        if not ALLOW_EXTERNAL_DOMAINS and seed_netloc and netloc and netloc != seed_netloc:
            continue

        if score_link(u) < -10:
            continue

        links.append(u)

    links = list(dict.fromkeys(links))
    links.sort(key=score_link, reverse=True)
    return links[:MAX_LINKS_PER_PAGE]

def crawl_one(
    url: str,
    seed_url: str,
    visited: set[str],
    leads_seen: set[str],
    enqueue_fn,
) -> tuple[int, int]:
    url = normalize_url(url)
    if not url:
        return 0, 0
    if url in visited:
        return 0, 0
    visited.add(url)

    seed_netloc = normalize_netloc(urlparse(seed_url).netloc)

    html = fetch(url)
    if not html:
        return 0, 0

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")
    headings = " ".join(
        h.get_text(" ", strip=True) for h in soup.select("h1, h2, h3")
    )[:1000]
    page_text = soup.get_text(" ", strip=True)[:5000]
    role, role_conf = detect_role(title, headings, page_text, url)
    lib_conf = library_confidence(title, headings, page_text, url)

    contact_type, email, contact_url = detect_contact(url, html)
    company_name = derive_company_name(title, url)

    leads_saved = 0
    if contact_type in ("email", "form"):
        if email:
            lead_key = normalize_email(email)
        elif contact_url:
            lead_key = normalize_url(contact_url)
        else:
            lead_key = normalize_url(url)
        if email:
            lead_domain = normalize_netloc(email.split("@", 1)[1])
        else:
            lead_domain = normalize_netloc(urlparse(contact_url or url).netloc)
        if (DEDUPE_FOR_FORMS and contact_type == "form" and lead_domain) or (DEDUPE_BY_DOMAIN and lead_domain):
            lead_id = sha_id(f"lead_domain:{lead_domain}")
        else:
            lead_id = sha_id(lead_key)
        if not is_lead_skipped(lead_id, lead_domain) and lead_id not in leads_seen:
            allowed = True
            if email:
                lead_host = lead_domain
            else:
                lead_host = lead_domain
            if is_blocked_domain(lead_host):
                allowed = False
            if contact_type == "form" and REQUIRE_SAME_DOMAIN_FORM:
                contact_host = normalize_netloc(urlparse(contact_url or url).netloc)
                source_host = normalize_netloc(urlparse(url).netloc)
                if contact_host and source_host and contact_host != source_host:
                    allowed = False
            if MIN_ROLE_CONFIDENCE > 0 and int(role_conf or 0) < MIN_ROLE_CONFIDENCE:
                allowed = False
            if LIBRARIES_ONLY and lib_conf < MIN_LIBRARY_CONFIDENCE:
                allowed = False
            if allowed:
                item = {
                    "lead_id": lead_id,
                    "email": email,
                    "contact_type": contact_type,
                    "contact_url": contact_url,
                    "lead_domain": lead_domain,
                    "company_name": company_name,
                    "role": role or "unknown",
                    "role_confidence": int(role_conf or 0),
                    "library_confidence": int(lib_conf or 0),
                    "source_url": url,
                    "status": "new",
                    "draft_message": build_draft(role),
                }
                safe_upsert_lead(item)
                append_lead_export(item)
                leads_seen.add(lead_id)
                leads_saved = 1
                if email:
                    print(f"Lead saved: {role} email {email}")
                else:
                    print(f"Lead saved: {role} form {contact_url}")

    links = extract_links(url, soup, seed_netloc)
    for nxt in links:
        if nxt not in visited:
            enqueue_fn(nxt, seed_url)

    return leads_saved, 1

def find_mailtos(soup: BeautifulSoup) -> list[str]:
    emails = set()
    for a in soup.select("a[href^='mailto:']"):
        href = a.get("href", "")
        addr = href.replace("mailto:", "").split("?")[0].strip().lower()
        if addr and is_candidate_email(addr):
            emails.add(addr)
    return sorted(emails)

def extract_emails_from_soup(soup: BeautifulSoup) -> list[str]:
    emails = set()

    for e in find_mailtos(soup):
        emails.add(e)

    for e in EMAIL_RE.findall(str(soup)):
        e = (e or "").strip().lower()
        if is_candidate_email(e):
            emails.add(e)

    text = soup.get_text(" ", strip=True)
    for pat in OBFUSCATED_EMAIL_PATTERNS:
        for m in pat.findall(text):
            if len(m) == 3:
                e = f"{m[0]}@{m[1]}.{m[2]}".strip().lower()
                if is_candidate_email(e):
                    emails.add(e)

    return sorted(emails)

def pick_contact_link(base_url: str, soup: BeautifulSoup) -> str | None:
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        path = urlparse(url).path.lower()
        if any(h in path for h in CONTACT_HINTS):
            return normalize_url(url)
    return None

def detect_contact(page_url: str, html: str) -> tuple[str | None, str | None, str | None]:
    """
    Returns: (contact_type, email, contact_url)
    contact_type: "email" | "form" | None
    """
    soup = BeautifulSoup(html, "html.parser")

    emails = extract_emails_from_soup(soup)
    if emails:
        return "email", emails[0], page_url

    contact_url = pick_contact_link(page_url, soup)
    if contact_url:
        html2 = fetch(contact_url)
        if html2:
            soup2 = BeautifulSoup(html2, "html.parser")
            emails2 = extract_emails_from_soup(soup2)
            if emails2:
                return "email", emails2[0], contact_url
        return "form", None, contact_url

    base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    for path in COMMON_CONTACT_PATHS:
        guess = normalize_url(base + path)
        html3 = fetch(guess)
        if not html3:
            continue
        soup3 = BeautifulSoup(html3, "html.parser")
        emails3 = extract_emails_from_soup(soup3)
        if emails3:
            return "email", emails3[0], guess
        return "form", None, guess

    return None, None, None

def build_draft(role: str | None) -> str:
    if role in ("music_supervisor", "publisher"):
        disco_line = "Our DISCO portfolio link here: [DISCO PORTFOLIO LINK]\n\n"
        if DISCO_PORTFOLIO_LINK:
            disco_line = f"Our DISCO portfolio link here: {DISCO_PORTFOLIO_LINK}\n\n"
        return (
            "Hi\n\n"
            "I came across your profile online and wanted to reach out from Blak Marigold Studio.\n\n"
            "I know you are busy so I will keep it quick. We support sync teams with clean deliverables, fast turnarounds, and "
            "alternate mixes when you need options for picture. We also handle mixing, mastering, and production in house when a track "
            "needs finishing.\n\n"
            + disco_line +
            "If you can share what styles or briefs you are covering lately, I can send a short list of tracks that fit and the exact "
            "deliverables available.\n\n"
            "Best\n"
            "Blak Marigold Studio\n"
            "BlakMarigold.com\n"
        )

    return (
        "Hi\n\n"
        "I came across your music profile online and wanted to reach out from Blak Marigold Studio in Austin.\n\n"
        "I know you are busy so I will keep it quick. We help artists get release ready records with mixing, mastering, and full "
        "production when needed. We have 20 plus years of experience and over 1.4 billion streams across platforms, so we take "
        "quality and turnaround seriously.\n\n"
        "If you are working on a new release, reply with a link to your best track and what you want improved. I can tell you what "
        "I would change and what it would take to get it where you want.\n\n"
        "Best\n"
        "Blak Marigold Studio\n"
        "BlakMarigold.com\n"
    )

def load_seeds(path: str = "seeds.txt") -> list[str]:
    if not os.path.exists(path):
        print("seeds.txt not found. Create it with one URL per line.")
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out

def load_queries(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            out = []
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    continue
                out.append(s)
            return out
    except FileNotFoundError:
        return []

def load_discovery_state() -> dict:
    try:
        import json
        with open(DISCOVERY_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def save_discovery_state(state: dict):
    try:
        import json
        with open(DISCOVERY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

def discovery_providers() -> list[str]:
    if DISCOVERY_PROVIDERS:
        return [p.strip() for p in DISCOVERY_PROVIDERS.split(",") if p.strip()]
    return [DISCOVERY_PROVIDER or "brave"]

def provider_quota_available(provider: str, used: dict) -> bool:
    if provider == "brave":
        return used.get("brave", 0) < DISCOVERY_DAILY_QUERY_LIMIT_BRAVE
    if provider == "serper":
        return used.get("serper", 0) < DISCOVERY_DAILY_QUERY_LIMIT_SERPER
    if provider == "openai":
        return used.get("openai", 0) < DISCOVERY_DAILY_QUERY_LIMIT_OPENAI
    return False

def bump_provider_usage(provider: str, used: dict):
    used[provider] = used.get(provider, 0) + 1

def domain_ok(url: str) -> bool:
    try:
        host = normalize_netloc(urlparse(url).netloc)
        if host.endswith(".edu"):
            return False
        if host_in_set(host, BLOCKED_DISCOVERY_DOMAINS):
            return False
        if host_in_set(host, BLOCKED_EMAIL_DOMAINS):
            return False
        if LIBRARIES_ONLY and host_in_set(host, BLOCKED_LIBRARY_DOMAINS):
            return False
        return True
    except Exception:
        return True

def is_blocked_domain(host: str) -> bool:
    h = normalize_netloc(host)
    if not h:
        return False
    if h.endswith(".edu"):
        return True
    if host_in_set(h, BLOCKED_DISCOVERY_DOMAINS):
        return True
    if host_in_set(h, BLOCKED_EMAIL_DOMAINS):
        return True
    if LIBRARIES_ONLY and host_in_set(h, BLOCKED_LIBRARY_DOMAINS):
        return True
    return False

def host_in_set(host: str, domain_set: set[str]) -> bool:
    h = normalize_netloc(host)
    if not h:
        return False
    for d in domain_set:
        if h == d or h.endswith("." + d):
            return True
    return False

def is_blog_url(url: str) -> bool:
    try:
        path = urlparse(url).path.lower()
        return any(h in path for h in BLOG_PATH_HINTS)
    except Exception:
        return False

def library_confidence(title: str, headings: str, body: str, url: str) -> int:
    t = (title or "").lower()
    h = (headings or "").lower()
    b = (body or "").lower()
    u = (url or "").lower()
    score = 0
    for kw in LIBRARY_KEYWORDS:
        if kw in t:
            score += 25
        if kw in h:
            score += 20
        if kw in u:
            score += 15
        if kw in b:
            score += 8
    if any(p in u for p in ("/catalog", "/library", "/licensing", "/production-music")):
        score += 10
    if any(p in u for p in BLOG_PATH_HINTS):
        score -= 30
    if not any(k in t for k in LIBRARY_KEYWORDS) and not any(k in h for k in LIBRARY_KEYWORDS):
        score = min(score, 40)
    if score < 0:
        score = 0
    if score > 100:
        score = 100
    return score

def derive_company_name(title: str, url: str) -> str | None:
    try:
        host = normalize_netloc(urlparse(url).netloc)
        return host or None
    except Exception:
        return None

def extract_openai_output_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("output_text"), str):
        return data.get("output_text", "")
    parts = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts)

def extract_openai_urls(data: dict) -> list[str]:
    urls = []
    if not isinstance(data, dict):
        return urls
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for ann in content.get("annotations", []):
                url = ann.get("url")
                if url:
                    urls.append(url)
    text = extract_openai_output_text(data)
    if text:
        urls.extend(re.findall(r"https?://[^\s\)>\]\"']+", text))
    cleaned = []
    seen = set()
    for u in urls:
        nu = normalize_url(u)
        if not nu or not is_http_url(nu):
            continue
        if nu in seen:
            continue
        seen.add(nu)
        cleaned.append(nu)
    return cleaned

def openai_search_urls(query: str, count: int) -> list[str]:
    if not OPENAI_API_KEY:
        return []
    payload = {
        "model": OPENAI_SEARCH_MODEL,
        "tools": [{"type": "web_search"}],
        "input": (
            "Find official websites for music libraries or production music companies. "
            f"Query: {query}\n"
            f"Return up to {count} URLs."
        ),
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/responses",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        urls = extract_openai_urls(data)
        return urls[:count]
    except Exception:
        return []

def brave_search_urls(query: str, count: int) -> list[str]:
    if not BRAVE_API_KEY:
        return []
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "count": str(min(count, 20))}
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
        "User-Agent": USER_AGENT,
    }
    r = requests.get(endpoint, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    results = (((data or {}).get("web") or {}).get("results") or [])
    urls = []
    for item in results:
        u = (item or {}).get("url")
        if u:
            urls.append(u)
    return urls

def serper_search_urls(query: str, count: int) -> list[str]:
    if not SERPER_API_KEY:
        return []
    endpoint = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": min(count, 20)}
    r = requests.post(endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    organic = (data or {}).get("organic") or []
    urls = []
    for item in organic:
        u = (item or {}).get("link")
        if u:
            urls.append(u)
    return urls

def discover_seed_urls() -> list[str]:
    if not DISCOVERY_ENABLED:
        return []
    queries = load_queries(DISCOVERY_QUERIES_FILE)
    if not queries:
        return []

    state = load_discovery_state()
    today = utc_now().date().isoformat()
    if state.get("date") != today:
        state = {"date": today, "query_index": state.get("query_index", 0), "used": {}}
    used = state.get("used", {})

    providers = discovery_providers()
    if not providers:
        return []

    start = state.get("query_index", 0) % len(queries)
    batch = min(DISCOVERY_BATCH_SIZE, len(queries))

    found = []
    seen = set()

    for i in range(batch):
        if len(found) >= DISCOVERY_MAX_URLS:
            break
        q = queries[(start + i) % len(queries)]

        urls = []
        offset = (start + i) % len(providers)
        ordered = providers[offset:] + providers[:offset]
        for provider in ordered:
            if len(urls) >= DISCOVERY_PER_QUERY:
                break
            if provider == "brave" and not BRAVE_API_KEY:
                continue
            if provider == "serper" and not SERPER_API_KEY:
                continue
            if provider == "openai" and not OPENAI_API_KEY:
                continue
            if not provider_quota_available(provider, used):
                continue
            if provider == "brave":
                urls.extend(brave_search_urls(q, DISCOVERY_PER_QUERY - len(urls)))
            elif provider == "serper":
                urls.extend(serper_search_urls(q, DISCOVERY_PER_QUERY - len(urls)))
            elif provider == "openai":
                urls.extend(openai_search_urls(q, DISCOVERY_PER_QUERY - len(urls)))
            bump_provider_usage(provider, used)

        for u in urls:
            nu = normalize_url(u)
            if not nu:
                continue
            if nu in seen:
                continue
            if LIBRARIES_ONLY and is_blog_url(nu):
                continue
            if not domain_ok(nu):
                continue
            seen.add(nu)
            found.append(nu)
            if len(found) >= DISCOVERY_MAX_URLS:
                break

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    state["query_index"] = (start + batch) % len(queries)
    state["used"] = used
    save_discovery_state(state)

    print(f"Discovery added {len(found)} seed urls")
    return found

def main():
    seeds = load_seeds("seeds.txt")
    discovered = discover_seed_urls()
    if discovered:
        seed_set = {normalize_url(s) for s in seeds if s}
        for u in discovered:
            nu = normalize_url(u)
            if not nu:
                continue
            if nu not in seed_set:
                seeds.append(nu)
                seed_set.add(nu)
    if not seeds:
        return

    if queue_enabled() and QUEUE_MODE == "producer":
        sqs = SqsQueue(SQS_QUEUE_URL)
        sent = 0
        for s in seeds:
            u = normalize_url(s)
            if not u:
                continue
            sqs.send(u, s)
            sent += 1
        print(f"Queued {sent} seed urls to SQS.")
        return

    queue: list[tuple[str, str]] = []
    for s in seeds:
        queue.append((normalize_url(s), s))

    visited = set()
    pages_visited = 0
    leads_saved = 0
    leads_seen = set()

    max_pages_per_run = MAX_PAGES_PER_RUN if MAX_PAGES_PER_RUN > 0 else float("inf")
    if queue_enabled() and QUEUE_MODE == "worker":
        sqs = SqsQueue(SQS_QUEUE_URL)
        idle_rounds = 0
        while pages_visited < max_pages_per_run:
            msgs = sqs.receive()
            if not msgs:
                idle_rounds += 1
                if idle_rounds >= 3:
                    break
                continue
            idle_rounds = 0
            for msg in msgs:
                body = msg.get("Body") or ""
                receipt = msg.get("ReceiptHandle")
                try:
                    payload = json.loads(body)
                except Exception:
                    if receipt:
                        sqs.delete(receipt)
                    continue
                url = normalize_url(payload.get("url", ""))
                seed_url = payload.get("seed_url") or url
                if not url:
                    if receipt:
                        sqs.delete(receipt)
                    continue

                def enqueue_worker(nxt: str, seed: str):
                    if should_skip_cached(nxt):
                        return
                    sqs.send(nxt, seed)

                saved, visited_count = crawl_one(url, seed_url, visited, leads_seen, enqueue_worker)
                pages_visited += visited_count
                leads_saved += saved
                if receipt:
                    sqs.delete(receipt)
        print(f"Done. Visited {pages_visited} pages. Saved {leads_saved} leads.")
        return

    while queue and pages_visited < max_pages_per_run:
        if MAX_LEADS_PER_RUN > 0 and leads_saved >= MAX_LEADS_PER_RUN:
            break
        url, seed_url = queue.pop(0)
        def enqueue_local(nxt: str, seed: str):
            queue.append((nxt, seed))

        saved, visited_count = crawl_one(url, seed_url, visited, leads_seen, enqueue_local)
        pages_visited += visited_count
        leads_saved += saved

    print(f"Done. Visited {pages_visited} pages. Saved {leads_saved} leads.")

if __name__ == "__main__":
    main()

