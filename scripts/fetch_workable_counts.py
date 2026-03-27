"""Fetch open-job counts from Workable using /count endpoints only.

Tries a small set of ``/count`` URL variants per slug and uses the first
usable response. If all variants fail, stores ``0`` for that slug so the whole
list is always completed in one run.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.workable_apply_slug import extract_workable_apply_slug

YAML_PATH = Path("data/companies.yaml")
OUTPUT_PATH = Path("data/workable_counts.yaml")

DELAY_BETWEEN_SLUGS_SEC = 1.25
TIMEOUT_SEC = (12, 30)
_RETRY_TOTAL = 5
_RETRY_BACKOFF_FACTOR = 1.5
_RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

_COUNT_URL_CANDIDATES = (
    "https://apply.workable.com/api/v1/accounts/{slug}/jobs/count",
    "https://apply.workable.com/api/v1/accounts/{slug}/jobs/count?country=Greece",
    "https://apply.workable.com/api/v1/accounts/{slug}/jobs/count?country=GR",
)

_USER_AGENT = (
    "awesome-greek-tech-jobs/1.0 "
    "(+https://github.com/leftkats/awesome-greek-tech-jobs; "
    "python-requests; Greece job board snapshot)"
)


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
        }
    )
    retry = Retry(
        total=_RETRY_TOTAL,
        connect=_RETRY_TOTAL,
        read=_RETRY_TOTAL,
        backoff_factor=_RETRY_BACKOFF_FACTOR,
        status_forcelist=_RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_count_from_count_endpoints(
    session: requests.Session, slug: str, idx: int = 0, total: int = 0
) -> int | None:
    """Try /count variants first; skip geo-mismatched results and continue."""
    prefix = f"[{idx}/{total}] {slug}"
    headers = {
        "Origin": "https://apply.workable.com",
        "Referer": f"https://apply.workable.com/{slug}/",
    }

    for candidate in _COUNT_URL_CANDIDATES:
        url = candidate.format(slug=slug)
        try:
            resp = session.get(url, headers=headers, timeout=TIMEOUT_SEC)
        except requests.RequestException as e:
            print(f"{prefix}: /count FAILED ({url}) → {e}", file=sys.stderr)
            continue

        if resp.status_code != 200:
            print(
                f"{prefix}: /count HTTP {resp.status_code} ({url}) → {resp.text[:200]}".strip(),
                file=sys.stderr,
            )
            continue

        try:
            data = resp.json()
        except ValueError:
            print(f"{prefix}: /count invalid JSON ({url})", file=sys.stderr)
            continue

        raw_total = data.get("total")
        raw_incountry = data.get("incountry")
        if not isinstance(raw_total, int) or not isinstance(raw_incountry, int):
            print(f"{prefix}: /count missing keys ({url})", file=sys.stderr)
            continue

        # If there are jobs but requester-geo "incountry" is 0, this endpoint is
        # likely not reflecting Greece. Continue to the next /count candidate.
        if raw_total > 0 and raw_incountry == 0:
            print(f"{prefix}: /count geo mismatch ({url}) total={raw_total} incountry=0; trying next")
            continue

        print(f"{prefix}: /count hit ({url}) → {raw_incountry}/{raw_total}")
        return raw_incountry

    return None


def fetch_count(session: requests.Session, slug: str, idx: int = 0, total: int = 0) -> int:
    """Fetch count from /count endpoints only; return 0 on failure."""
    prefix = f"[{idx}/{total}] {slug}"
    count_value = _fetch_count_from_count_endpoints(session, slug, idx=idx, total=total)
    if isinstance(count_value, int):
        return count_value
    print(f"{prefix}: all /count endpoints failed; using 0", file=sys.stderr)
    return 0


def main() -> int:
    with YAML_PATH.open(encoding="utf-8") as f:
        companies = yaml.safe_load(f)

    slugs: list[str] = []
    seen: set[str] = set()
    for c in companies or []:
        slug = extract_workable_apply_slug(c.get("careers_url"))
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    session = _build_session()
    accounts: dict[str, int] = {}

    print(f"Fetching {len(slugs)} Workable accounts…")
    for i, slug in enumerate(slugs, 1):
        if i > 1:
            time.sleep(DELAY_BETWEEN_SLUGS_SEC)
        accounts[slug] = fetch_count(session, slug, idx=i, total=len(slugs))

    total_open = sum(accounts.values())
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metric": "incountry_greece",
        "accounts": accounts,
        "total_open": total_open,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        f.write(
            "# Workable Greece incountry counts from /count endpoints (generated by "
            "scripts/fetch_workable_counts)\n"
        )
        yaml.dump(
            out,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
    print(f"Wrote {OUTPUT_PATH} ({len(slugs)} accounts, total_open={total_open})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
