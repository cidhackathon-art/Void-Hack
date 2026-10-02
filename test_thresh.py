import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

marker_129 = set(con.execute("""
    SELECT distinct Sender_Account 
    FROM transactions 
    WHERE Narration LIKE 'IMPS/P2A%' OR Device_Type = 'Web_Emulator'
""").fetchall())
marker_129 = {r[0] for r in marker_129}

for thresh in [0.20, 0.40, 0.50, 0.70, 0.80, 0.90, 0.95]:
    q = f"""
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
        WHERE sum_out >= {thresh} * in_amt
    """
    res = {r[0] for r in con.execute(q).fetchall()}
    m_129 = len(res.intersection(marker_129))
    non_129 = len(res - marker_129)
    print(f"Thresh {thresh:.2f}: 129 matches = {m_129}/129, Non-129 matches = {non_129}")

con.close()
