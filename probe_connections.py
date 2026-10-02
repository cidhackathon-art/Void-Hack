import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- 1. Connections from UPI/REF Receiver ---")
print("\nREF Receiver -> later P2A Sender:")
ref_p2a = con.execute("""
    SELECT 
        count(distinct r.Receiver_Account) as ref_receivers_becoming_p2a_senders,
        count(distinct r.row_id) as distinct_ref_txns,
        count(distinct p.row_id) as distinct_p2a_txns,
        count(*) as matching_txn_pairs
    FROM transactions r
    JOIN transactions p 
      ON r.Receiver_Account = p.Sender_Account
     AND p.Timestamp > r.Timestamp
    WHERE r.Narration LIKE 'UPI/REF%'
      AND p.Narration LIKE 'IMPS/P2A%'
""").fetchall()
print("REF -> P2A:", ref_p2a)

print("\nREF Receiver -> later WALLET_LOAD Sender:")
ref_wl = con.execute("""
    SELECT 
        count(distinct r.Receiver_Account) as ref_receivers_becoming_wl_senders,
        count(distinct r.row_id) as distinct_ref_txns,
        count(distinct w.row_id) as distinct_wl_txns,
        count(*) as matching_txn_pairs
    FROM transactions r
    JOIN transactions w 
      ON r.Receiver_Account = w.Sender_Account
     AND w.Timestamp > r.Timestamp
    WHERE r.Narration LIKE 'UPI/REF%'
      AND w.Narration LIKE 'UPI/WALLET_LOAD%'
""").fetchall()
print("REF -> WALLET_LOAD:", ref_wl)

print("\n--- 2. Details of REF -> P2A connections ---")
ref_p2a_details = con.execute("""
    SELECT 
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
      AND p.Narration LIKE 'IMPS/P2A%'
""").fetchall()
print("REF -> P2A gaps and ratios:", ref_p2a_details)

print("\n--- 3. P2A Receiver -> WALLET_LOAD Sender ---")
p2a_wl = con.execute("""
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
    WHERE p.Narration LIKE 'IMPS/P2A%'
      AND w.Narration LIKE 'UPI/WALLET_LOAD%'
""").fetchall()
print("P2A -> WALLET_LOAD:", p2a_wl)

print("\n--- 4. Full 2-hop: REF -> P2A -> WALLET_LOAD ---")
two_hop_full = con.execute("""
    SELECT 
        count(distinct r.row_id) as ref_tx_count,
        count(distinct p.row_id) as p2a_tx_count,
        count(distinct w.row_id) as wl_tx_count,
        count(distinct r.Sender_Account) as distinct_r_senders,
        count(distinct r.Receiver_Account) as distinct_r_receivers,
        count(distinct p.Receiver_Account) as distinct_p_receivers,
        count(distinct w.Receiver_Account) as distinct_w_receivers,
        count(*) as total_two_hop_paths
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
""").fetchall()
print("Full 2-hop:", two_hop_full)

print("\n--- 5. What follows WALLET_LOAD? (Check for 3-hop) ---")
post_wl = con.execute("""
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
    WHERE r.Narration LIKE 'UPI/REF%'
      AND p.Narration LIKE 'IMPS/P2A%'
      AND w.Narration LIKE 'UPI/WALLET_LOAD%'
    GROUP BY t4.Narration, t4.Device_Type, t4.Payment_Mode
    ORDER BY tx_count DESC
    LIMIT 20
""").fetchall()
print("Post WALLET_LOAD (t4):", post_wl)

con.close()
