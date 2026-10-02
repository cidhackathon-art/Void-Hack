import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

res = con.execute("""
    SELECT count(*) 
    FROM transactions 
    WHERE Sender_Account IN (
        SELECT distinct Receiver_Account 
        FROM transactions 
        WHERE Narration LIKE 'UPI/WALLET_LOAD%'
    )
""").fetchone()[0]

print("Total outgoing transactions EVER sent by the 385 WL receivers:", res)

# Also check timestamps of the dataset and where WL sits
wl_times = con.execute("""
    SELECT min(Timestamp), max(Timestamp)
    FROM transactions
    WHERE Narration LIKE 'UPI/WALLET_LOAD%'
""").fetchone()
print("WALLET_LOAD timestamp range:", wl_times)

all_times = con.execute("""
    SELECT min(Timestamp), max(Timestamp)
    FROM transactions
""").fetchone()
print("All transactions timestamp range:", all_times)

con.close()
