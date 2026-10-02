import duckdb
from pathlib import Path
from backend.detect import DetectionEngine

DB_PATH = Path(r"C:\projects\Void-Hack\data\transactions.duckdb")
engine = DetectionEngine(db_path=DB_PATH)

con = duckdb.connect(str(DB_PATH), read_only=True)
all_s = engine.score_all_accounts_fast(con=con)

sink_scores = [a["risk_score"] for a in all_s if a["ind_sink"] == 1]
print(f"Total sink accounts scored: {len(sink_scores)}")
from collections import Counter
print("Score distribution for sink accounts:", Counter(sink_scores))

# Also print indicator breakdown for sink accounts
sample_sink_obj = [a for a in all_s if a["ind_sink"] == 1][0]
print("Sample sink indicator values:", sample_sink_obj)

con.close()
