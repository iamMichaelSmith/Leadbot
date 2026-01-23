# Music Library Lead Finder

## Summary
Music Library Lead Finder is a hybrid on-device + AWS lead discovery system for music libraries. It discovers licensing catalogs, extracts contact signals (emails or forms), scores library relevance, and stores structured leads in DynamoDB for review. It uses Brave/Serper discovery, enforces crawl limits, and keeps a human-in-the-loop workflow with no automated outreach.

## Quickstart (Local)
```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python run.py
```

## Results (Typical)
- 30-60 library-focused leads per run (depends on thresholds and query volume)
- Library confidence gating to reduce non-library noise
- JSONL export for local review

## Demo data
- `samples/sample_leads.jsonl`
- `samples/sample_run.log`

## Architecture (current)
<img width="2506" height="1111" alt="SMVP-main Diagram" src="https://github.com/user-attachments/assets/ae0e3f37-be7b-4d49-806a-0b63fda41a4f" />


## Testing (optional)
```bash
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pytest
```

---

## Hybrid Lead Discovery and Outreach System

### Project Type

Local automation system for lead discovery, qualification, and outreach review

Built for a professional recording studio environment

### Primary Goals

- Discover **music libraries / production music catalogs** and licensing contacts
- Use **public websites and directories only**
- Respect platform rules and avoid aggressive automation
- Centralize leads in a reviewable dashboard
- Allow human approval before any outreach
- Support multi engineer workflows in a studio environment

---

## Why This Project Exists

Blak Marigold Studio needed a lightweight, always on system that could:

- Run locally inside the studio
- Collect leads passively during work windows
- Prepare outreach without spamming
- Allow engineers to review leads between sessions
- Scale later without redesigning the system

The local node was chosen because it is:

- Always on
- Low cost
- Easy to isolate on a local network
- Ideal for automation and background services

---

## High-Level Architecture

### Local (Hybrid Edge Node)

- Python-based crawler and lead processor
- Draft message generator
- Local web dashboard for approvals
- Cron-based scheduling (added later)
- Search discovery via Brave/Serper (optional)

### Cloud (AWS)

- DynamoDB for lead storage and deduplication
- Optional future integrations (SES, S3)

### Human in the Loop

- No auto outreach at MVP stage
- All leads reviewed manually
- Contact forms never auto-submit

---

## Project Folder Structure

All project files live in the local edge node user's home directory.

```
/home/knolly
├── leadbot
│   ├── .venv
│   ├── run.py
│   ├── seeds.txt
│   └── .env
├── leadbot_dashboard
│   ├── .venv
│   ├── app.py
│   └── .env
└── leadbot_logs
    └── collector_manual.log

```

This structure keeps:

- Crawling logic isolated
- Dashboard logic isolated
- Logs centralized

---

## Step 1: Create Project Directories

From a fresh edge-node install, connect via SSH and confirm your user.

```bash
whoami
echo$HOME

```

Create the project directories.

```bash
mkdir -p ~/leadbot ~/leadbot_dashboard ~/leadbot_logs

```

```bash
ls -la ~ | egrep"leadbot|leadbot_dashboard|leadbot_logs"

```

Expected result

All three directories should be visible.

Image Placeholder: terminal showing the three project directories after creation.

---

## Step 2: Install System Dependencies

Update the system and install Python tooling.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

```

### Why These Dependencies Exist

- **python3**
    
    Core language used for automation and services
    
- **python3-venv**
    
    Allows isolated virtual environments per project
    
    Prevents dependency conflicts
    
- **python3-pip**
    
    Python package manager used to install libraries
    

### Test Checkpoint

```bash
python3 --version
pip3 --version

```

You should see version numbers for both.

---

## Step 3: Set Up Python Virtual Environment (Crawler)

Navigate to the crawler folder.

```bash
cd ~/leadbot

```

Create and activate a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate

```

Your terminal prompt should now show:

```
(.venv)

```

### Install Python Libraries

```bash
pip install -r requirements.txt

```

### Why Each Python Dependency Is Used

- **boto3**
    
    AWS SDK for Python
    
    Used to read and write leads to DynamoDB
    
- **requests**
    
    HTTP client for fetching public web pages
    
- **beautifulsoup4**
    
    HTML parser used to extract emails, links, and text
    
- **python-dotenv**
    
    Loads environment variables from `.env`
    
    Keeps secrets out of source code

---

### Test Checkpoint

Run this test script to confirm everything works.

```bash
python - <<'EOF'
import boto3, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
print("Python environment OK")
EOF

```

Expected output:

```
Python environment OK

Image Placeholder: dependency install + successful test output.
```

This confirms your environment is production-ready.



## Part 2: AWS Integration, IAM Least Privilege, DynamoDB, and First Crawl

This section continues the Studio MVP build **after Python dependencies (BeautifulSoup, requests, etc.) were installed and validated**. It documents AWS CLI installation, IAM setup with least privilege, DynamoDB provisioning, and the first confirmed crawler execution.

---

## Step 4: Install AWS CLI on the local node

The AWS CLI is required to:

- Authenticate the local node with AWS
- create and verify DynamoDB tables
- Inspect crawl results in real time
- support repeatable infrastructure setup

### Install AWS CLI

```bash
sudo apt update
sudo apt install -y awscli

```

### Verify installation

```bash
aws --version

```

Expected output:

```
aws-cli/2.x.x

```

---

## Step 5: Create an IAM user with least privilege

A **dedicated IAM user** is created specifically for the crawler.

This user does **not** have broad AWS permissions.

### IAM design goals

- DynamoDB only
- No wildcard admin access
- Scoped to required tables
- Safe for long-running unattended execution

### IAM user

- User name example: `MusicLibraryLeadFinderCrawler`
- Authentication: Access key + secret key
- Programmatic access only

### IAM policy (least privilege)

Attach a custom inline policy similar to:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:DescribeTable",
        "dynamodb:Scan",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:*:table/MusicLibraryLeads",
        "arn:aws:dynamodb:us-east-1:*:table/MusicLibraryPages"
      ]
    }
  ]
}

```

This ensures:

- The crawler can write and read leads
- The crawler cannot access unrelated AWS services
- Credentials can be rotated safely later

---

## Step 6: Configure AWS CLI on the local node

Configure AWS CLI using the IAM user credentials.

```bash
aws configure

```

Enter:

- Access Key ID
- Secret Access Key
- Default region: `us-east-1`
- Output format: `json`

### Verify authentication

```bash
aws sts get-caller-identity

```

Expected output includes:

- Account ID
- User ARN
- Confirms credentials are active

---

## Step 7: Verify DynamoDB access

Confirm the IAM user can interact with DynamoDB.

```bash
aws dynamodb list-tables --region us-east-1

```

At this stage, only tables from previous projects may appear. This confirms:

- IAM permissions are valid
- DynamoDB connectivity is working

---

## Step 8: Create DynamoDB tables for Music Library Lead Finder

Two tables are required:

- `MusicLibraryLeads` – stores extracted leads
- `MusicLibraryPages` – stores crawl history and errors

### Create MusicLibraryLeads table

```bash
aws dynamodb create-table \
  --region us-east-1 \
  --table-name MusicLibraryLeads \
  --attribute-definitions AttributeName=lead_id,AttributeType=S \
  --key-schema AttributeName=lead_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

```

### Create MusicLibraryPages table

```bash
aws dynamodb create-table \
  --region us-east-1 \
  --table-name MusicLibraryPages \
  --attribute-definitions AttributeName=page_url,AttributeType=S \
  --key-schema AttributeName=page_url,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

```

---

## Step 9: Wait for tables to become ACTIVE

Table creation is asynchronous.

```bash
aws dynamodb wait table-exists --region us-east-1 --table-name MusicLibraryLeads
aws dynamodb wait table-exists --region us-east-1 --table-name MusicLibraryPages

```



### Confirm table status

```bash
aws dynamodb describe-table --region us-east-1 --table-name MusicLibraryLeads --query "Table.TableStatus"
aws dynamodb describe-table --region us-east-1 --table-name MusicLibraryPages --query "Table.TableStatus"

```

Expected:

```
"ACTIVE"

```
Image Placeholder: DynamoDB tables show ACTIVE status.

---

## Step 10: Configure crawler environment variables

The crawler reads the configuration from a `.env` file.

### Create or edit `.env`

```bash
nano ~/leadbot/.env

```

```
AWS_REGION=us-east-1
LEADS_TABLE=MusicLibraryLeads
PAGES_TABLE=MusicLibraryPages

USER_AGENT=MusicLibraryLeadFinder/1.0 (contact: hello@blakmarigold.com)

MAX_PAGES_PER_RUN=60
REQUEST_TIMEOUT=20
SLEEP_BETWEEN_REQUESTS=2.0

```

Notes:

- The user agent identifies the crawler responsibly
- The email is informational only, not used for sending messages

---

## Step 11: Create a seed sources file

The crawler only accesses **explicitly allowed public websites**.
Seeds are always loaded; discovery adds extra URLs on top of this list.

### Create seeds file

```bash
nano ~/leadbot/seeds.txt

```

```
# MUSIC LIBRARY SOURCES
https://www.themusicase.com
https://www.audionetwork.com
https://www.dewolfemusic.com
https://www.dramedybox.com
https://www.blacktoastmusic.com
https://www.atommusicaudio.com
https://www.synctracks.com
https://www.bulletproofbear.com
https://www.moderngiantmusic.com
https://www.apmmusic.com
https://soundimage.org
https://www.audiosparx.com

```
Image Placeholder: library-only seeds list in terminal/editor.



Verify:

```bash
cat ~/leadbot/seeds.txt

```

---

## Step 12: First crawler execution

Run the crawler manually to validate full system operation.

```bash
cd ~/leadbot
source .venv/bin/activate
python run.py | tee ~/leadbot_logs/collector_manual.log

```
Image Placeholder: crawler output showing library-only leads.



This:

- starts crawling approved sources
- extracts potential leads
- writes results to DynamoDB
- logs activity to disk

---

## Step 13: Confirm the crawler is running

In a second terminal session:

```bash
ps aux | grep run.py

```



Expected:

- a live `python run.py` process
- confirms crawler is active

Image Placeholder: running process in terminal (ps aux).

---

## Step 14: Verify DynamoDB is receiving data

```bash
aws dynamodb scan \
  --table-name MusicLibraryLeads \
  --limit 5 \
  --region us-east-1

```

If items appear, the full ingestion pipeline is confirmed.

Image Placeholder: DynamoDB scan showing library_confidence fields.

---

## Step 15: Monitor crawl logs in real time

```bash
tail -f ~/leadbot_logs/collector_manual.log

```

Used to observe:

- page fetches
- timeouts
- parse success
- lead discovery

---

## Current checkpoint summary

At this point, Studio MVP has demonstrated:

- Least privilege IAM architecture
- Local node running a long-lived crawler
- DynamoDB write access verified
- Controlled crawl scope via seed allow list
- Real-time logging and inspection
- Live process validation using system tools

This checkpoint marks the transition into:

- library confidence scoring and quality gates
- approval workflows
- scheduling and time window controls
- dashboard and multi-engineer access

## Advancing Lead Quality and Credibility

After completing the first working version of the scraper, I reviewed the initial DynamoDB results and identified a key limitation. While the system successfully identified leads, **many were not actual music libraries**.

The first pass primarily returned:

- Directory and listicle pages
- Blog/press pages that are not licensing catalogs
- Pages without direct library contact details

Although technically correct, these leads required additional effort to convert into meaningful conversations.

### Design Decision

Rather than increasing crawl depth or scraping more aggressively, I chose to improve **lead intelligence**. The goal was to extract higher-quality contacts using only publicly available information while adhering to ethical scraping guidelines.

### What Changed

The crawler was upgraded to:

- Extract direct emails from mailto links and visible page content
- Detect and decode common email obfuscation patterns
- Automatically follow contact, booking, or licensing pages when emails are not present on the initial page
- Preserve contact form URLs as valid fallback leads

### Result

This iteration significantly improved lead quality by converting many “form only” results into **direct email leads**, while still capturing legitimate non-email contacts when necessary.

### Code Introduced

```python
defextract_emails_from_soup(soup: BeautifulSoup) ->list[str]:
    emails =set()

for ain soup.select('a[href^="mailto:"]'):
        email = a.get("href","").replace("mailto:","").split("?")[0]
if email:
            emails.add(email.lower())

    emails.update(e.lower()for ein EMAIL_RE.findall(str(soup)))

    text = soup.get_text(" ", strip=True)
for min OBFUSCATED_EMAIL_PATTERNS[0].findall(text):
        emails.add(f"{m[0]}@{m[1]}.{m[2]}".lower())

returnsorted(emails)

```

```python
defdetect_contact(soup: BeautifulSoup, page_url: str):
    emails = extract_emails_from_soup(soup)
if emails:
return"email", emails[0],None

for ain soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True).lower()
        href = urljoin(page_url, a["href"])
ifany(kin labelfor kin ["contact","booking","licensing"]):
return"form",None, href

returnNone,None,None

```


### Pause Point

After upgrading the crawler, I realized some websites were not working. The crawler was fine, but a few seed domains were either dead, blocking automated requests, or timing out. So I added a quick validation step to automatically remove bad seeds and keep only websites that actually respond.

## Step 16: Validate seed websites and remove the ones that fail

### Goal

Before crawling, test every URL in `seeds.txt` and generate two lists:

- `seeds_working.txt` (only the sites that respond)
- `seeds_failed.txt` (sites that fail plus the reason)

Then replace `seeds.txt` with the working list so the crawler only hits valid sources.

### Commands

SSH into the local node and activate the environment:

```bash
ssh knolly@192.168.1.85
cd ~/leadbot
source .venv/bin/activate

```

Optional quick sanity check that the node has internet and DNS is working:

```bash
ping -c 3 8.8.8.8
ping -c 3 blakmarigold.com

```

### Create the validator script

Open a new file:

```bash
nano validate_seeds.py

```

Paste this entire script:

```python
import socket
import requests
from urllib.parse import urlparse

IN_FILE = "seeds.txt"
OUT_OK = "seeds_working.txt"
OUT_BAD = "seeds_failed.txt"

TIMEOUT = 12

def can_resolve(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except Exception:
        return False

def check_url(url: str) -> tuple[bool, str]:
    try:
        host = urlparse(url).netloc
        if not host:
            return False, "bad_url"

        if not can_resolve(host):
            return False, "dns_fail"

        r = requests.head(
            url,
            allow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": "StudioMusic Library Lead Finder/1.0"}
        )

        if r.status_code < 400:
            return True, f"ok_{r.status_code}"
        return False, f"bad_status_{r.status_code}"

    except requests.exceptions.Timeout:
        return False, "timeout"
    except Exception as e:
        return False, f"error_{type(e).__name__}"

def main():
    ok = []
    bad = []

    with open(IN_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            good, reason = check_url(line)
            if good:
                ok.append(line)
                print(f"OK   {line}")
            else:
                bad.append((line, reason))
                print(f"FAIL {line}  ({reason})")

    with open(OUT_OK, "w", encoding="utf-8") as f:
        f.write("\n".join(ok) + ("\n" if ok else ""))

    with open(OUT_BAD, "w", encoding="utf-8") as f:
        for url, reason in bad:
            f.write(f"{url}\t{reason}\n")

    print("\nSaved:")
    print(f"  working -> {OUT_OK}")
    print(f"  failed  -> {OUT_BAD}")

if __name__ == "__main__":
    main()

```

Save and exit:

- Save: `CTRL + O`, then `Enter`
- Exit: `CTRL + X`

### Run the validator

```bash
python validate_seeds.py

```

Check the counts and view failures:

```bash
wc -l seeds.txt seeds_working.txt
head -n 30 seeds_failed.txt

```

### Replace seeds.txt with the working list

This is the key step that removes broken sites from future runs:

```bash
mv seeds.txt seeds_original.txt
mv seeds_working.txt seeds.txt

```

At this point, the crawler is guaranteed to only start from websites that respond.

---

## Step 17: Run the crawler again using the cleaned seed list

```bash
cd ~/leadbot
source .venv/bin/activate
python run.py | tee ~/leadbot_logs/collector_manual.log

```

You should now see fewer “Fetch failed” messages and more lines like:

- `Lead saved: publisher email ...`
- `Lead saved: library form ...`
- `Done. Visited X pages.`

---

## Part 3: Library-Only Discovery Upgrade (current)

After the initial broad discovery version, the project pivoted to music libraries only.
The crawler now targets production music libraries and sync licensing catalogs, and blocks
blogs, news, press, and unrelated platforms.

### What changed

- Library-only discovery mode with minimum library confidence
- Search discovery via Brave/Serper with rotation and daily quotas
- URL caching in DynamoDB to skip re-crawls
- Optional JSONL lead export for local review
- Library-focused seeds and queries

---

## Step 18: Update Python dependencies

Dependencies are now managed via requirements.txt:

```bash
cd ~/leadbot
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Step 19: Update .env for library-only mode

Edit .env:

```bash
nano ~/leadbot/.env
```

Example (library-only):

```
LIBRARIES_ONLY=1
MIN_LIBRARY_CONFIDENCE=60
MIN_ROLE_CONFIDENCE=0

DISCOVERY_ENABLED=1
DISCOVERY_PROVIDERS=brave,serper
DISCOVERY_QUERIES_FILE=queries.txt
DISCOVERY_MAX_URLS=80
DISCOVERY_PER_QUERY=10
DISCOVERY_BATCH_SIZE=50
DISCOVERY_STATE_FILE=discovery_state.json

VISITED_CACHE_ENABLED=1
VISITED_CACHE_TABLE=MusicLibraryPages
VISITED_CACHE_TTL_HOURS=0

EXPORT_LEADS_FILE=leads_export.jsonl
```

---

## Step 20: Replace seeds.txt with library sources

Create a library-only seed list:

```bash
nano ~/leadbot/seeds.txt
```

Example:

```
https://www.themusicase.com
https://www.audionetwork.com
https://www.dewolfemusic.com
https://www.dramedybox.com
https://www.blacktoastmusic.com
https://www.atommusicaudio.com
https://www.synctracks.com
https://www.bulletproofbear.com
https://www.moderngiantmusic.com
https://www.apmmusic.com
https://soundimage.org
https://www.audiosparx.com
```

---

## Step 21: Replace queries.txt with library queries

```bash
nano ~/leadbot/queries.txt
```

Example:

```
production music library licensing
music library catalog contact
royalty free music library sync licensing
production music catalog library
library music for film tv licensing
music library submissions contact
production music library roster
instrumental music library licensing
music library sync licensing company
production music library contact
```

---

## Step 22: Run the crawler (library-only)

```bash
cd ~/leadbot
source .venv/bin/activate
python run.py | tee ~/leadbot_logs/collector_manual.log
```

Check exports:

```bash
tail -n 20 ~/leadbot/leads_export.jsonl
```
