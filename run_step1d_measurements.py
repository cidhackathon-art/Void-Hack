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
OUTPUT_JSON_PATH = PROJECT_ROOT / "data" / "measurements_step1d.json"

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

def run_step1d():
    print("==========================================================================================")
    print("  OPERATION ABHEDYA-CHAKRA -- PHASE 2 STEP 1D: FIND THE REAL SIGNAL (MEASUREMENT ONLY)")
    print("  Strictly Read-Only DuckDB | No Thresholds | No Mule/Fraud Labels | Descriptive Only")
    print("==========================================================================================\n")

    # DB Safety: Before
    meta_before = get_db_file_metadata()
    print("DATABASE STATE BEFORE MEASUREMENTS:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------\n")

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # =========================================================================
    # 1. RATIO BUMP INSPECTION: [0.95, 0.97) vs CONTROLS [0.93, 0.95) & [0.97, 0.99)
    # =========================================================================
    print("1. RATIO BUMP INSPECTION: [0.95, 0.97) vs CONTROLS [0.93, 0.95) & [0.97, 0.99)")
    print("-" * 95)
    t0 = time.perf_counter()

    # Pre-aggregate matched table
    con.execute("""
        CREATE TEMP TABLE matched_asof AS
        SELECT 
            tin.Receiver_Account as acc,
            tin.Amount as a_in,
            tout.Amount as a_out,
            tout.Amount / tin.Amount as ratio,
            epoch(tout.Timestamp) - epoch(tin.Timestamp) as gap_sec,
            tout.Device_Type as dev_out,
            tout.Payment_Mode as mode_out,
            tout.Narration as narr_out,
            tin.Device_Type as dev_in,
            tin.Payment_Mode as mode_in,
            tin.Narration as narr_in
        FROM transactions tin
        ASOF JOIN transactions tout
          ON tin.Receiver_Account = tout.Sender_Account
         AND tin.Timestamp < tout.Timestamp
    """)

    # Overview table
    res1_overview = con.execute("""
        SELECT 
            CASE 
                WHEN ratio >= 0.93 AND ratio < 0.95 THEN 'Control Lower [0.93, 0.95)'
                WHEN ratio >= 0.95 AND ratio < 0.97 THEN 'Target Bump   [0.95, 0.97)'
                WHEN ratio >= 0.97 AND ratio < 0.99 THEN 'Control Upper [0.97, 0.99)'
            END as bin_group,
            count(*) as pair_count,
            count(distinct acc) as distinct_accounts,
            min(gap_sec) as min_gap,
            quantile_cont(gap_sec, 0.25) as p25_gap,
            median(gap_sec) as median_gap,
            quantile_cont(gap_sec, 0.75) as p75_gap,
            quantile_cont(gap_sec, 0.90) as p90_gap,
            max(gap_sec) as max_gap
        FROM matched_asof
        WHERE ratio >= 0.93 AND ratio < 0.99
        GROUP BY bin_group
        ORDER BY bin_group
    """).fetchall()

    print(f"{'Group':<28} {'Pairs':<10} {'Accounts':<10} {'Min Gap':<10} {'p25 (h)':<10} {'Median (h)':<12} {'p75 (h)':<10} {'p90 (h)':<10} {'Max (h)':<10}")
    print("-" * 115)
    sec1_overview_data = {}
    for r in res1_overview:
        g_name = r[0]
        sec1_overview_data[g_name] = {
            "pair_count": r[1],
            "distinct_accounts": r[2],
            "gap_seconds": {
                "min": r[3],
                "p25": round(r[4], 1),
                "median": round(r[5], 1),
                "p75": round(r[6], 1),
                "p90": round(r[7], 1),
                "max": r[8]
            },
            "gap_hours": {
                "min": round(r[3] / 3600, 2),
                "p25": round(r[4] / 3600, 2),
                "median": round(r[5] / 3600, 2),
                "p75": round(r[6] / 3600, 2),
                "p90": round(r[7] / 3600, 2),
                "max": round(r[8] / 3600, 2)
            }
        }
        print(f"{g_name:<28} {r[1]:<10,d} {r[2]:<10,d} {r[3]:<10.0f}s {r[4]/3600:<10.2f} {r[5]/3600:<12.2f} {r[6]/3600:<10.2f} {r[7]/3600:<10.2f} {r[8]/3600:<10.2f}")

    # Device Type Comparison
    res1_dev = con.execute("""
        SELECT 
            dev_out,
            count(CASE WHEN ratio >= 0.93 AND ratio < 0.95 THEN 1 END) as cnt_lower,
            count(CASE WHEN ratio >= 0.95 AND ratio < 0.97 THEN 1 END) as cnt_bump,
            count(CASE WHEN ratio >= 0.97 AND ratio < 0.99 THEN 1 END) as cnt_upper
        FROM matched_asof
        WHERE ratio >= 0.93 AND ratio < 0.99
        GROUP BY dev_out
        ORDER BY cnt_bump DESC
    """).fetchall()

    print("\nDevice_Type Breakdown across Bins:")
    print(f"{'Device_Type (Outgoing)':<25} {'Control Lower':<18} {'Target Bump [0.95,0.97)':<26} {'Control Upper':<18} {'Bump Excess':<12}")
    print("-" * 105)
    sec1_dev_data = []
    for r in res1_dev:
        expected = (r[1] + r[3]) / 2.0
        excess = r[2] - expected
        sec1_dev_data.append({
            "device": r[0],
            "control_lower": r[1],
            "target_bump": r[2],
            "control_upper": r[3],
            "excess": round(excess, 1)
        })
        print(f"{r[0]:<25} {r[1]:<18,d} {r[2]:<26,d} {r[3]:<18,d} {excess:+,.0f}")

    # Payment Mode Comparison
    res1_mode = con.execute("""
        SELECT 
            mode_out,
            count(CASE WHEN ratio >= 0.93 AND ratio < 0.95 THEN 1 END) as cnt_lower,
            count(CASE WHEN ratio >= 0.95 AND ratio < 0.97 THEN 1 END) as cnt_bump,
            count(CASE WHEN ratio >= 0.97 AND ratio < 0.99 THEN 1 END) as cnt_upper
        FROM matched_asof
        WHERE ratio >= 0.93 AND ratio < 0.99
        GROUP BY mode_out
        ORDER BY cnt_bump DESC
    """).fetchall()

    print("\nPayment_Mode Breakdown across Bins:")
    print(f"{'Payment_Mode (Outgoing)':<25} {'Control Lower':<18} {'Target Bump [0.95,0.97)':<26} {'Control Upper':<18} {'Bump Excess':<12}")
    print("-" * 105)
    sec1_mode_data = []
    for r in res1_mode:
        expected = (r[1] + r[3]) / 2.0
        excess = r[2] - expected
        sec1_mode_data.append({
            "mode": r[0],
            "control_lower": r[1],
            "target_bump": r[2],
            "control_upper": r[3],
            "excess": round(excess, 1)
        })
        print(f"{r[0]:<25} {r[1]:<18,d} {r[2]:<26,d} {r[3]:<18,d} {excess:+,.0f}")

    # Top-20 Accounts in Bump
    res1_top_accs = con.execute("""
        SELECT 
            acc,
            count(*) as cnt,
            min(gap_sec) as min_gap,
            max(gap_sec) as max_gap,
            string_agg(distinct dev_out, ', ') as dev_types
        FROM matched_asof
        WHERE ratio >= 0.95 AND ratio < 0.97
        GROUP BY acc
        ORDER BY cnt DESC
        LIMIT 20
    """).fetchall()

    print("\nTop-20 Accounts in Target Bump [0.95, 0.97):")
    print(f"{'Account ID':<18} {'Count in Bump':<16} {'Min Gap (s)':<14} {'Max Gap (s)':<14} {'Device Types'}")
    print("-" * 85)
    sec1_acc_data = []
    for r in res1_top_accs:
        sec1_acc_data.append({
            "account": r[0],
            "count": r[1],
            "min_gap_sec": r[2],
            "max_gap_sec": r[3],
            "device_types": r[4]
        })
        print(f"{r[0]:<18} {r[1]:<16} {r[2]:<14.0f} {r[3]:<14.0f} {r[4]}")

    # Top-20 Narration Patterns in Bump vs Controls
    res1_top_narr = con.execute("""
        WITH bump_narr AS (
            SELECT 
                regexp_extract(narr_out, '^[^/]+/[^/#]+') as narr_prefix,
                count(CASE WHEN ratio >= 0.93 AND ratio < 0.95 THEN 1 END) as cnt_lower,
                count(CASE WHEN ratio >= 0.95 AND ratio < 0.97 THEN 1 END) as cnt_bump,
                count(CASE WHEN ratio >= 0.97 AND ratio < 0.99 THEN 1 END) as cnt_upper
            FROM matched_asof
            WHERE ratio >= 0.93 AND ratio < 0.99
            GROUP BY narr_prefix
        )
        SELECT narr_prefix, cnt_lower, cnt_bump, cnt_upper, (cnt_bump - (cnt_lower + cnt_upper)/2.0) as excess
        FROM bump_narr
        ORDER BY cnt_bump DESC
        LIMIT 20
    """).fetchall()

    print("\nTop-20 Narration Patterns in Bump vs Controls:")
    print(f"{'Narration Pattern':<25} {'Control Lower':<16} {'Target Bump':<16} {'Control Upper':<16} {'Excess in Bump'}")
    print("-" * 95)
    sec1_narr_data = []
    for r in res1_top_narr:
        sec1_narr_data.append({
            "narration_prefix": r[0],
            "control_lower": r[1],
            "target_bump": r[2],
            "control_upper": r[3],
            "excess": round(r[4], 1)
        })
        print(f"{r[0]:<25} {r[1]:<16,d} {r[2]:<16,d} {r[3]:<16,d} {r[4]:+,.1f}")

    lat_sec1 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 1 Total Execution Time: {lat_sec1:.2f} ms\n")

    # =========================================================================
    # 2. OVERALL: AMOUNT QUANTILES, SHARES, AND CONCENTRATION
    # =========================================================================
    print("==========================================================================================")
    print("2. OVERALL POPULATION: AMOUNT QUANTILES, SHARES & ATTRIBUTE CONCENTRATION")
    print("-" * 95)
    t0 = time.perf_counter()

    # Amount quantiles
    amt_q = con.execute("""
        SELECT quantile_cont(Amount, [0.50, 0.90, 0.99, 0.999, 1.0])
        FROM transactions
    """).fetchone()[0]
    q_labels = ["p50", "p90", "p99", "p99.9", "max"]
    amt_dict = {k: round(float(v), 2) for k, v in zip(q_labels, amt_q)}

    print("Overall Transaction Amount Quantiles (INR):")
    for k in q_labels:
        print(f"  {k:<8}: INR {amt_dict[k]:,.2f}")

    # Device_Type shares and concentration
    res2_dev = con.execute("""
        SELECT 
            Device_Type,
            count(*) as tx_count,
            round(count(*) * 100.0 / 2000000, 2) as pct_share,
            count(distinct Sender_Account) as distinct_senders,
            round(count(distinct Sender_Account) * 100.0 / 24873, 2) as pct_senders_using,
            count(distinct Receiver_Account) as distinct_receivers
        FROM transactions
        GROUP BY Device_Type
        ORDER BY tx_count DESC
    """).fetchall()

    print("\nDevice_Type Overall Shares & Account Concentration:")
    print(f"{'Device_Type':<20} {'Transactions':<15} {'% Share':<12} {'Distinct Senders':<20} {'% of All Accounts'}")
    print("-" * 85)
    sec2_dev_data = []
    for r in res2_dev:
        sec2_dev_data.append({
            "device": r[0],
            "transaction_count": r[1],
            "share_pct": r[2],
            "distinct_senders": r[3],
            "account_coverage_pct": r[4]
        })
        print(f"{r[0]:<20} {r[1]:<15,d} {f'{r[2]:.2f}%':<12} {r[3]:<20,d} {f'{r[4]:.2f}%':<15}")

    # Payment_Mode shares and concentration
    res2_mode = con.execute("""
        SELECT 
            Payment_Mode,
            count(*) as tx_count,
            round(count(*) * 100.0 / 2000000, 2) as pct_share,
            count(distinct Sender_Account) as distinct_senders,
            round(count(distinct Sender_Account) * 100.0 / 24873, 2) as pct_senders_using
        FROM transactions
        GROUP BY Payment_Mode
        ORDER BY tx_count DESC
    """).fetchall()

    print("\nPayment_Mode Overall Shares & Account Concentration:")
    print(f"{'Payment_Mode':<20} {'Transactions':<15} {'% Share':<12} {'Distinct Senders':<20} {'% of All Accounts'}")
    print("-" * 85)
    sec2_mode_data = []
    for r in res2_mode:
        sec2_mode_data.append({
            "mode": r[0],
            "transaction_count": r[1],
            "share_pct": r[2],
            "distinct_senders": r[3],
            "account_coverage_pct": r[4]
        })
        print(f"{r[0]:<20} {r[1]:<15,d} {f'{r[2]:.2f}%':<12} {r[3]:<20,d} {f'{r[4]:.2f}%':<15}")

    # Top-20 Narration Patterns & Concentration
    res2_narr = con.execute("""
        SELECT 
            regexp_extract(Narration, '^[^/]+/[^/#]+') as narr_prefix,
            count(*) as tx_count,
            round(count(*) * 100.0 / 2000000, 2) as pct_share,
            count(distinct Sender_Account) as distinct_senders,
            round(count(distinct Sender_Account) * 100.0 / 24873, 2) as pct_senders_using
        FROM transactions
        GROUP BY narr_prefix
        ORDER BY tx_count DESC
        LIMIT 20
    """).fetchall()

    print("\nTop-20 Narration Patterns & Account Concentration:")
    print(f"{'Narration Pattern':<25} {'Transactions':<15} {'% Share':<12} {'Distinct Senders':<18} {'Account Coverage'}")
    print("-" * 90)
    sec2_narr_data = []
    for r in res2_narr:
        sec2_narr_data.append({
            "narration": r[0],
            "transaction_count": r[1],
            "share_pct": r[2],
            "distinct_senders": r[3],
            "account_coverage_pct": r[4]
        })
        print(f"{r[0]:<25} {r[1]:<15,d} {f'{r[2]:.2f}%':<12} {r[3]:<18,d} {f'{r[4]:.2f}%':<15}")

    lat_sec2 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 2 Total Execution Time: {lat_sec2:.2f} ms\n")

    # =========================================================================
    # 3. EXCESS-OVER-NULL TEST: W in {5m, 15m, 1h, 6h} x Tol in {1%, 3%, 5%, 10%}
    # =========================================================================
    print("==========================================================================================")
    print("3. EXCESS-OVER-NULL MATRIX (REAL VS PERMUTED AMOUNTS, SEED = 42)")
    print("Same account, outgoing within W after incoming, ratio within tolerance")
    print("-" * 95)
    t0 = time.perf_counter()

    # Create permuted table with seed 42
    con.execute(f"""
        CREATE TEMP TABLE tx_permuted AS
        WITH shuf AS (
            SELECT Amount as perm_amt, row_number() over (order by hash(row_id + {RANDOM_SEED})) as rn
            FROM transactions
        ),
        orig AS (
            SELECT row_id, Sender_Account, Receiver_Account, Timestamp, Amount, row_number() over () as rn
            FROM transactions
        )
        SELECT orig.row_id, orig.Sender_Account, orig.Receiver_Account, orig.Timestamp, orig.Amount, shuf.perm_amt
        FROM orig
        JOIN shuf ON orig.rn = shuf.rn
    """)

    windows_p3 = [
        (300, "5m"),
        (900, "15m"),
        (3600, "1h"),
        (21600, "6h")
    ]
    tols_p3 = [
        (0.01, "1%"),
        (0.03, "3%"),
        (0.05, "5%"),
        (0.10, "10%")
    ]

    sec3_matrix_data = []
    print(f"{'Window (W)':<12} {'Tolerance':<12} {'Real Pairs':<14} {'Null Pairs':<14} {'Excess (Real - Null)':<22} {'Real / Null Ratio'}")
    print("-" * 88)

    for w_sec, w_lbl in windows_p3:
        query_w = f"""
            WITH tin AS (
                SELECT Receiver_Account as acc, Timestamp, Amount, perm_amt 
                FROM tx_permuted
            ),
            tout AS (
                SELECT Sender_Account as acc, Timestamp, Amount, perm_amt 
                FROM tx_permuted
            )
            SELECT 
                count(*) as total_window_pairs,
                count(CASE WHEN abs(tout.Amount / tin.Amount - 1.0) <= 0.01 THEN 1 END) as r_1,
                count(CASE WHEN abs(tout.perm_amt / tin.perm_amt - 1.0) <= 0.01 THEN 1 END) as n_1,
                count(CASE WHEN abs(tout.Amount / tin.Amount - 1.0) <= 0.03 THEN 1 END) as r_3,
                count(CASE WHEN abs(tout.perm_amt / tin.perm_amt - 1.0) <= 0.03 THEN 1 END) as n_3,
                count(CASE WHEN abs(tout.Amount / tin.Amount - 1.0) <= 0.05 THEN 1 END) as r_5,
                count(CASE WHEN abs(tout.perm_amt / tin.perm_amt - 1.0) <= 0.05 THEN 1 END) as n_5,
                count(CASE WHEN abs(tout.Amount / tin.Amount - 1.0) <= 0.10 THEN 1 END) as r_10,
                count(CASE WHEN abs(tout.perm_amt / tin.perm_amt - 1.0) <= 0.10 THEN 1 END) as n_10
            FROM tin
            JOIN tout ON tin.acc = tout.acc
              AND tout.Timestamp > tin.Timestamp
              AND tout.Timestamp <= tin.Timestamp + INTERVAL {w_sec} SECOND
        """
        w_res = con.execute(query_w).fetchone()
        tot_pairs = w_res[0]

        r_map = {0.01: w_res[1], 0.03: w_res[3], 0.05: w_res[5], 0.10: w_res[7]}
        n_map = {0.01: w_res[2], 0.03: w_res[4], 0.05: w_res[6], 0.10: w_res[8]}

        for t_val, t_lbl in tols_p3:
            r_cnt = r_map[t_val]
            n_cnt = n_map[t_val]
            excess = r_cnt - n_cnt
            ratio_val = round(r_cnt / max(1, n_cnt), 3)

            sec3_matrix_data.append({
                "window": w_lbl,
                "window_seconds": w_sec,
                "tolerance": t_lbl,
                "tolerance_float": t_val,
                "total_window_pairs": tot_pairs,
                "real_pairs": r_cnt,
                "null_pairs": n_cnt,
                "excess": excess,
                "real_over_null": ratio_val
            })
            excess_str = f"{excess:+d}"
            print(f"{w_lbl:<12} {t_lbl:<12} {r_cnt:<14,d} {n_cnt:<14,d} {excess_str:<22} {ratio_val:.3f}x")

    lat_sec3 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 3 Total Execution Time: {lat_sec3:.2f} ms\n")

    # =========================================================================
    # 4. WINDOW CONSERVATION: sum(outgoing in W) / incoming amount (REAL VS NULL)
    # =========================================================================
    print("==========================================================================================")
    print("4. WINDOW CONSERVATION: sum(outgoing in W) / incoming amount (REAL VS NULL)")
    print("-" * 95)
    t0 = time.perf_counter()

    sec4_conservation_data = {}
    p_keys = ["p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99", "max"]

    for w_sec, w_lbl in windows_p3:
        query_cons = f"""
            WITH tin AS (
                SELECT row_id, Receiver_Account as acc, Timestamp, Amount, perm_amt 
                FROM tx_permuted
            ),
            tout AS (
                SELECT Sender_Account as acc, Timestamp, Amount, perm_amt 
                FROM tx_permuted
            ),
            window_agg AS (
                SELECT 
                    tin.row_id,
                    tin.Amount as real_in,
                    tin.perm_amt as null_in,
                    sum(tout.Amount) as real_out_sum,
                    sum(tout.perm_amt) as null_out_sum
                FROM tin
                JOIN tout ON tin.acc = tout.acc
                  AND tout.Timestamp > tin.Timestamp
                  AND tout.Timestamp <= tin.Timestamp + INTERVAL {w_sec} SECOND
                GROUP BY tin.row_id, tin.Amount, tin.perm_amt
            )
            SELECT 
                count(*) as tx_with_outgoing,
                quantile_cont(real_out_sum / real_in, [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 1.0]) as real_q,
                quantile_cont(null_out_sum / null_in, [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 1.0]) as null_q
            FROM window_agg
        """
        res_c = con.execute(query_cons).fetchone()
        cnt_out = res_c[0]
        real_q = [round(float(v), 4) for v in res_c[1]]
        null_q = [round(float(v), 4) for v in res_c[2]]

        sec4_conservation_data[w_lbl] = {
            "window": w_lbl,
            "window_seconds": w_sec,
            "incoming_tx_with_outgoing": cnt_out,
            "real_percentiles": {k: v for k, v in zip(p_keys, real_q)},
            "null_percentiles": {k: v for k, v in zip(p_keys, null_q)}
        }

        print(f"\nWindow W = {w_lbl} (Rows with outgoing in window: {cnt_out:,}):")
        print(f"  {'Model':<10} {'p1':<10} {'p5':<10} {'p10':<10} {'p25':<10} {'Median (p50)':<14} {'p75':<10} {'p90':<10} {'p95':<10} {'p99':<10} {'Max':<12}")
        print("  " + "-" * 110)
        print(f"  {'Real':<10} {real_q[0]:<10.4f} {real_q[1]:<10.4f} {real_q[2]:<10.4f} {real_q[3]:<10.4f} {real_q[4]:<14.4f} {real_q[5]:<10.4f} {real_q[6]:<10.4f} {real_q[7]:<10.4f} {real_q[8]:<10.4f} {real_q[9]:<12.4f}")
        print(f"  {'Null':<10} {null_q[0]:<10.4f} {null_q[1]:<10.4f} {null_q[2]:<10.4f} {null_q[3]:<10.4f} {null_q[4]:<14.4f} {null_q[5]:<10.4f} {null_q[6]:<10.4f} {null_q[7]:<10.4f} {null_q[8]:<10.4f} {null_q[9]:<12.4f}")

    lat_sec4 = (time.perf_counter() - t0) * 1000
    print(f"\nPart 4 Total Execution Time: {lat_sec4:.2f} ms\n")

    con.close()

    # =========================================================================
    # 5. SAFETY & STORAGE INTEGRITY
    # =========================================================================
    print("==========================================================================================")
    print("5. SAFETY VERIFICATION (DATABASE STATE AFTER MEASUREMENTS)")
    print("-" * 95)
    meta_after = get_db_file_metadata()
    print(f"  DuckDB:  {meta_after['DuckDB']['size_bytes']:,} bytes ({meta_after['DuckDB']['size_mb']} MB) | mtime: {meta_after['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_after['Parquet']['size_bytes']:,} bytes ({meta_after['Parquet']['size_mb']} MB) | mtime: {meta_after['Parquet']['mtime']}")

    duckdb_untouched = (meta_before["DuckDB"]["size_bytes"] == meta_after["DuckDB"]["size_bytes"]) and (meta_before["DuckDB"]["mtime"] == meta_after["DuckDB"]["mtime"])
    parquet_untouched = (meta_before["Parquet"]["size_bytes"] == meta_after["Parquet"]["size_bytes"]) and (meta_before["Parquet"]["mtime"] == meta_after["Parquet"]["mtime"])

    print(f"  Verification: DuckDB Untouched = {duckdb_untouched} | Parquet Untouched = {parquet_untouched}")
    print("==========================================================================================")

    # Save to JSON
    output_payload = {
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "database_state_verified": duckdb_untouched and parquet_untouched,
            "random_seed": RANDOM_SEED,
            "duckdb_file": meta_after["DuckDB"],
            "parquet_file": meta_after["Parquet"]
        },
        "part_1_ratio_bump_inspection": {
            "overview": sec1_overview_data,
            "device_type_breakdown": sec1_dev_data,
            "payment_mode_breakdown": sec1_mode_data,
            "top_20_accounts": sec1_acc_data,
            "top_20_narration_patterns": sec1_narr_data
        },
        "part_2_overall_population": {
            "amount_quantiles": amt_dict,
            "device_type_shares": sec2_dev_data,
            "payment_mode_shares": sec2_mode_data,
            "top_20_narrations": sec2_narr_data
        },
        "part_3_excess_over_null_matrix": sec3_matrix_data,
        "part_4_window_conservation": sec4_conservation_data
    }

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, default=float)

    print(f"\nSaved structured measurements to: {OUTPUT_JSON_PATH}")

if __name__ == "__main__":
    run_step1d()
