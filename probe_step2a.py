import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- 1. UPI/REF basic count and preview ---")
ref_tx = con.execute("""
    SELECT count(*), min(Timestamp), max(Timestamp), min(Amount), max(Amount)
    FROM transactions
    WHERE Narration LIKE '%REF%'
""").fetchall()
print("REF summary:", ref_tx)

print("\n--- 1b. Check narration values containing REF ---")
print(con.execute("SELECT Narration, count(*) FROM transactions WHERE Narration LIKE '%REF%' GROUP BY Narration").fetchall())

print("\n--- 1c. Do REF receivers send P2A? ---")
p2a_match = con.execute("""
    SELECT 
        count(distinct r.Receiver_Account) as ref_receivers_sending_p2a,
        count(*) as total_ref_to_p2a_pairs,
        count(distinct r.row_id) as participating_ref_tx,
        count(distinct p.row_id) as participating_p2a_tx
    FROM transactions r
    JOIN transactions p 
      ON r.Receiver_Account = p.Sender_Account
     AND p.Timestamp > r.Timestamp
    WHERE r.Narration = 'UPI/REF'
      AND (p.Narration = 'IMPS/P2A' OR p.Device_Type = 'Web_Emulator')
""").fetchall()
print("REF -> P2A connections:", p2a_match)

print("\n--- 1d. Do REF receivers send WALLET_LOAD? ---")
wl_match = con.execute("""
    SELECT 
        count(distinct r.Receiver_Account) as ref_receivers_sending_wl,
        count(*) as total_ref_to_wl_pairs,
        count(distinct r.row_id) as participating_ref_tx,
        count(distinct w.row_id) as participating_wl_tx
    FROM transactions r
    JOIN transactions w 
      ON r.Receiver_Account = w.Sender_Account
     AND w.Timestamp > r.Timestamp
    WHERE r.Narration = 'UPI/REF'
      AND (w.Narration = 'UPI/WALLET_LOAD' OR w.Device_Type = 'Linux_Script')
""").fetchall()
print("REF -> WALLET_LOAD connections:", wl_match)

print("\n--- 1e. Do P2A receivers send WALLET_LOAD? ---")
p2a_to_wl = con.execute("""
    SELECT 
        count(distinct p.Receiver_Account) as p2a_receivers_sending_wl,
        count(*) as total_p2a_to_wl_pairs,
        count(distinct p.row_id) as participating_p2a_tx,
        count(distinct w.row_id) as participating_wl_tx
    FROM transactions p
    JOIN transactions w 
      ON p.Receiver_Account = w.Sender_Account
     AND w.Timestamp > p.Timestamp
    WHERE (p.Narration = 'IMPS/P2A' OR p.Device_Type = 'Web_Emulator')
      AND (w.Narration = 'UPI/WALLET_LOAD' OR w.Device_Type = 'Linux_Script')
""").fetchall()
print("P2A -> WALLET_LOAD connections:", p2a_to_wl)

print("\n--- 1f. Full 2-hop: REF -> P2A -> WALLET_LOAD ---")
two_hop = con.execute("""
    SELECT 
        count(distinct r.row_id) as ref_count,
        count(distinct p.row_id) as p2a_count,
        count(distinct w.row_id) as wl_count,
        count(distinct r.Sender_Account) as acc_s0,
        count(distinct r.Receiver_Account) as acc_s1,
        count(distinct p.Receiver_Account) as acc_s2,
        count(distinct w.Receiver_Account) as acc_s3,
        count(*) as total_paths
    FROM transactions r
    JOIN transactions p 
      ON r.Receiver_Account = p.Sender_Account
     AND p.Timestamp > r.Timestamp
    JOIN transactions w 
      ON p.Receiver_Account = w.Sender_Account
     AND w.Timestamp > p.Timestamp
    WHERE r.Narration = 'UPI/REF'
      AND (p.Narration = 'IMPS/P2A' OR p.Device_Type = 'Web_Emulator')
      AND (w.Narration = 'UPI/WALLET_LOAD' OR w.Device_Type = 'Linux_Script')
""").fetchall()
print("REF -> P2A -> WALLET_LOAD 2-hop:", two_hop)

print("\n--- 1g. What happens after WALLET_LOAD? (Hop 3 inspection) ---")
after_wl = con.execute("""
    SELECT 
        t4.Narration,
        t4.Device_Type,
        t4.Payment_Mode,
        count(*) as tx_count,
        count(distinct t4.Sender_Account) as distinct_senders
    FROM transactions r
    JOIN transactions p 
      ON r.Receiver_Account = p.Sender_Account
     AND p.Timestamp > r.Timestamp
    JOIN transactions w 
      ON p.Receiver_Account = w.Sender_Account
     AND w.Timestamp > p.Timestamp
    JOIN transactions t4 
      ON w.Receiver_Account = t4.Sender_Account
     AND t4.Timestamp > w.Timestamp
    WHERE r.Narration = 'UPI/REF'
      AND (p.Narration = 'IMPS/P2A' OR p.Device_Type = 'Web_Emulator')
      AND (w.Narration = 'UPI/WALLET_LOAD' OR w.Device_Type = 'Linux_Script')
    GROUP BY t4.Narration, t4.Device_Type, t4.Payment_Mode
    ORDER BY tx_count DESC
    LIMIT 20
""").fetchall()
print("After WALLET_LOAD (t4):", after_wl)

con.close()
