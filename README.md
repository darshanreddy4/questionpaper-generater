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
| **Support access** | Not available. | A school admin can grant a time-limited, read-only support view. The operator can then open the school as support, and every item opened is written to the school's audit log. |
| **Same in both modes** | AI key order (teacher key, school key, platform key, built-in writer), paper generation, PDF and Word export, Indian languages and password rules. | *(same)* |

Use single when one school or college hosts its own copy. Use multi when you run one service for several schools
and bill them.

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
