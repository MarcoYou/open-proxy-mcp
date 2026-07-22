"""Fast, indexed company-name resolution for Korean listed companies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import difflib
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_MARKET_CAP_PATH = _ROOT / "data/market_cap.json"
_LEGAL_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated",
    "ltd", "limited", "plc",
}
_KOREAN_LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s*[\(（]주[\)）]\s*|\s*㈜\s*|\s*주식회사\s*)$",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣]+")


def _nfkc_casefold(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def normalize_raw(value: str) -> str:
    """Case-insensitive official-name key without destructive punctuation changes."""
    return re.sub(r"\s+", " ", _nfkc_casefold(value))


def name_tokens(value: str) -> tuple[str, ...]:
    """Search tokens shared by Korean, English, and mixed company names."""
    text = _KOREAN_LEGAL_SUFFIX_RE.sub("", _nfkc_casefold(value))
    text = text.replace("&", " and ").replace("앤", " and ")
    tokens = _NON_WORD_RE.sub(" ", text).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return tuple(tokens)


def normalize_phrase(value: str) -> str:
    return " ".join(name_tokens(value))


def normalize_compact(value: str) -> str:
    return "".join(name_tokens(value))


@dataclass(frozen=True, slots=True)
class MarketContext:
    market_caps: dict[str, int]
    active_tickers: frozenset[str] | None = None
    as_of_date: str = ""
    source: str = "none"


def _local_market_context() -> MarketContext:
    try:
        raw = json.loads(_LOCAL_MARKET_CAP_PATH.read_text(encoding="utf-8"))
        caps = {
            str(ticker): int(item.get("market_cap_won") or 0)
            for ticker, item in raw.items()
            if isinstance(item, dict) and item.get("market_cap_won")
        }
        # This file is a top-company sample, not a complete listing registry.
        return MarketContext(market_caps=caps, source="local_popularity_prior")
    except (OSError, ValueError, TypeError):
        return MarketContext(market_caps={})


def _database_market_context() -> MarketContext | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=1) as conn:
            latest = conn.execute("SELECT MAX(bas_dd) FROM krx_weekly").fetchone()
            as_of_date = str(latest[0]) if latest and latest[0] else ""
            if not as_of_date:
                return None
            rows = conn.execute(
                "SELECT isu_cd, mktcap, mkt FROM krx_weekly WHERE bas_dd=%s",
                (as_of_date,),
            ).fetchall()
        caps = {str(ticker): int(cap or 0) for ticker, cap, _market in rows if ticker}
        market_counts: dict[str, int] = {}
        for _ticker, _cap, market in rows:
            market_counts[str(market or "").upper()] = market_counts.get(str(market or "").upper(), 0) + 1
        # A partial snapshot must never be treated as the complete active universe.
        fresh = False
        try:
            fresh = date.today() - datetime.strptime(as_of_date, "%Y%m%d").date() <= timedelta(days=14)
        except ValueError:
            pass
        complete = market_counts.get("KOSPI", 0) >= 700 and market_counts.get("KOSDAQ", 0) >= 1_200
        active = frozenset(caps) if complete and fresh else None
        return MarketContext(caps, active, as_of_date, "krx_weekly")
    except Exception:
        return None


_market_context: MarketContext | None = None
_market_context_lock = asyncio.Lock()
_market_context_loaded_at = 0.0
_MARKET_CONTEXT_TTL_SECONDS = 3_600


async def load_market_context() -> MarketContext:
    global _market_context, _market_context_loaded_at
    if _market_context is not None and time.monotonic() - _market_context_loaded_at < _MARKET_CONTEXT_TTL_SECONDS:
        return _market_context
    async with _market_context_lock:
        if _market_context is None or time.monotonic() - _market_context_loaded_at >= _MARKET_CONTEXT_TTL_SECONDS:
            database = await asyncio.to_thread(_database_market_context)
            local = _local_market_context()
            if database:
                merged = dict(local.market_caps)
                merged.update(database.market_caps)
                _market_context = MarketContext(
                    merged,
                    database.active_tickers,
                    database.as_of_date,
                    database.source,
                )
            else:
                _market_context = local
            _market_context_loaded_at = time.monotonic()
    return _market_context


class CompanyResolver:
    """Immutable lookup indexes built once per corp-code master revision."""

    _STRONG_KINDS = {"ticker", "corp_code", "official", "alias", "normalized"}

    def __init__(
        self,
        corps: list[dict[str, Any]],
        aliases: dict[str, str],
        market: MarketContext | None = None,
    ) -> None:
        self.corps = corps
        self.aliases = aliases
        self.market = market or MarketContext({})
        self._ticker: dict[str, list[int]] = {}
        self._corp_code: dict[str, list[int]] = {}
        self._official: dict[str, list[int]] = {}
        self._phrase: dict[str, list[int]] = {}
        self._compact: dict[str, list[int]] = {}
        self._tokens: dict[str, set[int]] = {}
        self._alias: dict[str, list[int]] = {}
        self._listed_ids: set[int] = set()
        self._build_indexes()

    @staticmethod
    def _add(index: dict[str, list[int]], key: str, row_id: int) -> None:
        if key:
            index.setdefault(key, []).append(row_id)

    def _build_indexes(self) -> None:
        canonical_names: dict[str, list[int]] = {}
        for row_id, corp in enumerate(self.corps):
            ticker = str(corp.get("stock_code") or "").strip()
            corp_code = str(corp.get("corp_code") or "").strip()
            if ticker:
                self._listed_ids.add(row_id)
                self._add(self._ticker, ticker, row_id)
            self._add(self._corp_code, corp_code, row_id)
            if not ticker:
                continue

            for name in (corp.get("corp_name", ""), corp.get("corp_eng_name", "")):
                if not name:
                    continue
                raw = normalize_raw(name)
                phrase = normalize_phrase(name)
                compact = normalize_compact(name)
                self._add(self._official, raw, row_id)
                self._add(self._phrase, phrase, row_id)
                self._add(self._compact, compact, row_id)
                for token in name_tokens(name):
                    self._tokens.setdefault(token, set()).add(row_id)
                    if re.fullmatch(r"[가-힣]{3,}", token):
                        for end in range(2, min(len(token), 8) + 1):
                            self._tokens.setdefault(token[:end], set()).add(row_id)
            canonical_names.setdefault(normalize_raw(corp.get("corp_name", "")), []).append(row_id)

        for alias, target in self.aliases.items():
            target_ids = canonical_names.get(normalize_raw(target), [])
            for key in {
                normalize_raw(alias), normalize_phrase(alias), normalize_compact(alias)
            }:
                if key and target_ids:
                    self._alias.setdefault(key, []).extend(target_ids)

    def _active_preferred(
        self,
        row_ids: set[int] | list[int],
        *,
        numeric: bool,
        kind: str,
    ) -> list[int]:
        ids = list(dict.fromkeys(row_ids))
        if numeric:
            return ids
        active = self.market.active_tickers
        if active is not None:
            current = [i for i in ids if self.corps[i].get("stock_code") in active]
            if current:
                return current
            # KRX weekly covers KOSPI/KOSDAQ. Preserve exact KONEX or newly listed
            # companies, but never revive inactive companies for inferred searches.
            return ids if kind in self._STRONG_KINDS else []
        listed = [i for i in ids if i in self._listed_ids]
        return listed or ids

    def _rank_key(self, row_id: int) -> tuple[int, int, int]:
        corp = self.corps[row_id]
        ticker = str(corp.get("stock_code") or "")
        cap = int(self.market.market_caps.get(ticker, 0))
        modified = int(corp.get("modify_date") or 0)
        return cap, 1 if ticker else 0, modified

    def _decorate(self, row_id: int, kind: str, inferred: bool, alternatives: int) -> dict[str, Any]:
        corp = dict(self.corps[row_id])
        ticker = str(corp.get("stock_code") or "")
        cap = int(self.market.market_caps.get(ticker, 0))
        corp["_resolution"] = {
            "match_kind": kind,
            "inferred": inferred,
            "auto_selected": False,
            "candidate_count": alternatives + 1,
            "market_cap_won": cap or None,
            "market_data_as_of": self.market.as_of_date or None,
            "market_data_source": self.market.source,
            "ranking_signal": "market_cap" if self.market.as_of_date else "local_popularity_prior",
            "active_registry_used": self.market.active_tickers is not None,
            "dominant": False,
            "strong_disambiguated": False,
        }
        return corp

    def search(self, query: str) -> list[dict[str, Any]]:
        raw_query = (query or "").strip()
        if not raw_query:
            return []
        numeric = raw_query.isdigit()
        row_ids: list[int] = []
        kind = ""

        if re.fullmatch(r"\d{6}", raw_query):
            row_ids, kind = self._ticker.get(raw_query, []), "ticker"
        elif re.fullmatch(r"\d{8}", raw_query):
            row_ids, kind = self._corp_code.get(raw_query, []), "corp_code"
        else:
            raw = normalize_raw(raw_query)
            phrase = normalize_phrase(raw_query)
            compact = normalize_compact(raw_query)
            alias_ids = self._alias.get(raw) or self._alias.get(phrase) or self._alias.get(compact)
            if alias_ids:
                # Curated aliases include intentional historical-name redirects.
                row_ids, kind = alias_ids, "alias"
            elif raw in self._official:
                row_ids, kind = self._official[raw], "official"
            else:
                row_ids = self._phrase.get(phrase) or self._compact.get(compact) or []
                if row_ids:
                    kind = "normalized"

            query_tokens = name_tokens(raw_query)
            # A one-word brand such as "Hyundai" also equals the legal-suffix-stripped
            # name "HYUNDAI CORPORATION". Treat it as a brand search when the token is
            # shared by more companies; raw official names and curated aliases already won.
            if kind == "normalized" and len(query_tokens) == 1:
                brand_ids = self._tokens.get(query_tokens[0], set())
                if len(brand_ids) > len(row_ids):
                    row_ids, kind = list(brand_ids), "token"

            if not row_ids:
                token_sets = [self._tokens.get(token, set()) for token in query_tokens]
                if token_sets and all(token_sets):
                    row_ids = list(set.intersection(*token_sets))
                    kind = "token"

            if not row_ids and compact:
                # Korean brand fragments are not whitespace-tokenizable. This fallback scans
                # only the in-memory master and runs only after every indexed path missed.
                row_ids = [
                    i for i in self._listed_ids
                    for corp in (self.corps[i],)
                    if compact in normalize_compact(corp.get("corp_name", ""))
                    or compact in normalize_compact(corp.get("corp_eng_name", ""))
                ]
                if row_ids:
                    kind = "substring"

            if not row_ids and len(compact) >= 5:
                close_keys = difflib.get_close_matches(compact, self._compact, n=5, cutoff=0.88)
                if close_keys:
                    row_ids = [row_id for key in close_keys for row_id in self._compact[key]]
                    kind = "fuzzy"

        preferred = self._active_preferred(row_ids, numeric=numeric, kind=kind)
        ranked = sorted(preferred, key=self._rank_key, reverse=True)
        if not ranked:
            return []

        inferred = kind not in self._STRONG_KINDS
        auto_selected = False
        if inferred and len(ranked) > 1:
            top_cap = self._rank_key(ranked[0])[0]
            second_cap = self._rank_key(ranked[1])[0]
            auto_selected = top_cap > 0
            dominant = bool(self.market.as_of_date) and top_cap > 0 and (
                second_cap == 0 or top_cap >= second_cap * 1.5
            )
        elif inferred:
            auto_selected = True
            dominant = True
        else:
            dominant = False

        results = [self._decorate(i, kind, inferred, len(ranked) - 1) for i in ranked]
        if results:
            results[0]["_resolution"]["auto_selected"] = auto_selected
            results[0]["_resolution"]["dominant"] = dominant
            if not inferred and len(ranked) > 1:
                korean_names = {normalize_raw(self.corps[i].get("corp_name", "")) for i in ranked}
                top_cap = self._rank_key(ranked[0])[0]
                second_cap = self._rank_key(ranked[1])[0]
                results[0]["_resolution"]["strong_disambiguated"] = (
                    len(korean_names) == 1 and top_cap > 0 and second_cap == 0
                )
        return results


_resolver: CompanyResolver | None = None
_resolver_source_id: int | None = None
_resolver_market_id: int | None = None
_resolver_lock = threading.Lock()


async def get_company_resolver(
    corps: list[dict[str, Any]], aliases: dict[str, str]
) -> CompanyResolver:
    global _resolver, _resolver_source_id, _resolver_market_id
    source_id = id(corps)
    market = await load_market_context()
    market_id = id(market)
    if _resolver is not None and _resolver_source_id == source_id and _resolver_market_id == market_id:
        return _resolver
    with _resolver_lock:
        if _resolver is None or _resolver_source_id != source_id or _resolver_market_id != market_id:
            _resolver = CompanyResolver(corps, aliases, market)
            _resolver_source_id = source_id
            _resolver_market_id = market_id
    return _resolver
