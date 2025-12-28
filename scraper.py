"""
Honey Store Data Scraper
Scrapes store information from Honey's public API endpoints
"""

import requests
import json
import time
from urllib.parse import urlencode
from typing import Dict, List, Optional
import csv
from datetime import datetime
import sqlite3
import hashlib
import argparse


class HoneyScraper:
    """Scraper for Honey store data"""

    BASE_URL = "https://d.joinhoney.com"

    def __init__(
            self,
            delay: float = 0.5,
            db_path: str = "honey_stores.db",
            worker_id: int = 0,
            num_workers: int = 1
    ):
        self.delay = delay
        self.db_path = db_path
        self.worker_id = worker_id
        self.num_workers = num_workers

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self._init_database()

    def _domain_belongs_to_this_worker(self, domain: str) -> bool:
        # Stable hash -> int
        h = hashlib.md5(domain.encode("utf-8")).hexdigest()
        shard = int(h, 16) % self.num_workers
        return shard == self.worker_id


    def _init_database(self):
        """Initialize SQLite database with schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create stores table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                store_id TEXT PRIMARY KEY,
                domain TEXT,
                partial_url TEXT,
                name TEXT,
                label TEXT,
                country TEXT,
                url TEXT,
                logo_url TEXT,
                active INTEGER,
                supported INTEGER,
                support_stage TEXT,
                created INTEGER,
                updated INTEGER,
                checked INTEGER,
                score INTEGER,
                shoppers_24h INTEGER,
                shoppers_30d INTEGER,
                shoppers_change INTEGER,
                num_savings_24h INTEGER,
                num_savings_30d INTEGER,
                avg_savings_24h REAL,
                avg_savings_30d REAL,
                metadata TEXT,
                affiliate_url TEXT,
                affiliate_restrictions TEXT,
                ugc_allowed INTEGER,
                free_shipping_threshold REAL,
                force_js_redirect INTEGER,
                launchpad_pathname TEXT,
                raw_json TEXT,
                store_last_refreshed INTEGER
            )
        """)

        # Create coupons table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT,
            coupon_key TEXT,            -- NEW
            code TEXT,
            deal_id TEXT,
            description TEXT,
            created INTEGER,
            expires INTEGER,
            exclusive INTEGER,
            hidden INTEGER,
            restrictions TEXT,
            rank INTEGER,
            applied_acc_count INTEGER,
            applied_acc_last_ts INTEGER,
            applied_acc_last_discount REAL,
            url TEXT,
            meta_json TEXT,
            sources_json TEXT,
            tags_json TEXT,
            FOREIGN KEY (store_id) REFERENCES stores(store_id),
            UNIQUE(store_id, coupon_key) -- NEW

            )
        """)

        # Create partial_urls table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS partial_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT,
                domain TEXT,
                partial_url TEXT,
                FOREIGN KEY (store_id) REFERENCES stores(store_id)
            )
        """)

        # Create scraped_domains tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scraped_domains (
                domain TEXT PRIMARY KEY,
                scraped_at INTEGER,
                store_count INTEGER
            )
        """)

        # Create coupon usage reports table (user-generated data)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coupon_usage_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coupon_id INTEGER NOT NULL,
                store_id TEXT NOT NULL,
                code TEXT NOT NULL,
                worked INTEGER NOT NULL,
                amount_saved REAL,
                amount_spent REAL,
                notes TEXT,
                reported_at INTEGER NOT NULL,
                FOREIGN KEY (coupon_id) REFERENCES coupons(id),
                FOREIGN KEY (store_id) REFERENCES stores(store_id)
            )
        """)

        # Create indices for better query performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_domain ON stores(domain)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_country ON stores(country)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_active ON stores(active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coupons_store ON coupons(store_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_partial_urls_store ON partial_urls(store_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_reports_coupon ON coupon_usage_reports(coupon_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_reports_store ON coupon_usage_reports(store_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_reports_code ON coupon_usage_reports(code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_last_refreshed ON stores(store_last_refreshed)")

        conn.commit()
        conn.close()
        print(f"Database initialized: {self.db_path}")

    def _store_exists(self, store_id: str) -> bool:
        """Check if store already exists in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM stores WHERE store_id = ?", (store_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def _domain_scraped(self, domain: str) -> bool:
        """Check if domain has been scraped"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM scraped_domains WHERE domain = ?", (domain,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def _save_store_to_db(
            self,
            domain: Optional[str],
            store_id: str,
            partial_url: Optional[str],
            details: Dict,
            *,
            save_store: bool = True,
            save_coupons: bool = True,
            save_partial_urls: bool = True,
    ):
        """
        Save store data to database.

        Use flags to control what gets written:
          - Full ingest: save_store=True, save_coupons=True, save_partial_urls=True
          - Coupon refresh: save_store=False, save_coupons=True, save_partial_urls=False
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 1) Store row (optional)
            if save_store:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO stores (
                        store_id, domain, partial_url, name, label, country, url, logo_url,
                        active, supported, support_stage, created, updated, checked, score,
                        shoppers_24h, shoppers_30d, shoppers_change, num_savings_24h, num_savings_30d,
                        avg_savings_24h, avg_savings_30d, metadata, affiliate_url, affiliate_restrictions,
                        ugc_allowed, free_shipping_threshold, force_js_redirect, launchpad_pathname, raw_json,
                        store_last_refreshed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        store_id,
                        domain,
                        partial_url,
                        details.get("name"),
                        details.get("label"),
                        details.get("country"),
                        details.get("url"),
                        details.get("logoUrl"),
                        1 if details.get("active") else 0,
                        1 if details.get("supported") else 0,
                        details.get("supportStage"),
                        details.get("created"),
                        details.get("updated"),
                        details.get("checked"),
                        details.get("score"),
                        details.get("shoppers24h"),
                        details.get("shoppers30d"),
                        details.get("shoppersChange"),
                        details.get("numSavings24h"),
                        details.get("numSavings30d"),
                        details.get("avgSavings24h"),
                        details.get("avgSavings30d"),
                        details.get("metadata"),
                        details.get("affiliateURL"),
                        details.get("affiliateRestrictions"),
                        1 if details.get("ugcAllowed") else 0,
                        details.get("freeShippingThreshold"),
                        1 if details.get("forceJsRedirect") else 0,
                        details.get("launchpadPathname"),
                        json.dumps(details),
                        int(time.time() * 1000),
                    ),
                )

            # 2) Coupons (optional)
            if save_coupons:
                # Upsert coupons
                for coupon in details.get("publicCoupons", []):
                    ckey = self._coupon_key(coupon)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO coupons (
                            store_id, coupon_key, code, deal_id, description, created, expires,
                            exclusive, hidden, restrictions, rank, applied_acc_count,
                            applied_acc_last_ts, applied_acc_last_discount, url,
                            meta_json, sources_json, tags_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            store_id,
                            ckey,
                            coupon.get("code"),
                            coupon.get("dealId"),
                            coupon.get("description"),
                            coupon.get("created"),
                            coupon.get("expires"),
                            1 if coupon.get("exclusive") else 0,
                            1 if coupon.get("hidden") else 0,
                            coupon.get("restrictions"),
                            coupon.get("rank"),
                            coupon.get("applied_acc_count"),
                            coupon.get("applied_acc_last_ts"),
                            coupon.get("applied_acc_last_discount"),
                            coupon.get("url"),
                            json.dumps(coupon.get("meta", {})),
                            json.dumps(coupon.get("sources", [])),
                            json.dumps(coupon.get("tags", [])),
                        ),
                    )

                # Always bump refresh timestamp when we do a coupon write
                cursor.execute(
                    "UPDATE stores SET store_last_refreshed = ? WHERE store_id = ?",
                    (int(time.time() * 1000), store_id),
                )

            # 3) Partial URLs (optional)
            if save_partial_urls:
                cursor.execute("DELETE FROM partial_urls WHERE store_id = ?", (store_id,))
                for pu in details.get("partialUrls", []):
                    cursor.execute(
                        """
                        INSERT INTO partial_urls (store_id, domain, partial_url)
                        VALUES (?, ?, ?)
                        """,
                        (store_id, pu.get("domain"), pu.get("partialURL")),
                    )

            conn.commit()

        except Exception as e:
            conn.rollback()
            print(f"Error saving store {store_id} to database: {e}")

        finally:
            conn.close()

    def _mark_domain_scraped(self, domain: str, store_count: int):
        """Mark domain as scraped"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO scraped_domains (domain, scraped_at, store_count)
            VALUES (?, ?, ?)
        """, (domain, int(time.time() * 1000), store_count))
        conn.commit()
        conn.close()

    def get_supported_domains(self) -> List[str]:
        """
        Fetch all supported domains from Honey
        
        Returns:
            List of domain strings
        """
        url = f"{self.BASE_URL}/v2/stores/partials/supported-domains"
        print(f"Fetching supported domains from {url}...")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            domains = response.json()
            print(f"Found {len(domains)} supported domains")
            return domains
        except Exception as e:
            print(f"Error fetching domains: {e}")
            return []

    def get_store_ids_by_domain(self, domain: str) -> List[Dict]:
        """
        Get store IDs for a specific domain
        
        Args:
            domain: Domain name (e.g., "amazon.de")
            
        Returns:
            List of dicts with storeId and partialURL
        """
        variables = json.dumps({"domain": domain})
        params = {
            "operationName": "ext_getStorePartialsByDomain",
            "variables": variables
        }
        url = f"{self.BASE_URL}/v3?{urlencode(params)}"

        max_retries = 3
        retry_delay = self.delay

        for attempt in range(max_retries):
            try:
                time.sleep(retry_delay)
                response = self.session.get(url, timeout=30)

                # Check for rate limiting
                if response.status_code == 429:
                    retry_delay *= 2  # Exponential backoff
                    print(f"  ⚠️ Rate limited. Waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(retry_delay)
                    continue

                response.raise_for_status()
                data = response.json()

                if "data" in data and "getPartialURLsByDomain" in data["data"]:
                    return data["data"]["getPartialURLsByDomain"]
                return []

            except requests.exceptions.Timeout:
                print(f"  ⚠️ Timeout for {domain}. Retry {attempt + 1}/{max_retries}...")
                retry_delay *= 1.5
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            except requests.exceptions.RequestException as e:
                print(f"  ⚠️ Request error for {domain}: {e}. Retry {attempt + 1}/{max_retries}...")
                retry_delay *= 1.5
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            except Exception as e:
                print(f"Error fetching store IDs for {domain}: {e}")
                break

        return []

    def get_store_details(self, store_id: str, max_ugc: int = 3, success_count: int = 1) -> Optional[Dict]:
        """
        Get detailed store information by store ID
        
        Args:
            store_id: Store ID
            max_ugc: Maximum user generated content to return
            success_count: Success count parameter
            
        Returns:
            Store details dict or None
        """
        variables = json.dumps({
            "storeId": store_id,
            "maxUGC": max_ugc,
            "successCount": success_count
        })
        params = {
            "operationName": "ext_getStoreById",
            "variables": variables,
            "operationVersion": "18"
        }
        url = f"{self.BASE_URL}/v3?{urlencode(params)}"

        max_retries = 3
        retry_delay = self.delay

        for attempt in range(max_retries):
            try:
                time.sleep(retry_delay)
                response = self.session.get(url, timeout=30)

                # Check for rate limiting
                if response.status_code == 429:
                    retry_delay *= 2  # Exponential backoff
                    print(f"    ⚠️ Rate limited. Waiting {retry_delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(retry_delay)
                    continue

                response.raise_for_status()
                data = response.json()

                if "data" in data and "getStoreById" in data["data"]:
                    return data["data"]["getStoreById"]
                return None

            except requests.exceptions.Timeout:
                print(f"    ⚠️ Timeout for store {store_id}. Retry {attempt + 1}/{max_retries}...")
                retry_delay *= 1.5
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            except requests.exceptions.RequestException as e:
                print(f"    ⚠️ Request error for store {store_id}: {e}. Retry {attempt + 1}/{max_retries}...")
                retry_delay *= 1.5
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            except Exception as e:
                print(f"Error fetching store details for {store_id}: {e}")
                break

        return None

    def scrape_all_stores(self, max_domains: Optional[int] = None, skip_existing: bool = True):
        """
        Scrape all store data and save to database
        
        Args:
            max_domains: Limit number of domains to process (None for all)
            skip_existing: Skip domains already scraped
        """
        print("Starting scrape...")
        start_time = datetime.now()

        # Get all domains
        domains = self.get_supported_domains()

        if not domains:
            print("No domains found. Exiting.")
            return

        # Shard domains across workers
        if self.num_workers > 1:
            before = len(domains)
            domains = [d for d in domains if self._domain_belongs_to_this_worker(d)]
            print(
                f"Sharding enabled: worker {self.worker_id}/{self.num_workers} -> {len(domains)} of {before} domains")

        if max_domains:
            domains = domains[:max_domains]
            print(f"Limited to first {max_domains} domains")

        processed = 0
        skipped = 0
        errors = 0

        # Process each domain
        for i, domain in enumerate(domains, 1):
            # Skip if already scraped
            if skip_existing and self._domain_scraped(domain):
                skipped += 1
                if i % 100 == 0:
                    print(f"[{i}/{len(domains)}] Skipped {skipped} already-scraped domains...")
                continue

            print(f"\n[{i}/{len(domains)}] Processing domain: {domain}")

            # Get store IDs for domain
            store_mappings = self.get_store_ids_by_domain(domain)

            if not store_mappings:
                print(f"  No stores found for {domain}")
                self._mark_domain_scraped(domain, 0)
                continue

            print(f"  Found {len(store_mappings)} store(s)")
            domain_store_count = 0

            # Get details for each store
            for mapping in store_mappings:
                store_id = mapping.get("storeId")
                partial_url = mapping.get("partialURL")

                # Skip if store already exists
                if skip_existing and self._store_exists(store_id):
                    print(f"    ⏭ Store {store_id} already in database")
                    domain_store_count += 1
                    continue

                print(f"    Fetching details for store {store_id} ({partial_url})...")
                store_details = self.get_store_details(store_id)

                if store_details:
                    self._save_store_to_db(domain, store_id, partial_url, store_details)
                    processed += 1
                    domain_store_count += 1
                    print(f"      ✓ {store_details.get('name', 'Unknown')} - {store_details.get('country', 'N/A')}")
                else:
                    errors += 1

            # Mark domain as scraped
            self._mark_domain_scraped(domain, domain_store_count)

            # Progress update
            if i % 100 == 0:
                print(f"\n  Progress: {processed} stores saved, {skipped} domains skipped, {errors} errors")

        elapsed = datetime.now() - start_time
        print(f"\n{'='*60}")
        print(f"Scraping complete!")
        print(f"Total domains processed: {len(domains)}")
        print(f"Domains skipped (already scraped): {skipped}")
        print(f"Stores saved to database: {processed}")
        print(f"Errors: {errors}")
        print(f"Time elapsed: {elapsed}")
        print(f"Database: {self.db_path}")
        print(f"{'='*60}")

    def _save_data(self, data: List[Dict], filename: str):
        """Save data to JSON file (legacy method for export)"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def export_to_json(self, output_file: str = "honey_stores.json", limit: Optional[int] = None):
        """
        Export database to JSON format
        
        Args:
            output_file: Output JSON file path
            limit: Limit number of stores to export (None for all)
        """
        print(f"Exporting database to {output_file}...")

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = "SELECT * FROM stores"
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        stores = []

        for row in cursor.fetchall():
            store_data = dict(row)
            # Parse raw JSON back to object
            if store_data.get('raw_json'):
                store_data['details'] = json.loads(store_data['raw_json'])
            stores.append(store_data)

        conn.close()

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(stores, f, indent=2, ensure_ascii=False)

        print(f"Exported {len(stores)} stores to {output_file}")

    def export_to_csv(self, csv_file: str = "honey_stores.csv", limit: Optional[int] = None):
        """
        Export store data to CSV format
        
        Args:
            csv_file: Output CSV file
            limit: Limit number of stores to export (None for all)
        """
        print(f"Exporting database to {csv_file}...")

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
            SELECT 
                s.domain, s.store_id, s.partial_url, s.name, s.country, s.url,
                s.active, s.supported, s.shoppers_30d, s.logo_url, s.created, s.updated,
                COUNT(c.id) as num_coupons
            FROM stores s
            LEFT JOIN coupons c ON s.store_id = c.store_id
            GROUP BY s.store_id
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)

        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = [
                'domain', 'store_id', 'partial_url', 'name', 'country', 'url',
                'active', 'supported', 'shoppers_30d', 'num_coupons',
                'logo_url', 'created', 'updated'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            count = 0
            for row in cursor.fetchall():
                writer.writerow(dict(row))
                count += 1

        conn.close()
        print(f"CSV export complete: {count} stores in {csv_file}")

    def get_stats(self) -> Dict:
        """Get database statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        stats = {}

        # Total stores
        cursor.execute("SELECT COUNT(*) FROM stores")
        stats['total_stores'] = cursor.fetchone()[0]

        # Total domains scraped
        cursor.execute("SELECT COUNT(*) FROM scraped_domains")
        stats['domains_scraped'] = cursor.fetchone()[0]

        # Total coupons
        cursor.execute("SELECT COUNT(*) FROM coupons")
        stats['total_coupons'] = cursor.fetchone()[0]

        # Active stores
        cursor.execute("SELECT COUNT(*) FROM stores WHERE active = 1")
        stats['active_stores'] = cursor.fetchone()[0]

        # Stores by country (top 10)
        cursor.execute("""
            SELECT country, COUNT(*) as count 
            FROM stores 
            WHERE country IS NOT NULL
            GROUP BY country 
            ORDER BY count DESC 
            LIMIT 10
        """)
        stats['top_countries'] = dict(cursor.fetchall())

        # Stores with coupons
        cursor.execute("""
            SELECT COUNT(DISTINCT store_id) 
            FROM coupons
        """)
        stats['stores_with_coupons'] = cursor.fetchone()[0]

        conn.close()
        return stats

    def print_stats(self):
        """Print database statistics"""
        stats = self.get_stats()
        print(f"\n{'='*60}")
        print("DATABASE STATISTICS")
        print(f"{'='*60}")
        print(f"Total stores: {stats['total_stores']:,}")
        print(f"Domains scraped: {stats['domains_scraped']:,}")
        print(f"Active stores: {stats['active_stores']:,}")
        print(f"Total coupons: {stats['total_coupons']:,}")
        print(f"Stores with coupons: {stats['stores_with_coupons']:,}")
        print(f"\nTop 10 countries:")
        for country, count in stats['top_countries'].items():
            print(f"  {country}: {count:,}")
        print(f"{'='*60}\n")

    def _coupon_key(self, coupon: Dict) -> str:
        # Prefer dealId if present; otherwise derive a stable signature
        deal_id = coupon.get("dealId") or ""
        code = coupon.get("code") or ""
        desc = coupon.get("description") or ""
        created = str(coupon.get("created") or "")
        expires = str(coupon.get("expires") or "")
        raw = f"{deal_id}|{code}|{desc}|{created}|{expires}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def refresh_coupons(self, refresh_hours: float = 6.0, limit: Optional[int] = None):
        """
        Refresh coupons for stores already in DB, but only for stores whose domain
        maps to this worker's shard (domain sharding correctness).

        - Select stale stores (store_last_refreshed older than threshold)
        - Filter by _domain_belongs_to_this_worker(domain)
        - Order by oldest refreshed first
        - Reuse _save_store_to_db with flags
        """
        now_ms = int(time.time() * 1000)
        threshold_ms = now_ms - int(refresh_hours * 3600 * 1000)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        sql = """
              SELECT store_id, domain, COALESCE(store_last_refreshed, 0) AS last_ref
              FROM stores
              WHERE domain IS NOT NULL
                AND (store_last_refreshed IS NULL 
                 OR store_last_refreshed 
                  < ?)
              ORDER BY last_ref ASC 
              """

        params = [threshold_ms]

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        # Shard filter (domain -> worker)
        candidates = [(store_id, domain) for (store_id, domain, _last_ref) in rows]
        store_ids = [sid for (sid, dom) in candidates if self._domain_belongs_to_this_worker(dom)]

        print(
            f"Refreshing coupons for {len(store_ids)} store(s) on worker "
            f"{self.worker_id}/{self.num_workers} (refresh_hours={refresh_hours})"
        )

        updated = 0
        errors = 0

        for i, store_id in enumerate(store_ids, 1):
            print(f"[{i}/{len(store_ids)}] Refresh store {store_id}")
            details = self.get_store_details(store_id)
            if not details:
                errors += 1
                continue

            self._save_store_to_db(
                domain=None,
                store_id=store_id,
                partial_url=None,
                details=details,
                save_store=False,
                save_coupons=True,
                save_partial_urls=False,
            )
            updated += 1

        print(f"Coupon refresh done: updated={updated}, errors={errors}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--db", type=str, default="honey_stores.db")
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=["auto", "stats", "limit", "refresh-coupons"]
    )
    parser.add_argument("--refresh-hours", type=float, default=6.0)
    parser.add_argument("--refresh-limit", type=int, default=None)

    args = parser.parse_args()

    scraper = HoneyScraper(
        delay=args.delay,
        db_path=args.db,
        worker_id=args.worker_id,
        num_workers=args.workers
    )

    if args.mode == "stats":
        scraper.print_stats()
        return

    if args.mode == "limit":
        scraper.scrape_all_stores(max_domains=args.limit, skip_existing=True)
        scraper.print_stats()
        return

    if args.mode == "refresh-coupons":
        scraper.refresh_coupons(refresh_hours=args.refresh_hours, limit=args.refresh_limit)
        scraper.print_stats()
        return

    # auto
    scraper.scrape_all_stores(max_domains=None, skip_existing=True)
    scraper.print_stats()



if __name__ == "__main__":
    main()
