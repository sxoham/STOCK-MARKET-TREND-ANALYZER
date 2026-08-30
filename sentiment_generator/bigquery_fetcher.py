import os
import re
import html
import datetime
import zoneinfo
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
import pandas as pd

from .config import (
    MARKET_TIMEZONE, STOCKS, COMPANY_ALIASES, BIGQUERY_CANDIDATE_TERMS, DATA_DIR
)
from .cache import generate_article_id
from .news_fetcher import NewsFetcher, is_article_url

logger = logging.getLogger(__name__)

# Output path for the isolated January 2024 Reliance POC staging file
BIGQUERY_POC_RELIANCE_PARQUET = os.path.join(DATA_DIR, "bigquery_poc_reliance_2024_01.parquet")


class BigQueryGKGExtractor:
    """
    Ingestion adapter for GDELT Global Knowledge Graph (GKG) 2.0 via Google BigQuery.

    Architecture & Invariants:
    - Queries `gdelt-bq.gdeltv2.gkg_partitioned` with strict partition pruning on _PARTITIONDATE.
    - Extracts genuine article titles from the <PAGE_TITLE> XML tag in XMLExtras (available since Sep 2019).
    - Strictly rejects records with missing or empty <PAGE_TITLE> (never substitutes metadata/URLs).
    - Preserves timestamp provenance: GKG DATE is labeled as 'gdelt_gkg_observation' unless
      <PAGE_PRECISEPUBTIMESTAMP> is available.
    - Reuses the production NSE trading session cutoff logic (15:30:00 IST) and calendar from NewsFetcher.
    - Reuses production company aliases and contextual regex matching from NewsFetcher.
    - Deterministically generates article_id for seamless compatibility and deduplication with raw_articles.
    """

    def __init__(self, trading_calendar: List[str]):
        self.trading_calendar = sorted(list(set(trading_calendar)))
        self.news_fetcher = NewsFetcher(trading_calendar=self.trading_calendar)
        self.tz_utc = zoneinfo.ZoneInfo("UTC")
        self.tz_market = zoneinfo.ZoneInfo(MARKET_TIMEZONE)

        # Audit & telemetry counters
        self.stats = {
            "rows_scanned": 0,
            "candidates_extracted": 0,
            "rejected_missing_title": 0,
            "rejected_non_article_url": 0,
            "rejected_company_match": 0,
            "rejected_invalid_timestamp": 0,
            "rejected_no_trading_session": 0,
            "duplicates_removed": 0,
            "accepted_articles": 0,
            "before_1530_count": 0,
            "after_1530_count": 0,
            "rollover_count": 0,
        }

    @staticmethod
    def generate_query(
        ticker: str,
        start_date: str,
        end_date: str,
        project_table: str = "gdelt-bq.gdeltv2.gkg_partitioned",
        xml_column: str = "Extras"
    ) -> str:
        """
        Constructs an optimized, partition-pruned BigQuery SQL query for a specific ticker and date range.
        Selects only the required columns and filters candidates as early as practical.
        """
        if ticker in BIGQUERY_CANDIDATE_TERMS:
            aliases = BIGQUERY_CANDIDATE_TERMS[ticker]
        else:
            aliases = COMPANY_ALIASES.get(ticker, [STOCKS.get(ticker, "")])

        # Build regex patterns for organizations, persons, and page title
        escaped_aliases = [re.escape(a) for a in aliases if len(a.strip()) > 0]
        alias_pattern = "|".join(escaped_aliases)

        query = f"""
SELECT
  GKGRECORDID,
  DATE,
  SourceCommonName,
  DocumentIdentifier,
  V2Organizations,
  V2Persons,
  REGEXP_EXTRACT({xml_column}, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS page_title,
  REGEXP_EXTRACT({xml_column}, r'<PAGE_PRECISEPUBTIMESTAMP>(.*?)</PAGE_PRECISEPUBTIMESTAMP>') AS precise_pub_time
FROM
  `{project_table}`
WHERE
  _PARTITIONDATE BETWEEN '{start_date}' AND '{end_date}'
  AND (
    REGEXP_CONTAINS(V2Organizations, r'(?i)\\b({alias_pattern})\\b')
    OR REGEXP_CONTAINS(V2Persons, r'(?i)\\b({alias_pattern})\\b')
    OR REGEXP_CONTAINS({xml_column}, r'(?i)<PAGE_TITLE>.*?\\b({alias_pattern})\\b.*?</PAGE_TITLE>')
  )
  AND REGEXP_CONTAINS({xml_column}, r'<PAGE_TITLE>.+?</PAGE_TITLE>')
ORDER BY
  DATE ASC
""".strip()
        return query

    def parse_gkg_record(
        self,
        record: Dict[str, Any],
        ticker: str
    ) -> Optional[Dict[str, Any]]:
        """
        Parses and validates a single BigQuery GKG row into a normalized raw article dict.
        Returns None if rejected with reason tracked in stats.
        """
        self.stats["candidates_extracted"] += 1

        # 1. Headline Extraction & Validation
        raw_title = record.get("page_title")
        if not raw_title or not str(raw_title).strip():
            self.stats["rejected_missing_title"] += 1
            return None

        # Clean & HTML-unescape title
        headline = html.unescape(str(raw_title).strip())
        # Clean internal whitespace
        headline = " ".join(headline.split())
        if not headline:
            self.stats["rejected_missing_title"] += 1
            return None

        # 1b. Document / URL Quality Validation (Filter CMS taxonomy, tag, category, author, search index pages)
        doc_url = record.get("DocumentIdentifier")
        if not doc_url or not is_article_url(str(doc_url)):
            self.stats["rejected_non_article_url"] += 1
            return None

        # 2. Timestamp Parsing (UTC -> Asia/Kolkata IST)
        date_raw = record.get("DATE")
        if not date_raw:
            self.stats["rejected_invalid_timestamp"] += 1
            return None

        clean_ts = re.sub(r'[^0-9]', '', str(date_raw))
        if len(clean_ts) != 14:
            self.stats["rejected_invalid_timestamp"] += 1
            return None

        try:
            dt_utc = datetime.datetime.strptime(clean_ts, "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
            ist_dt = dt_utc.astimezone(self.tz_market)
        except Exception:
            self.stats["rejected_invalid_timestamp"] += 1
            return None

        # 3. Company / Entity Relevance Verification (Headline is authoritative + Date-Aware)
        if not self.news_fetcher.is_relevant_to_company(headline, ticker, article_datetime=ist_dt):
            self.stats["rejected_company_match"] += 1
            return None
        match_reason = "Direct headline match with company alias"

        # Determine timestamp basis & published_at
        precise_pub = record.get("precise_pub_time")
        if precise_pub and len(re.sub(r'[^0-9]', '', str(precise_pub))) == 14:
            pub_clean = re.sub(r'[^0-9]', '', str(precise_pub))
            try:
                pub_utc = datetime.datetime.strptime(pub_clean, "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
                published_at_iso = pub_utc.astimezone(self.tz_market).isoformat()
                timestamp_basis = "publisher_published_at"
            except Exception:
                published_at_iso = None
                timestamp_basis = "gdelt_gkg_observation"
        else:
            published_at_iso = None
            timestamp_basis = "gdelt_gkg_observation"

        # 4. Trading Session Mapping
        trading_date = self.news_fetcher.map_to_nse_trading_session(ist_dt)
        if not trading_date:
            self.stats["rejected_no_trading_session"] += 1
            return None

        # Telemetry: Track before/after 15:30 IST
        cutoff = datetime.time(15, 30, 0)
        if ist_dt.time() < cutoff:
            self.stats["before_1530_count"] += 1
        else:
            self.stats["after_1530_count"] += 1

        cal_date_str = ist_dt.strftime("%Y-%m-%d")
        if trading_date != cal_date_str:
            self.stats["rollover_count"] += 1

        # 5. URL & Document Normalization
        raw_url = record.get("DocumentIdentifier") or ""
        url = self.news_fetcher.normalize_url(raw_url) if raw_url else None
        source = str(record.get("SourceCommonName") or "GDELT_GKG").strip()
        company = STOCKS.get(ticker, ticker)

        # 6. Deterministic Article ID Generation
        art_id = generate_article_id(
            ticker=ticker,
            trading_date=trading_date,
            headline=headline,
            url=url
        )

        return {
            "article_id": art_id,
            "ticker": ticker,
            "company": company,
            "headline": headline,
            "source": source,
            "url": url,
            "published_at": published_at_iso,
            "seen_at": ist_dt.isoformat(),
            "source_timestamp": clean_ts,
            "trading_date": trading_date,
            "timestamp_basis": timestamp_basis,
            "gkg_record_id": record.get("GKGRECORDID"),
            "company_match_reason": match_reason,
            "finbert_label": None,
            "finbert_confidence": None,
            "sentiment_score": None,
        }

    def process_records(
        self,
        records: List[Dict[str, Any]],
        ticker: str
    ) -> List[Dict[str, Any]]:
        """
        Parses, validates, filters, and deduplicates a batch of BigQuery GKG records.
        """
        self.stats["rows_scanned"] = len(records)
        raw_candidates = []

        for rec in records:
            parsed = self.parse_gkg_record(rec, ticker)
            if parsed:
                raw_candidates.append(parsed)

        # Deduplicate using canonical URL / normalized headline rules
        deduped = self.news_fetcher._deduplicate_articles(raw_candidates)
        self.stats["duplicates_removed"] = len(raw_candidates) - len(deduped)
        self.stats["accepted_articles"] = len(deduped)

        return deduped

    def export_staging_parquet(
        self,
        articles: List[Dict[str, Any]],
        output_path: str = BIGQUERY_POC_RELIANCE_PARQUET
    ):
        """
        Exports the candidate articles to an isolated staging Parquet file for auditing.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df = pd.DataFrame(articles)
        df.to_parquet(output_path, index=False)
        logger.info(f"Exported {len(df)} staging articles to {output_path}")
        return output_path
