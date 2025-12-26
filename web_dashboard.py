"""
Honey Scraper Web Dashboard
Flask web application for monitoring and controlling the scraper
"""

from flask import Flask, render_template, jsonify, request, send_file, make_response
from scraper import HoneyScraper
import sqlite3
import json
import threading
import time
from datetime import datetime
from typing import Dict, Optional
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'honey-scraper-secret-key-change-me'

# Global scraper state
scraper_instance: Optional[HoneyScraper] = None
scraper_thread: Optional[threading.Thread] = None
scraper_state = {
    'running': False,
    'current_domain': None,
    'domains_processed': 0,
    'total_domains': 0,
    'stores_saved': 0,
    'errors': 0,
    'consecutive_errors': 0,
    'last_error': None,
    'started_at': None,
    'mode': None,
    'should_stop': False,
    'delay': 0.5
}

DB_PATH = os.path.join(os.path.dirname(__file__), 'honey_stores.db')


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def update_scraper_state(key: str, value):
    """Thread-safe state update"""
    scraper_state[key] = value


class MonitoredScraper(HoneyScraper):
    """Scraper with progress monitoring and error handling"""
    
    def get_store_ids_by_domain(self, domain: str):
        """Override to add error handling"""
        try:
            result = super().get_store_ids_by_domain(domain)
            # Reset consecutive errors on success
            if result:
                update_scraper_state('consecutive_errors', 0)
            return result
        except Exception as e:
            update_scraper_state('last_error', str(e))
            consecutive = scraper_state.get('consecutive_errors', 0) + 1
            update_scraper_state('consecutive_errors', consecutive)
            
            # Stop if too many consecutive errors (likely blocked)
            if consecutive >= 10:
                print(f"⚠️ CRITICAL: {consecutive} consecutive errors. Stopping to prevent ban.")
                update_scraper_state('should_stop', True)
            return []
    
    def get_store_details(self, store_id: str, max_ugc: int = 3, success_count: int = 1):
        """Override to add error handling"""
        try:
            result = super().get_store_details(store_id, max_ugc, success_count)
            # Reset consecutive errors on success
            if result:
                update_scraper_state('consecutive_errors', 0)
            return result
        except Exception as e:
            update_scraper_state('last_error', str(e))
            consecutive = scraper_state.get('consecutive_errors', 0) + 1
            update_scraper_state('consecutive_errors', consecutive)
            
            # Stop if too many consecutive errors
            if consecutive >= 10:
                print(f"⚠️ CRITICAL: {consecutive} consecutive errors. Stopping to prevent ban.")
                update_scraper_state('should_stop', True)
            return None
    
    def scrape_all_stores(self, max_domains: Optional[int] = None, skip_existing: bool = True):
        """Scrape with progress updates and error handling"""
        update_scraper_state('running', True)
        update_scraper_state('should_stop', False)
        update_scraper_state('started_at', datetime.now().isoformat())
        update_scraper_state('mode', f"{'Resume' if skip_existing else 'Fresh'} - {'All domains' if not max_domains else f'{max_domains} domains'}")
        update_scraper_state('consecutive_errors', 0)
        update_scraper_state('last_error', None)
        
        try:
            print("Starting monitored scrape...")
            start_time = datetime.now()
            
            # Get all domains
            domains = self.get_supported_domains()
            if not domains:
                print("No domains found. Exiting.")
                update_scraper_state('last_error', 'No domains found in domains list')
                return
            
            print(f"Total domains available: {len(domains)}")
            
            if max_domains:
                domains = domains[:max_domains]
                print(f"Limited to {max_domains} domains")
            
            if skip_existing:
                # Count how many will be skipped
                already_scraped = sum(1 for d in domains if self._domain_scraped(d))
                print(f"Already scraped: {already_scraped}/{len(domains)} domains")
                if already_scraped == len(domains):
                    print("⚠️ All selected domains already scraped!")
                    update_scraper_state('last_error', 'All selected domains already scraped')
                    return
            
            update_scraper_state('total_domains', len(domains))
            
            processed = 0
            skipped = 0
            errors = 0
            
            # Process each domain
            for i, domain in enumerate(domains, 1):
                # Check for stop signal
                if scraper_state.get('should_stop', False):
                    print("\n⚠️ Stop signal received. Halting scrape.")
                    break
                
                update_scraper_state('current_domain', domain)
                update_scraper_state('domains_processed', i)
                
                # Skip if already scraped
                if skip_existing and self._domain_scraped(domain):
                    print(f"  ⏭️  Skipping {domain} (already scraped)")
                    skipped += 1
                    continue
                
                print(f"\n[{i}/{len(domains)}] Processing domain: {domain}")
                
                # Get store IDs for domain
                store_mappings = self.get_store_ids_by_domain(domain)
                
                if not store_mappings:
                    self._mark_domain_scraped(domain, 0)
                    continue
                
                domain_store_count = 0
                
                # Get details for each store
                for mapping in store_mappings:
                    # Check for stop signal
                    if scraper_state.get('should_stop', False):
                        break
                    
                    store_id = mapping.get("storeId")
                    partial_url = mapping.get("partialURL")
                    
                    if skip_existing and self._store_exists(store_id):
                        domain_store_count += 1
                        continue
                    
                    store_details = self.get_store_details(store_id)
                    
                    if store_details:
                        self._save_store_to_db(domain, store_id, partial_url, store_details)
                        processed += 1
                        update_scraper_state('stores_saved', processed)
                    else:
                        errors += 1
                        update_scraper_state('errors', errors)
                    
                    domain_store_count += 1
                
                # Mark domain as scraped
                self._mark_domain_scraped(domain, domain_store_count)
                
                # Check if we should stop due to errors
                if scraper_state.get('should_stop', False):
                    print("\n⚠️ Too many errors. Stopping to prevent ban.")
                    break
            
            elapsed = datetime.now() - start_time
            print(f"\nScraping complete! Processed: {processed}, Skipped: {skipped}, Errors: {errors}")
            print(f"Time elapsed: {elapsed}")
            
            if errors > 0:
                print(f"\n⚠️ Total errors: {errors}")
                if scraper_state.get('consecutive_errors', 0) >= 5:
                    print("⚠️ WARNING: Multiple consecutive errors detected.")
                    print("   This may indicate rate limiting or IP blocking.")
                    print("   Consider increasing delay or waiting before resuming.")
            
        finally:
            update_scraper_state('running', False)
            update_scraper_state('current_domain', None)
            update_scraper_state('should_stop', False)


@app.route('/')
def index():
    """Main dashboard page"""
    response = make_response(render_template('dashboard.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/stats')
def api_stats():
    """Get database statistics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Basic stats
        cursor.execute("SELECT COUNT(*) FROM stores")
        total_stores = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scraped_domains")
        domains_scraped = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM coupons")
        total_coupons = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM stores WHERE active = 1")
        active_stores = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT store_id) FROM coupons")
        stores_with_coupons = cursor.fetchone()[0]
        
        # Top countries
        cursor.execute("""
            SELECT country, COUNT(*) as count 
            FROM stores 
            WHERE country IS NOT NULL
            GROUP BY country 
            ORDER BY count DESC 
            LIMIT 10
        """)
        top_countries = [dict(row) for row in cursor.fetchall()]
        
        # Recent stores
        cursor.execute("""
            SELECT name, country, url, updated 
            FROM stores 
            ORDER BY updated DESC 
            LIMIT 10
        """)
        recent_stores = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'stats': {
                'total_stores': total_stores,
                'domains_scraped': domains_scraped,
                'total_coupons': total_coupons,
                'active_stores': active_stores,
                'stores_with_coupons': stores_with_coupons,
                'top_countries': top_countries,
                'recent_stores': recent_stores
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/scraper/status')
def api_scraper_status():
    """Get scraper status"""
    return jsonify({
        'success': True,
        'status': scraper_state
    })


@app.route('/api/scraper/start', methods=['POST'])
def api_scraper_start():
    """Start scraper"""
    global scraper_instance, scraper_thread
    
    if scraper_state['running']:
        return jsonify({'success': False, 'error': 'Scraper is already running'}), 400
    
    data = request.json or {}
    max_domains = data.get('max_domains', None)
    skip_existing = data.get('skip_existing', True)
    
    if max_domains:
        try:
            max_domains = int(max_domains)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid max_domains value'}), 400
    
    # Start scraper in background thread
    delay = scraper_state.get('delay', 0.5)
    scraper_instance = MonitoredScraper(delay=delay, db_path=DB_PATH)
    
    # Determine mode for display
    if max_domains:
        mode = f"Limited ({max_domains} domains)"
    else:
        mode = "Full scrape"
    
    if skip_existing:
        mode += " - Skip existing"
    else:
        mode += " - Fresh"
    
    update_scraper_state('mode', mode)
    
    scraper_thread = threading.Thread(
        target=scraper_instance.scrape_all_stores,
        args=(max_domains, skip_existing),
        daemon=True
    )
    scraper_thread.start()
    
    return jsonify({'success': True, 'message': f'Scraper started: {mode}'})


@app.route('/api/scraper/stop', methods=['POST'])
def api_scraper_stop():
    """Stop scraper"""
    update_scraper_state('should_stop', True)
    return jsonify({'success': True, 'message': 'Stop signal sent (scraper will stop after current domain)'})


@app.route('/api/scraper/delay', methods=['POST'])
def api_scraper_delay():
    """Update scraper delay in real-time"""
    try:
        data = request.json
        delay = float(data.get('delay', 0.5))
        
        if delay < 0:
            return jsonify({'success': False, 'error': 'Delay must be non-negative'}), 400
        
        if delay > 10:
            return jsonify({'success': False, 'error': 'Delay must be 10 seconds or less'}), 400
        
        # Update state
        update_scraper_state('delay', delay)
        
        # Update running scraper if active
        global scraper_instance
        if scraper_instance:
            scraper_instance.delay = delay
        
        return jsonify({
            'success': True,
            'delay': delay,
            'message': f'Delay updated to {delay}s' + (' (active scraper updated)' if scraper_state['running'] else '')
        })
    except (ValueError, TypeError) as e:
        return jsonify({'success': False, 'error': f'Invalid delay value: {str(e)}'}), 400


@app.route('/api/stores')
def api_stores():
    """Get stores with pagination and search"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        country = request.args.get('country', '', type=str)
        active_only = request.args.get('active_only', 'false', type=str).lower() == 'true'
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Build query
        where_clauses = []
        params = []
        
        if search:
            where_clauses.append("(name LIKE ? OR domain LIKE ? OR url LIKE ?)")
            search_param = f"%{search}%"
            params.extend([search_param, search_param, search_param])
        
        if country:
            where_clauses.append("country = ?")
            params.append(country)
        
        if active_only:
            where_clauses.append("active = 1")
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # Get total count (use simple query without alias)
        cursor.execute(f"SELECT COUNT(*) FROM stores WHERE {where_sql}", params)
        total = cursor.fetchone()[0]
        
        # Get stores (rebuild where clause with table alias for JOIN query)
        where_clauses_aliased = []
        if search:
            where_clauses_aliased.append("(s.name LIKE ? OR s.domain LIKE ? OR s.url LIKE ?)")
        if country:
            where_clauses_aliased.append("s.country = ?")
        if active_only:
            where_clauses_aliased.append("s.active = 1")
        
        where_sql_aliased = " AND ".join(where_clauses_aliased) if where_clauses_aliased else "1=1"
        
        offset = (page - 1) * per_page
        query = f"""
            SELECT 
                s.store_id, s.name, s.domain, s.country, s.url, s.active, 
                s.supported, s.shoppers_30d, s.created, s.updated,
                COUNT(c.id) as coupon_count
            FROM stores s
            LEFT JOIN coupons c ON s.store_id = c.store_id
            WHERE {where_sql_aliased}
            GROUP BY s.store_id
            ORDER BY s.updated DESC
            LIMIT ? OFFSET ?
        """
        params.extend([per_page, offset])
        
        cursor.execute(query, params)
        stores = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'stores': stores,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/store/<store_id>')
def api_store_detail(store_id):
    """Get detailed store information"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get store
        cursor.execute("SELECT * FROM stores WHERE store_id = ?", (store_id,))
        store_row = cursor.fetchone()
        
        if not store_row:
            conn.close()
            return jsonify({'success': False, 'error': 'Store not found'}), 404
        
        store = dict(store_row)
        
        # Get coupons with usage stats
        cursor.execute("""
            SELECT 
                c.*,
                COUNT(r.id) as usage_report_count,
                SUM(CASE WHEN r.worked = 1 THEN 1 ELSE 0 END) as worked_count,
                SUM(CASE WHEN r.worked = 0 THEN 1 ELSE 0 END) as failed_count,
                AVG(CASE WHEN r.worked = 1 THEN r.amount_saved END) as avg_savings
            FROM coupons c
            LEFT JOIN coupon_usage_reports r ON c.id = r.coupon_id
            WHERE c.store_id = ?
            GROUP BY c.id
            ORDER BY c.created DESC
        """, (store_id,))
        coupons = [dict(row) for row in cursor.fetchall()]
        
        # Get partial URLs
        cursor.execute("SELECT * FROM partial_urls WHERE store_id = ?", (store_id,))
        partial_urls = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        # Parse raw JSON if available
        if store.get('raw_json'):
            try:
                store['details'] = json.loads(store['raw_json'])
            except:
                pass
        
        return jsonify({
            'success': True,
            'store': store,
            'coupons': coupons,
            'partial_urls': partial_urls
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/coupon/<int:coupon_id>/usage')
def api_coupon_usage(coupon_id):
    """Get usage reports for a coupon"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM coupon_usage_reports
            WHERE coupon_id = ?
            ORDER BY reported_at DESC
        """, (coupon_id,))
        
        reports = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({'success': True, 'reports': reports})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/coupon/report', methods=['POST'])
def api_report_coupon_usage():
    """Report coupon usage"""
    try:
        data = request.json
        
        required = ['coupon_id', 'store_id', 'code', 'worked']
        if not all(k in data for k in required):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO coupon_usage_reports (
                coupon_id, store_id, code, worked, amount_saved, 
                amount_spent, notes, reported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['coupon_id'],
            data['store_id'],
            data['code'],
            1 if data['worked'] else 0,
            data.get('amount_saved'),
            data.get('amount_spent'),
            data.get('notes'),
            int(time.time() * 1000)
        ))
        
        conn.commit()
        report_id = cursor.lastrowid
        conn.close()
        
        return jsonify({'success': True, 'report_id': report_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/countries')
def api_countries():
    """Get list of countries"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT country, COUNT(*) as count
            FROM stores
            WHERE country IS NOT NULL
            GROUP BY country
            ORDER BY count DESC
        """)
        countries = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({'success': True, 'countries': countries})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export/csv')
def api_export_csv():
    """Export stores to CSV"""
    try:
        scraper = HoneyScraper(db_path=DB_PATH)
        filename = f"honey_stores_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        scraper.export_to_csv(filename)
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export/json')
def api_export_json():
    """Export stores to JSON"""
    try:
        scraper = HoneyScraper(db_path=DB_PATH)
        filename = f"honey_stores_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        scraper.export_to_json(filename)
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def main():
    """Run Flask app"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Honey Scraper Web Dashboard')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    
    args = parser.parse_args()
    
    print("="*60)
    print("HONEY SCRAPER WEB DASHBOARD")
    print("="*60)
    print(f"Starting server on http://{args.host}:{args.port}")
    print(f"Database: {DB_PATH}")
    print("="*60)
    
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
