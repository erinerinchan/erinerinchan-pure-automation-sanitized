# pip install requests python-dotenv rapidfuzz pypdf selenium webdriver-manager
# Usage: python pure_metadata.py
# Fetches publication metadata from OpenAlex and Scopus,
# then outputs a structured report for manual entry into HKUST Pure.

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, quote_plus

try:
    import requests
except ImportError:
    sys.exit("ERROR: 'requests' library is required. Install with: pip install requests")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional

try:
    from rapidfuzz import fuzz as rf_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        HAS_PYPDF = True
    except ImportError:
        HAS_PYPDF = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENALEX_BASE = "https://api.openalex.org"
SCOPUS_BASE = "https://api.elsevier.com/content"

HKUST_VARIANTS = [
    "hong kong university of science and technology",
    "hkust",
    "hk ust",
    "hong kong univ sci & technol",
    "hong kong univ. sci. technol.",
    "hong kong univ of sci & technol",
    "hong kong univ science technol",
]

RGC_PATTERNS = [
    r"research\s+grants?\s+council",
    r"\brgc\b",
    r"\bugc[/\\]rgc\b",
    r"general\s+research\s+fund",
    r"\bgrf\b",
    r"early\s+career\s+scheme",
    r"\becs\b.*grant",
    r"collaborative\s+research\s+fund",
    r"\bcrf\b.*grant",
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "PureMetadataHelper/1.0 (mailto:research@ust.hk)"})

# Performance tuning (override via env vars if needed)
REQUEST_PAUSE_SECONDS = float(os.environ.get("PURE_REQUEST_PAUSE_SECONDS", "0"))
ENABLE_PDF_PAGE_COUNT = os.environ.get("PURE_ENABLE_PDF_PAGE_COUNT", "0").strip().lower() in ("1", "true", "yes", "on")

WOS_SMART_SEARCH_URL = "https://www.webofscience.com/wos/woscc/smart-search"

# Paths checked for config file (first found wins)
CONFIG_PATHS = [
    Path.cwd() / ".pure_keys.json",          # project-local
    Path.home() / ".pure_keys.json",          # user home
]


def _build_wos_search_query(doi="", title=""):
    """Prefer DOI search for Web of Science, else fall back to title."""
    doi_value = (doi or "").strip()
    if doi_value:
        return f"DO=({doi_value})"
    query = (title or "").strip()
    if query:
        return query
    return ""


def build_web_of_science_search_url(doi="", title=""):
    """Build a Web of Science smart-search URL for title or DOI lookup."""
    query = _build_wos_search_query(doi=doi, title=title)
    if not query:
        return None
    return f"{WOS_SMART_SEARCH_URL}?search-main-box={quote_plus(query)}"

# Environment variable names to try for each key (first found wins)
SCOPUS_ENV_NAMES = ["SCOPUS_API_KEY", "ELSEVIER_API_KEY", "SCOPUS_KEY"]
OPENALEX_ENV_NAMES = ["OPENALEX_API_KEY", "OPENALEX_KEY"]  # optional premium key

# ---------------------------------------------------------------------------
# API-key management — auto-detect without user input when possible
# ---------------------------------------------------------------------------

def _find_config_file():
    """Return the first existing config file path, or None."""
    for p in CONFIG_PATHS:
        if p.is_file():
            return p
    return None


def _load_keys_from_config():
    """Load stored keys from JSON config file."""
    cfg = _find_config_file()
    if not cfg:
        return {}
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_keys_to_config(keys_dict):
    """Persist keys to the user-home config file (creates if needed)."""
    cfg = Path.home() / ".pure_keys.json"
    existing = {}
    if cfg.is_file():
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(keys_dict)
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    print(f"  Keys saved to {cfg}")


def _env_lookup(names):
    """Return the first non-empty env var value from a list of candidate names."""
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def resolve_api_keys():
    """Auto-detect API keys from env vars, .env, and config file.

    Returns (scopus_key, openalex_key). Only prompts the user if
    a key cannot be found anywhere.
    """
    cfg_keys = _load_keys_from_config()

    scopus_key = _env_lookup(SCOPUS_ENV_NAMES) or cfg_keys.get("SCOPUS_API_KEY", "")
    openalex_key = _env_lookup(OPENALEX_ENV_NAMES) or cfg_keys.get("OPENALEX_API_KEY", "")

    found = []
    if scopus_key:
        found.append("Scopus")
    if openalex_key:
        found.append("OpenAlex (premium)")
    if found:
        print(f"  Auto-detected API keys: {', '.join(found)}")
    else:
        print("  No API keys found. Only OpenAlex (free) will be queried.")
        print("  To enable Scopus, set environment variables or create ~/.pure_keys.json")

    missing = []
    if not scopus_key:
        missing.append("SCOPUS_API_KEY")
    if missing:
        print(f"  Missing: {', '.join(missing)}")

    return scopus_key, openalex_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text):
    """Strip HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    return " ".join(text.split()).strip()


def _normalize(title):
    """Lower-case, remove punctuation, collapse spaces — for comparison."""
    if not title:
        return ""
    t = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(t.split())


def _fuzzy_ratio(a, b):
    """Return 0-100 similarity score."""
    if HAS_RAPIDFUZZ:
        return rf_fuzz.token_sort_ratio(_normalize(a), _normalize(b))
    # Simple fallback: Jaccard on word sets
    sa, sb = set(_normalize(a).split()), set(_normalize(b).split())
    if not sa or not sb:
        return 0
    return int(100 * len(sa & sb) / len(sa | sb))


def _is_hkust(affiliation_str):
    """Check whether an affiliation string looks like HKUST."""
    if not affiliation_str:
        return False
    low = affiliation_str.lower()
    return any(v in low for v in HKUST_VARIANTS)


def _extract_hkust_department(raw_affiliation):
    """Try to extract the HKUST department / school from a raw affiliation string.

    Common patterns:
      "Department of Mechanical and Aerospace Engineering, HKUST, ..."
      "Dept. of Civil Engineering, Hong Kong University of Science and Technology"
      "School of Engineering, HKUST"
    Returns the department string or '' if not identifiable.
    """
    if not raw_affiliation or not _is_hkust(raw_affiliation):
        return ""
    # Split on commas / semicolons and look for Dept / School / Division tokens
    parts = re.split(r"[;,]", raw_affiliation)
    dept_patterns = [
        r"(?:department|dept\.?)\s+of\s+.+",
        r"(?:school|division|center|centre|institute|lab|laboratory)\s+(?:of|for)\s+.+",
        r"(?:school|division|center|centre|institute)\s+\w+",
    ]
    for part in parts:
        part = part.strip()
        for pat in dept_patterns:
            if re.search(pat, part, re.IGNORECASE):
                # Make sure this part isn't the institution name itself
                if not _is_hkust(part) or re.search(r"(?:dept|department|school|division|center|centre|institute)", part, re.IGNORECASE):
                    return part
    return ""


def _detect_rgc(text):
    """Return True if funding/acknowledgment text mentions RGC."""
    if not text:
        return None  # unknown
    low = text.lower()
    for pat in RGC_PATTERNS:
        if re.search(pat, low):
            return True
    return False


def _safe_get(url, params=None, headers=None, label="API"):
    """GET with basic error handling."""
    try:
        r = SESSION.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        print(f"  [{label}] HTTP {r.status_code}: {r.text[:200]}")
        return None
    except requests.RequestException as e:
        print(f"  [{label}] Request error: {e}")
        return None


def _maybe_pause():
    """Optional throttle between requests; disabled by default for speed."""
    if REQUEST_PAUSE_SECONDS > 0:
        time.sleep(REQUEST_PAUSE_SECONDS)


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

def fetch_openalex(doi=None, title=None):
    """Query OpenAlex. Returns normalized dict or None."""
    print("\n--- Querying OpenAlex ---")
    data = None

    if doi:
        url = f"{OPENALEX_BASE}/works/https://doi.org/{doi}"
        data = _safe_get(url, params={"mailto": "research@ust.hk"}, label="OpenAlex")

    if not data and title:
        params = {"search": title, "mailto": "research@ust.hk", "per_page": 5}
        resp = _safe_get(f"{OPENALEX_BASE}/works", params=params, label="OpenAlex")
        if resp and resp.get("results"):
            best, best_score = None, 0
            for r in resp["results"]:
                score = _fuzzy_ratio(title, r.get("title", ""))
                if score > best_score:
                    best, best_score = r, score
            if best_score >= 75:
                data = best
                print(f"  Matched by title (score {best_score})")
            else:
                print(f"  No good title match (best score {best_score})")

    if not data:
        print("  No result from OpenAlex.")
        return None

    print("  Found in OpenAlex.")

    # Parse authors
    authors = []
    for authorship in data.get("authorships", []):
        author = authorship.get("author", {})
        name = author.get("display_name", "")
        institutions = authorship.get("institutions", [])
        aff_names = [inst.get("display_name", "") for inst in institutions]
        raw_affs = authorship.get("raw_affiliation_strings", [])
        is_corresponding = authorship.get("is_corresponding", False)
        hkust = any(_is_hkust(a) for a in aff_names) or any(_is_hkust(r) for r in raw_affs)

        # Extract HKUST department from raw affiliation strings
        department = ""
        if hkust:
            for r in raw_affs:
                department = _extract_hkust_department(r)
                if department:
                    break
            # Fallback: check institution-level display names for sub-orgs
            if not department:
                for inst in institutions:
                    if _is_hkust(inst.get("display_name", "")):
                        # Check lineage for sub-unit
                        lineage = inst.get("lineage", [])
                        if len(lineage) > 1:
                            # lineage[0] is the inst itself; lineage[1+] may be parents
                            pass  # lineage goes upward, not helpful

        authors.append({
            "name": name,
            "affiliations": aff_names,
            "raw_affiliations": raw_affs,
            "is_hkust": hkust,
            "department": department,
            "corresponding": is_corresponding,
        })

    # OA info
    oa = data.get("open_access", {})
    oa_status = oa.get("oa_status", "closed")
    oa_url = oa.get("oa_url")

    # License — extract from primary location and best_oa_location
    license_str = ""
    primary_loc_raw = data.get("primary_location") or {}
    license_str = primary_loc_raw.get("license", "") or ""
    if not license_str:
        # Check all locations for the best license
        for loc in data.get("locations", []):
            loc_license = loc.get("license", "") or ""
            if loc_license:
                license_str = loc_license
                break
    # Also check best_oa_location
    best_oa = data.get("best_oa_location") or {}
    if not license_str:
        license_str = best_oa.get("license", "") or ""

    # Funding
    grants = data.get("grants", [])
    funder_names = " ".join(g.get("funder_display_name", "") for g in grants)
    rgc = _detect_rgc(funder_names)

    # Abstract — OpenAlex uses inverted index
    abstract_inv = data.get("abstract_inverted_index")
    abstract = ""
    if abstract_inv:
        word_positions = []
        for word, positions in abstract_inv.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort()
        abstract = " ".join(w for _, w in word_positions)

    primary_loc = data.get("primary_location") or {}
    source = primary_loc.get("source") or {}

    # Dates — OpenAlex provides publication_date (usually the earliest known)
    # and created_date (when the record was added to OpenAlex, not useful here).
    # We store publication_date; if a primary_location has a "version" of
    # "publishedVersion" vs "acceptedVersion" we can infer online vs print,
    # but OpenAlex rarely distinguishes them, so we also check Crossref.
    oa_pub_date = data.get("publication_date", "")  # earliest date available

    # Try locations for an online/ahead-of-print date
    oa_online_date = ""
    locations = data.get("locations", [])
    for loc in locations:
        if loc.get("version") == "submittedVersion":
            continue
        landing = loc.get("landing_page_url", "") or ""
        # The first location with a date tends to be the earliest
    # Use Crossref-style dates if embedded (OpenAlex mirrors some)
    # published-online → online_date, published-print → pub_date
    # These live under data["doi_registration_agency"] or aren't directly exposed
    # so we rely on Scopus online dates when available.

    return {
        "source": "OpenAlex",
        "id": data.get("id", ""),
        "doi": (data.get("doi") or "").replace("https://doi.org/", ""),
        "title": _clean(data.get("title", "")),
        "abstract": _clean(abstract),
        "pub_year": data.get("publication_year"),
        "pub_date": oa_pub_date,
        "online_date": oa_online_date,
        "journal": source.get("display_name", ""),
        "issn": source.get("issn_l", ""),
        "issn_list": source.get("issn", []),
        "volume": data.get("biblio", {}).get("volume", ""),
        "issue": data.get("biblio", {}).get("issue", ""),
        "first_page": data.get("biblio", {}).get("first_page", ""),
        "last_page": data.get("biblio", {}).get("last_page", ""),
        "authors": authors,
        "oa_status": oa_status,
        "oa_url": oa_url,
        "license": license_str,
        "keywords": [kw.get("display_name", "") for kw in data.get("keywords", [])],
        "concepts": [c.get("display_name", "") for c in data.get("concepts", []) if c.get("score", 0) > 0.3],
        "topics": [t.get("display_name", "") for t in data.get("topics", [])],
        "rgc_funded": rgc,
        "funding_text": funder_names,
        "type": data.get("type", ""),
        "language": data.get("language", "en"),
    }


# ---------------------------------------------------------------------------
# Scopus
# ---------------------------------------------------------------------------

def fetch_scopus(doi=None, title=None, api_key=None):
    """Query Scopus. Returns normalized dict or None."""
    if not api_key:
        print("\n--- Skipping Scopus (no API key) ---")
        return None
    print("\n--- Querying Scopus ---")

    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    data = None

    if doi:
        params = {"query": f"DOI({doi})", "view": "COMPLETE"}
        resp = _safe_get(f"{SCOPUS_BASE}/search/scopus", params=params, headers=headers, label="Scopus")
        results = (resp or {}).get("search-results", {}).get("entry", [])
        if results and results[0].get("dc:title"):
            data = results[0]

    if not data and title:
        safe_title = title.replace('"', '\\"')
        params = {"query": f'TITLE("{safe_title}")', "view": "COMPLETE"}
        resp = _safe_get(f"{SCOPUS_BASE}/search/scopus", params=params, headers=headers, label="Scopus")
        results = (resp or {}).get("search-results", {}).get("entry", [])
        if results:
            best, best_score = None, 0
            for r in results:
                score = _fuzzy_ratio(title, r.get("dc:title", ""))
                if score > best_score:
                    best, best_score = r, score
            if best_score >= 75:
                data = best
                print(f"  Matched by title (score {best_score})")

    if not data:
        print("  No result from Scopus.")
        return None
    print("  Found in Scopus.")

    # Fetch abstract via Abstract Retrieval API if we have a scopus_id
    scopus_id = data.get("dc:identifier", "").replace("SCOPUS_ID:", "")
    eid = data.get("eid", "")
    abstract = _clean(data.get("dc:description", ""))

    if not abstract and scopus_id:
        _maybe_pause()
        abs_resp = _safe_get(
            f"{SCOPUS_BASE}/abstract/scopus_id/{scopus_id}",
            headers=headers, params={"view": "FULL"}, label="Scopus-Abstract"
        )
        if abs_resp:
            core = abs_resp.get("abstracts-retrieval-response", {}).get("coredata", {})
            abstract = _clean(core.get("dc:description", ""))

    # Authors — build affiliation map first
    affils = data.get("affiliation", [])
    if isinstance(affils, dict):
        affils = [affils]
    aff_map = {}  # afid → {name, city, country}
    for af in (affils or []):
        afid = af.get("afid", "")
        aname = af.get("affilname", "")
        acity = af.get("affiliation-city", "")
        acountry = af.get("affiliation-country", "")
        aff_map[afid] = {"name": aname, "city": acity, "country": acountry}

    author_list = data.get("author", [])
    authors = []
    for a in (author_list or []):
        name = a.get("authname", "") or f'{a.get("given-name", "")} {a.get("surname", "")}'.strip()
        # Resolve author → affiliation link
        afid_raw = a.get("afid", {})
        if isinstance(afid_raw, dict):
            author_afids = [afid_raw.get("$", "")]
        elif isinstance(afid_raw, list):
            author_afids = [x.get("$", "") if isinstance(x, dict) else str(x) for x in afid_raw]
        else:
            author_afids = [str(afid_raw)] if afid_raw else []

        aff_names = []
        is_hkust = False
        department = ""
        for aid in author_afids:
            info = aff_map.get(aid, {})
            if info.get("name"):
                aff_names.append(info["name"])
                if _is_hkust(info["name"]):
                    is_hkust = True

        # If no per-author afid link, fall back to checking all affiliations
        if not aff_names:
            for info in aff_map.values():
                aff_names.append(info.get("name", ""))
                if _is_hkust(info.get("name", "")):
                    is_hkust = True

        # Extract department from Scopus affiliation name-dept field
        if is_hkust:
            for info in aff_map.values():
                if _is_hkust(info.get("name", "")):
                    department = _extract_hkust_department(info.get("name", ""))
                    if department:
                        break

        authors.append({
            "name": name,
            "affiliations": aff_names,
            "is_hkust": is_hkust,
            "department": department,
            "corresponding": False,
        })

    # Keywords
    kw_raw = data.get("authkeywords", "")
    keywords = [k.strip() for k in kw_raw.split("|") if k.strip()] if kw_raw else []

    # Funding
    fund_acr = data.get("fund-acr", "")
    fund_sponsor = data.get("fund-sponsor", "")
    fund_text = f"{fund_acr} {fund_sponsor}"
    rgc = _detect_rgc(fund_text)

    # Build Scopus URL using the Scopus document ID (dc:identifier → "SCOPUS_ID:105030037032")
    # The correct link format is: https://www.scopus.com/pages/publications/{scopus_doc_id}
    scopus_link = ""
    scopus_doc_id = scopus_id  # already stripped of "SCOPUS_ID:" prefix above
    if scopus_doc_id:
        scopus_link = f"https://www.scopus.com/pages/publications/{scopus_doc_id}"

    # ISSN
    issn = data.get("prism:issn", "")
    eissn = data.get("prism:eIssn", "")

    # Dates — Scopus search returns prism:coverDate (print/cover date)
    # The coverDisplayDate sometimes says "Available online DD Month YYYY"
    cover_date = data.get("prism:coverDate", "")          # e.g. 2026-04-01
    cover_display = data.get("prism:coverDisplayDate", "")  # e.g. "April 2026" or "Available online 9 January 2026"
    online_date_scopus = ""
    # If coverDisplayDate mentions "Available online", extract that date
    online_match = re.search(r"(?:available\s+online|online)\s+(\d{1,2}\s+\w+\s+\d{4})", cover_display, re.IGNORECASE)
    if online_match:
        online_date_scopus = online_match.group(1)

    return {
        "source": "Scopus",
        "id": eid,
        "scopus_id": scopus_id,
        "doi": (data.get("prism:doi") or ""),
        "title": _clean(data.get("dc:title", "")),
        "abstract": abstract,
        "pub_year": int(str(cover_date)[:4]) if cover_date else None,
        "pub_date": cover_date,
        "online_date": online_date_scopus,
        "journal": data.get("prism:publicationName", ""),
        "issn": issn,
        "eissn": eissn,
        "volume": data.get("prism:volume", ""),
        "issue": data.get("prism:issueIdentifier", ""),
        "pages": data.get("prism:pageRange", ""),
        "article_number": data.get("article-number", ""),
        "authors": authors,
        "oa_status": "",
        "oa_url": None,
        "keywords": keywords,
        "rgc_funded": rgc,
        "funding_text": fund_text.strip(),
        "scopus_link": scopus_link,
        "scopus_doc_id": scopus_doc_id,
        "type": data.get("subtypeDescription", ""),
        "language": "",
    }


# ---------------------------------------------------------------------------
# Merge & Report
# ---------------------------------------------------------------------------

def _pick(sources, key, default=""):
    """Pick the first non-empty value from sources for a given key."""
    for s in sources:
        if s and s.get(key):
            val = s[key]
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (list, int)):
                return val
    return default


def _pick_longest_str(sources, key):
    """Pick the longest string value (useful for abstract)."""
    best = ""
    for s in sources:
        if s and s.get(key) and isinstance(s[key], str) and len(s[key]) > len(best):
            best = s[key]
    return best


def _merge_keywords(sources, max_kw=15):
    """Combine and deduplicate keywords from all sources."""
    seen_lower = set()
    merged = []
    for s in sources:
        if not s:
            continue
        for kw_key in ("keywords", "concepts", "topics"):
            for kw in s.get(kw_key, []):
                kw = kw.strip()
                if kw and kw.lower() not in seen_lower:
                    seen_lower.add(kw.lower())
                    merged.append(kw)
    return merged[:max_kw]


def _merge_authors(sources):
    """Merge author lists, preferring source with most detail."""
    # Pick the list with the most authors as base, enrich from others
    best = []
    for s in sources:
        if s and s.get("authors") and len(s["authors"]) > len(best):
            best = s["authors"]
    if not best:
        return []

    # Enrich HKUST flags, departments, and affiliations from other sources
    for s in sources:
        if not s or not s.get("authors"):
            continue
        for au in s["authors"]:
            for bu in best:
                if _fuzzy_ratio(au.get("name", ""), bu.get("name", "")) <= 80:
                    continue
                if au.get("is_hkust"):
                    bu["is_hkust"] = True
                if au.get("corresponding"):
                    bu["corresponding"] = True
                # Merge department (prefer non-empty)
                if au.get("department") and not bu.get("department"):
                    bu["department"] = au["department"]
                # Merge affiliations
                if au.get("affiliations"):
                    existing = set(a.lower() for a in bu.get("affiliations", []))
                    for aff in au["affiliations"]:
                        if aff.lower() not in existing:
                            bu.setdefault("affiliations", []).append(aff)
                            existing.add(aff.lower())
                # Merge raw affiliations
                if au.get("raw_affiliations"):
                    existing_raw = set(a.lower() for a in bu.get("raw_affiliations", []))
                    for raff in au["raw_affiliations"]:
                        if raff.lower() not in existing_raw:
                            bu.setdefault("raw_affiliations", []).append(raff)
                            existing_raw.add(raff.lower())
    return best


def _format_date(date_str):
    """Try to produce a human-friendly date."""
    if not date_str:
        return ""
    # Already nice
    return date_str


def _count_pdf_pages(doi=None, oa_url=None):
    """Try to download the PDF and count its pages.

    Attempts several URL strategies:
      1. OpenAlex OA URL (if it points to a PDF)
      2. Unpaywall via DOI (free API, returns best OA PDF link)
      3. DOI content negotiation requesting application/pdf
    Returns page count as a string, or '' if unavailable.
    """
    if not HAS_PYPDF:
        return ""

    candidate_urls = []

    # 1. OA URL from OpenAlex
    if oa_url:
        candidate_urls.append(oa_url)

    # 2. Unpaywall API (free, returns best OA PDF link)
    if doi:
        try:
            resp = SESSION.get(
                f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                params={"email": "research@ust.hk"}, timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                best_loc = data.get("best_oa_location") or {}
                pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")
                if pdf_url and pdf_url not in candidate_urls:
                    candidate_urls.append(pdf_url)
        except Exception:
            pass

    # 3. DOI content negotiation for PDF
    if doi:
        candidate_urls.append(f"https://doi.org/{doi}")

    import io
    for url in candidate_urls:
        try:
            resp = SESSION.get(
                url,
                headers={"Accept": "application/pdf"},
                timeout=30,
                allow_redirects=True,
                stream=True,
            )
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and "pdf" in content_type.lower():
                reader = PdfReader(io.BytesIO(resp.content))
                page_count = len(reader.pages)
                if page_count > 0:
                    print(f"  PDF page count from {url[:80]}: {page_count}")
                    return str(page_count)
        except Exception:
            continue

    return ""


def _crossref_date_parts_to_str(parts):
    """Convert Crossref date-parts [[2026,1,9]] → '2026-01-09'."""
    if not parts or not parts[0]:
        return ""
    dp = parts[0]
    if len(dp) >= 3:
        return f"{dp[0]}-{dp[1]:02d}-{dp[2]:02d}"
    if len(dp) == 2:
        return f"{dp[0]}-{dp[1]:02d}"
    if len(dp) == 1:
        return str(dp[0])
    return ""


def _fetch_crossref_dates(doi):
    """Quick Crossref lookup to get published-online/print dates and license.

    Returns (online_date, print_date, license_str).
    Crossref is free and explicitly provides both date types + license.
    """
    if not doi:
        return "", "", ""
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    resp = _safe_get(url, params={"mailto": "research@ust.hk"}, label="Crossref")
    if not resp:
        return "", "", ""
    msg = resp.get("message", {})
    online = _crossref_date_parts_to_str(msg.get("published-online", {}).get("date-parts", []))
    print_d = _crossref_date_parts_to_str(msg.get("published-print", {}).get("date-parts", []))
    # Some records only have "published" (no online/print distinction)
    if not online and not print_d:
        generic = _crossref_date_parts_to_str(msg.get("published", {}).get("date-parts", []))
        print_d = generic

    # License — Crossref returns a list of license objects
    license_str = ""
    licenses = msg.get("license", [])
    for lic in licenses:
        url_val = lic.get("URL", "")
        if url_val:
            license_str = url_val
            # Prefer the vor (version of record) content-version license
            if lic.get("content-version", "") == "vor":
                license_str = url_val
                break

    return online, print_d, license_str


def _determine_rgc(sources):
    """Aggregate RGC determination."""
    for s in sources:
        if s and s.get("rgc_funded") is True:
            return True
    # Check if any source has explicit False (funding info present but no RGC)
    has_funding_info = any(s and s.get("funding_text") for s in sources)
    if has_funding_info:
        return False
    return None  # unknown


# License URL → human-readable name mapping
_LICENSE_MAP = [
    ("creativecommons.org/licenses/by-nc-nd/4.0", "CC BY-NC-ND 4.0"),
    ("creativecommons.org/licenses/by-nc-nd/3.0", "CC BY-NC-ND 3.0"),
    ("creativecommons.org/licenses/by-nc-sa/4.0", "CC BY-NC-SA 4.0"),
    ("creativecommons.org/licenses/by-nc-sa/3.0", "CC BY-NC-SA 3.0"),
    ("creativecommons.org/licenses/by-nc/4.0", "CC BY-NC 4.0"),
    ("creativecommons.org/licenses/by-nc/3.0", "CC BY-NC 3.0"),
    ("creativecommons.org/licenses/by-nd/4.0", "CC BY-ND 4.0"),
    ("creativecommons.org/licenses/by-nd/3.0", "CC BY-ND 3.0"),
    ("creativecommons.org/licenses/by-sa/4.0", "CC BY-SA 4.0"),
    ("creativecommons.org/licenses/by-sa/3.0", "CC BY-SA 3.0"),
    ("creativecommons.org/licenses/by/4.0", "CC BY 4.0"),
    ("creativecommons.org/licenses/by/3.0", "CC BY 3.0"),
    ("creativecommons.org/publicdomain/zero", "CC0 (Public Domain)"),
    ("elsevier.com/open-access/userlicense/1.0", "Elsevier User License"),
    ("elsevier.com/tdm/userlicense/1.0", "Elsevier TDM License"),
    ("springer.com/tdm", "Springer TDM License"),
    ("wiley.com/tdm", "Wiley TDM License"),
]


def _normalize_license(raw):
    """Convert a license URL or identifier to a human-readable label."""
    if not raw:
        return ""
    raw_lower = raw.lower()
    for pattern, label in _LICENSE_MAP:
        if pattern in raw_lower:
            return label
    # If it's a URL we don't recognize, return it as-is
    if raw.startswith("http"):
        return raw
    return raw


def build_pure_metadata(doi, title, oa_result, scopus_result):
    """Build a flat dict of merged metadata for Pure automation."""
    sources = [s for s in [oa_result, scopus_result] if s]
    if not sources:
        return {}

    merged_doi = doi or _pick(sources, "doi")
    merged_title = _pick(sources, "title") or title
    abstract = _pick_longest_str(sources, "abstract")
    volume = _pick(sources, "volume")
    issue = _pick(sources, "issue")

    pages = ""
    for s in sources:
        if s:
            p = s.get("pages", "") or s.get("first_page", "")
            if p:
                lp = s.get("last_page", "")
                if lp and lp != p and "-" not in str(p):
                    p = f"{p}-{lp}"
                pages = str(p)
                break
    article_number = _pick(sources, "article_number")

    num_pages = ""
    oa_url = oa_result.get("oa_url") if oa_result else None
    if ENABLE_PDF_PAGE_COUNT:
        num_pages = _count_pdf_pages(doi=merged_doi, oa_url=oa_url)
    if not num_pages and pages and "-" in str(pages):
        try:
            parts = str(pages).split("-")
            num_pages = str(int(parts[1]) - int(parts[0]) + 1)
        except (ValueError, IndexError):
            pass

    pub_type = _pick(sources, "type")
    is_journal = "article" in pub_type.lower() if pub_type else True
    lang = _pick(sources, "language", "en")
    keywords = _merge_keywords(sources)
    online_date = _pick(sources, "online_date")
    print_date = _pick(sources, "pub_date")

    return {
        "doi": merged_doi,
        "title": merged_title,
        "abstract": abstract,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "num_pages": num_pages,
        "article_number": article_number,
        "peer_reviewed": is_journal,
        "language": lang,
        "keywords": keywords,
        "online_date": online_date,
        "print_date": print_date,
    }


def generate_report(doi, title, oa_result, scopus_result):
    """Build the final markdown-like report."""
    sources = [s for s in [oa_result, scopus_result] if s]

    if not sources:
        return "\n*** NO DATA FOUND from any source. Check DOI / title and API keys. ***\n"

    # Merged fields
    merged_title = _pick(sources, "title")
    abstract = _pick_longest_str(sources, "abstract")
    pub_year = _pick(sources, "pub_year")
    pub_date = _pick(sources, "pub_date")
    journal = _pick(sources, "journal")
    issn = _pick(sources, "issn")
    eissn = _pick(sources, "eissn", "")
    if not eissn:
        # Check issn_list from OpenAlex
        if oa_result and oa_result.get("issn_list"):
            for i in oa_result["issn_list"]:
                if i != issn:
                    eissn = i
                    break
    volume = _pick(sources, "volume")
    issue = _pick(sources, "issue")

    # Pages / article number
    pages = ""
    article_number = ""
    for s in sources:
        if s:
            p = s.get("pages", "") or s.get("first_page", "")
            if p:
                lp = s.get("last_page", "")
                if lp and lp != p and "-" not in str(p):
                    p = f"{p}-{lp}"
                pages = str(p)
            # Look for article number if available
            if not article_number:
                article_number = s.get("article_number", "")
    # If still not found, try _pick fallback
    if not article_number:
        article_number = _pick(sources, "article_number")

    merged_doi = doi or _pick(sources, "doi")
    authors = _merge_authors(sources)
    keywords = _merge_keywords(sources)
    rgc = _determine_rgc(sources)
    pub_type = _pick(sources, "type")

    # OA info
    oa_status = "closed"
    oa_url = None
    if oa_result:
        oa_status = oa_result.get("oa_status", "closed") or "closed"
        oa_url = oa_result.get("oa_url")

    is_open = oa_status.lower() in ("gold", "green", "bronze", "hybrid", "diamond")

    # Links — build from whichever source provides the IDs
    oa_link = ""
    scopus_link = ""
    wos_link = build_web_of_science_search_url(doi=merged_doi, title=merged_title)

    if oa_result:
        oa_id = oa_result.get("id", "")
        if oa_id:
            short_id = oa_id.replace("https://openalex.org/", "")
            oa_link = f"https://openalex.org/{short_id}"
    if scopus_result:
        scopus_link = scopus_result.get("scopus_link", "")

    # Only show direct record links — no DOI-search fallbacks

    # External IDs
    # Scopus: doc ID from dc:identifier (e.g. "105030037032")
    scopus_doc_id = scopus_result.get("scopus_doc_id", "") if scopus_result else ""
    scopus_eid = scopus_result.get("id", "") if scopus_result else ""
    oa_id_short = ""
    if oa_result and oa_result.get("id"):
        oa_id_short = oa_result["id"].replace("https://openalex.org/", "")
        if oa_id_short.startswith("W"):
            oa_id_short = "w" + oa_id_short[1:]

    # Peer-reviewed logic
    is_journal = "article" in pub_type.lower() if pub_type else True
    peer_review = "Peer-reviewed" if is_journal else "Not peer-reviewed (check type)"

    # Publication status dates — distinguish online vs print
    online_date = _pick(sources, "online_date")
    print_date = _pick(sources, "pub_date")

    # Enrich with Crossref (free), which explicitly separates online/print dates + license
    crossref_license = ""
    if merged_doi and (not online_date or not print_date):
        cr_online, cr_print, crossref_license = _fetch_crossref_dates(merged_doi)
        if cr_online and not online_date:
            online_date = cr_online
        if cr_print and not print_date:
            print_date = cr_print
        # If we still only have print_date and Crossref gave us online too
        if cr_online and cr_print and not online_date:
            online_date = cr_online
            print_date = cr_print
    elif merged_doi:
        # Already have both dates, but still fetch for license
        _, _, crossref_license = _fetch_crossref_dates(merged_doi)

    # If both online_date and print_date are the same, just show one
    if online_date and print_date and online_date == print_date:
        online_date = ""

    # Funding text
    fund_texts = [s.get("funding_text", "") for s in sources if s and s.get("funding_text")]
    longest_fund = max(fund_texts, key=len) if fund_texts else ""

    # ----- Build report -----
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  HKUST PURE METADATA FORM RECOMMENDATIONS")
    lines.append("=" * 60)
    lines.append(f"Title searched / DOI: {merged_doi or merged_title}")
    lines.append(f"Sources matched: {', '.join(s['source'] for s in sources)}")
    lines.append("")

    lines.append(f"Peer-reviewed*:")
    lines.append(f"  → Recommend: {peer_review}")
    lines.append("")

    lines.append("Publication status:")
    lines.append("  - Publication statuses and dates:")
    if online_date:
        lines.append(f"    - E-pub ahead of print (online): {_format_date(online_date)}")
    if print_date:
        if online_date:
            lines.append(f"    - Published (print): {_format_date(print_date)}")
        else:
            # Only one date available — can't be sure if it's online or print
            lines.append(f"    - Published (online/print date not distinguished): {_format_date(print_date)}")
            lines.append(f"      → Tip: check the publisher page to confirm if this is the online or print date")
    else:
        lines.append(f"    - Published: [not available — check publisher]")
    lines.append("")

    lines.append("Publication information:")
    lang = _pick(sources, "language", "en")
    lang_display = "English" if lang in ("en", "english", "") else lang
    lines.append(f"  - Original language: {lang_display}")
    lines.append(f"  - Title of the contribution in original language:")
    lines.append(f"    {merged_title}")
    lines.append(f"  - Abstract / Description:")
    if abstract:
        wrapped = textwrap.fill(abstract, width=90, initial_indent="    ", subsequent_indent="    ")
        lines.append(wrapped)
    else:
        lines.append("    [Abstract not available from APIs — copy from publisher page]")
    lines.append("")

    # Number of pages — optional PDF download (disabled by default), else page-range fallback
    num_pages = ""
    if ENABLE_PDF_PAGE_COUNT:
        print("  Checking PDF for page count...")
        num_pages = _count_pdf_pages(doi=merged_doi, oa_url=oa_url)
    if not num_pages and pages and "-" in str(pages):
        try:
            parts = str(pages).split("-")
            num_pages = str(int(parts[1]) - int(parts[0]) + 1)
            print(f"  Page count from page range: {num_pages}")
        except (ValueError, IndexError):
            pass
    lines.append(f"  - Number of pages: {num_pages or '[could not determine — check publisher PDF]'}")
    lines.append("")

    lines.append("Contributors and affiliations:")
    for i, au in enumerate(authors, 1):
        name = au.get("name", "Unknown")
        is_hkust = au.get("is_hkust", False)
        person_type = "Internal person" if is_hkust else "External person"
        affs = au.get("affiliations", [])
        raw_affs = au.get("raw_affiliations", [])
        department = au.get("department", "")
        if is_hkust:
            if department:
                aff_str = f"HKUST — {department}"
            elif affs:
                aff_str = "; ".join(affs)
            elif raw_affs:
                aff_str = "; ".join(raw_affs)
            else:
                aff_str = "HKUST (check department in Pure)"
        else:
            if affs:
                aff_str = "; ".join(affs)
            elif raw_affs:
                aff_str = "; ".join(raw_affs)
            else:
                aff_str = "[External]"
        corr = "Yes" if au.get("corresponding") else "No"
        lines.append(f"  {i}. {name}")
        lines.append(f"     → Type: {person_type}")
        lines.append(f"     → Affiliation: {aff_str}")
        if raw_affs and any(raw_aff.strip() for raw_aff in raw_affs):
            raw_aff_str = "; ".join(raw_aff for raw_aff in raw_affs if raw_aff.strip())
            if raw_aff_str and raw_aff_str != aff_str:
                lines.append(f"     → Source affiliation text: {raw_aff_str}")
        if is_hkust and department:
            lines.append(f"     → HKUST Department: {department}")
        lines.append(f"     → Corresponding author: {corr}")
    if not authors:
        lines.append("  [No author data retrieved — enter manually]")
    lines.append(f"  Total authors: {len(authors)}")
    lines.append("")

    lines.append("Journal:")
    lines.append(f"  - Journal*: {journal}")
    issn_parts = []
    if issn:
        issn_parts.append(f"ISSN: {issn}")
    if eissn:
        issn_parts.append(f"E-ISSN: {eissn}")
    if issn_parts:
        lines.append(f"  - {' / '.join(issn_parts)}")
    if volume:
        lines.append(f"  - Volume: {volume}")
    if issue:
        lines.append(f"  - Issue number: {issue}")
    if pages and article_number:
        lines.append(f"  - Pages (from-to): {pages}")
        lines.append(f"  - Article number: {article_number}")
    elif pages:
        lines.append(f"  - Pages (from-to) / Article number: {pages}")
    elif article_number:
        lines.append(f"  - Article number: {article_number}")
    lines.append("")

    # Determine license from OpenAlex and/or Crossref
    license_raw = ""
    if oa_result:
        license_raw = oa_result.get("license", "") or ""
    if not license_raw and crossref_license:
        license_raw = crossref_license
    elif crossref_license and not license_raw:
        license_raw = crossref_license

    # Normalize license URL to human-readable name
    license_display = _normalize_license(license_raw)

    lines.append("Electronic version(s) of this work:")
    if merged_doi:
        lines.append(f"  - FINAL PUBLISHED VERSION → DOI: {merged_doi}")
    if is_open:
        lines.append(f"  - Public access: Open ({oa_status})")
        if oa_url:
            lines.append(f"    → Free PDF / link: {oa_url}")
        if license_display:
            lines.append(f"    → License: {license_display}")
            if license_raw and license_raw != license_display:
                lines.append(f"      (Source: {license_raw})")
        else:
            lines.append(f"    → License: [not determined — check publisher page]")
    else:
        lines.append(f"  - Public access: Closed")
        if license_display:
            lines.append(f"    → License (from publisher): {license_display}")
            if license_raw and license_raw != license_display:
                lines.append(f"      (Source: {license_raw})")
        else:
            lines.append(f"    → No license/document upload needed")
    lines.append("")

    # DOI-based fallback links when API-sourced links aren't available
    if not scopus_link and merged_doi:
        scopus_link = f"https://www.scopus.com/record/display.uri?origin=inward&doi={merged_doi}"

    lines.append("Other links:")
    if scopus_link:
        lines.append(f"  - Link to publication in Scopus: {scopus_link}")
    if merged_doi:
        lines.append(f"  - DOI: https://doi.org/{quote(merged_doi, safe='/')}")
    if wos_link:
        lines.append(f"  - Link to publication in Web of Science: {wos_link}")
    if oa_link:
        lines.append(f"  - Link to publication in OpenAlex: {oa_link}")
    if not (scopus_link or wos_link or oa_link):
        lines.append("  [No links available — no DOI or API results]")
    lines.append("")

    lines.append("Keywords:")
    if keywords:
        lines.append(f"  - {', '.join(keywords)}")
    else:
        lines.append("  [No keywords retrieved — add manually]")
    lines.append("")

    lines.append("RGC FUNDED*:")
    if rgc is True:
        lines.append("  → Yes")
        lines.append(f"    (Detected in funding info: {longest_fund[:200]})")
    elif rgc is False:
        lines.append("  → No")
        if longest_fund:
            lines.append(f"    (Funding info found but no RGC: {longest_fund[:200]})")
    else:
        lines.append("  → [Unknown — no funding info retrieved. Check paper acknowledgments.]")
    lines.append("")

    # HKUST Research Output Classification
    has_hkust_author = any(a.get("is_hkust") for a in authors)
    lines.append("Research output classification:")
    if has_hkust_author:
        pt_lower = (pub_type or "").lower()
        is_conference = any(kw in pt_lower for kw in ("conference", "proceeding", "paper"))
        if is_conference:
            lines.append("  1. Academic research")
            lines.append("  2. 32 publication in refereed conference paper")
        else:
            lines.append("  1. Academic research: refereed")
            lines.append("  2. 21 publication in refereed journal")
    else:
        lines.append("  N/A")
    lines.append("")

    lines.append("External publication IDs:")
    if scopus_doc_id:
        lines.append(f"  - Scopus: {scopus_doc_id}")
    elif scopus_eid:
        lines.append(f"  - Scopus EID: {scopus_eid}")
    if oa_id_short:
        lines.append(f"  - OpenAlex: {oa_id_short}")
    if merged_doi:
        lines.append(f"  - DOI: {merged_doi}")
    lines.append("")

    lines.append("-" * 60)
    lines.append("Additional helpful notes:")
    lines.append("  - Best practice: If DOI exists, first try Pure's built-in import:")
    lines.append("    +Add → Import from online source → Scopus")
    lines.append("  - If import misses fields or needs HKUST-specific overrides")
    lines.append("    (affiliations, RGC), use the values above to fill/edit manually.")
    lines.append(f"  - Total authors: {len(authors)}")
    lines.append(f"  - Publication type detected: {pub_type or 'Journal article (assumed)'}")
    lines.append("=" * 60)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  HKUST Pure — Publication Metadata Helper")
    print("  Fetches data from OpenAlex and Scopus")
    print("=" * 60)
    print()

    # --- API keys (auto-detect from env / .env / config file) ---
    scopus_key, _openalex_key = resolve_api_keys()

    pure_auto = None  # browser automation instance (lazy-init)

    try:
        while True:
            # --- Get DOI or title ---
            doi = ""
            title = ""
            entered_title = ""
            print()
            user_input = input("Enter DOI (e.g. 10.1016/j.jmps.2026.106512) or publication title (or 'quit' to exit):\n> ").strip()
            if not user_input:
                print("No input provided. Please try again.")
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("Exiting. Goodbye!")
                break

            # Detect if it looks like a DOI
            doi_match = re.match(r"^(https?://doi\.org/|doi:?)?\s*(10\.\d{4,9}/\S+)$", user_input, re.IGNORECASE)
            if doi_match:
                doi = doi_match.group(2).rstrip(".")
                print(f"\nDetected DOI: {doi}")
            else:
                title = user_input
                entered_title = user_input
                print(f"\nUsing title search: \"{title}\"")

            # --- Fetch from each source ---
            print("\nFetching metadata...")

            with ThreadPoolExecutor(max_workers=2) as pool:
                oa_future = pool.submit(fetch_openalex, doi=doi, title=title)
                scopus_future = pool.submit(fetch_scopus, doi=doi, title=title, api_key=scopus_key or None)
                oa_result = oa_future.result()
                scopus_result = scopus_future.result()

            # If we got a DOI from one source but started with title, propagate
            if not doi:
                doi = _pick([oa_result, scopus_result], "doi")
                if doi:
                    print(f"\n  Resolved DOI from sources: {doi}")
                    # Re-fetch missing sources with DOI
                    if not oa_result:
                        _maybe_pause()
                        oa_result = fetch_openalex(doi=doi)
                    if not scopus_result and scopus_key:
                        _maybe_pause()
                        scopus_result = fetch_scopus(doi=doi, api_key=scopus_key)

            # If we started with DOI but have no title yet, get it from results
            if not title:
                title = _pick([oa_result, scopus_result], "title")

            # --- Generate and print report ---
            report = generate_report(doi, title, oa_result, scopus_result)
            print(report)


            # --- Save to file and open in Notepad ---
            safe_name = re.sub(r"[^\w\-.]", "_", (doi or title)[:60])
            filename = os.path.join(tempfile.gettempdir(), f"pure_report_{safe_name}.txt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"Report saved to {filename}")
            try:
                subprocess.Popen(["notepad.exe", filename])
            except OSError:
                print("  Could not open Notepad automatically. Open the file manually.")

            # --- Open DOI, Scopus, OpenAlex, and Web of Science in browser ---
            import webbrowser
            # Try to extract links from the report or from the results
            doi_link = ""
            scopus_link = ""
            openalex_link = ""
            resolved_title = _pick([oa_result, scopus_result], "title") or title
            # Keep WOS aligned with the latest user-entered title when title input is used.
            wos_title = entered_title or resolved_title
            wos_query = _build_wos_search_query(doi=doi, title=wos_title)
            wos_link = build_web_of_science_search_url(doi=doi, title=wos_title)
            if doi:
                doi_link = f"https://doi.org/{quote(doi, safe='/')}"
            if scopus_result and scopus_result.get("scopus_link"):
                scopus_link = scopus_result["scopus_link"]
            if oa_result and oa_result.get("id"):
                openalex_id = oa_result["id"].replace("https://openalex.org/", "")
                openalex_link = f"https://openalex.org/{openalex_id}"
            # Fallback: try to extract from report text
            if not doi_link:
                m = re.search(r"DOI:\s*(10\.\d{4,9}/\S+)", report, re.IGNORECASE)
                if m:
                    doi_link = f"https://doi.org/{quote(m.group(1), safe='/')}"
            if not scopus_link:
                m = re.search(r"Link to publication in Scopus: (https?://\S+)", report)
                if m:
                    scopus_link = m.group(1)
            if not openalex_link:
                m = re.search(r"Link to publication in OpenAlex: (https?://\S+)", report)
                if m:
                    openalex_link = m.group(1)

            # Debug: print extracted links before opening
            print("\n[DEBUG] Extracted links:")
            print(f"  Scopus:    {scopus_link}")
            print(f"  DOI:       {doi_link}")
            print(f"  WoS query: {wos_query}")
            print(f"  WoS link:  {wos_link}")
            print(f"  OpenAlex:  {openalex_link}")

            # Open Scopus, DOI, Web of Science, and OpenAlex as browser tabs when possible.
            for label, link in (("Scopus", scopus_link), ("DOI", doi_link), ("Web of Science", wos_link), ("OpenAlex", openalex_link)):
                if link:
                    print(f"Opening {label} link in browser: {link}")
                    webbrowser.open(link, new=2, autoraise=True)

            print("\nDone.")



    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if pure_auto:
            pure_auto.close()


if __name__ == "__main__":
    main()
