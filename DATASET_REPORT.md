# Operation Abhedya-Chakra — Empirical Dataset & Performance Report
**Event:** Void Hacks() 8.0 — Cyber Security & Digital Forensics (SVVV Indore & Indore Police)  
**Phase:** Phase 1 — Data Foundation & Architecture Baseline  
**Report Generation:** Verified Empirical Metrics (No extrapolated or assumed numbers)  

---

## 1. Actual Dataset Schema & Types
The source file `VoidHacks8_MuleAccount_2M_Transactions.csv` contains 11 source columns plus the hardened stable `row_id`:

| Column Name | Inferred DuckDB Type | Semantic Role | Sample Value |
|:---|:---|:---|:---|
| `row_id` | `BIGINT` | Permanent Deterministic Identifier | `1` |
| `Transaction_ID` | `VARCHAR` | Transaction Identifier (Non-unique) | `TXN401119292` |
| `Sender_Account` | `VARCHAR` | Remitter / Origin Account ID | `KKBK10000000` |
| `Receiver_Account` | `VARCHAR` | Beneficiary / Destination Account ID | `ICIC10000335` |
| `Sender_IFSC` | `VARCHAR` | Originating Bank IFSC Code | `KKBK0001000` |
| `Receiver_IFSC` | `VARCHAR` | Beneficiary Bank IFSC Code | `ICIC0001335` |
| `Amount` | `DOUBLE` | Transaction Value in INR | `455541.61` |
| `Timestamp` | `TIMESTAMP` | Transaction Timestamp (YYYY-MM-DD HH:MM:SS) | `2026-09-22 01:13:51` |
| `Payment_Mode` | `VARCHAR` | Banking Transfer Protocol | `IMPS` |
| `Narration` | `VARCHAR` | Transaction Remarks / Reference String | `UPI/REF/TASK_EARNING_REFUND_37961` |
| `IP_Address` | `VARCHAR` | Originating IPv4 Address | `103.114.236.26` |
| `Device_Type` | `VARCHAR` | Client Platform / User-Agent Classification | `Android` |

---

## 2. Quantitative Dataset Statistics
All statistics computed directly on DuckDB table `transactions`:

* **Total Row Count:** `2,000,000` (Exactly 2.00 Million rows)
* **CSV Rows = DuckDB Rows = Parquet Rows:** `2,000,000` (Triple-check match verified)
* **Unique Sender Accounts:** `24,488`
* **Unique Receiver Accounts:** `24,573`
* **Total Distinct Accounts Across Dataset:** `24,873` accounts
* **Temporal Span:** Exactly 15 days (`2026-09-15 00:00:00` to `2026-09-29 23:59:58`)
* **Amount Distribution:**
  * *Minimum Amount:* `₹50.00`
  * *Maximum Amount:* `₹499,823.99`
  * *Average Amount:* `₹1,760.88`
  * *Non-positive / Zero Amounts:* `0` (Zero invalid amounts)
* **Null Values across all fields:** `0` (Zero nulls across all 11 columns + row_id)

---

## 3. Duplicate Analysis & Tie Detection
* **Exact whole-row duplicates:** `0` (Every row in the 2M dataset is distinct)
* **Measured Repeated Transaction_ID Finding:**
  * **2,250 repeated Transaction_IDs / 2,252 extra rows**
  * Breakdown:
    * Appears `2x`: `2,248 IDs` (4,496 rows)
    * Appears `3x`: `2 IDs` (`TXN642182041`, `TXN391364739` — 6 rows)
    * Appears `4x+`: `0 IDs`
    * Total rows involved in repeated Transaction_IDs: `4,502 rows`
  * **Data Policy:** Do NOT delete or dedupe any data. All 2,000,000 rows preserved intact.
* **Tie Detection in ORDER BY (11 Columns):** `0 tied rows`
  * Deterministic order guaranteed: Every tuple `(Timestamp, Transaction_ID, Sender_Account, Receiver_Account, Receiver_IFSC, Sender_IFSC, Amount, Payment_Mode, Narration, IP_Address, Device_Type)` is strictly unique.

---

## 4. Stable row_id Verification & Rebuild Reproducibility
* `COUNT(DISTINCT row_id)`: `2,000,000`
* `MIN(row_id)`: `1`
* `MAX(row_id)`: `2,000,000`
* `NULL row_id`: `0`
* **Two-Rebuild Reproducibility Test:**
  * Rebuild 1 Fingerprint vs Rebuild 2 Fingerprint: **100% Exact Match across all sample points (`row_id` 1, 100, 500k, 1M, 1.5M, 2M)**.

---

## 5. Original CSV Integrity (Untouched Verification)
* **Actual Size Before Rebuild:** `286,788,986 bytes`
* **Actual Size After Rebuild:**  `286,788,986 bytes`
* **Modified Time Before/After:** `2026-10-01 23:13:53.871022`
* **SHA-256 Hash:** `2c9f81fd34f728c0b7c1e803cb49e1e231c1d9204a77badfcb737f50adf73101`
* **Status:** 100% UNTOUCHED (Zero bytes modified).

---

## 6. Post-Hardening Storage & Benchmark Metrics (Measured)
* **Rebuild Execution Time:** `29.33 seconds` total
  * *Ingestion + row_id generation:* `11.84 seconds`
  * *Index creation (5 indexes):* `15.37 seconds`
  * *Parquet export (ZSTD):* `1.97 seconds`
* **Storage Footprint:**
  * Raw CSV: `286,788,986 bytes` (**273.50 MB**)
  * DuckDB Database: `369,897,472 bytes` (**352.76 MB**)
  * Parquet Cache: `69,482,546 bytes` (**66.26 MB**)
* **API Latency (Real Uvicorn + curl):**
  * `GET /health`: **~1.5 ms**
  * `GET /account/ICIC10000335`: **~80 ms**
  * `GET /account/ICIC10000335/transactions`: **~95 ms**
  * `GET /account/NON_EXISTENT_ACC_9999`: **HTTP 404 Not Found**
