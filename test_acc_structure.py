import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- Testing Web_Emulator 129 accounts analysis ---")
# 129 accounts that sent Web_Emulator / IMPS/P2A
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
            median(epoch(tout.Timestamp) - epoch(tin.Timestamp)) as med_gap_s,
            max(epoch(tout.Timestamp) - epoch(tin.Timestamp)) as max_gap_s
        FROM transactions tin
        JOIN transactions tout 
          ON tin.Receiver_Account = tout.Sender_Account
         AND tout.Timestamp > tin.Timestamp
        WHERE tin.Receiver_Account IN (SELECT acc FROM p2a_senders)
        GROUP BY tin.Receiver_Account
    )
    SELECT 
        p.acc,
        coalesce(i.in_degree, 0) as in_deg,
        coalesce(o.out_degree, 0) as out_deg,
        coalesce(o.distinct_receivers, 0) as distinct_rec,
        coalesce(i.total_in_amt, 0.0) as in_amt,
        coalesce(o.total_out_amt, 0.0) as out_amt,
        coalesce(o.total_out_amt, 0.0) / nullif(coalesce(i.total_in_amt, 0.0), 0) as out_in_ratio,
        tg.med_gap_s,
        coalesce(o.distinct_ips, 0) as distinct_ips
    FROM p2a_senders p
    LEFT JOIN incoming_stats i ON p.acc = i.acc
    LEFT JOIN outgoing_stats o ON p.acc = o.acc
    LEFT JOIN time_gaps tg ON p.acc = tg.acc
"""
res = con.execute(acc_query).fetchall()
print(f"Total accounts fetched: {len(res)}")
print("Sample account:", res[0])

# Top /16 prefixes across the 129 accounts
top_16 = con.execute("""
    WITH p2a_senders AS (
        SELECT distinct Sender_Account as acc
        FROM transactions
        WHERE Narration LIKE 'IMPS/P2A%' OR Device_Type = 'Web_Emulator'
    )
    SELECT 
        regexp_extract(IP_Address, '^([0-9]+\\.[0-9]+)') as pref16,
        count(*) as cnt,
        count(distinct Sender_Account) as acc_cnt
    FROM transactions
    WHERE Sender_Account IN (SELECT acc FROM p2a_senders)
    GROUP BY pref16
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()
print("Top /16 prefixes across 129 accounts:", top_16)

con.close()
