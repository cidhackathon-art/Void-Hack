import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

# 385 sink accounts
sinks = con.execute("""
    WITH in_acc AS (SELECT Receiver_Account as acc FROM transactions GROUP BY Receiver_Account),
         out_acc AS (SELECT Sender_Account as acc FROM transactions GROUP BY Sender_Account)
    SELECT in_acc.acc
    FROM in_acc
    LEFT JOIN out_acc ON in_acc.acc = out_acc.acc
    WHERE out_acc.acc IS NULL
""").fetchall()
sink_accs = {r[0] for r in sinks}
print(f"Total pure sink accounts: {len(sink_accs)}")

# Sample sink account activity
sample_sink = list(sink_accs)[0]
print(f"Sample sink account: {sample_sink}")
txns = con.execute("SELECT * FROM transactions WHERE Receiver_Account = ?", [sample_sink]).fetchall()
print(f"Incoming txns for {sample_sink}: {len(txns)}")
for t in txns[:3]:
    print("  ", t[0], t[1], t[2], "->", t[3], t[6], t[7], t[9], t[11])

con.close()
