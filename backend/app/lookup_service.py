from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from rapidfuzz import fuzz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

import pure_metadata  # noqa: E402

DOI_PATTERN = re.compile(r"^(https?://doi\.org/|doi:?)?\s*(10\.\d{4,9}/\S+)$", re.IGNORECASE)
SCOPUS_PUBLIC_LINK_PATTERN = re.compile(r"scopus\.com/pages/publications/(\d+)", re.IGNORECASE)
SCOPUS_EID_DOC_PATTERN = re.compile(r"2-s2\.0-(\d+)", re.IGNORECASE)
SCOPUS_API_ID_PATH_PATTERN = re.compile(r"/scopus_id/(\d+)", re.IGNORECASE)


def _derive_scopus_doc_id(scopus_doc_id: str, scopus_eid: str, scopus_link: str) -> str:
    candidate = (scopus_doc_id or "").strip()
    if candidate:
        if candidate.isdigit():
            return candidate
        raw_match = re.search(r"(\d{8,})", candidate)
        if raw_match:
            return raw_match.group(1)

    for value in [scopus_link, scopus_eid]:
        if not value:
            continue
        link_match = SCOPUS_PUBLIC_LINK_PATTERN.search(value)
        if link_match:
            return link_match.group(1)
        api_match = SCOPUS_API_ID_PATH_PATTERN.search(value)
        if api_match:
            return api_match.group(1)
        eid_match = SCOPUS_EID_DOC_PATTERN.search(value)
        if eid_match:
            return eid_match.group(1)

    return ""


def _parse_query(query: str, query_type: str) -> tuple[str, str]:
    raw = (query or "").strip()
    qtype = (query_type or "auto").strip().lower()

    if not raw:
        raise ValueError("Query cannot be empty")

    if qtype == "doi":
        match = DOI_PATTERN.match(raw)
        if not match:
            raise ValueError("Invalid DOI format")
        return match.group(2).rstrip("."), ""

    if qtype == "title":
        return "", raw

    match = DOI_PATTERN.match(raw)
    if match:
        return match.group(2).rstrip("."), ""
    return "", raw


def _resolve_title_candidates(title: str, openalex_api_key: str | None) -> list[dict[str, Any]]:
    params = {"search": title, "mailto": "research@ust.hk", "per_page": 5}
    if openalex_api_key:
        params["api_key"] = openalex_api_key

    try:
        response = requests.get("https://api.openalex.org/works", params=params, timeout=30)
        if response.status_code != 200:
            return []
        results = response.json().get("results", [])
    except requests.RequestException:
        return []

    candidates: list[dict[str, Any]] = []
    for item in results:
        cand_title = (item.get("title") or "").strip()
        score = int(fuzz.token_sort_ratio(title, cand_title)) if cand_title else 0
        journal = (((item.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip()
        doi_url = (item.get("doi") or "").strip()
        doi = doi_url.replace("https://doi.org/", "") if doi_url else ""
        year = item.get("publication_year")
        candidates.append(
            {
                "doi": doi,
                "title": cand_title,
                "journal": journal,
                "year": str(year) if year else "Not available",
                "score": score,
            }
        )

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def _title_needs_confirmation(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return False
    top = candidates[0]["score"]
    second = candidates[1]["score"] if len(candidates) > 1 else -1
    if top < 85:
        return True
    if len(candidates) > 1 and (top - second) <= 4:
        return True
    return False


def _fetch_wos_by_doi(doi: str) -> dict[str, Any] | None:
    wos_key = os.environ.get("WOS_API_KEY", "").strip()
    if not wos_key or not doi:
        return None

    try:
        response = requests.get(
            "https://api.clarivate.com/apis/wos-starter/v1/documents",
            params={"q": f"DO={doi}", "limit": 1, "db": "WOS"},
            headers={"X-ApiKey": wos_key},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        hits = response.json().get("hits", [])
        if not hits:
            return None
        top = hits[0]
        identifiers = top.get("identifiers", {}) if isinstance(top, dict) else {}
        return {
            "source": "WoS",
            "title": (top.get("title") or "").strip(),
            "journal": ((top.get("source") or {}).get("sourceTitle") or "").strip(),
            "pub_year": ((top.get("source") or {}).get("publishYear")),
            "doi": doi,
            "wos_ut": identifiers.get("ut", "") if isinstance(identifiers, dict) else "",
            "wos_record_url": top.get("url", "") if isinstance(top, dict) else "",
        }
    except requests.RequestException:
        return None


def _extract_external_ids(
    oa_result: dict[str, Any] | None,
    scopus_result: dict[str, Any] | None,
    wos_result: dict[str, Any] | None,
) -> dict[str, str]:
    scopus_eid = ((scopus_result or {}).get("id", "") or "").strip()
    scopus_link = ((scopus_result or {}).get("scopus_source_url", "") or "").strip()
    scopus_doc_id = _derive_scopus_doc_id(
        scopus_doc_id=((scopus_result or {}).get("scopus_doc_id", "") or "").strip(),
        scopus_eid=scopus_eid,
        scopus_link=scopus_link,
    )

    openalex_id_raw = ((oa_result or {}).get("id", "") or "").strip()
    openalex_id = openalex_id_raw.replace("https://openalex.org/", "") if openalex_id_raw else ""

    wos_ut = ((wos_result or {}).get("wos_ut", "") or "").strip()

    return {
        "scopus_eid": scopus_eid,
        "scopus_doc_id": scopus_doc_id,
        "openalex_id": openalex_id,
        "wos_ut": wos_ut,
    }


def _extract_links(
    doi: str,
    title: str,
    oa_result: dict[str, Any] | None,
    scopus_result: dict[str, Any] | None,
    wos_result: dict[str, Any] | None,
) -> dict[str, str]:
    doi_link = (oa_result or {}).get("doi_url", "")
    scopus_link = (scopus_result or {}).get("scopus_source_url", "")
    scopus_eid = ((scopus_result or {}).get("id", "") or "").strip()
    scopus_doc_id = _derive_scopus_doc_id(
        scopus_doc_id=((scopus_result or {}).get("scopus_doc_id", "") or "").strip(),
        scopus_eid=scopus_eid,
        scopus_link=scopus_link,
    )
    if scopus_doc_id:
        scopus_link = f"https://www.scopus.com/pages/publications/{scopus_doc_id}"

    openalex_link = (oa_result or {}).get("id", "")
    wos_link = (wos_result or {}).get("wos_record_url", "")

    if not wos_link:
        # Web of Science only exposes record URL with API entitlement;
        # fallback to smart-search is still useful for user navigation.
        wos_link = pure_metadata.build_web_of_science_search_url(doi=doi, title=title) or ""

    return {
        "doi": doi_link,
        "scopus": scopus_link,
        "openalex": openalex_link,
        "web_of_science": wos_link,
    }


def lookup_record(query: str, query_type: str) -> dict[str, Any]:
    doi, title = _parse_query(query, query_type)

    scopus_key = os.environ.get("SCOPUS_API_KEY", "").strip() or None
    openalex_key = os.environ.get("OPENALEX_API_KEY", "").strip() or None
    if not scopus_key or not openalex_key:
        resolved_scopus_key, resolved_openalex_key = pure_metadata.resolve_api_keys()
        if not scopus_key:
            scopus_key = (resolved_scopus_key or "").strip() or None
        if not openalex_key:
            openalex_key = (resolved_openalex_key or "").strip() or None

    if title:
        candidates = _resolve_title_candidates(title, openalex_key)
        if not candidates:
            raise ValueError("No matching records found for the provided title")
        if _title_needs_confirmation(candidates):
            return {
                "requires_confirmation": True,
                "message": "Multiple matches or uncertain title match. Please confirm one candidate before proceeding.",
                "candidates": candidates[:5],
            }
        best = candidates[0]
        doi = best.get("doi", "")
        title = best.get("title", "") or title

    with ThreadPoolExecutor(max_workers=2) as pool:
        oa_future = pool.submit(pure_metadata.fetch_openalex, doi=doi, title=title, api_key=openalex_key)
        scopus_future = pool.submit(pure_metadata.fetch_scopus, doi=doi, title=title, api_key=scopus_key)
        oa_result = oa_future.result()
        scopus_result = scopus_future.result()

    if not doi:
        doi = pure_metadata._pick([oa_result, scopus_result], "doi")  # pylint: disable=protected-access
    if not title:
        title = pure_metadata._pick([oa_result, scopus_result], "title")  # pylint: disable=protected-access

    wos_result = _fetch_wos_by_doi(doi=doi)

    sources = [s for s in [oa_result, scopus_result, wos_result] if s]
    resolution = {
        "doi": pure_metadata._pick(sources, "doi") or "Not available",  # pylint: disable=protected-access
        "title": pure_metadata._pick(sources, "title") or "Not available",  # pylint: disable=protected-access
        "journal": pure_metadata._pick(sources, "journal") or "Not available",  # pylint: disable=protected-access
        "year": str(pure_metadata._pick(sources, "pub_year") or "Not available"),  # pylint: disable=protected-access
    }

    structured_output = pure_metadata.generate_report(doi=doi, title=title, oa_result=oa_result, scopus_result=scopus_result)
    external_ids = _extract_external_ids(oa_result=oa_result, scopus_result=scopus_result, wos_result=wos_result)
    links = _extract_links(doi=doi, title=title, oa_result=oa_result, scopus_result=scopus_result, wos_result=wos_result)

    return {
        "requires_confirmation": False,
        "query": query,
        "resolved": resolution,
        "structured_output": structured_output,
        "external_ids": external_ids,
        "links": links,
        "sources": {
            "openalex": bool(oa_result),
            "scopus": bool(scopus_result),
            "wos": bool(wos_result),
        },
        "notes": {
            "scopus_key_used": bool(scopus_key),
            "openalex_key_used": bool(openalex_key),
            "wos_key_used": bool(os.environ.get("WOS_API_KEY", "").strip()),
        },
    }
