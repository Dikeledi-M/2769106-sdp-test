# Repository Analysis Tool (RAT)

A web-app dashboard that makes a Git repository less opaque. RAT measures **how a
repository has evolved, who has had the most impact where, and which parts of the
project are the most volatile** — computing metrics for **every author, every
file, every directory, and the repository as a whole**.

The dashboard manages **multiple repositories** at once and accepts a repository
in the two forms required by the spec:

1. **ZIP archive upload** of a working copy that includes the `.git` file or
   directory — extracted and ingested on the server.
2. **Remote repository URL** — deeply cloned (full history, not shallow) and
   ingested.

Every metrics view is filterable by:

- **Repository** — pick a repository from the dashboard.
- **Author** — restrict the measured commits to one developer.
- **File or directory** — restrict the reported objects to a path subtree.
- **Commits** — either a **specified period of time** (`from`/`to`) or a
  **manually selected list of commits** (checkboxes).

---

## Table of contents

1. [Tech stack](#tech-stack)
2. [Prerequisites](#prerequisites)
3. [Quick start](#quick-start)
4. [Manual setup (without make)](#manual-setup-without-make)
5. [Using the web app](#using-the-web-app)
6. [Metric definitions](#metric-definitions)
7. [HTTP API walkthrough (curl)](#http-api-walkthrough-curl)
8. [Running the tests](#running-the-tests)
9. [Configuration (.env)](#configuration-env)
10. [Data and storage](#data-and-storage)
11. [Project structure](#project-structure)
12. [Troubleshooting](#troubleshooting)

---

## Tech stack

| Layer    | Technology |
| -------- | ---------- |
| Backend  | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, SQLite (PostgreSQL-ready), PyJWT, GitPython |
| Frontend | React 18, TypeScript, Vite 5, React Router 6 |
| Tests    | pytest + httpx (backend), `tsc --noEmit` + Vite build (frontend) |

---

## Prerequisites

| Requirement | Why | Check |
| ----------- | --- | ----- |
| **Python 3.12+** | Backend runtime | `python3 --version` |
| **Node.js 18+ with npm** | Frontend build/dev server | `node --version && npm --version` |
| **git executable on PATH** | Required — the backend shells out to `git` to ingest uploaded ZIPs and to clone remote repositories | `git --version` |
| `make` *(optional)* | Convenience wrapper for the commands below | `make --version` |
| `zip` *(optional)* | To create a test ZIP archive | `zip --version` |

> **Note:** repository ingestion and cloning are performed by the backend using
> the system `git` binary. If `git` is missing, imports will fail with an error
> visible in the dashboard.

---

## Quick start

From the repository root, in **two terminals**.

### Terminal 1 — backend (http://localhost:8000)

```bash
make backend-install   # creates backend/.venv and installs requirements
make db-upgrade        # applies Alembic migrations (creates backend/rat.db)
make backend-dev       # runs uvicorn with auto-reload on port 8000
```

- API root: <http://localhost:8000>
- Interactive API docs (Swagger UI): <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/v1/health>

### Terminal 2 — frontend (http://localhost:5173)

```bash
make frontend-install  # npm install
make frontend-dev      # Vite dev server
```

Open <http://localhost:5173> in your browser. The Vite dev server proxies every
`/api` request to `http://localhost:8000`, so the browser sees a single origin
(no CORS configuration needed in development).

---

## Manual setup (without make)

The Makefile only wraps the commands below — run them by hand if you prefer.

### Backend

```bash
cd backend

# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) override any setting — defaults work out of the box
cp .env.example .env

# 4. Create / update the database schema
alembic upgrade head

# 5. Run the API server
uvicorn app.main:app --reload --port 8000
```

> The SQLite database file (`backend/rat.db`) is created relative to the
> `backend/` directory, so **run all backend commands from `backend/`**.

### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server with API proxy on http://localhost:5173
```

### Production-style frontend build (optional)

```bash
cd frontend
npm run build      # type-checks with tsc, then bundles into frontend/dist/
npm run preview    # serves the production build
```

The built app requests the API under the relative path `/api/v1`, so when
serving `dist/` you must reverse-proxy `/api` to the backend (the Vite dev
server already does this for you). If you serve the frontend from a different
origin instead, add that origin to `BACKEND_CORS_ORIGINS` in `backend/.env`.

---

## Using the web app

The UI ships with GitHub-inspired light and dark themes. Use the round moon/sun
button at the top right (on the sign-in page and in the top bar) to switch; the
choice is remembered in your browser, and until you pick one the OS preference
is followed automatically.

### 1. Create an account

Open <http://localhost:5173>. You will be redirected to the login page — click
**Register** and create a user. Rules:

- **username**: 3–64 characters, letters/digits/`_ . -` only
- **email**: must be a valid address
- **password**: 8–128 characters

After registering you are signed in automatically (the app stores a short-lived
access token plus a rotating refresh token and renews it transparently).

### 2. Import a repository

The dashboard offers two import forms. Both run **in the background**: the new
repository row starts as `Queued`, moves to `Ingesting…`, and finally becomes
`Ready` (or `Error`, with the failure reason displayed on the row). The
dashboard polls automatically every 2.5 seconds while an import is running.

**Option A — Upload a ZIP archive (with `.git`)**

Create the ZIP from the directory that *contains* the repository, so the `.git`
directory is included:

```bash
# The repository lives in ./myrepo (it must have a .git directory)
zip -r myrepo.zip myrepo
```

or from inside the repository:

```bash
cd myrepo
zip -r ../myrepo.zip .        # the leading dot includes hidden files (.git)
```

Then: choose the file, optionally set a display name, and click **Upload and
ingest**. The archive may contain the repository at its root or nested one or
two folders deep (a wrapper folder is detected automatically).

**Option B — Clone from a URL**

Paste a remote URL (e.g. `https://github.com/owner/repo.git`), optionally set a
display name, and click **Clone and ingest**. The backend performs a full,
**deep clone** (the complete history — no shallow truncation) and then ingests
it. Interactive credential prompts are disabled (`GIT_TERMINAL_PROMPT=0`), so
for private repositories use a URL that already embeds a token, or upload a ZIP
instead.

### 3. Explore metrics

Click **Open** on a `Ready` repository to enter its analysis page. The page
shows:

- A summary of the repository (commits, authors, default branch, HEAD, last
  ingestion time).
- A **Filters** panel (see below).
- **Stat cards** for the currently applied commit set: `|H|` (commits in the
  set), added lines, removed lines, growth, churn, modifications.
- A **Files** table and a **Directories** table with the full metric columns.
- An **Authors** table with modifications, churn, and ownership share.

### 4. Apply filters

All filters narrow the same *commit set* `H` and combine with AND semantics:

| Filter | How to use it | Semantics |
| ------ | ------------- | --------- |
| **Author** | Dropdown of all ingested authors | Keeps only the commits authored by that developer (the authors table then shows that author's full contribution) |
| **File or directory** | Text input with autocomplete suggestions (all ingested files + derived directories) | Restricts the reported files/directories/authors to that path and everything under it; the repository summary keeps reporting the whole repo |
| **From / To** | Date-time pickers | Half-open committer-date range — **From is inclusive, To is exclusive** |
| **Specific commits** | Checkbox list of the latest 200 commits (newest first) | Measures only the checked commits. **Merge commits cannot be measured** and are shown greyed out |

The commit-set based tables update when you click **Apply filters**; active
filters appear as chips next to the buttons and **Reset** clears everything.

---

## Metric definitions

All metrics are computed from **line-level diffs** over a commit set `H`
(by default: every non-merge commit reachable from HEAD).

Notation for an object `x` (a file, a directory, or the repository root):

| Symbol | Name | Definition |
| ------ | ---- | ---------- |
| `\|H\|` | commit-set size | number of (non-merge) commits in the filtered set |
| `λ(x, h)` | churn of one commit | lines added + lines removed in `x` by commit `h` |
| `added(x, H)` | added lines | Σ of added lines over `h ∈ H` |
| `removed(x, H)` | removed lines | Σ of removed lines over `h ∈ H` |
| `Δ(x, H)` | growth | `added − removed` (can be negative) |
| `λ(x, H)` | churn | `added + removed` |
| `Γ(x, h)` | modified indicator | `1` if `λ(x, h) > 0`, else `0` |
| `n(x, H)` | modifications | Σ of `Γ(x, h)` over `h ∈ H` — how many commits actually touched `x` |
| `η(x, H)` | modification frequency | `n / \|H\|` (0 when the set is empty) |
| `ρ(x, H)` | churn rate | `λ / \|H\|` (0 when the set is empty) |
| `ω(a, x, H)` | ownership | author `a`'s churn ÷ total churn of `x` (0 when there is no churn) |

Details worth knowing:

- **Directories** aggregate their whole subtree: per commit, the churn of all
  files underneath is summed first, and the commit counts as a modification
  only if that rolled-up churn is positive.
- **Repository metrics are directory metrics evaluated at the root** of the
  commit tree.
- **Author metrics** count only commits by that author (`I(a, h)` indicator):
  author modifications Σ `Γ`, author churn Σ `λ`, and ownership share
  `ω = λ_a / λ`.
- A **pure rename** changes no lines, so it contributes 0 churn and is not
  counted as a modification; a rename with content changes is attributed to the
  new path.
- **Binary files** and **merge commits** are excluded from all metrics.
- Every file touched by the commit set appears in the tables — including a
  file that was only renamed, which shows 0 churn and 0 modifications. Objects
  with no activity at all in the set are not listed.

---

## HTTP API walkthrough (curl)

The API is served under `/api/v1`. Full interactive documentation is available
at <http://localhost:8000/docs> (OpenAPI JSON at
<http://localhost:8000/api/v1/openapi.json>). All repository and metrics
endpoints require a `Bearer` access token; repositories are private to their
owner.

```bash
BASE=http://localhost:8000/api/v1

# 1. Register a user (201 Created)
curl -s -X POST "$BASE/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "username": "you", "password": "password123"}'

# 2. Log in and capture the access token
TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "password123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# 3. Import a repository — form A: ZIP upload (multipart)
curl -s -X POST "$BASE/repositories/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/absolute/path/to/myrepo.zip" \
  -F "name=myrepo"

#    ... or form B: deep clone from a URL
curl -s -X POST "$BASE/repositories/clone" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://github.com/octocat/Hello-World.git", "name": "hello-world"}'

# 4. Poll the repository until "status" becomes "ready"
curl -s "$BASE/repositories/1" -H "Authorization: Bearer $TOKEN"

# 5. Metrics — repository root, files, directories, authors
curl -s "$BASE/repositories/1/metrics/repository"  -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/repositories/1/metrics/files"       -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/repositories/1/metrics/directories" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/repositories/1/metrics/authors"     -H "Authorization: Bearer $TOKEN"

# 6. With filters: period of time, author, path, manual commit list
curl -s "$BASE/repositories/1/metrics/files?from=2024-01-01T00:00:00Z&to=2024-07-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/repositories/1/metrics/authors?path=src&author=1&author=2" \
  -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/repositories/1/metrics/repository?commit=<sha1>&commit=<sha2>" \
  -H "Authorization: Bearer $TOKEN"
```

### Endpoint reference

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET`  | `/api/v1/health` | Liveness check |
| `POST` | `/api/v1/auth/register` | Create an account |
| `POST` | `/api/v1/auth/login` | Obtain an access + refresh token pair |
| `POST` | `/api/v1/auth/refresh` | Rotate the token pair |
| `POST` | `/api/v1/auth/logout` | Revoke the refresh token |
| `GET`  | `/api/v1/auth/me` | Current user |
| `GET`  | `/api/v1/repositories` | List the user's repositories (newest first) |
| `POST` | `/api/v1/repositories/upload` | Import from a `.zip` archive (multipart, background) |
| `POST` | `/api/v1/repositories/clone` | Import from a URL via deep clone (background) |
| `GET`  | `/api/v1/repositories/{id}` | Repository detail, including import `status` |
| `DELETE` | `/api/v1/repositories/{id}` | Delete the repository, its rows, and its stored files (204) |
| `GET`  | `/api/v1/repositories/{id}/authors` | Ingested authors |
| `GET`  | `/api/v1/repositories/{id}/commits` | Commit list, newest first (`limit` ≤ 1000, `offset`) |
| `GET`  | `/api/v1/repositories/{id}/files` | Ingested non-binary file paths (for path suggestions) |
| `GET`  | `/api/v1/repositories/{id}/metrics/files` | File metrics |
| `GET`  | `/api/v1/repositories/{id}/metrics/directories` | Directory metrics |
| `GET`  | `/api/v1/repositories/{id}/metrics/repository` | Repository-root metrics |
| `GET`  | `/api/v1/repositories/{id}/metrics/authors` | Author metrics (ownership) |

### Metrics query parameters

| Parameter | Applies to | Meaning |
| --------- | ---------- | ------- |
| `from` | all metrics | ISO 8601 timestamp; keep commits with committer date **≥** `from` |
| `to` | all metrics | ISO 8601 timestamp; keep commits with committer date **<** `to` |
| `commit` | all metrics | Explicit commit SHA; **repeatable** (e.g. `?commit=a1b2&commit=c3d4`) |
| `author` | all metrics | Canonical author id; **repeatable** — intersects with the set |
| `reference` | all metrics | Keep only commits reachable from this SHA |
| `path` | files, directories, authors | Restrict reported objects to this file/directory subtree |

The repository endpoint has no `path` parameter — it always reports the root.
An unknown `reference` SHA returns `400`; another user's repository returns
`404`.

---

## Running the tests

### Backend — pytest (110 tests)

```bash
make test-backend
# or
cd backend && .venv/bin/python -m pytest
```

Coverage of the suite:

| File | What it exercises |
| ---- | ----------------- |
| `tests/test_auth.py` | register/login/refresh-rotation/logout/me, validation, conflicts |
| `tests/test_git_ingest.py` | git parsing and ingestion into the database |
| `tests/test_metrics.py` | file, directory, repository and author metrics, all rates, and the API endpoints |
| `tests/test_repositories.py` | ZIP upload, URL clone, zip-slip rejection, broken archives, error states, listings, per-owner isolation |

Test isolation: every test gets a fresh in-memory SQLite database and its own
temporary upload/clone directories, so running the suite never touches
`backend/rat.db` or your uploaded data. Clone tests use **local paths**, so the
suite needs the `git` executable but **no network access**.

### Frontend — type-check + production build

```bash
cd frontend && npm run build     # runs `tsc --noEmit` then `vite build`
```

A successful run type-checks every `.ts`/`.tsx` file and produces `frontend/dist/`.

### Suggested end-to-end smoke test (manual)

1. Start both servers (Quick start) and register a user at
   <http://localhost:5173>.
2. Create a ZIP of any git repository **including `.git`** and upload it; or
   clone a small public repository by URL.
3. Wait for the status badge to become **Ready** (watch it move through
   Queued → Ingesting…).
4. Open the repository. Verify the file/directory/author tables and the summary
   cards render numbers.
5. Apply one filter of each kind (author, path, From/To range, commit
   checkboxes) and confirm the tables and the `|H|` card update; press **Reset**
   to return to the full history.

---

## Configuration (.env)

All settings have working development defaults, so **no `.env` file is
required**. To override anything, copy `backend/.env.example` to
`backend/.env` (the backend loads `.env` from its working directory) or export
the variables before starting the server.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DATABASE_URL` | `sqlite:///./rat.db` | SQLAlchemy database URL. The schema is PostgreSQL-compatible; e.g. `postgresql+psycopg://rat:rat@localhost:5432/rat` |
| `SECRET_KEY` | dev placeholder | HS256 signing key. **Must be ≥ 32 bytes — set a strong random value in any shared deployment** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access-token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime |
| `BACKEND_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed browser origins |
| `DATA_DIR` | `data` | Base storage directory |
| `UPLOAD_DIR` | `data/uploads` | Where uploaded ZIP archives are extracted |
| `CLONE_DIR` | `data/clones` | Where remote repositories are cloned |

---

## Data and storage

| What | Where |
| ---- | ----- |
| Database | `backend/rat.db` (SQLite) |
| Uploaded archives / extracted copies | `backend/data/uploads/{repository_id}/` |
| Cloned repositories | `backend/data/clones/{repository_id}/` |

All of these are git-ignored. **Deleting a repository** from the dashboard (or
`DELETE /api/v1/repositories/{id}`) removes its database rows (repository,
commits, files, authors) and its stored files.

To reset everything: stop the servers, delete `backend/rat.db` and the contents
of `backend/data/uploads/` and `backend/data/clones/` (keep the `.gitkeep`
files), then re-run `make db-upgrade` and restart.

---

## Project structure

```
.
├── Makefile                     # convenience targets (see Quick start)
├── README.md
├── backend/
│   ├── alembic/                 # database migrations
│   ├── alembic.ini
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── .env.example             # copy to .env to override settings
│   ├── app/
│   │   ├── main.py              # FastAPI app + router registration + CORS
│   │   ├── api/
│   │   │   ├── deps.py          # auth dependencies + repository ownership helper
│   │   │   └── routes/          # health, auth, repositories, metrics endpoints
│   │   ├── core/                # settings + JWT/password security
│   │   ├── db/                  # SQLAlchemy engine/session/base
│   │   ├── models/              # User, RefreshToken, Repository, Commit, CommitFile, Author
│   │   ├── schemas/             # Pydantic request/response models
│   │   └── services/
│   │       ├── git_ingest.py        # walks git history into the database
│   │       ├── repository_import.py # ZIP extraction (zip-slip safe) + deep clone
│   │       └── metrics.py           # the metrics engine (file/dir/repo/author)
│   └── tests/                   # pytest suite (110 tests) + scripted git history fixture
└── frontend/
    ├── package.json
    ├── vite.config.ts           # dev proxy: /api -> http://localhost:8000
    └── src/
        ├── api/                 # typed fetch client + endpoint modules
        ├── auth/                # AuthContext + route guard
        ├── pages/               # LoginPage, DashboardPage, RepositoryDetailPage
        ├── types.ts             # shared API types
        └── index.css            # design tokens + component styles
```

---

## Troubleshooting

| Symptom | Cause / fix |
| ------- | ----------- |
| Dashboard row shows `Error: … "git" not found` or clone fails immediately | The `git` executable is missing from `PATH`. Install git and restart the backend |
| ZIP import fails with “no `.git` directory found” | The archive was created without hidden files. Recreate it with `zip -r repo.zip .` from inside the repository (the `.` includes `.git`) |
| ZIP import fails with “not a valid ZIP archive” | The uploaded file is not a ZIP (or is corrupt). Only `.zip` files are accepted |
| Clone of a private repository fails | Credentials cannot be entered interactively. Embed a token in the URL (e.g. `https://<token>@github.com/owner/repo.git`) or upload a ZIP instead |
| `429`-style slowness / repo stuck on `Ingesting…` | Large histories take time to ingest; the page keeps polling. Check the backend terminal for progress; a row flips to `Error` with a message on failure |
| `Address already in use` on port 8000 | Another process is on the port: `lsof -i :8000` (or run uvicorn with `--port 8001` and update the proxy target in `frontend/vite.config.ts`) |
| Frontend loads but every request fails with 401 | Access token expired and refresh failed (e.g. you deleted `rat.db`). Sign out and sign in again |
| CORS errors in the browser console | You are serving the frontend from an origin not in `BACKEND_CORS_ORIGINS`. Add it to `backend/.env` and restart the backend — or use the Vite dev server, which proxies `/api` |
| Metrics look empty | The applied commit set is empty (e.g. From/To range with no commits, or no commits checked). Press **Reset** and re-apply |
