import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- Device Types IP Analysis ---")
dev_ip = con.execute("""
    SELECT 
        Device_Type,
        count(*) as tx_cnt,
        count(distinct IP_Address) as distinct_ips,
        count(distinct Sender_Account) as distinct_senders
    FROM transactions
    GROUP BY Device_Type
    ORDER BY tx_cnt DESC
""").fetchall()
for d in dev_ip:
    print(d)

print("\n--- Top /16 prefixes per device ---")
top_prefixes = con.execute("""
    WITH dev_pref AS (
        SELECT 
            Device_Type,
            regexp_extract(IP_Address, '^([0-9]+\\.[0-9]+)') as prefix_16,
            count(*) as cnt
        FROM transactions
        GROUP BY Device_Type, prefix_16
    )
    SELECT Device_Type, prefix_16, cnt
    FROM (
        SELECT *, row_number() over (partition by Device_Type order by cnt desc) as rn
        FROM dev_pref
    )
    WHERE rn <= 5
    ORDER BY Device_Type, cnt DESC
""").fetchall()
for p in top_prefixes:
    print(p)

con.close()
