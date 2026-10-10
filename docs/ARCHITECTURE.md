# ExamForge: architecture, workflow, token economy and failure handling

This document describes how ExamForge turns uploaded study material into a question paper and an
answer key, where it spends (and avoids spending) language-model tokens, and what happens when
something goes wrong. Every claim here maps to code in `app/`; file names are given so you can check.

---

## 1. The big picture

```mermaid
flowchart TB
    subgraph Browser["Browser (vanilla JS single-page app)"]
        UI["Library, Wizard, Paper editor,<br/>Question bank, Usage"]
    end

    subgraph API["FastAPI process"]
        MW["Security middleware<br/>CSP, request id, size limits"]
        AUTH["Auth: scrypt passwords, signed tokens,<br/>roles, rate limits"]
        ROUTES["Routes: documents, papers,<br/>patterns, bank, usage, admin"]
        RUNNER["JobRunner<br/>claims queued rows, heartbeats, reaper"]
    end

    subgraph Workers["Worker threads"]
        ING["Ingestion worker"]
        GEN["Generation worker"]
    end

    subgraph Core["Domain logic - plain code, no model"]
        EXTRACT["Extract text<br/>(book_to_skill + extras)"]
        CHUNK["Chunk + BM25 index"]
        BP["Blueprint builder"]
        VAL["Validator + repair loop"]
        QA["Quality report"]
        RENDER["Diagram + math renderers"]
        EXPORT["PDF / DOCX exporters"]
    end

    subgraph Model["Language model (optional)"]
        LLM["OpenAI-compatible or Anthropic"]
        CACHE[("Response cache")]
        OFF["Offline template engine<br/>(no key configured)"]
    end

    DB[("Database<br/>SQLite default, PostgreSQL supported")]
    FS[("Upload storage")]

    UI -->|REST + bearer token| MW --> AUTH --> ROUTES
    ROUTES --> DB
    ROUTES --> FS
    ROUTES -->|wake| RUNNER
    RUNNER <--> DB
    RUNNER --> ING --> EXTRACT --> CHUNK --> DB
    RUNNER --> GEN --> BP --> VAL
    GEN --> CHUNK
    VAL <--> LLM
    VAL <--> OFF
    LLM <--> CACHE
    VAL --> QA --> DB
    ROUTES --> EXPORT
    EXPORT --> RENDER
    ING -. figures .-> LLM
```

**Design rule:** the model is used only for the part that needs language skill (writing a question
and its worked answer). Everything that can be decided by code is decided by code: structure,
marks, difficulty split, topic coverage, validation, duplicate detection, diagrams, layout.

---

## 2. What happens when a teacher uploads a file

```mermaid
sequenceDiagram
    actor T as Teacher
    participant API as API
    participant FS as Storage
    participant DB as Database
    participant R as JobRunner
    participant W as Ingestion worker
    participant M as Vision model

    T->>API: POST /api/documents (file)
    API->>API: Check extension, size, empty file, sanitise filename
    API->>FS: Save as source.ext under a random id
    API->>DB: Document status = queued
    API-->>T: 202 Accepted
    R->>DB: Claim queued row (one conditional UPDATE)
    R->>W: Run process_document
    W->>W: Extract text (PDF, DOCX, EPUB, PPTX, ...)
    W->>W: Strip invisible Unicode (prompt-injection defence)
    alt Scanned PDF or image and a vision model exists
        W->>M: Transcribe pages
    else Scanned PDF and no vision model
        W->>DB: status = failed, with a clear message
    end
    opt Vision model configured
        W->>W: Crop diagrams (max 20 images)
        W->>M: Describe each figure, return diagram spec (cached)
    end
    W->>W: Split into chunks by heading, detect chapters
    W->>DB: Save chunks, figure chunks and stats, status = ready
    T->>API: Poll GET /api/documents
```

---

## 3. What happens when a teacher generates a paper

```mermaid
flowchart TD
    A["Teacher picks pattern, subject, syllabus,<br/>difficulty mix, documents, diagram mode"] --> B["POST /api/papers"]
    B --> B1{"Daily token budget<br/>spent?"}
    B1 -- yes --> B2["HTTP 429, nothing queued"]
    B1 -- no --> C["Paper row saved, status = queued"]
    C --> D["JobRunner claims it -> generating"]
    D --> E["BLUEPRINT (code only)<br/>one slot per question:<br/>section, type, marks, difficulty,<br/>Bloom level, topic, diagram?, OR choice?"]
    E --> F["RETRIEVE (code only)<br/>BM25 picks passages per slot,<br/>used passages are down-weighted"]
    F --> G["BATCH slots per section<br/>3 to 8 per call"]
    G --> H["COMPRESS sources<br/>keep only relevant sentences"]
    H --> I["MODEL WRITES a batch<br/>question + answer + marking steps<br/>+ diagram spec as JSON"]
    I --> J["PARSE + REPAIR JSON<br/>fences, LaTeX backslashes, trailing commas"]
    J --> K["VALIDATE each question"]
    K --> L{"Valid and not<br/>a duplicate?"}
    L -- yes --> M["Accept"]
    L -- no --> N{"Attempts left?"}
    N -- yes --> O["Retry ONLY the failed slots<br/>with the exact error as feedback"]
    O --> I
    N -- no --> P["Placeholder question,<br/>flagged for Regenerate"]
    M --> Q["ASSEMBLE paper"]
    P --> Q
    Q --> R["QUALITY REPORT<br/>score, difficulty drift, coverage,<br/>duplicates, missing answers, diagrams"]
    R --> S["Save paper = ready<br/>+ questions to the bank + usage log"]
    S --> T["Teacher reviews, edits,<br/>regenerates single questions"]
    T --> U["Export PDF / DOCX<br/>question paper, answer key,<br/>teacher's copy, sets A to D"]
```

### What the blueprint guarantees
| Concern | How it is enforced | Where |
|---|---|---|
| Total marks | Pattern defines count, marks and "attempt any N" per section | `exam/patterns.py` |
| Difficulty mix | Greedy allocation by marks so the split matches e.g. 30/50/20 | `exam/blueprint.py` |
| Syllabus coverage | Each slot goes to the topic with the biggest weight deficit | `exam/blueprint.py` |
| Bloom spread | Derived from difficulty and question type | `exam/blueprint.py` |
| Diagrams | Slots chosen by subject and topic keywords, share set by diagram mode | `exam/blueprint.py` |
| Internal choice | A fixed number of slots per section require an OR alternative | `exam/blueprint.py` |

---

## 4. Per-batch retry loop (the self-healing part)

```mermaid
stateDiagram-v2
    [*] --> Pending: slots in batch
    Pending --> Calling: attempt n of 3
    Calling --> Parsed: JSON received
    Calling --> Backoff: network / 429 / 5xx
    Backoff --> Calling: wait, retry
    Calling --> Pending: model call failed, error recorded
    Parsed --> Checked: per-slot validation
    Checked --> Accepted: valid and unique
    Checked --> Pending: invalid, error becomes feedback
    Accepted --> [*]
    Pending --> Placeholder: attempts exhausted
    Placeholder --> [*]: flagged, can be regenerated by hand
```

What the validator rejects, and tells the model to fix:

- Missing slot in the reply.
- MCQ without exactly 4 distinct options, or without a valid correct-option index.
- Assertion-reason text that does not state both A and R.
- Match-the-following lists that are not equal length.
- Case studies with fewer than 2 sub-questions (marks are re-split to total exactly).
- Missing model answer.
- Required diagram that does not validate or draw; a missing OR alternative.
- A question too similar to another in the paper, or already in your question bank.

Marking-scheme steps are rescaled so they always add up to the question's marks.

---

## 5. How tokens are reduced

```mermaid
flowchart LR
    subgraph Spent["Where tokens are spent"]
        W["Writing questions + answers"]
        V["Reading figures<br/>(only when diagrams are uploaded)"]
    end

    subgraph Free["Done in code, zero tokens"]
        a["Blueprint and difficulty split"]
        b["Search and ranking (BM25)"]
        c["Validation and duplicate checks"]
        d["Diagram drawing, math images"]
        e["PDF and DOCX layout"]
        f["Sets A to D shuffling"]
    end

    subgraph Savers["Reducers on what is spent"]
        s1["Batching: one call for 3-8 questions,<br/>rules paid once per batch"]
        s2["Sentence-level source trimming:<br/>max 1200 chars per passage,<br/>irrelevant sentences never added"]
        s3["Short 'already used' list:<br/>12 items x 90 chars"]
        s4["Retry only failed slots"]
        s5["Stable system prompt ->provider prompt caching<br/>(Anthropic cache_control, OpenAI prefix cache)"]
        s6["Response cache: same upload,<br/>same seed rerun, same pattern = free"]
        s7["Optional cheaper 'fast' model<br/>for vision and pattern reading"]
        s8["Image cap: 20 per document"]
        s9["max_tokens sized to the batch"]
        s10["Daily per-user token budget"]
    end

    Spent --- Savers
```

### Numbers you can verify
- A 38-question CBSE Class 10 paper is written in **11 calls**, not 38 (see the smoke script output).
- Every paper stores `quality.usage` with `prompt_tokens`, `completion_tokens`,
  `cached_prompt_tokens`, `cache_hits` and `tokens_saved_by_cache`.
- `/api/usage` (you) and `/api/admin/usage` (administrators) report totals by job type and by user.
- With no API key the offline engine runs entirely in code and spends **0 tokens**.

### What is not cached on purpose
- Replies that did not parse as valid JSON.
- "Regenerate question": you asked for a different question, so replaying would defeat the purpose.
- Retries that carry error feedback.

---

## 5a. Who writes each question (bank, code, or model)

```mermaid
flowchart TD
    S["Slot: type, marks, difficulty, Bloom, topic"] --> R{"Reuse from bank enabled and a<br/>matching approved question exists?"}
    R -- yes --> B["Bank question<br/>0 tokens, already verified"]
    R -- no --> C{"Can code write it exactly?<br/>easy or medium, low marks,<br/>topic has a template or source sentences"}
    C -- yes --> K["Code writes it<br/>0 tokens, answer exact by construction"]
    C -- no --> M["Model writes it<br/>in a batch with other model slots"]
    K -->|"3 failed tries"| M
    M --> V{"Computer check of the answer key<br/>(sympy: arithmetic, final value, solve)"}
    V -- contradiction --> F["Retry with the exact contradiction as feedback"]
    F --> M
    V -- "still wrong after retries" --> X["Kept but flagged 'Check the answer'<br/>never stored in the bank"]
    V -- ok --> A["Accepted, marked 'Answer checked'"]
    B --> P["Paper"]
    K --> P
    A --> P
    X --> P
```

- **Code path (maths):** 22 parametric templates (linear and quadratic equations, discriminant, Pythagoras, similar
  triangles, circles, cylinders, coordinate distance, lines, mean, median, probability, AP, sets, percentages, simple
  interest, pairs of equations, derivatives, integrals, determinants). Answers come from the construction itself,
  diagrams are drawn from the same numbers, and a template is used only when the slot's topic really matches it.
- **Code path (other subjects):** definitions, fill-in-the-blanks, true/false, matching and easy multiple choice built
  from sentences in your own uploaded material.
- **Model path:** hard questions, long answers, case studies, application and analysis, assertion-reason, topics with no
  template, and every "regenerate with an instruction".
- **Computer check:** every model-written answer key is re-checked in code. It re-computes each numeric equality in the
  worked steps, compares the last step with the stated answer, and independently solves any single equation the
  question asks to solve (also against the option marked correct). It only reports real contradictions; what it cannot
  parse it leaves alone. The check runs again when a teacher saves an edit.
- **Provenance:** each question records `origin` (code, bank, model) and `verified` (construction, source, checked,
  flagged), shown as badges in the editor and summarised in the quality report.

---

## 5b. Which AI is used, and who pays

```mermaid
flowchart LR
    J["Any AI job:<br/>write questions, read pictures,<br/>learn a pattern"] --> R{"Key to use?"}
    R -->|"1. teacher's own key"| U["Billing: their AI provider"]
    R -->|"2. school's key (admin set)"| W["Billing: the school's AI provider"]
    R -->|"3. platform key"| P["Billing: ExamForge meters tokens<br/>and charges the configured rates"]
    R -->|"4. none"| O["Built-in code engine<br/>no tokens"]
    P --> B["Platform fee + token charges"]
    U --> F["Platform fee only"]
    W --> F
    O --> F
```

- **When AI is used at all:** only for questions that need judgement (the writing mode "Automatic" sends just those),
  for reading pictures, diagrams and scans (an explicit tick on each upload, at most 20 images per file) and for
  learning a pattern from a sample paper. Reading text from PDF, Word, PowerPoint, EPUB and the like is plain code.
- **Writing mode** (wizard step 3): Automatic (bank, then code, then AI), Code only (no AI at all), or AI for everything.
  Step 4 shows how many questions each writer will take before anything is generated.
- **Bring your own key** (AI & billing page): OpenAI, Anthropic, or any OpenAI-compatible endpoint. Keys are
  encrypted at rest (Fernet), never returned to the browser, and tested without echoing them. Custom endpoints must be
  https and may not point at private networks unless the operator allows it (protection against server-side request
  forgery). A teacher's key overrides the school key, which overrides the platform key.
- **Charging model:** `EXAMFORGE_PLATFORM_FEE_MONTHLY` per school per month, plus `EXAMFORGE_PRICE_INPUT_PER_M` and
  `EXAMFORGE_PRICE_OUTPUT_PER_M` per million tokens, applied only to jobs on the platform key. Each job stores its charge
  at the time of use, so a later price change never rewrites history. A month with no platform tokens totals the fee
  alone. Own-key tokens are shown but never charged. The daily token budget also applies to the platform key only.
- **Not built yet:** online payment, invoices as documents, and per-school tenancy (today one deployment is one school).

---

## 5c. Many schools on one service

Set `EXAMFORGE_TENANCY=multi` to host several schools or colleges. The default, `single`, behaves as before.

| Piece | How it works |
|---|---|
| School | An `Organization`. Every user belongs to one (`User.org_id`). Usage and audit rows carry `org_id` too. |
| Sign-up | A new school registers and its first user becomes administrator. Everyone else joins with an invitation code. A lone teacher or tutor can use Create account, which opens a private workspace of one (an organization with `kind="individual"`, shown to the operator as such). `EXAMFORGE_ALLOW_SCHOOL_SIGNUP=false` closes open school sign-up and `EXAMFORGE_ALLOW_INDIVIDUAL_SIGNUP=false` closes individual sign-up; invitations still work. |
| Invitations | Single-use, expiring (default 14 days), optionally locked to one email. Only a hash is stored; the code is shown once. |
| Isolation | Documents, papers, patterns, the question bank, usage, billing, audit log and the school-wide AI key are all scoped to the school. A request for another school's record gets 404, exactly as if it did not exist. Administrators manage their own school only. |
| Inside a school | Source files, papers, the question bank (including reuse and repeat-avoidance) and custom exam patterns belong to the whole school: everyone in it sees them and can use them, and each shows who made it. Only the person who made a paper or pattern, or the school administrator, can edit or delete it. Only the administrator can delete source files and bank questions. AI keys stay personal; the school key, users, invitations, audit log, usage and billing are administrator-only. A single-school install keeps each teacher's work private. |
| Support access | Platform support sees a school's content only while that school's administrator has allowed it (1 hour to 3 days, optional note, revocable at any moment, one grant at a time). The operator then opens a session of at most two hours that is read-only and limited to papers, source files and the question bank: no people, keys, billing, usage, audit log, downloads or changes. The permission is re-checked on every request, so stopping it cuts off an open session at once. Starting a session and opening each paper, file or bank item is written to the school's own audit log with the operator's email; other schools never see it. |
| Operator | Emails in `EXAMFORGE_SUPERADMIN_EMAILS` get a Platform page: list schools, onboard one, suspend or reactivate, set per-school fee and token prices, see each school's month total. The operator sees counts and charges, never documents, papers or questions. |
| Pricing | Platform defaults come from the environment. Each school may override the monthly fee and the input and output token prices; a blank override falls back to the default. |
| Billing | Unchanged: platform tokens are charged to the school that used them, own-key tokens are not charged. |
| AI cache | Cache keys are namespaced per school, so one school never receives another's cached answer. One token meter and rate limiter are shared. |
| Upgrade | The first start in multi mode moves existing users, usage, audit rows and the workspace AI key into one default school. Running it again changes nothing. |
| Suspension | A suspended school cannot sign in and its existing sessions stop working on the next request. |

Not built: online payment, invoices, per-school custom domains, single sign-on.

## 5d. Indian languages

Papers can be set in Hindi, Marathi, Nepali, Sanskrit, Bengali, Assamese, Punjabi, Gujarati, Odia, Tamil, Telugu, Kannada
and Malayalam (nine scripts). The paper language is a choice in the wizard; documents in these scripts are recognised on
upload and the wizard offers to match the paper to them.

| Area | What happens |
|---|---|
| Reading | Word, sentence and heading detection understand vowel signs, the danda (।) and chapter words such as अध्याय or పాఠం. Zero-width joiners after an Indic letter survive the sanitiser (they are spelling in Malayalam, Telugu and Kannada); hidden characters elsewhere are still stripped. Scanned pages are transcribed in their own script. |
| Writing | The prompt names the language and script and asks for no transliteration. Each question is checked: one written in English for a Hindi paper is sent back for rewriting like any other failed check. Output budgets are about 3.5 times larger and batches half as big, because these scripts cost several tokens per word. The pattern's own wording (instructions, section titles) is translated once per paper (one small AI call). |
| Who writes | AI writes every question. The bank (kept per language) and the built-in templates are English wording, so they are not used for other languages. With no AI, a non-English paper falls back to English where the built-in writer can write at all, and the paper is marked "review" with a warning. |
| PDF | Noto Sans fonts for each script ship in `app/fonts` (SIL OFL) and HarfBuzz shaping (`uharfbuzz`) forms conjuncts and places vowel signs. Headings, "Time Allowed", the marking-scheme table and the footer print in the paper's language. |
| DOCX | Word does the shaping. Each Indic run gets its complex-script font (Nirmala UI), size, bold and language tag. |
| Checks | The computer check on worked answers recognises "solve" in the Indian languages; digits in any script are read as numbers. |
| Cost | Token estimates and the cost preview count Indic text at its real rate. |

Known limits:

- Text in the PDF prints correctly but copying it out gives broken letters (conjunct glyphs have no text mapping). Use the DOCX for text that must be copied or edited.
- Diagram labels use Latin letters and digits only; Indic letters in a label are left out. Captions and question text are unaffected.
- Mathematics stays in LaTeX; a formula containing Indic words is printed as plain text.
- The heading words for each language are standard exam-paper phrasing but have not been reviewed by a native speaker; a school should read them once (`app/export/labels.py`). Nepali, Sanskrit and Assamese print English headings.
- Not covered: Urdu and other right-to-left scripts, and PDFs that store Hindi in legacy font encodings (Kruti Dev and similar) rather than Unicode.
- The DOCX output has been checked structurally, not opened in Word.

## 6. Job lifecycle and crash recovery

```mermaid
stateDiagram-v2
    [*] --> queued: created by the API
    queued --> running: JobRunner claims it<br/>(conditional UPDATE, attempts + 1)
    running --> running: heartbeat every 15 s
    running --> ready: success
    running --> failed: handled error with a message
    running --> queued: heartbeat stale 90 s,<br/>or owner process is dead at boot
    running --> failed: stale and attempts >= 3
    ready --> [*]
    failed --> [*]
```

- The database is the queue, so a restart loses nothing.
- Two server processes can share one database: a job is claimed by exactly one of them.
- Retrying a document clears its earlier chunks first, so there are no duplicates.

---

## 7. Failure handling, end to end

| Stage | What can go wrong | What the system does | User sees |
|---|---|---|---|
| Sign-in | Brute force | 8 attempts per 5 minutes per client, then 429 | "Too many attempts" |
| Upload | Wrong type, too big, empty, hostile name | 415 / 413 / 422; filename reduced to its base name | Specific message |
| Extraction | Corrupt file, XML DOCTYPE/ENTITY | Document marked failed with a message | Failed badge + reason |
| Scanned PDF | No text layer | Vision OCR if configured, otherwise a clear instruction | "Configure a vision model or run OCR" |
| Figures | Vision call fails or returns junk | That figure is skipped, upload still succeeds | Fewer figures |
| Invisible text | Hidden prompt-injection characters | Stripped before indexing | Count in stats |
| Worker crash | Process killed mid-job | Heartbeat expires, job re-queued (max 3 tries) | "Resuming after an interruption" |
| Budget | Daily tokens spent | 429 before any model work | Budget message |
| Model HTTP | 429 / 5xx / network blip | Backoff with `retry-after`, up to 4 tries | Slower, not failed |
| Model HTTP | 400 for unsupported parameter | Auto-adjusts (`max_completion_tokens`, drops `temperature` / `response_format`) | Nothing |
| Model output | Not valid JSON | Repairs fences, LaTeX backslashes, trailing commas; else one retry at low temperature | Nothing |
| Model output | Wrong shape or weak question | Per-slot feedback retry (3 attempts) | Nothing |
| Model output | Duplicate or repeats an old paper | Rejected with "write a different one" | Nothing |
| Diagram | Spec invalid or cannot draw | Required: retry. Optional: dropped. Never crashes the paper | Question without figure |
| Slot never succeeds | Retries exhausted | Placeholder question, quality score lowered | Flagged, "Regenerate" button |
| Whole run | Unexpected exception | Paper marked failed with the message | Failed badge + reason |
| Editing | Invalid diagram or ids in a saved edit | 422 with the exact problem | Inline error |
| Math | LaTeX that the renderer cannot parse | Falls back to readable Unicode text | Plain-text maths |
| PDF layout | One question taller than a page | Rebuilds without the marks column | Same content |
| Export abuse | Too many downloads or diagram renders | Rate limited (40 and 90 per minute) | 429 |

---

## 8. Data model

```mermaid
erDiagram
    USER ||--o{ DOCUMENT : owns
    USER ||--o{ PAPER : owns
    USER ||--o{ PATTERN : saves
    USER ||--o{ BANK_QUESTION : accumulates
    USER ||--o{ USAGE_LOG : incurs
    DOCUMENT ||--o{ CHUNK : "text and figures"
    PAPER ||--o{ BANK_QUESTION : "feeds"
    USER ||--o{ AUDIT_LOG : triggers

    PAPER {
        string status "queued, generating, ready, failed"
        json request
        json content
        json quality
        int attempts
        datetime heartbeat_at
    }
    CHUNK {
        string kind "text or figure"
        string chapter
        text text
        json meta "figure image and diagram spec"
    }
    LLM_CACHE {
        string key "hash of model, prompts, images"
        text response
    }
```

---

## 9. Security posture in one view

- Passwords: scrypt with per-user salt. Tokens: signed, expiring. Roles: administrator and teacher.
- Every record is owner-scoped; other users get 404, not 403, so ids do not leak.
- Uploaded files are stored under random ids, never under their own names.
- Source documents are treated as untrusted data in every prompt, and hidden Unicode is stripped.
- Model-written diagram expressions run through a whitelist parser, never `eval`.
- Content-Security-Policy, `nosniff`, frame denial, `no-store` on API responses.
- API keys live only in server environment variables and are never sent to the browser.
- Audit log for sign-in, uploads, generation, edits, exports and administrator actions.

---

## 10. Switching to PostgreSQL

SQLite is the zero-setup default. For several users or several server processes, use PostgreSQL:

```bash
export EXAMFORGE_DB_PASSWORD='choose-a-strong-password'
docker compose up -d postgres
export EXAMFORGE_DATABASE_URL="postgresql://examforge:${EXAMFORGE_DB_PASSWORD}@127.0.0.1:5432/examforge"
python run.py
```

- `postgres://` and `postgresql://` URLs are accepted; the psycopg 3 driver is selected automatically.
- Connections are pooled (`EXAMFORGE_DB_POOL_SIZE`, `EXAMFORGE_DB_MAX_OVERFLOW`) and health-checked before use.
- Tables are created on first start. Columns added in later versions are added automatically.
  A Postgres advisory lock makes this safe when several processes boot at once.
- Existing SQLite data is not copied over; start fresh or export what you need first.
- Run the live test with `EXAMFORGE_TEST_POSTGRES_URL=... pytest tests/test_postgres.py`.

---

## 11. Limits to be aware of

- Model-written answer keys can still be wrong. Code re-checks arithmetic, final values and single equations
  (section 5a), but it cannot judge word problems, proofs or science reasoning; teachers should review answers,
  especially for questions badged "Check the answer" or without an "Answer checked" badge.
- Offline mode builds questions from templates and definitional sentences. It is a safe fallback,
  not a replacement for a model.
- PDF fonts cover Latin, Greek and mathematical symbols. Indic and CJK scripts are not rendered yet.
- Equations: fractions, roots, sums, integrals, Greek, matrices, determinants, cases and aligned blocks are typeset. Other LaTeX environments, and 3D or implicit-curve diagrams, fall back to plain text or are not drawn.
- DOCX equations are embedded as images (LaTeX kept in the alt text), not editable Word equations.
