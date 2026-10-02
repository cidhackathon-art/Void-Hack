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

for tol in [0.001, 0.005, 0.01, 0.02]:
    low = 0.9800 - tol
    high = 0.9800 + tol
    q = f"""
        WITH in_s AS (
            SELECT Receiver_Account as acc, sum(Amount) as in_amt
            FROM transactions GROUP BY Receiver_Account
        ),
        out_s AS (
            SELECT Sender_Account as acc, sum(Amount) as out_amt
            FROM transactions GROUP BY Sender_Account
        )
        SELECT in_s.acc
        FROM in_s
        JOIN out_s ON in_s.acc = out_s.acc
        WHERE (out_s.out_amt / in_s.in_amt) BETWEEN {low} AND {high}
    """
    res = {r[0] for r in con.execute(q).fetchall()}
    m_129 = len(res.intersection(marker_129))
    non_129 = len(res - marker_129)
    print(f"Tolerance +/-{tol:.3f} [{low:.4f}, {high:.4f}]: 129 matches = {m_129}/129, Non-129 matches = {non_129}")

con.close()
