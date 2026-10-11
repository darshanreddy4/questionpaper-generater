# ExamForge

Reads your teaching material (PDF, DOCX, EPUB, HTML, RTF, TXT and more) and produces question papers with answer
keys in the pattern your school or college uses, exported as PDF or Word. Supports English and Indian languages
(Hindi, Kannada, Tamil, Telugu, Bengali, Marathi, Gujarati, Punjabi, Malayalam, Odia, Urdu and others), maths
and science notation, and diagrams.

It can run for one school, or as a service for many schools, each fully separated from the others.

## Quick start

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py          # http://127.0.0.1:8000
```

The first person to register becomes the administrator. Everything is stored in `./data/` (SQLite) unless
`EXAMFORGE_DATABASE_URL` points to PostgreSQL.

## Setting up a fresh clone (step by step)

Three things are **git-ignored** and will not exist right after `git clone` — you create them locally:

| Not in git | What it is | How you get it |
|---|---|---|
| `.venv/` | The Python virtual environment | Created by `python -m venv .venv` below |
| `.env` | Your machine's settings (tenancy mode, superadmin email, secret keys, AI key) | Copy `.env.example` -> `.env` and edit it. Optional: without it the app runs fine in single-school mode |
| `data/` | SQLite database, uploaded documents, generated figures | Created automatically the first time you run the server |

`.env.example` itself **is** committed (it's the template); only your personal `.env` is ignored, so every machine
keeps its own secret keys and tenancy choice.

### Windows (PowerShell)

```powershell
git clone <your-repo-url>
cd questionpaper-generater

# 1. Virtual environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Settings (optional for single-school mode; required for multi-school)
Copy-Item .env.example .env
notepad .env   # set EXAMFORGE_TENANCY, EXAMFORGE_SUPERADMIN_EMAILS, etc. if you want multi-school mode

# 3. Run
.\.venv\Scripts\python.exe run.py
# -> http://127.0.0.1:8000
```

Open the URL, register the first account (it becomes the administrator, or the platform operator in multi-school
mode if its email matches `EXAMFORGE_SUPERADMIN_EMAILS`), and you're set up.

To stop and restart the server later:

```powershell
# find and stop whatever is listening on port 8000
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }

# start it again
.\.venv\Scripts\python.exe run.py
```

### macOS / Linux (bash)

```bash
git clone <your-repo-url>
cd questionpaper-generater

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # set EXAMFORGE_TENANCY, EXAMFORGE_SUPERADMIN_EMAILS, etc. if you want multi-school mode

.venv/bin/python run.py   # http://127.0.0.1:8000
```

### Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

### Notes

- Nothing in `data/` is shared between machines — a fresh clone always starts with an empty database and no
  uploaded documents, even if you had papers generated on another machine.
- `EXAMFORGE_SECRET_KEY` and `EXAMFORGE_ENCRYPTION_KEY` are generated randomly if left unset in `.env`, which means
  sessions and any stored AI keys stop working every time the server restarts. Set them to fixed values (see the
  "Multi-school service" section below) for anything other than quick local testing.
- `OPENAI_API_KEY` (or an Anthropic key) in `.env` is optional. Without one, ExamForge still works end to end using
  its built-in, non-AI writer (exact maths templates and questions built from your uploaded material).

By default the app runs in single-school mode: the login page offers only "Sign in" and "Create account". For
"Register a school" and "Join with code", copy `.env.example` to `.env` (git-ignored, one per machine) before starting;
it turns on multi-school mode. Accounts and papers live in `./data/`, which is not in git, so a new machine starts empty.

Without any AI key the built-in writer produces papers straight from your uploaded sources. For much better
questions, add an AI key: each teacher or school can use their own, or the operator can supply a platform key.

## Single-school vs multi-school mode

![Single vs multi-school mode](docs/single-vs-multi.png)

| | **Single** (default) | **Multi** (`EXAMFORGE_TENANCY=multi`) |
|---|---|---|
| **Login page** | Sign in, Create account | Sign in, Create account (for an individual teacher or tutor), Register a school, Join with code |
| **Who can sign up** | Anyone can create an account (unless registration is disabled). The first user becomes admin; later users become teachers in that one school. | A school registers itself and its first user becomes that school's admin; teachers join with an invitation code from their school admin. A lone teacher or tutor uses Create account and gets a private workspace of one (listed as "individual" to the operator). |
| **Number of schools** | One school per install. | Many schools and individual teachers on one install, each fully isolated: its own papers, source files, question bank and users. |
| **Teachers' work** | Private: each teacher sees only their own papers and sources. The admin sees everything. | Shared inside a school: every teacher sees the school's papers, source files and bank. Edits stay with the author or the admin; only the admin can delete source files and bank questions. |
| **Platform operator** | None. | Accounts in `EXAMFORGE_SUPERADMIN_EMAILS` get a Platform console: onboard schools, set fees and token prices per school, suspend a school, see usage and billing. They cannot see school content. |
| **Platform library** | Not available. | The operator publishes source files and questions with a Publish button; every school and teacher can browse them under Platform library and add what they want to their own library as an independent copy. |
| **Support access** | Not available. | A school admin can grant a time-limited, read-only support view. The operator can then open the school as support, and every item opened is written to the school's audit log. |
| **Same in both modes** | AI key order (teacher key, school key, platform key, built-in writer), paper generation, PDF and Word export, Indian languages and password rules. | *(same)* |

Use single when one school or college hosts its own copy. Use multi when you run one service for several schools
and bill them.

### Platform library (multi mode)

The platform operator can publish content for every school.

1. Sign in as the operator and upload files in **Source library** (or use the questions already in **Question bank**).
2. Click **Publish** in front of a file or a question, or **Publish all shown** in the question bank. **Unpublish**
   withdraws it.
3. Every school and individual teacher sees it under **Platform library**. They click **Add to my library** (or **Add**
   / **Add all matching** for questions) and it is copied into their own source library or question bank.

A copy is independent: if you later unpublish or delete the original, schools that already added it keep their copy.
Added files and questions follow the school's usual rules (everyone in the school can use them, only the administrator
can delete them). Adding the same question twice is skipped. Only files that have finished processing can be
published, and the operator can publish only items in the operator's own library, never a school's.

### Switching off individual sign-up (multi mode)

`EXAMFORGE_ALLOW_INDIVIDUAL_SIGNUP` is an on/off setting for the "Create account" tab.

- **Default:** on, so anyone can use "Create account" and get a private workspace of their own.
- **Turning it off:** set `EXAMFORGE_ALLOW_INDIVIDUAL_SIGNUP=false` in `.env` and restart. The "Create account" tab
  disappears from the login page and the server refuses individual sign-ups. Use this if you want only schools to
  join, not individual teachers.
- **What keeps working:** "Register a school" and "Join with code" still work, because school registration has its
  own setting (`EXAMFORGE_ALLOW_SCHOOL_SIGNUP`, also on by default).

## Multi-school service

```bash
EXAMFORGE_TENANCY=multi \
EXAMFORGE_SUPERADMIN_EMAILS=you@example.com \
EXAMFORGE_SECRET_KEY=$(openssl rand -hex 32) \
EXAMFORGE_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
.venv/bin/python run.py
```

- Each school sees only its own papers, source files and question bank; every teacher in a school shares them.
- Only the school administrator can delete source files or bank questions.
- The platform operator (`EXAMFORGE_SUPERADMIN_EMAILS`) manages schools, fees and token prices, but cannot see
  school content unless a school administrator grants a time-limited, read-only support view.
- AI calls resolve in this order: teacher's own key, school key, platform key, built-in writer. Tokens used on a
  platform key are billed at the school's rates; tokens on your own key are never charged.

Set `EXAMFORGE_SECRET_KEY` and `EXAMFORGE_ENCRYPTION_KEY` to fixed values in production, otherwise sessions and
stored AI keys do not survive a restart.

## PostgreSQL

```bash
export EXAMFORGE_DB_PASSWORD=choose-a-password
docker compose up -d postgres
export EXAMFORGE_DATABASE_URL="postgresql+psycopg://examforge:$EXAMFORGE_DB_PASSWORD@127.0.0.1:5432/examforge"
.venv/bin/python run.py
```

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests
# end-to-end over HTTP against a running server (creates schools and users, so use a scratch data dir):
EXAMFORGE_DATA_DIR=/tmp/ef_e2e EXAMFORGE_TENANCY=multi EXAMFORGE_SUPERADMIN_EMAILS=operator@e2e.test .venv/bin/python run.py &
.venv/bin/python scripts/e2e.py http://127.0.0.1:8000
```

## Documentation and limits

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Known limits:

- Indian-language text in PDFs is not copyable (the characters are drawn as shapes); use the Word export when
  teachers need to edit.
- Diagram labels are Latin only.
- Section headings in Indian languages have not been reviewed by native speakers.
- Word files are generated with python-docx and have not been opened in every version of Word.
- The built-in writer is a fallback; textbook chapters need an AI key for good questions.
- `.mobi` files need Calibre installed.

## Licence and third-party code

`book_to_skill/` is a vendored document reader under its own licence (`book_to_skill/LICENSE.md`). Fonts are Noto
(SIL OFL). See [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt). Add a licence for ExamForge itself before
publishing publicly.
