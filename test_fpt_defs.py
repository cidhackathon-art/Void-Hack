import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

# 129 marker group
marker_129 = set(con.execute("""
    SELECT distinct Sender_Account 
    FROM transactions 
    WHERE Narration LIKE 'IMPS/P2A%' OR Device_Type = 'Web_Emulator'
""").fetchall())
marker_129 = {r[0] for r in marker_129}
print(f"Marker group count: {len(marker_129)}")

# Pre-calculate largest incoming transaction per account
print("\n--- Testing Definition 1: Immediate next outgoing after largest incoming transaction <= 900s ---")
d1_query = """
    WITH max_in AS (
        SELECT Receiver_Account as acc, Amount, Timestamp,
               row_number() over (partition by Receiver_Account order by Amount desc, Timestamp asc) as rn
        FROM transactions
    ),
    largest_in AS (
        SELECT acc, Amount as in_amt, Timestamp as in_ts
        FROM max_in WHERE rn = 1
    ),
    asof_match AS (
        SELECT 
            lin.acc,
            lin.in_amt,
            epoch(tout.Timestamp) - epoch(lin.in_ts) as gap_s,
            tout.Amount as out_amt
        FROM largest_in lin
        ASOF JOIN transactions tout
          ON lin.acc = tout.Sender_Account
         AND lin.in_ts < tout.Timestamp
    )
    SELECT acc, gap_s, out_amt, in_amt
    FROM asof_match
    WHERE gap_s <= 900
"""
d1_res = con.execute(d1_query).fetchall()
d1_accs = {r[0] for r in d1_res}
m129_d1 = len(d1_accs.intersection(marker_129))
outside_d1 = len(d1_accs - marker_129)
print(f"Def 1 (Gap <= 900s after largest in): 129 matches = {m129_d1} / 129, Non-129 matches = {outside_d1}")

print("\n--- Testing Definition 2: Gap <= 900s after largest in AND out_amt >= 0.05 * in_amt ---")
d2_accs = {r[0] for r in d1_res if r[2] >= 0.05 * r[3]}
m129_d2 = len(d2_accs.intersection(marker_129))
outside_d2 = len(d2_accs - marker_129)
print(f"Def 2 (Gap <= 900s AND out_amt >= 5% of max in): 129 matches = {m129_d2} / 129, Non-129 matches = {outside_d2}")

print("\n--- Testing Definition 3: Amount-weighted: sum(outgoing in 900s after largest in) >= 0.20 * max in ---")
d3_query = """
    WITH max_in AS (
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
    SELECT acc
    FROM window_outs
    WHERE sum_out >= 0.20 * in_amt
"""
d3_res = con.execute(d3_query).fetchall()
d3_accs = {r[0] for r in d3_res}
m129_d3 = len(d3_accs.intersection(marker_129))
outside_d3 = len(d3_accs - marker_129)
print(f"Def 3 (sum(out in 900s) >= 20% of max in): 129 matches = {m129_d3} / 129, Non-129 matches = {outside_d3}")

print("\n--- Let's also check Def 1 with smaller window, e.g. 750s (max observed in 129 group was 750s) ---")
d4_query = """
    WITH max_in AS (
        SELECT Receiver_Account as acc, Amount, Timestamp,
               row_number() over (partition by Receiver_Account order by Amount desc, Timestamp asc) as rn
        FROM transactions
    ),
    largest_in AS (
        SELECT acc, Amount as in_amt, Timestamp as in_ts
        FROM max_in WHERE rn = 1
    ),
    asof_match AS (
        SELECT 
            lin.acc,
            epoch(tout.Timestamp) - epoch(lin.in_ts) as gap_s
        FROM largest_in lin
        ASOF JOIN transactions tout
          ON lin.acc = tout.Sender_Account
         AND lin.in_ts < tout.Timestamp
    )
    SELECT acc
    FROM asof_match
    WHERE gap_s <= 750
"""
d4_res = con.execute(d4_query).fetchall()
d4_accs = {r[0] for r in d4_res}
m129_d4 = len(d4_accs.intersection(marker_129))
outside_d4 = len(d4_accs - marker_129)
print(f"Def 4 (Gap <= 750s after largest in): 129 matches = {m129_d4} / 129, Non-129 matches = {outside_d4}")

con.close()
