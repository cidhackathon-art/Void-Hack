# Void Hacks 8.0 — Operation Abhedya-Chakra
## Phase 1 & Step 0: Data Foundation & Hardening

### 🚀 Quick Start for Teammates

#### 1. Install Dependencies:
```bash
pip install -r requirements.txt
```

#### 2. Rebuild Data (DuckDB + Parquet with stable row_id):
Make sure `VoidHacks8_MuleAccount_2M_Transactions.csv` is in this root directory, then run:
```bash
python rebuild_data.py
```
*(Takes ~29 seconds to build 2M rows DuckDB database + Parquet cache + 5 indexes).*

#### 3. Run Forensic API Server:
```bash
python -m uvicorn backend.main:app --reload --port 8000
```
Endpoints:
* `GET http://127.0.0.1:8000/health`
* `GET http://127.0.0.1:8000/account/{account_id}`
* `GET http://127.0.0.1:8000/account/{account_id}/transactions`

#### 4. Run Automated Verification & Benchmarks:
```bash
python make_dup_proof.py
python benchmark_phase1.py
python backend/validation.py
```

#### 5. GitHub Push Instructions:
Large files (`.csv`, `data/*.duckdb`, `data/*.parquet`) are automatically ignored via `.gitignore`.
To push to GitHub:
```bash
git add .
git commit -m "Phase 1: Data Foundation, DuckDB engine, and Step 0 Data Hardening"
git push origin master
```
