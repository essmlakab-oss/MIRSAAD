# Mirsaad — Ground Truth Under Attack

**FDI detection for environmental sensor networks**

Soil-moisture telemetry can be falsified: an attacker writes plausible readings into a station's feed, and a false drought (or false normal) propagates into every evaluation made from it. Mirsaad is an enterprise-grade cybersecurity platform that answers a critical question: *Can a station's hourly feed defend itself?*

---

## 📚 Documentation

All the project documentation is in the [`documentation/`](documentation) folder:

| Document | Content |
|---|---|
| [`Donnees.pdf`](documentation/Donnees.pdf) | Data: USCRN cleaning, ERA5, neighbour stations, attack datasets |
| [`Rapport_Modele_Supervise.pdf`](documentation/Rapport_Modele_Supervise.pdf) | Supervised detector v3.1: witnesses, digital twins, two LightGBM stages, results |
| [`Rapport_OneClass.pdf`](documentation/Rapport_OneClass.pdf) | One-Class hybrid detector (second opinion) |
| [`Rapport_Arene.pdf`](documentation/Rapport_Arene.pdf) | Attacker vs defender arena: simulator, defender, interface |

The trust chain (cryptographic gate, attack engine, causal detectors, evaluation) is documented in [`trust-chain/README.md`](trust-chain/README.md) and in the reports inside each of its sub-folders.

---

## 🔓 Model Access (Public Hugging Face)

The machine learning artifacts — the attack detector, the per-station digital twins, and the attack-type classifier — are hosted in a **public** Hugging Face repository.

Set `FDI_HF_REPO_ID` in `backend/.env` to the repository that hosts these artifacts (see Environment Variables below). They are downloaded automatically on first boot. **No token and no access request are required.**

If you fork the project or want to avoid anonymous rate limits, you can optionally set `HF_TOKEN` in `backend/.env`. For private forks, point `FDI_HF_REPO_ID` at your own repository.

---

## 🚀 What it does

### 1. The Machine Learning Core
- **Model 1 — Attack Detector (LightGBM v3).** Every station-hour is passed to a two-stage LightGBM detector using cross-fitted digital twins. Verdicts: `NORMAL`, `SUSPECT`, `ATTAQUE`, and `HISTORIQUE INSUFFISANT` (if there isn't enough history), each with the firing rule as its reason.
- **Model 2 — Attack Type Classifier.** On demand, the call returns a `type` — one of `drift · fake_storm · hidden_storm · replay · smooth_bias · stealth_era5`. The answer is cached per run.

### 2. Explainable AI (XAI)
When an anomaly is detected, the interface provides three distinct layers of forensic evidence:
- **SHAP Contributions (Feature Relevance):** A game-theoretic breakdown over the Stage-1 LightGBM booster features. It shows exactly which witness sensors (10cm, 20cm, ERA5, neighboring stations) pushed the prediction up or down. It is exact, deterministic, and fast (~160ms).
- **ECOD Family Decomposition:** Breaks the anomaly score down into 5 physical families: *Paired-step fingerprint, Depth-relationship, Unresolved increment, Rain plausibility, and Replay*.
- **Temporal Chart (Expected vs. Actual):** A time-series reconstruction around the flagged hour (using `exp5_tu` and `residual_tu`) to visually prove if the anomaly was a natural event or a digital injection.

### 3. Premium User Interface & 3D Data Inspector
- **Dynamic Ingestion:** A sleek, glassmorphism control center allows users to drag-and-drop a CSV of real station-hours, or click **"Load Demo Dataset"** to instantly test the system results.
- **3D Data Inspector:** A spatial 3D station view featuring three fixed nodes: Target (5cm), Witness (10cm), and Weather/ERA5. As the user scrubs the timeline, the nodes display real values for that exact hour. If the hour is an attack, the target node's beacon flashes red and glitches.
- **24h Evidence Strip:** A visual timeline highlighting the verdicts for the surrounding 24 hours and more through scrolling.

### 4. AI Integrations (Powered by Groq)
- **The Analyst (Copilot):** A server-side chat assistant that reads the loaded run and the operator's selected hour. It answers questions in prose (e.g., *"How many attacks were found?"*) and navigates the user directly to the relevant XAI panel via a spotlight jump.
- **AI Interpretation:** A one-click button that takes the complex XAI mathematical payload (ECOD + SHAP) and uses an LLM to rewrite it into a plain-English business summary for non-technical stakeholders.

### 5. Attack Arena
Attacker vs defender (`/dashboard/arena`): inject a false-data attack (7 families: spike, bias, drift, replay, fake storm, hidden storm, stealth) into a clean 2025 copy of a station and watch the supervised detector decide, step by step. Run batches (Launcher) and read the scoreboard, detection heatmap, and registry of misses.

### 6. Security & Architecture
- **Auth-Gated:** Supabase sessions with user/admin roles. Every FDI, XAI, and AI route requires a signed-in user; only the auth endpoints (register, login, refresh, logout, forgot-password) are public.
- **Privacy & Performance:** The browser never receives raw data; it only receives verdicts and XAI payloads. Runs are stored in a server-side memory cache (TTL + LRU).

---

## 🏗️ System Architecture

```text
CSV upload (or demo dataset)
  → schema validation / column renaming
  → Model 1 (LightGBM v3: Stage 1 temporal + Stage 2 context)
  → verdict (NORMAL · SUSPECT · ATTAQUE · HISTORIQUE INSUFFISANT) + rule reason
      ├─ on demand → Model 2 (type_classifier.joblib)
      │              → {detected, confidence, type}   (cached per run)
      ├─ on demand → XAI (SHAP M1 · ECOD families · temporal twin residual)
      └─ on demand → Groq analyst (server-side, reads the latest run)
  → LLM interpretation (server-validated XAI payload → business summary)
  → 3D Data Inspector + 24 h evidence strip + explainable verdicts
```

`POST /api/fdi/predict` validates and renames the uploaded CSV, stores the scored frame and its engineered features server-side, and returns the verdict summary. `GET /api/xai/…` builds the per-hour payload (SHAP, ECOD families, temporal reconstruction, rule details) once per run-hour and caches it.

---

## 📂 Code Structure

```text
backend/
  app/                   FastAPI entrypoint + auth + fdi (models, XAI, store)
                         + routers + ai (Groq client, analyst, interpreter)
  Data/                  ERA5, SCAN, reference data, attack samples
  requirements.txt       pinned (the artifact unpickles by exact versions)

frontend/
  src/                   routes (TanStack Start console), components
                         (XAI panel, 3D inspector, analyst bubble), lib
                         (fdi-api client, auth, types)
  package.json
```

`backend/app/` is the whole API: `main.py` wires the routers behind Supabase auth; `fdi/` holds Model 1, the frozen Model 2 stack, the explanation pipeline and the in-memory run store; `ai/` holds the Groq client, the analyst prompt and the XAI interpreter.

---

## ⚙️ How to Run

### 1. Backend (FastAPI)
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill SUPABASE_*, FDI_HF_REPO_ID, and GROQ_API_KEY
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend (TanStack Start / React)
```bash
cd frontend
npm install
npm run dev             # http://localhost:8080
```

Open `http://localhost:8080`, sign up, and click **Load demo data** on the FDI page. The model artifacts are downloaded from Hugging Face on first boot.

---

## 🔑 Environment Variables

| Name | Default | Required | Purpose |
|---|---|---|---|
| `SUPABASE_URL` | — | yes | Supabase project URL (auth) |
| `SUPABASE_PUBLISHABLE_KEY` | — | yes | anon/publishable key (sign-up, sign-in, refresh) |
| `SUPABASE_SECRET_KEY` | — | yes | service key; reads roles, administers accounts |
| `SUPABASE_JWT_SECRET` | empty | no | only for legacy shared-HS256 projects |
| `AUTH_EMAIL_REDIRECT_URL` | `http://localhost:8080/dashboard` | no | confirmation/reset redirect target |
| `AUTH_PASSWORD_MIN_LENGTH` | `8` | no | mirrors the Supabase provider setting |
| `AUTH_AUTOCONFIRM_SIGNUP` | `true` | no | sign-ups pre-confirmed via admin API |
| `AUTH_COOKIE_SECURE` | `false` | no | set `true` on any https deployment |
| `AUTH_COOKIE_SAMESITE` | `lax` | no | session cookie policy |
| `AUTH_COOKIE_DOMAIN` | empty | no | leave empty for localhost |
| `HF_TOKEN` | — | no | optional read token for the HF artifact repo; only needed for private forks or to avoid anonymous rate limits |
| `FDI_HF_REPO_ID` | — | yes | Hugging Face repository hosting the model artifacts |
| `FDI_DETECTOR_FILENAME` | `detecteur_v3_final.joblib` | no | Model 1 artifact file |
| `FDI_TYPE_FILENAME` | `type_classifier.joblib` | no | Model 2 classifier artifact |
| `FDI_WITNESS_FILENAME` | `witness_ref.joblib` | no | Witness reference pack |
| `FDI_TWINS_ZIP_FILENAME` | `twins.zip` | no | Zipped per-station digital twins |
| `FDI_MODEL_PATH` | empty | no | local artifact override; skips the Hub when set |
| `groq_model` | — | no | Groq API key (Analyst; blank disables the copilot) |
| `GROQ_MODEL_NAME` | `openai/gpt-oss-120b` | no | Groq chat model |
| `GROQ_MAX_TOKENS` | `1024` | no | completion budget (reasoning + answer) |
| `GROQ_TIMEOUT` | `20` | no | seconds before the copilot is reported unavailable |
| `CIS_DATA` | `backend/Data/` | — | where Model 2 finds ERA5/SCAN |

---

## 📊 Data Layout

```text
backend/Data/
  ERA5_USCRN/                    ERA5 hourly re-analysis
  Voisins/scan/                  SCAN neighbour stations
  reference/                     clean 2022–2024 archive
  AI_Realistic_Attacks_v2/       attack seed samples
```

The seed directories and reference pack are research assets and are not included in this repository.

---

## 📄 License / Contact

No LICENSE file is present; rights are held by the project authors.

For questions or bug reports, open an issue in this repository.