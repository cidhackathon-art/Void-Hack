import sys
import duckdb
from pathlib import Path

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
con = duckdb.connect(str(DB_PATH), read_only=True)

print("--- Check narration patterns with prefixes ---")
print("REF:", con.execute("SELECT count(*), min(Narration), max(Narration) FROM transactions WHERE Narration LIKE 'UPI/REF%'").fetchall())
print("P2A:", con.execute("SELECT count(*), min(Narration), max(Narration) FROM transactions WHERE Narration LIKE 'IMPS/P2A%'").fetchall())
print("WALLET_LOAD:", con.execute("SELECT count(*), min(Narration), max(Narration) FROM transactions WHERE Narration LIKE 'UPI/WALLET_LOAD%'").fetchall())

print("\n--- Also check Device_Type matching ---")
print("Web_Emulator count:", con.execute("SELECT count(*) FROM transactions WHERE Device_Type = 'Web_Emulator'").fetchall())
print("Linux_Script count:", con.execute("SELECT count(*) FROM transactions WHERE Device_Type = 'Linux_Script'").fetchall())

print("\n--- Check correlation between Device_Type and Narration ---")
print("Web_Emulator narrations:", con.execute("SELECT regexp_extract(Narration, '^[^/]+/[^/#]+'), count(*) FROM transactions WHERE Device_Type = 'Web_Emulator' GROUP BY 1").fetchall())
print("Linux_Script narrations:", con.execute("SELECT regexp_extract(Narration, '^[^/]+/[^/#]+'), count(*) FROM transactions WHERE Device_Type = 'Linux_Script' GROUP BY 1").fetchall())

con.close()
