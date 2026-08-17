from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from rapidfuzz import fuzz

from .cache import SqliteResponseCache
from .excel_export import write_review_sheet
from .jobs import JobRegistry
from .schemas import SourceSelection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

import pure_metadata  # noqa: E402


def _normalize_record_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapped = {c.lower().strip(): c for c in df.columns}
    doi_col = mapped.get("doi")
    title_col = mapped.get("title")

    if title_col is None and doi_col is None:
        raise ValueError("Input file must include at least one of: title, doi")

    if title_col is None:
        df["title"] = ""
    else:
        df["title"] = df[title_col]

    if doi_col is None:
        df["doi"] = ""
    else:
        df["doi"] = df[doi_col]

    if "record_id" not in mapped:
        df["record_id"] = [f"REC-{idx + 1:05d}" for idx in range(len(df))]
    else:
        df["record_id"] = df[mapped["record_id"]]

    return df


def _fetch_crossref(doi: str, title: str) -> dict[str, Any] | None:
    if doi:
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        try:
            response = requests.get(url, params={"mailto": "research@ust.hk"}, timeout=30)
            if response.status_code == 200:
                msg = response.json().get("message", {})
                titles = msg.get("title", [])
                journal_parts = msg.get("container-title", [])
                authors = []
                for author in msg.get("author", []):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    full = " ".join(part for part in [given, family] if part)
                    authors.append({"name": full.strip()})

                year = None
                issued = msg.get("issued", {}).get("date-parts", [])
                if issued and issued[0]:
                    year = issued[0][0]

                return {
                    "source": "Crossref",
                    "doi": msg.get("DOI", ""),
                    "title": titles[0] if titles else "",
                    "journal": journal_parts[0] if journal_parts else "",
                    "pub_year": year,
                    "authors": authors,
                    "type": msg.get("type", ""),
                }
            if response.status_code == 404:
                return None
        except requests.RequestException:
            return None

    if title:
        try:
            response = requests.get(
                "https://api.crossref.org/works",
                params={"query.title": title, "rows": 5, "mailto": "research@ust.hk"},
                timeout=30,
            )
            if response.status_code != 200:
                return None
            items = response.json().get("message", {}).get("items", [])
            if not items:
                return None
            best = max(items, key=lambda item: fuzz.token_sort_ratio(title, " ".join(item.get("title", []))))
            matched_title = " ".join(best.get("title", []))
            if fuzz.token_sort_ratio(title, matched_title) < 75:
                return None
            return {
                "source": "Crossref",
                "doi": best.get("DOI", ""),
                "title": matched_title,
                "journal": " ".join(best.get("container-title", [])),
                "pub_year": (best.get("issued", {}).get("date-parts", [[None]])[0][0]),
                "authors": [
                    {"name": " ".join([a.get("given", ""), a.get("family", "")]).strip()}
                    for a in best.get("author", [])
                ],
                "type": best.get("type", ""),
            }
        except requests.RequestException:
            return None

    return None


def _fetch_wos(doi: str, title: str, api_key: str | None) -> dict[str, Any] | None:
    if not api_key:
        return None

    query_value = f"DO={doi}" if doi else f"TI=({title})"
    try:
        response = requests.get(
            "https://api.clarivate.com/apis/wos-starter/v1/documents",
            params={"q": query_value, "limit": 1, "db": "WOS"},
            headers={"X-ApiKey": api_key},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        hits = response.json().get("hits", [])
        if not hits:
            return None
        top = hits[0]
        return {
            "source": "WoS",
            "doi": doi,
            "title": top.get("title", ""),
            "journal": top.get("source", {}).get("sourceTitle", ""),
            "pub_year": top.get("source", {}).get("publishYear"),
            "authors": [{"name": name} for name in top.get("names", {}).get("authors", [])],
            "type": top.get("documentType", ""),
        }
    except requests.RequestException:
        return None


def _extract_compare_fields(source_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not source_payload:
        return {}
    author_names = []
    for author in source_payload.get("authors", []):
        name = author.get("name", "") if isinstance(author, dict) else str(author)
        if name:
            author_names.append(name)

    return {
        "title": source_payload.get("title", ""),
        "authors": author_names,
        "year": source_payload.get("pub_year"),
        "journal": source_payload.get("journal", ""),
        "doi": source_payload.get("doi", ""),
    }


def _compute_field_mismatches(comparison: dict[str, dict[str, Any]]) -> list[str]:
    mismatches: list[str] = []
    fields = ["title", "authors", "year", "journal", "doi"]
    for field_name in fields:
        values = []
        for source_data in comparison.values():
            value = source_data.get(field_name)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                values.append("|".join(value).lower())
            else:
                values.append(str(value).strip().lower())
        if len(set(values)) > 1:
            mismatches.append(field_name)
    return mismatches


def _update_duplicate_flags(rows: list[dict[str, Any]], threshold: int) -> None:
    for row in rows:
        row["is_duplicate"] = row.get("duplicate_score_max", 0) >= threshold


def _compute_duplicate_scores(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row["duplicate_score_max"] = 0

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            score = fuzz.token_sort_ratio(rows[i].get("title", ""), rows[j].get("title", ""))
            rows[i]["duplicate_score_max"] = max(rows[i]["duplicate_score_max"], score)
            rows[j]["duplicate_score_max"] = max(rows[j]["duplicate_score_max"], score)


def _derive_status(row: dict[str, Any]) -> str:
    if not row.get("doi"):
        return "missing"
    if row.get("is_duplicate"):
        return "duplicate"
    if row.get("mismatches"):
        return "discrepancy"
    return "verified"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(rows),
        "verified": 0,
        "discrepancies": 0,
        "duplicates": 0,
        "missing_doi": 0,
        "unresolved": 0,
    }
    for row in rows:
        status = row.get("status")
        if status == "verified":
            summary["verified"] += 1
        if status == "discrepancy":
            summary["discrepancies"] += 1
        if status == "duplicate":
            summary["duplicates"] += 1
        if not row.get("doi"):
            summary["missing_doi"] += 1
        if not row.get("comparison"):
            summary["unresolved"] += 1
    return summary


def run_validation_job(
    *,
    job_id: str,
    upload_path: Path,
    registry: JobRegistry,
    cache: SqliteResponseCache,
    data_dir: Path,
    sources: SourceSelection,
    force_refresh: bool,
    duplicate_threshold: int,
    scopus_api_key: str | None,
    wos_api_key: str | None,
) -> None:
    registry.update(job_id, status="running", message="Reading uploaded records", progress=0.05)

    if upload_path.suffix.lower() in {".xlsx", ".xls"}:
        dataframe = pd.read_excel(upload_path)
    else:
        dataframe = pd.read_csv(upload_path)

    dataframe = _normalize_record_columns(dataframe)
    total = len(dataframe)
    registry.update(job_id, total_records=total, message="Validating records", progress=0.1)

    cache_stats = {
        "openalex": {"hits": 0, "misses": 0, "bypassed": 0},
        "crossref": {"hits": 0, "misses": 0, "bypassed": 0},
        "scopus": {"hits": 0, "misses": 0, "bypassed": 0},
        "wos": {"hits": 0, "misses": 0, "bypassed": 0},
    }

    rows: list[dict[str, Any]] = []

    for idx, item in enumerate(dataframe.to_dict(orient="records"), start=1):
        doi = str(item.get("doi", "") or "").strip()
        title = str(item.get("title", "") or "").strip()

        row_result: dict[str, Any] = {
            "record_id": str(item.get("record_id", f"REC-{idx:05d}")),
            "title": title,
            "doi": doi,
            "comparison": {},
            "mismatches": [],
            "issues": [],
            "cache": {},
        }

        def resolve_source(source_name: str, fetcher):
            if force_refresh:
                cache_stats[source_name]["bypassed"] += 1
                row_result["cache"][source_name] = "bypass"
                payload = fetcher()
                cache.set(source_name, doi, title, payload)
                return payload

            lookup = cache.get(source_name, doi, title)
            row_result["cache"][source_name] = lookup.status
            if lookup.status == "hit":
                cache_stats[source_name]["hits"] += 1
                return lookup.payload

            cache_stats[source_name]["misses"] += 1
            payload = fetcher()
            cache.set(source_name, doi, title, payload)
            return payload

        fetch_tasks = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            if sources.openalex:
                fetch_tasks.append(("openalex", pool.submit(resolve_source, "openalex", lambda: pure_metadata.fetch_openalex(doi=doi, title=title))))
            if sources.crossref:
                fetch_tasks.append(("crossref", pool.submit(resolve_source, "crossref", lambda: _fetch_crossref(doi=doi, title=title))))
            if sources.scopus:
                fetch_tasks.append(("scopus", pool.submit(resolve_source, "scopus", lambda: pure_metadata.fetch_scopus(doi=doi, title=title, api_key=scopus_api_key))))
            if sources.wos:
                fetch_tasks.append(("wos", pool.submit(resolve_source, "wos", lambda: _fetch_wos(doi=doi, title=title, api_key=wos_api_key))))

            fetched: dict[str, Any] = {}
            for source_name, task in fetch_tasks:
                fetched[source_name] = task.result()

        comparison = {
            "openalex": _extract_compare_fields(fetched.get("openalex")),
            "crossref": _extract_compare_fields(fetched.get("crossref")),
            "scopus": _extract_compare_fields(fetched.get("scopus")),
            "wos": _extract_compare_fields(fetched.get("wos")),
        }

        comparison = {k: v for k, v in comparison.items() if v}
        row_result["comparison"] = comparison

        if not row_result["doi"]:
            for source_name in ["openalex", "crossref", "scopus", "wos"]:
                candidate = (fetched.get(source_name) or {}).get("doi", "")
                if candidate:
                    row_result["doi"] = candidate
                    break

        row_result["mismatches"] = _compute_field_mismatches(comparison)
        if row_result["mismatches"]:
            row_result["issues"].append("Field mismatches: " + ", ".join(row_result["mismatches"]))
        if not row_result["doi"]:
            row_result["issues"].append("Missing DOI")
        if not comparison:
            row_result["issues"].append("No source returned a usable record")

        rows.append(row_result)

        progress = 0.1 + (0.75 * (idx / max(total, 1)))
        registry.update(
            job_id,
            processed_records=idx,
            total_records=total,
            progress=progress,
            message=f"Processed {idx}/{total} records",
        )

    _compute_duplicate_scores(rows)
    _update_duplicate_flags(rows, threshold=duplicate_threshold)
    for row in rows:
        row["status"] = _derive_status(row)

    summary = _summarize(rows)

    registry.update(job_id, progress=0.9, message="Generating Excel review sheet")
    result_path = data_dir / "jobs" / f"{job_id}.json"
    excel_path = data_dir / "jobs" / f"{job_id}.xlsx"
    write_review_sheet(rows, excel_path)

    payload = {
        "job_id": job_id,
        "duplicate_threshold": duplicate_threshold,
        "summary": summary,
        "cache_stats": cache_stats,
        "rows": rows,
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    registry.update(
        job_id,
        status="completed",
        progress=1.0,
        message="Completed",
        result_path=result_path,
        excel_path=excel_path,
        processed_records=total,
        total_records=total,
    )


def spawn_validation_thread(*, target, kwargs) -> threading.Thread:
    thread = threading.Thread(target=target, kwargs=kwargs, daemon=True)
    thread.start()
    return thread
