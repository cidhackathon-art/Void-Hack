import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

RANDOM_SEED = 42

print("--- Testing Null Permutation of Narration Only (Seed 42) ---")
# Permute narration only:
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

print("Permuted table created.")

# Real 2-hop count:
real_2hop = con.execute("""
    SELECT count(*)
    FROM transactions r
    JOIN transactions p 
      ON r.Receiver_Account = p.Sender_Account
     AND p.Timestamp > r.Timestamp
    JOIN transactions w 
      ON p.Receiver_Account = w.Sender_Account
     AND w.Timestamp > p.Timestamp
    WHERE r.Narration LIKE 'UPI/REF%'
      AND p.Narration LIKE 'IMPS/P2A%'
      AND w.Narration LIKE 'UPI/WALLET_LOAD%'
""").fetchone()[0]

print("Real 2-hop count:", real_2hop)

# Null 2-hop count:
null_2hop = con.execute("""
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

print("Null 2-hop count:", null_2hop)

# Null 3-hop count:
null_3hop = con.execute("""
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

print("Null 3-hop count:", null_3hop)

con.close()
