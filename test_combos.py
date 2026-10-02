import duckdb
from pathlib import Path
from collections import Counter

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

# Test the revised 4+1 indicators across all 24,873 accounts
pt_min = 0.9750
pt_max = 0.9850

test_q = f"""
    WITH all_accounts AS (
        SELECT Sender_Account as acc FROM transactions
        UNION
        SELECT Receiver_Account as acc FROM transactions
    ),
    in_stats AS (
        SELECT 
            Receiver_Account as acc,
            count(*) as in_deg,
            sum(Amount) as in_amt
        FROM transactions
        GROUP BY Receiver_Account
    ),
    out_stats AS (
        SELECT 
            Sender_Account as acc,
            count(*) as out_deg,
            sum(Amount) as out_amt,
            count(distinct Receiver_Account) as distinct_recvs,
            sum(CASE WHEN Device_Type IN ('Linux_Script', 'Web_Emulator') OR IP_Address LIKE '185.%' OR IP_Address LIKE '194.%' THEN 1 ELSE 0 END) as rare_infra_cnt
        FROM transactions
        GROUP BY Sender_Account
    ),
    max_in AS (
        SELECT Receiver_Account as acc, Amount, Timestamp,
               row_number() over (partition by Receiver_Account order by Amount desc, Timestamp asc) as rn
        FROM transactions
    ),
    largest_in AS (
        SELECT acc, Amount as in_amt, Timestamp as in_ts
        FROM max_in WHERE rn = 1
    ),
    window_outs AS (
        SELECT 
            lin.acc,
            lin.in_amt,
            sum(tout.Amount) as sum_out
        FROM largest_in lin
        JOIN transactions tout
          ON lin.acc = tout.Sender_Account
         AND tout.Timestamp > lin.in_ts
         AND tout.Timestamp <= lin.in_ts + INTERVAL 900 SECOND
        GROUP BY lin.acc, lin.in_amt
    )
    SELECT 
        a.acc,
        -- Indicator 1: pass_through (envelope [0.9750, 0.9850])
        CASE WHEN coalesce(o.out_amt, 0.0) / nullif(coalesce(i.in_amt, 0.0), 0) BETWEEN {pt_min} AND {pt_max} THEN 1 ELSE 0 END as ind_pt,
        -- Indicator 2: fan_out (out>=3, in<=8, out>in)
        CASE WHEN coalesce(o.out_deg, 0) >= 3 AND coalesce(i.in_deg, 0) <= 8 AND coalesce(o.out_deg, 0) > coalesce(i.in_deg, 0) THEN 1 ELSE 0 END as ind_fo,
        -- Indicator 3: fast_pass_through (>=50% of max incoming forwarded in 900s)
        CASE WHEN wo.sum_out IS NOT NULL AND wo.sum_out >= 0.50 * lin.in_amt THEN 1 ELSE 0 END as ind_fpt,
        -- Indicator 4: rare_infrastructure (device or IP)
        CASE WHEN coalesce(o.rare_infra_cnt, 0) > 0 THEN 1 ELSE 0 END as ind_rare,
        -- Indicator 5: sink
        CASE WHEN coalesce(i.in_deg, 0) >= 1 AND coalesce(o.out_deg, 0) == 0 THEN 1 ELSE 0 END as ind_sink
    FROM all_accounts a
    LEFT JOIN in_stats i ON a.acc = i.acc
    LEFT JOIN out_stats o ON a.acc = o.acc
    LEFT JOIN largest_in lin ON a.acc = lin.acc
    LEFT JOIN window_outs wo ON a.acc = wo.acc
"""

rows = con.execute(test_q).fetchall()
print(f"Total accounts queried: {len(rows)}")

# Weights: pt=30, fo=20, fpt=20, rare=30, sink=40
combos = Counter()
scores = []
for r in rows:
    acc, pt, fo, fpt, rare, sink = r
    score = pt*30 + fo*20 + fpt*20 + rare*30 + sink*40
    score = min(100, score)
    scores.append(score)
    inds = []
    if pt: inds.append("pass_through")
    if fo: inds.append("fan_out")
    if fpt: inds.append("fast_pass_through")
    if rare: inds.append("rare_infrastructure")
    if sink: inds.append("sink")
    combo_str = "+".join(inds) if inds else "none"
    combos[(score, combo_str)] += 1

print("\n--- Indicator Combinations Table ---")
tot = 0
for (sc, cmb), cnt in sorted(combos.items(), key=lambda x: -x[0][0]):
    print(f"Score {sc:>3} | Count: {cnt:>6} | Combination: {cmb}")
    tot += cnt
print(f"Total sum of counts: {tot} (Matches 24,873: {tot == 24873})")

# Check score percentiles / median
scores.sort()
med_score = scores[len(scores)//2]
print(f"\nMedian score across all accounts: {med_score}")
print(f"Score 0 count: {scores.count(0)}")

con.close()
