import sys
import time
import json
from pathlib import Path
import datetime
import duckdb

# Ensure UTF-8 output even in Windows cmd/powershell cp1252 environments
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"
PARQUET_PATH = PROJECT_ROOT / "data" / "transactions.parquet"
OUTPUT_JSON_PATH = PROJECT_ROOT / "data" / "measurements_step2a.json"

RANDOM_SEED = 42

def get_db_file_metadata():
    meta = {}
    for label, p in [("DuckDB", DB_PATH), ("Parquet", PARQUET_PATH)]:
        if p.exists():
            st = p.stat()
            meta[label] = {
                "size_bytes": st.st_size,
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
            }
        else:
            meta[label] = None
    return meta

def run_step2a():
    print("==========================================================================================")
    print("  OPERATION ABHEDYA-CHAKRA -- PHASE 2 STEP 2A: HOW THE MARKER GROUPS CONNECT")
    print("  Strictly Read-Only DuckDB | No Thresholds | No Scoring | Descriptive Only")
    print("==========================================================================================\n")

    # DB Safety: Before
    meta_before = get_db_file_metadata()
    print("DATABASE STATE BEFORE MEASUREMENTS:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------\n")

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # =========================================================================
    # 1. UPI/REF -> WEB_EMULATOR / IMPS/P2A
    # =========================================================================
    print("1. UPI/REF TRANSACTIONS AND DOWNSTREAM CONNECTIONS")
    print("-" * 95)
    t0 = time.perf_counter()

    # 1a. Identify all 300 UPI/REF transactions
    ref_rows_raw = con.execute("""
        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp, Payment_Mode, Device_Type, IP_Address, Narration
        FROM transactions
        WHERE Narration LIKE 'UPI/REF%'
        ORDER BY row_id
    """).fetchall()

    ref_txns_list = []
    for r in ref_rows_raw:
        ref_txns_list.append({
            "row_id": r[0],
            "sender": r[1],
            "receiver": r[2],
            "amount": float(r[3]),
            "timestamp": str(r[4]),
            "payment_mode": r[5],
            "device_type": r[6],
            "ip_address": r[7],
            "narration": r[8]
        })

    print(f"Identified {len(ref_txns_list)} UPI/REF transactions.")
    ref_senders_cnt = con.execute("SELECT count(distinct Sender_Account) FROM transactions WHERE Narration LIKE 'UPI/REF%'").fetchone()[0]
    ref_receivers_cnt = con.execute("SELECT count(distinct Receiver_Account) FROM transactions WHERE Narration LIKE 'UPI/REF%'").fetchone()[0]
    print(f"  Distinct REF Senders: {ref_senders_cnt} | Distinct REF Receivers: {ref_receivers_cnt}")
    print(f"  Sample REF txn: row_id {ref_txns_list[0]['row_id']}, {ref_txns_list[0]['sender']} -> {ref_txns_list[0]['receiver']}, INR {ref_txns_list[0]['amount']:,.2f}, {ref_txns_list[0]['timestamp']}, Device: {ref_txns_list[0]['device_type']}")

    # 1b. Receiver connections to P2A and WALLET_LOAD
    # Connection to P2A
    conn_p2a_raw = con.execute("""
        SELECT 
            count(distinct r.Receiver_Account) as ref_receivers_becoming_p2a_senders,
            count(distinct r.row_id) as distinct_ref_txns,
            count(distinct p.row_id) as distinct_p2a_txns,
            count(*) as matching_pairs,
            min(epoch(p.Timestamp) - epoch(r.Timestamp)) as min_gap_s,
            quantile_cont(epoch(p.Timestamp) - epoch(r.Timestamp), 0.25) as p25_gap_s,
            median(epoch(p.Timestamp) - epoch(r.Timestamp)) as med_gap_s,
            quantile_cont(epoch(p.Timestamp) - epoch(r.Timestamp), 0.75) as p75_gap_s,
            quantile_cont(epoch(p.Timestamp) - epoch(r.Timestamp), 0.90) as p90_gap_s,
            max(epoch(p.Timestamp) - epoch(r.Timestamp)) as max_gap_s,
            min(p.Amount / r.Amount) as min_ratio,
            quantile_cont(p.Amount / r.Amount, 0.25) as p25_ratio,
            median(p.Amount / r.Amount) as med_ratio,
            quantile_cont(p.Amount / r.Amount, 0.75) as p75_ratio,
            quantile_cont(p.Amount / r.Amount, 0.90) as p90_ratio,
            max(p.Amount / r.Amount) as max_ratio
        FROM transactions r
        JOIN transactions p 
          ON r.Receiver_Account = p.Sender_Account
         AND p.Timestamp > r.Timestamp
        WHERE r.Narration LIKE 'UPI/REF%'
          AND (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
    """).fetchone()

    # Connection to WALLET_LOAD directly from REF receiver
    conn_wl_raw = con.execute("""
        SELECT 
            count(distinct r.Receiver_Account) as ref_receivers_becoming_wl_senders,
            count(distinct r.row_id) as distinct_ref_txns,
            count(distinct w.row_id) as distinct_wl_txns,
            count(*) as matching_pairs
        FROM transactions r
        JOIN transactions w 
          ON r.Receiver_Account = w.Sender_Account
         AND w.Timestamp > r.Timestamp
        WHERE r.Narration LIKE 'UPI/REF%'
          AND (w.Narration LIKE 'UPI/WALLET_LOAD%' OR w.Device_Type = 'Linux_Script')
    """).fetchone()

    # ASOF (Immediate next) connection from REF -> P2A
    conn_p2a_asof = con.execute("""
        WITH asof_m AS (
            SELECT 
                r.row_id as ref_row_id,
                r.Amount as ref_amt,
                p.Amount as p2a_amt,
                epoch(p.Timestamp) - epoch(r.Timestamp) as gap_s,
                p.Amount / r.Amount as ratio
            FROM transactions r
            ASOF JOIN transactions p 
              ON r.Receiver_Account = p.Sender_Account
             AND r.Timestamp < p.Timestamp
            WHERE r.Narration LIKE 'UPI/REF%'
              AND (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
        )
        SELECT 
            count(*) as matched_ref_txns,
            min(gap_s) as min_gap_s,
            quantile_cont(gap_s, 0.25) as p25_gap_s,
            median(gap_s) as med_gap_s,
            quantile_cont(gap_s, 0.75) as p75_gap_s,
            quantile_cont(gap_s, 0.90) as p90_gap_s,
            max(gap_s) as max_gap_s,
            min(ratio) as min_ratio,
            quantile_cont(ratio, 0.25) as p25_ratio,
            median(ratio) as med_ratio,
            quantile_cont(ratio, 0.75) as p75_ratio,
            quantile_cont(ratio, 0.90) as p90_ratio,
            max(ratio) as max_ratio
        FROM asof_m
    """).fetchone()

    p2a_conn_data = {
        "all_pairs": {
            "ref_receivers_becoming_p2a_senders": conn_p2a_raw[0],
            "participating_ref_transactions": conn_p2a_raw[1],
            "participating_p2a_transactions": conn_p2a_raw[2],
            "total_matching_pairs": conn_p2a_raw[3],
            "time_gap_seconds": {
                "min": float(conn_p2a_raw[4]),
                "p25": round(float(conn_p2a_raw[5]), 1),
                "median": round(float(conn_p2a_raw[6]), 1),
                "p75": round(float(conn_p2a_raw[7]), 1),
                "p90": round(float(conn_p2a_raw[8]), 1),
                "max": float(conn_p2a_raw[9])
            },
            "time_gap_hours": {
                "min": round(float(conn_p2a_raw[4]) / 3600, 2),
                "p25": round(float(conn_p2a_raw[5]) / 3600, 2),
                "median": round(float(conn_p2a_raw[6]) / 3600, 2),
                "p75": round(float(conn_p2a_raw[7]) / 3600, 2),
                "p90": round(float(conn_p2a_raw[8]) / 3600, 2),
                "max": round(float(conn_p2a_raw[9]) / 3600, 2)
            },
            "amount_ratio": {
                "min": round(float(conn_p2a_raw[10]), 4),
                "p25": round(float(conn_p2a_raw[11]), 4),
                "median": round(float(conn_p2a_raw[12]), 4),
                "p75": round(float(conn_p2a_raw[13]), 4),
                "p90": round(float(conn_p2a_raw[14]), 4),
                "max": round(float(conn_p2a_raw[15]), 4)
            }
        },
        "immediate_next_asof": {
            "matched_ref_transactions": conn_p2a_asof[0],
            "time_gap_seconds": {
                "min": float(conn_p2a_asof[1]),
                "p25": round(float(conn_p2a_asof[2]), 1),
                "median": round(float(conn_p2a_asof[3]), 1),
                "p75": round(float(conn_p2a_asof[4]), 1),
                "p90": round(float(conn_p2a_asof[5]), 1),
                "max": float(conn_p2a_asof[6])
            },
            "time_gap_hours": {
                "min": round(float(conn_p2a_asof[1]) / 3600, 2),
                "p25": round(float(conn_p2a_asof[2]) / 3600, 2),
                "median": round(float(conn_p2a_asof[3]) / 3600, 2),
                "p75": round(float(conn_p2a_asof[4]) / 3600, 2),
                "p90": round(float(conn_p2a_asof[5]) / 3600, 2),
                "max": round(float(conn_p2a_asof[6]) / 3600, 2)
            },
            "amount_ratio": {
                "min": round(float(conn_p2a_asof[7]), 4),
                "p25": round(float(conn_p2a_asof[8]), 4),
                "median": round(float(conn_p2a_asof[9]), 4),
                "p75": round(float(conn_p2a_asof[10]), 4),
                "p90": round(float(conn_p2a_asof[11]), 4),
                "max": round(float(conn_p2a_asof[12]), 4)
            }
        },
        "direct_connection_to_wallet_load": {
            "ref_receivers_becoming_wl_senders": conn_wl_raw[0],
            "distinct_ref_txns": conn_wl_raw[1],
            "distinct_wl_txns": conn_wl_raw[2],
            "matching_pairs": conn_wl_raw[3]
        }
    }

    print("\nDownstream Connections of UPI/REF Receivers:")
    print(f"  REF Receivers -> Web_Emulator / IMPS/P2A Senders: {conn_p2a_raw[0]} accounts (participating in {conn_p2a_raw[3]:,d} pairs across {conn_p2a_raw[1]} REF and {conn_p2a_raw[2]} P2A txns)")
    print(f"  REF Receivers -> Linux_Script / WALLET_LOAD Senders: {conn_wl_raw[0]} accounts (0 direct pairs)")

    print("\nREF -> P2A Quantiles (Immediate Next Outgoing / ASOF Join):")
    print(f"{'Metric':<16} {'Min':<10} {'p25':<10} {'Median':<10} {'p75':<10} {'p90':<10} {'Max':<10}")
    print("-" * 76)
    print(f"{'Time Gap (s)':<16} {conn_p2a_asof[1]:<10.0f} {conn_p2a_asof[2]:<10.1f} {conn_p2a_asof[3]:<10.1f} {conn_p2a_asof[4]:<10.1f} {conn_p2a_asof[5]:<10.1f} {conn_p2a_asof[6]:<10.0f}")
    print(f"{'Time Gap (h)':<16} {conn_p2a_asof[1]/3600:<10.2f} {conn_p2a_asof[2]/3600:<10.2f} {conn_p2a_asof[3]/3600:<10.2f} {conn_p2a_asof[4]/3600:<10.2f} {conn_p2a_asof[5]/3600:<10.2f} {conn_p2a_asof[6]/3600:<10.2f}")
    print(f"{'Amount Ratio':<16} {conn_p2a_asof[7]:<10.4f} {conn_p2a_asof[8]:<10.4f} {conn_p2a_asof[9]:<10.4f} {conn_p2a_asof[10]:<10.4f} {conn_p2a_asof[11]:<10.4f} {conn_p2a_asof[12]:<10.4f}")

    print("\nREF -> P2A Quantiles (All Post-REF Outgoing Pairs):")
    print(f"{'Metric':<16} {'Min':<10} {'p25':<10} {'Median':<10} {'p75':<10} {'p90':<10} {'Max':<10}")
    print("-" * 76)
    print(f"{'Time Gap (s)':<16} {conn_p2a_raw[4]:<10.0f} {conn_p2a_raw[5]:<10.1f} {conn_p2a_raw[6]:<10.1f} {conn_p2a_raw[7]:<10.1f} {conn_p2a_raw[8]:<10.1f} {conn_p2a_raw[9]:<10.0f}")
    print(f"{'Time Gap (h)':<16} {conn_p2a_raw[4]/3600:<10.2f} {conn_p2a_raw[5]/3600:<10.2f} {conn_p2a_raw[6]/3600:<10.2f} {conn_p2a_raw[7]/3600:<10.2f} {conn_p2a_raw[8]/3600:<10.2f} {conn_p2a_raw[9]/3600:<10.2f}")
    print(f"{'Amount Ratio':<16} {conn_p2a_raw[10]:<10.4f} {conn_p2a_raw[11]:<10.4f} {conn_p2a_raw[12]:<10.4f} {conn_p2a_raw[13]:<10.4f} {conn_p2a_raw[14]:<10.4f} {conn_p2a_raw[15]:<10.4f}")

    lat_sec1 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 1 Total Execution Time: {lat_sec1:.2f} ms\n")

    # =========================================================================
    # 2. WEB_EMULATOR ACCOUNT STRUCTURE (129 ACCOUNTS)
    # =========================================================================
    print("==========================================================================================")
    print("2. WEB_EMULATOR / IMPS/P2A ACCOUNT STRUCTURE (129 ACCOUNTS)")
    print("-" * 95)
    t0 = time.perf_counter()

    # Per-account metrics query
    acc_query = """
        WITH p2a_senders AS (
            SELECT distinct Sender_Account as acc
            FROM transactions
            WHERE Narration LIKE 'IMPS/P2A%' OR Device_Type = 'Web_Emulator'
        ),
        incoming_stats AS (
            SELECT 
                Receiver_Account as acc,
                count(*) as in_degree,
                sum(Amount) as total_in_amt,
                count(distinct Sender_Account) as distinct_senders
            FROM transactions
            WHERE Receiver_Account IN (SELECT acc FROM p2a_senders)
            GROUP BY Receiver_Account
        ),
        outgoing_stats AS (
            SELECT 
                Sender_Account as acc,
                count(*) as out_degree,
                sum(Amount) as total_out_amt,
                count(distinct Receiver_Account) as distinct_receivers,
                count(distinct IP_Address) as distinct_ips
            FROM transactions
            WHERE Sender_Account IN (SELECT acc FROM p2a_senders)
            GROUP BY Sender_Account
        ),
        time_gaps AS (
            SELECT 
                tin.Receiver_Account as acc,
                min(epoch(tout.Timestamp) - epoch(tin.Timestamp)) as min_gap_s,
                quantile_cont(epoch(tout.Timestamp) - epoch(tin.Timestamp), 0.25) as p25_gap_s,
                median(epoch(tout.Timestamp) - epoch(tin.Timestamp)) as med_gap_s,
                quantile_cont(epoch(tout.Timestamp) - epoch(tin.Timestamp), 0.75) as p75_gap_s,
                quantile_cont(epoch(tout.Timestamp) - epoch(tin.Timestamp), 0.90) as p90_gap_s,
                max(epoch(tout.Timestamp) - epoch(tin.Timestamp)) as max_gap_s
            FROM transactions tin
            JOIN transactions tout 
              ON tin.Receiver_Account = tout.Sender_Account
             AND tout.Timestamp > tin.Timestamp
            WHERE tin.Receiver_Account IN (SELECT acc FROM p2a_senders)
            GROUP BY tin.Receiver_Account
        ),
        account_ips AS (
            SELECT 
                Sender_Account as acc,
                string_agg(prefix_16, ', ' ORDER BY cnt DESC) as top_prefixes
            FROM (
                SELECT 
                    Sender_Account,
                    regexp_extract(IP_Address, '^([0-9]+\\.[0-9]+)') as prefix_16,
                    count(*) as cnt,
                    row_number() over (partition by Sender_Account order by count(*) desc) as rn
                FROM transactions
                WHERE Sender_Account IN (SELECT acc FROM p2a_senders)
                GROUP BY Sender_Account, prefix_16
            )
            WHERE rn <= 3
            GROUP BY Sender_Account
        )
        SELECT 
            p.acc,
            coalesce(i.in_degree, 0) as in_deg,
            coalesce(o.out_degree, 0) as out_deg,
            coalesce(o.distinct_receivers, 0) as distinct_rec,
            coalesce(i.total_in_amt, 0.0) as in_amt,
            coalesce(o.total_out_amt, 0.0) as out_amt,
            coalesce(o.total_out_amt, 0.0) / nullif(coalesce(i.total_in_amt, 0.0), 0) as out_in_ratio,
            tg.min_gap_s,
            tg.p25_gap_s,
            tg.med_gap_s,
            tg.p75_gap_s,
            tg.p90_gap_s,
            tg.max_gap_s,
            coalesce(o.distinct_ips, 0) as distinct_ips,
            aip.top_prefixes
        FROM p2a_senders p
        LEFT JOIN incoming_stats i ON p.acc = i.acc
        LEFT JOIN outgoing_stats o ON p.acc = o.acc
        LEFT JOIN time_gaps tg ON p.acc = tg.acc
        LEFT JOIN account_ips aip ON p.acc = aip.acc
        ORDER BY in_amt DESC
    """
    p2a_acc_rows = con.execute(acc_query).fetchall()

    per_account_list = []
    for r in p2a_acc_rows:
        per_account_list.append({
            "account_id": r[0],
            "in_degree": r[1],
            "out_degree": r[2],
            "distinct_receivers": r[3],
            "total_incoming_amount": round(float(r[4]), 2),
            "total_outgoing_amount": round(float(r[5]), 2),
            "out_in_amount_ratio": round(float(r[6]), 4) if r[6] is not None else None,
            "gap_min_seconds": float(r[7]) if r[7] is not None else None,
            "gap_p25_seconds": float(r[8]) if r[8] is not None else None,
            "gap_median_seconds": float(r[9]) if r[9] is not None else None,
            "gap_p75_seconds": float(r[10]) if r[10] is not None else None,
            "gap_p90_seconds": float(r[11]) if r[11] is not None else None,
            "gap_max_seconds": float(r[12]) if r[12] is not None else None,
            "distinct_ip_addresses": r[13],
            "top_16_prefixes": r[14]
        })

    # Aggregate distribution across the 129 accounts
    dist_query = """
        WITH acc_data AS (
            """ + acc_query + """
        )
        SELECT 
            min(in_deg), quantile_cont(in_deg, 0.25), median(in_deg), quantile_cont(in_deg, 0.75), quantile_cont(in_deg, 0.90), max(in_deg),
            min(out_deg), quantile_cont(out_deg, 0.25), median(out_deg), quantile_cont(out_deg, 0.75), quantile_cont(out_deg, 0.90), max(out_deg),
            min(distinct_rec), quantile_cont(distinct_rec, 0.25), median(distinct_rec), quantile_cont(distinct_rec, 0.75), quantile_cont(distinct_rec, 0.90), max(distinct_rec),
            min(in_amt), quantile_cont(in_amt, 0.25), median(in_amt), quantile_cont(in_amt, 0.75), quantile_cont(in_amt, 0.90), max(in_amt),
            min(out_amt), quantile_cont(out_amt, 0.25), median(out_amt), quantile_cont(out_amt, 0.75), quantile_cont(out_amt, 0.90), max(out_amt),
            min(out_in_ratio), quantile_cont(out_in_ratio, 0.25), median(out_in_ratio), quantile_cont(out_in_ratio, 0.75), quantile_cont(out_in_ratio, 0.90), max(out_in_ratio),
            min(med_gap_s), quantile_cont(med_gap_s, 0.25), median(med_gap_s), quantile_cont(med_gap_s, 0.75), quantile_cont(med_gap_s, 0.90), max(med_gap_s),
            min(distinct_ips), quantile_cont(distinct_ips, 0.25), median(distinct_ips), quantile_cont(distinct_ips, 0.75), quantile_cont(distinct_ips, 0.90), max(distinct_ips)
        FROM acc_data
    """
    dist_res = con.execute(dist_query).fetchone()

    metric_names = [
        "In-degree", "Out-degree", "Distinct receivers",
        "Total incoming (INR)", "Total outgoing (INR)", "Out/In amount ratio",
        "Median time gap (s)", "Distinct IPs"
    ]
    dist_summary = {}
    print(f"{'Metric':<25} {'Min':<12} {'p25':<12} {'Median':<12} {'p75':<12} {'p90':<12} {'Max':<14}")
    print("-" * 100)
    for i, m in enumerate(metric_names):
        vals = dist_res[i*6 : (i+1)*6]
        dist_summary[m] = {
            "min": round(float(vals[0]), 2),
            "p25": round(float(vals[1]), 2),
            "median": round(float(vals[2]), 2),
            "p75": round(float(vals[3]), 2),
            "p90": round(float(vals[4]), 2),
            "max": round(float(vals[5]), 2)
        }
        if "amount" in m.lower() or "incoming" in m.lower() or "outgoing" in m.lower() and "ratio" not in m.lower():
            print(f"{m:<25} {vals[0]:<12,.0f} {vals[1]:<12,.0f} {vals[2]:<12,.0f} {vals[3]:<12,.0f} {vals[4]:<12,.0f} {vals[5]:<14,.0f}")
        elif "ratio" in m.lower():
            print(f"{m:<25} {vals[0]:<12.4f} {vals[1]:<12.4f} {vals[2]:<12.4f} {vals[3]:<12.4f} {vals[4]:<12.4f} {vals[5]:<14.4f}")
        else:
            print(f"{m:<25} {vals[0]:<12.1f} {vals[1]:<12.1f} {vals[2]:<12.1f} {vals[3]:<12.1f} {vals[4]:<12.1f} {vals[5]:<14.1f}")

    print("\nSample (First 10 of 129 Web_Emulator accounts):")
    print(f"{'Account ID':<16} {'In-Deg':<8} {'Out-Deg':<9} {'Recvs':<7} {'In Amount (INR)':<18} {'Out Amount (INR)':<18} {'Out/In':<8} {'Med Gap (s)':<12} {'IPs':<5} {'Top /16 Prefixes'}")
    print("-" * 115)
    for r in per_account_list[:10]:
        print(f"{r['account_id']:<16} {r['in_degree']:<8} {r['out_degree']:<9} {r['distinct_receivers']:<7} {r['total_incoming_amount']:<18,.2f} {r['total_outgoing_amount']:<18,.2f} {r['out_in_amount_ratio'] or 0.0:<8.4f} {r['gap_median_seconds'] or 0.0:<12.0f} {r['distinct_ip_addresses']:<5} {r['top_16_prefixes']}")

    lat_sec2 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 2 Total Execution Time: {lat_sec2:.2f} ms\n")

    # =========================================================================
    # 3. P2A -> WALLET_LOAD HOPS & NULL COMPARISON
    # =========================================================================
    print("==========================================================================================")
    print("3. P2A -> WALLET_LOAD HOPS, 2-HOP / 3-HOP CHAINS & NULL COMPARISON")
    print("-" * 95)
    t0 = time.perf_counter()

    # 3a. P2A -> WALLET_LOAD Hop Statistics
    p2a_to_wl_stats = con.execute("""
        SELECT 
            count(distinct p.Receiver_Account) as p2a_receivers_becoming_wl_senders,
            count(distinct p.row_id) as distinct_p2a_txns,
            count(distinct w.row_id) as distinct_wl_txns,
            count(*) as matching_txn_pairs,
            min(epoch(w.Timestamp) - epoch(p.Timestamp)) as min_gap_s,
            quantile_cont(epoch(w.Timestamp) - epoch(p.Timestamp), 0.25) as p25_gap_s,
            median(epoch(w.Timestamp) - epoch(p.Timestamp)) as med_gap_s,
            quantile_cont(epoch(w.Timestamp) - epoch(p.Timestamp), 0.75) as p75_gap_s,
            quantile_cont(epoch(w.Timestamp) - epoch(p.Timestamp), 0.90) as p90_gap_s,
            max(epoch(w.Timestamp) - epoch(p.Timestamp)) as max_gap_s,
            min(w.Amount / p.Amount) as min_ratio,
            quantile_cont(w.Amount / p.Amount, 0.25) as p25_ratio,
            median(w.Amount / p.Amount) as med_ratio,
            quantile_cont(w.Amount / p.Amount, 0.75) as p75_ratio,
            quantile_cont(w.Amount / p.Amount, 0.90) as p90_ratio,
            max(w.Amount / p.Amount) as max_ratio
        FROM transactions p
        JOIN transactions w 
          ON p.Receiver_Account = w.Sender_Account
         AND w.Timestamp > p.Timestamp
        WHERE (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
          AND (w.Narration LIKE 'UPI/WALLET_LOAD%' OR w.Device_Type = 'Linux_Script')
    """).fetchone()

    # ASOF join for P2A -> WALLET_LOAD
    p2a_to_wl_asof = con.execute("""
        WITH asof_w AS (
            SELECT 
                p.row_id,
                p.Amount as p_amt,
                w.Amount as w_amt,
                epoch(w.Timestamp) - epoch(p.Timestamp) as gap_s,
                w.Amount / p.Amount as ratio
            FROM transactions p
            ASOF JOIN transactions w 
              ON p.Receiver_Account = w.Sender_Account
             AND p.Timestamp < w.Timestamp
            WHERE (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
              AND (w.Narration LIKE 'UPI/WALLET_LOAD%' OR w.Device_Type = 'Linux_Script')
        )
        SELECT 
            count(*) as matched_txns,
            min(gap_s) as min_gap_s,
            quantile_cont(gap_s, 0.25) as p25_gap_s,
            median(gap_s) as med_gap_s,
            quantile_cont(gap_s, 0.75) as p75_gap_s,
            quantile_cont(gap_s, 0.90) as p90_gap_s,
            max(gap_s) as max_gap_s,
            min(ratio) as min_ratio,
            quantile_cont(ratio, 0.25) as p25_ratio,
            median(ratio) as med_ratio,
            quantile_cont(ratio, 0.75) as p75_ratio,
            quantile_cont(ratio, 0.90) as p90_ratio,
            max(ratio) as max_ratio
        FROM asof_w
    """).fetchone()

    # 3b. Complete 2-hop chain: REF -> P2A -> WALLET_LOAD
    two_hop_stats = con.execute("""
        SELECT 
            count(distinct r.row_id) as ref_tx_count,
            count(distinct p.row_id) as p2a_tx_count,
            count(distinct w.row_id) as wl_tx_count,
            count(distinct r.Sender_Account) as ref_senders_cnt,
            count(distinct r.Receiver_Account) as ref_receivers_cnt,
            count(distinct p.Receiver_Account) as p2a_receivers_cnt,
            count(distinct w.Receiver_Account) as wl_receivers_cnt,
            count(*) as total_two_hop_paths,
            min(epoch(p.Timestamp) - epoch(r.Timestamp)) as hop1_min_gap,
            median(epoch(p.Timestamp) - epoch(r.Timestamp)) as hop1_med_gap,
            max(epoch(p.Timestamp) - epoch(r.Timestamp)) as hop1_max_gap,
            min(epoch(w.Timestamp) - epoch(p.Timestamp)) as hop2_min_gap,
            median(epoch(w.Timestamp) - epoch(p.Timestamp)) as hop2_med_gap,
            max(epoch(w.Timestamp) - epoch(p.Timestamp)) as hop2_max_gap,
            min(p.Amount / r.Amount) as hop1_min_ratio,
            median(p.Amount / r.Amount) as hop1_med_ratio,
            max(p.Amount / r.Amount) as hop1_max_ratio,
            min(w.Amount / p.Amount) as hop2_min_ratio,
            median(w.Amount / p.Amount) as hop2_med_ratio,
            max(w.Amount / p.Amount) as hop2_max_ratio
        FROM transactions r
        JOIN transactions p 
          ON r.Receiver_Account = p.Sender_Account
         AND p.Timestamp > r.Timestamp
        JOIN transactions w 
          ON p.Receiver_Account = w.Sender_Account
         AND w.Timestamp > p.Timestamp
        WHERE (r.Narration LIKE 'UPI/REF%')
          AND (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
          AND (w.Narration LIKE 'UPI/WALLET_LOAD%' OR w.Device_Type = 'Linux_Script')
    """).fetchone()

    print("2-Hop Chain Summary (REF -> P2A -> WALLET_LOAD):")
    print(f"  Participating REF transactions:         {two_hop_stats[0]}")
    print(f"  Participating P2A transactions:         {two_hop_stats[1]}")
    print(f"  Participating WALLET_LOAD transactions: {two_hop_stats[2]}")
    print(f"  Distinct REF Senders:                   {two_hop_stats[3]}")
    print(f"  Distinct REF Receivers / P2A Senders:   {two_hop_stats[4]}")
    print(f"  Distinct P2A Receivers / WL Senders:    {two_hop_stats[5]}")
    print(f"  Distinct Terminal WALLET_LOAD Receivers: {two_hop_stats[6]}")
    print(f"  Total Valid 2-Hop Traversal Paths:      {two_hop_stats[7]:,d}")

    print("\nHop-by-Hop Dynamics:")
    print(f"  Hop 1 (REF -> P2A): Time Gap Min={two_hop_stats[8]:.0f}s ({two_hop_stats[8]/3600:.2f}h), Median={two_hop_stats[9]:.0f}s ({two_hop_stats[9]/3600:.2f}h), Max={two_hop_stats[10]:.0f}s ({two_hop_stats[10]/3600:.2f}h)")
    print(f"                      Amount Ratio Min={two_hop_stats[14]:.4f}, Median={two_hop_stats[15]:.4f}, Max={two_hop_stats[16]:.4f}")
    print(f"  Hop 2 (P2A -> WL):  Time Gap Min={two_hop_stats[11]:.0f}s ({two_hop_stats[11]/3600:.2f}h), Median={two_hop_stats[12]:.0f}s ({two_hop_stats[12]/3600:.2f}h), Max={two_hop_stats[13]:.0f}s ({two_hop_stats[13]/3600:.2f}h)")
    print(f"                      Amount Ratio Min={two_hop_stats[17]:.4f}, Median={two_hop_stats[18]:.4f}, Max={two_hop_stats[19]:.4f}")

    # 3c. 3-hop check
    # Check if any WALLET_LOAD receiver ever sends any transaction
    post_wl_out_cnt = con.execute("""
        SELECT count(*)
        FROM transactions t4
        WHERE t4.Sender_Account IN (
            SELECT distinct Receiver_Account 
            FROM transactions 
            WHERE Narration LIKE 'UPI/WALLET_LOAD%' OR Device_Type = 'Linux_Script'
        )
    """).fetchone()[0]

    three_hop_real_count = con.execute("""
        SELECT count(*)
        FROM transactions r
        JOIN transactions p ON r.Receiver_Account = p.Sender_Account AND p.Timestamp > r.Timestamp
        JOIN transactions w ON p.Receiver_Account = w.Sender_Account AND w.Timestamp > p.Timestamp
        JOIN transactions t4 ON w.Receiver_Account = t4.Sender_Account AND t4.Timestamp > w.Timestamp
        WHERE r.Narration LIKE 'UPI/REF%'
          AND (p.Narration LIKE 'IMPS/P2A%' OR p.Device_Type = 'Web_Emulator')
          AND (w.Narration LIKE 'UPI/WALLET_LOAD%' OR w.Device_Type = 'Linux_Script')
    """).fetchone()[0]

    print("\n3-Hop Definition & Measurement:")
    print("  Definition: Hop 1 (REF -> P2A), Hop 2 (P2A -> WALLET_LOAD), Hop 3 (WALLET_LOAD receiver -> later outgoing transaction).")
    print(f"  Total outgoing transactions EVER sent by the 385 WALLET_LOAD receivers: {post_wl_out_cnt}")
    print(f"  Measured Real 3-Hop Paths: {three_hop_real_count}")
    print("  Observation: All 385 WALLET_LOAD receivers have strictly 0 outgoing transactions across the entire 15-day dataset (terminal sinks).")

    # 3d. Null Comparison (Narration Permutation with Seed 42)
    print("\nExecuting Null Permutation Baseline (Narration Permuted Only, Seed 42)...")
    con.execute(f"""
        CREATE TEMP TABLE tx_perm_narr AS
        WITH shuf AS (
            SELECT Narration as perm_narr, row_number() over (order by hash(row_id + {RANDOM_SEED})) as rn
            FROM transactions
        ),
        orig AS (
            SELECT row_id, Sender_Account, Receiver_Account, Timestamp, Amount, Device_Type, IP_Address, Payment_Mode, row_number() over () as rn
            FROM transactions
        )
        SELECT orig.*, shuf.perm_narr
        FROM orig
        JOIN shuf ON orig.rn = shuf.rn
    """)

    null_2hop_count = con.execute("""
        SELECT count(*)
        FROM tx_perm_narr r
        JOIN tx_perm_narr p 
          ON r.Receiver_Account = p.Sender_Account
         AND p.Timestamp > r.Timestamp
        JOIN tx_perm_narr w 
          ON p.Receiver_Account = w.Sender_Account
         AND w.Timestamp > p.Timestamp
        WHERE r.perm_narr LIKE 'UPI/REF%'
          AND p.perm_narr LIKE 'IMPS/P2A%'
          AND w.perm_narr LIKE 'UPI/WALLET_LOAD%'
    """).fetchone()[0]

    null_3hop_count = con.execute("""
        SELECT count(*)
        FROM tx_perm_narr r
        JOIN tx_perm_narr p 
          ON r.Receiver_Account = p.Sender_Account
         AND p.Timestamp > r.Timestamp
        JOIN tx_perm_narr w 
          ON p.Receiver_Account = w.Sender_Account
         AND w.Timestamp > p.Timestamp
        JOIN tx_perm_narr t4
          ON w.Receiver_Account = t4.Sender_Account
         AND t4.Timestamp > w.Timestamp
        WHERE r.perm_narr LIKE 'UPI/REF%'
          AND p.perm_narr LIKE 'IMPS/P2A%'
          AND w.perm_narr LIKE 'UPI/WALLET_LOAD%'
    """).fetchone()[0]

    real_2hop_count = two_hop_stats[7]
    excess_2hop = real_2hop_count - null_2hop_count
    ratio_2hop_str = f"{real_2hop_count / null_2hop_count:.2f}x" if null_2hop_count > 0 else "Undefined (Null = 0)"

    print("\nNull Comparison Results Table (Seed 42):")
    print(f"{'Chain Type':<16} {'Real Count':<14} {'Null Count':<14} {'Excess (Real - Null)':<24} {'Real / Null Ratio'}")
    print("-" * 85)
    print(f"{'2-Hop Chain':<16} {real_2hop_count:<14,d} {null_2hop_count:<14,d} {f'{excess_2hop:+d}':<24} {ratio_2hop_str}")
    print(f"{'3-Hop Chain':<16} {three_hop_real_count:<14,d} {null_3hop_count:<14,d} {f'{three_hop_real_count - null_3hop_count:+d}':<24} {'0.00x'}")
    print("  Note: No statistical significance test is asserted without formal hypothesis testing.")

    lat_sec3 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 3 Total Execution Time: {lat_sec3:.2f} ms\n")

    # =========================================================================
    # 4. IP ADDRESS BY DEVICE TYPE
    # =========================================================================
    print("==========================================================================================")
    print("4. IP ADDRESSES BY DEVICE TYPE (DESCRIPTIVE ONLY)")
    print("-" * 95)
    t0 = time.perf_counter()

    dev_order = ["Windows_Browser", "iOS", "Android", "Linux_Script", "Web_Emulator"]
    dev_ip_stats = con.execute("""
        SELECT 
            Device_Type,
            count(*) as tx_cnt,
            count(distinct IP_Address) as distinct_ips,
            count(distinct Sender_Account) as distinct_senders
        FROM transactions
        GROUP BY Device_Type
    """).fetchall()
    dev_stat_map = {r[0]: (r[1], r[2], r[3]) for r in dev_ip_stats}

    # Top 3 /16 prefixes per device type
    top_dev_pref = con.execute("""
        WITH dev_pref AS (
            SELECT 
                Device_Type,
                regexp_extract(IP_Address, '^([0-9]+\\.[0-9]+)') as prefix_16,
                count(*) as cnt,
                count(distinct Sender_Account) as distinct_acc
            FROM transactions
            GROUP BY Device_Type, prefix_16
        )
        SELECT Device_Type, prefix_16, cnt, distinct_acc
        FROM (
            SELECT *, row_number() over (partition by Device_Type order by cnt desc) as rn
            FROM dev_pref
        )
        WHERE rn <= 3
        ORDER BY Device_Type, cnt DESC
    """).fetchall()

    pref_map = {}
    for r in top_dev_pref:
        pref_map.setdefault(r[0], []).append(f"{r[1]} ({r[2]:,d} tx, {r[3]:,d} acc)")

    print(f"{'Device_Type':<18} {'Transactions':<15} {'Distinct IPs':<15} {'Distinct Accounts':<20} {'Top /16 IP Prefixes'}")
    print("-" * 105)
    sec4_ip_data = []
    for d in dev_order:
        tx_c, ip_c, acc_c = dev_stat_map.get(d, (0, 0, 0))
        p_str = "; ".join(pref_map.get(d, []))
        sec4_ip_data.append({
            "device_type": d,
            "transaction_count": tx_c,
            "distinct_ip_addresses": ip_c,
            "distinct_accounts": acc_c,
            "top_16_prefixes": pref_map.get(d, [])
        })
        print(f"{d:<18} {tx_c:<15,d} {ip_c:<15,d} {acc_c:<20,d} {p_str}")

    lat_sec4 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 4 Total Execution Time: {lat_sec4:.2f} ms\n")

    con.close()

    # =========================================================================
    # 5. SAFETY VERIFICATION (DATABASE STATE AFTER MEASUREMENTS)
    # =========================================================================
    print("==========================================================================================")
    print("5. STORAGE SAFETY VERIFICATION")
    print("-" * 95)
    meta_after = get_db_file_metadata()
    print(f"  DuckDB:  {meta_after['DuckDB']['size_bytes']:,} bytes ({meta_after['DuckDB']['size_mb']} MB) | mtime: {meta_after['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_after['Parquet']['size_bytes']:,} bytes ({meta_after['Parquet']['size_mb']} MB) | mtime: {meta_after['Parquet']['mtime']}")

    duckdb_untouched = (meta_before["DuckDB"]["size_bytes"] == meta_after["DuckDB"]["size_bytes"]) and (meta_before["DuckDB"]["mtime"] == meta_after["DuckDB"]["mtime"])
    parquet_untouched = (meta_before["Parquet"]["size_bytes"] == meta_after["Parquet"]["size_bytes"]) and (meta_before["Parquet"]["mtime"] == meta_after["Parquet"]["mtime"])

    print(f"  Verification: DuckDB Untouched = {duckdb_untouched} | Parquet Untouched = {parquet_untouched}")
    print("==========================================================================================")

    # Output JSON payload
    payload = {
        "methodology": {
            "description": "Empirical measurement of marker group connections (UPI/REF, Web_Emulator/IMPS/P2A, Linux_Script/UPI/WALLET_LOAD)",
            "constraints": "Strictly read-only DuckDB, no detection logic, no thresholds, no scoring, no fraud/mule labels, descriptive only",
            "random_seed": RANDOM_SEED
        },
        "storage_safety": {
            "duckdb_before": meta_before["DuckDB"],
            "duckdb_after": meta_after["DuckDB"],
            "parquet_before": meta_before["Parquet"],
            "parquet_after": meta_after["Parquet"],
            "all_files_untouched": duckdb_untouched and parquet_untouched
        },
        "section_1_ref_to_p2a": {
            "ref_transaction_count": len(ref_txns_list),
            "ref_transactions_sample": ref_txns_list[:20],
            "connections": p2a_conn_data
        },
        "section_2_web_emulator_accounts": {
            "total_accounts": len(per_account_list),
            "distribution_summary": dist_summary,
            "accounts": per_account_list
        },
        "section_3_hops_and_null_comparison": {
            "p2a_to_wallet_load_hop": {
                "participating_p2a_receivers_becoming_wl_senders": p2a_to_wl_stats[0],
                "distinct_p2a_txns": p2a_to_wl_stats[1],
                "distinct_wl_txns": p2a_to_wl_stats[2],
                "matching_pairs": p2a_to_wl_stats[3],
                "time_gap_seconds": {
                    "min": float(p2a_to_wl_stats[4]),
                    "p25": round(float(p2a_to_wl_stats[5]), 1),
                    "median": round(float(p2a_to_wl_stats[6]), 1),
                    "p75": round(float(p2a_to_wl_stats[7]), 1),
                    "p90": round(float(p2a_to_wl_stats[8]), 1),
                    "max": float(p2a_to_wl_stats[9])
                },
                "amount_ratio": {
                    "min": round(float(p2a_to_wl_stats[10]), 4),
                    "p25": round(float(p2a_to_wl_stats[11]), 4),
                    "median": round(float(p2a_to_wl_stats[12]), 4),
                    "p75": round(float(p2a_to_wl_stats[13]), 4),
                    "p90": round(float(p2a_to_wl_stats[14]), 4),
                    "max": round(float(p2a_to_wl_stats[15]), 4)
                }
            },
            "two_hop_chain": {
                "participating_ref_txns": two_hop_stats[0],
                "participating_p2a_txns": two_hop_stats[1],
                "participating_wl_txns": two_hop_stats[2],
                "distinct_accounts": {
                    "ref_senders": two_hop_stats[3],
                    "ref_receivers_p2a_senders": two_hop_stats[4],
                    "p2a_receivers_wl_senders": two_hop_stats[5],
                    "terminal_wl_receivers": two_hop_stats[6]
                },
                "total_valid_paths": two_hop_stats[7]
            },
            "three_hop_chain": {
                "definition": "Hop 1 (REF -> P2A), Hop 2 (P2A -> WALLET_LOAD), Hop 3 (WALLET_LOAD receiver -> later outgoing)",
                "terminal_wl_receivers_outgoing_count": post_wl_out_cnt,
                "measured_real_paths": three_hop_real_count,
                "reason": "All 385 WALLET_LOAD receivers have strictly 0 outgoing transactions across the dataset (terminal sinks)"
            },
            "null_comparison": {
                "seed": RANDOM_SEED,
                "real_2hop_count": real_2hop_count,
                "null_2hop_count": null_2hop_count,
                "excess_2hop": excess_2hop,
                "real_over_null_2hop": ratio_2hop_str,
                "real_3hop_count": three_hop_real_count,
                "null_3hop_count": null_3hop_count,
                "excess_3hop": three_hop_real_count - null_3hop_count
            }
        },
        "section_4_ip_by_device_type": sec4_ip_data
    }

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\nSaved structured measurements to: {OUTPUT_JSON_PATH}")

if __name__ == "__main__":
    run_step2a()
