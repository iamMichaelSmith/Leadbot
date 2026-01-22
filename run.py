cd ~/leadbot
cat > run.py <<'PY'
# paste starts here
import os
import re
import time
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import boto3
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
LEADS_TABLE = os.getenv("LEADS_TABLE", "LeadbotLeads")
PAGES_TABLE = os.getenv("PAGES_TABLE", "LeadbotPages")

USER_AGENT = os.getenv("USER_AGENT", "StudioLeadbot/1.0")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SLEEP_BETWEEN_REQUESTS = float(os.getenv("SLEEP_BETWEEN_REQUESTS", "2.0"))
MAX_PAGES_PER_RUN = int(os.getenv("MAX_PAGES_PER_RUN", "60"))
MAX_LINKS_PER_PAGE = int(os.getenv("MAX_LINKS_PER_PAGE", "40"))
ALLOW_EXTERNAL_DOMAINS = os.getenv("ALLOW_EXTERNAL_DOMAINS", "false").lower() in ("1", "true", "yes")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
leads_table = dynamodb.Table(LEADS_TABLE)
pages_table = dynamodb.Table(PAGES_TABLE)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

# Email filtering
BLOCKED_EMAIL_DOMAINS = {
    "sentry.io",
    "ingest.sentry.io",
    "cloudflare.com",
    "wix.com",
    "squarespace.com",
    "wordpress.com",
    "privacy-protection.com",
    "domainsbyproxy.com",
    "whoisprivacyservice.com",
    "example.com",
}

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
    for s in BLOCKED_EMAIL_SUBSTRINGS:
        if s in e:
            return False
    domain = e.split("@", 1)[1]
    if domain in BLOCKED_EMAIL_DOMAINS:
        return False
    if domain.endswith(".sentry.io"):
        return False
    return True

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

def normalize_url(u: str) -> str:
    try:
        p = urlparse(u)
        u2 = p._replace(fragment="").geturl()
        return u2.strip()
    except Exception:
        return (u or "").strip()

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

def detect_role(text: str) -> tuple[str | None, int]:
    t = (text or "").lower()
    best = None
    best_score = 0
    for role, kws in ROLE_KEYWORDS.items():
        score = sum(1 for k in kws if k in t)
        if score > best_score:
            best_score = score
            best = role
    confidence = min(100, best_score * 25)
    return best, confidence

def fetch(url: str) -> str | None:
    url = normalize_url(url)
    try:
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

        try:
            netloc = urlparse(u).netloc.lower()
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
        return (
            "Hi there\n\n"
            "I came across your work and wanted to reach out from Blak Marigold Studio.\n\n"
            "If you ever need clean, reliable mixing and mastering support or alternate mixes for deliverables, "
            "we can turn things around quickly.\n\n"
            "If helpful, reply with a reference and any delivery needs and I can follow up.\n\n"
            "Best\n"
            "Blak Marigold Studio\n"
            "BlakMarigold.com\n"
        )

    return (
        "Hi\n\n"
        "If you are releasing music soon and want it to sound finished on Spotify and Apple, we can help with mixing and mastering.\n\n"
        "Send a link to your latest track and what you want to improve.\n\n"
        "Best\n"
        "Blak Marigold Studio\n"
        "BlakMarigold.com\n\n"
        "Our assistant reviews messages weekdays 10 AM to 2 PM and 6 PM to 8 PM Central.\n"
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

def main():
    seeds = load_seeds("seeds.txt")
    if not seeds:
        return

    queue: list[tuple[str, str]] = []  # (url, seed_url)
    for s in seeds:
        queue.append((normalize_url(s), s))

    visited = set()
    pages_visited = 0

    while queue and pages_visited < MAX_PAGES_PER_RUN:
        url, seed_url = queue.pop(0)
        url = normalize_url(url)
        if url in visited:
            continue
        visited.add(url)

        seed_netloc = urlparse(seed_url).netloc.lower()

        html = fetch(url)
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        if not html:
            continue

        pages_visited += 1

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")
        page_text = soup.get_text(" ", strip=True)[:5000]
        role, role_conf = detect_role(f"{title} {page_text}")

        contact_type, email, contact_url = detect_contact(url, html)

        # Save lead if we found a real email or a usable contact page
        if contact_type in ("email", "form"):
            lead_key = email if email else (contact_url if contact_url else url)
            lead_id = sha_id(lead_key)

            item = {
                "lead_id": lead_id,
                "email": email,
                "contact_type": contact_type,
                "contact_url": contact_url,
                "role": role or "unknown",
                "role_confidence": int(role_conf or 0),
                "source_url": url,
                "status": "new",
                "draft_message": build_draft(role),
            }

            safe_upsert_lead(item)

            if email:
                print(f"Lead saved: {role} email {email}")
            else:
                print(f"Lead saved: {role} form {contact_url}")

        # Follow links
        links = extract_links(url, soup, seed_netloc)
        for nxt in links:
            if nxt not in visited:
                queue.append((nxt, seed_url))

    print(f"Done. Visited {pages_visited} pages.")

if __name__ == "__main__":
    main()
# paste ends here
PY

