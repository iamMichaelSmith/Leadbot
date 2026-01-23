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

        try:
            r = requests.head(
                url,
                allow_redirects=True,
                timeout=TIMEOUT,
                headers={"User-Agent": "StudioLeadbot/1.0"}
            )
            if r.status_code < 400:
                return True, f"ok_{r.status_code}"
            if r.status_code not in (403, 405):
                return False, f"bad_status_{r.status_code}"
        except requests.exceptions.RequestException:
            pass

        r = requests.get(
            url,
            allow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": "StudioLeadbot/1.0"},
            stream=True,
        )
        r.close()
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
