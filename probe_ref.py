import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- Inspect UPI/REF rows ---")
sample_ref = con.execute("""
    SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp, Payment_Mode, Device_Type, IP_Address, Narration
    FROM transactions
    WHERE Narration LIKE '%REF%'
    LIMIT 10
""").fetchall()
for r in sample_ref:
    print(r)

print("\n--- Who are the senders and receivers of UPI/REF? ---")
ref_accs = con.execute("""
    SELECT 
        count(distinct Sender_Account) as distinct_senders,
        count(distinct Receiver_Account) as distinct_receivers,
        min(Timestamp) as min_ts,
        max(Timestamp) as max_ts
    FROM transactions
    WHERE Narration = 'UPI/REF'
""").fetchall()
print("REF accounts:", ref_accs)

print("\n--- Check if REF Senders appear in P2A or WALLET_LOAD ---")
print("REF Senders appearing as P2A Senders:", con.execute("""
    SELECT count(distinct r.Sender_Account)
    FROM transactions r
    JOIN transactions p ON r.Sender_Account = p.Sender_Account
    WHERE r.Narration = 'UPI/REF' AND p.Narration = 'IMPS/P2A'
""").fetchall())

print("REF Senders appearing as P2A Receivers:", con.execute("""
    SELECT count(distinct r.Sender_Account)
    FROM transactions r
    JOIN transactions p ON r.Sender_Account = p.Receiver_Account
    WHERE r.Narration = 'UPI/REF' AND p.Narration = 'IMPS/P2A'
""").fetchall())

print("REF Receivers appearing as P2A Receivers:", con.execute("""
    SELECT count(distinct r.Receiver_Account)
    FROM transactions r
    JOIN transactions p ON r.Receiver_Account = p.Receiver_Account
    WHERE r.Narration = 'UPI/REF' AND p.Narration = 'IMPS/P2A'
""").fetchall())

print("\n--- Let's check the accounts of P2A (Web_Emulator) ---")
p2a_accs = con.execute("""
    SELECT 
        count(distinct Sender_Account) as p2a_senders,
        count(distinct Receiver_Account) as p2a_receivers,
        min(Timestamp), max(Timestamp)
    FROM transactions
    WHERE Narration = 'IMPS/P2A'
""").fetchall()
print("P2A accs:", p2a_accs)

print("\n--- Let's check overlap of REF accounts with P2A accounts ---")
overlap = con.execute("""
    WITH ref_a AS (
        SELECT Sender_Account as acc FROM transactions WHERE Narration = 'UPI/REF'
        UNION
        SELECT Receiver_Account as acc FROM transactions WHERE Narration = 'UPI/REF'
    ),
    p2a_a AS (
        SELECT Sender_Account as acc FROM transactions WHERE Narration = 'IMPS/P2A'
        UNION
        SELECT Receiver_Account as acc FROM transactions WHERE Narration = 'IMPS/P2A'
    ),
    wl_a AS (
        SELECT Sender_Account as acc FROM transactions WHERE Narration = 'UPI/WALLET_LOAD'
        UNION
        SELECT Receiver_Account as acc FROM transactions WHERE Narration = 'UPI/WALLET_LOAD'
    )
    SELECT 
        (SELECT count(*) FROM ref_a) as ref_acc_cnt,
        (SELECT count(*) FROM p2a_a) as p2a_acc_cnt,
        (SELECT count(*) FROM wl_a) as wl_acc_cnt,
        (SELECT count(*) FROM ref_a INTERSECT SELECT acc FROM p2a_a) as ref_p2a_intersect,
        (SELECT count(*) FROM ref_a INTERSECT SELECT acc FROM wl_a) as ref_wl_intersect,
        (SELECT count(*) FROM p2a_a INTERSECT SELECT acc FROM wl_a) as p2a_wl_intersect
""").fetchall()
print("Account overlaps:", overlap)

con.close()
