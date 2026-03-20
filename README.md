<div align="center">

# ClinicCare V2

### Enterprise Clinic Management System

*RAG-powered clinical intelligence · LangGraph multi-agent workflows · Real-time scheduling*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-000000?style=flat-square&logo=next.js&logoColor=white)](https://nextjs.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.4-1C3C3C?style=flat-square&logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=flat-square&logo=mongodb&logoColor=white)](https://mongodb.com)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://typescriptlang.org)

</div>

---

## What Is This?

ClinicCare V2 is a full-stack clinic management platform built for real clinical workflows. It combines a structured patient and visit management system with two AI layers — a **RAG pipeline** that answers questions about a patient's clinical history, and a **multi-agent system** that handles reception, scheduling, and notifications autonomously.

The system supports three distinct roles — **Doctor**, **Receptionist**, and **Admin** — each with a purpose-built interface and strictly enforced backend permissions.

---

## Live Demo

> Try the application with any of the three role perspectives:

| Role | Email | Password |
|---|---|---|
| Doctor | `dr.anika.sharma@cliniccare.in` | `Doctor@123` |
| Doctor | `dr.rohan.mehta@cliniccare.in` | `Doctor@123` |
| Receptionist | `receptionist@cliniccare.in` | `Recept@123` |
| Admin | `admin@cliniccare.in` | `Admin@123` |

> **Note:** Demo accounts are read-only. Data is not modified.
> First load may take ~30 seconds (free tier cold start).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Next.js Frontend                       │
│         TanStack Query · Zustand · Tailwind CSS             │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────┐
│                    FastAPI Backend                          │
│              Async · JWT Auth · Role Guards                 │
├──────────────┬──────────────┬──────────────┬────────────────┤
│   Patient    │  RAG Engine  │ Agent System │   Admin API    │
│     CRUD     │   Pipeline   │  LangGraph   │   + Audit      │
└──────┬───────┴──────┬───────┴──────┬───────┴────────────────┘
       │              │              │
  ┌────▼────┐   ┌─────▼─────┐  ┌────▼────────┐
  │ MongoDB │   │ ChromaDB  │  │  Supabase   │
  │  Atlas  │   │  (Vectors)│  │ (LG State)  │
  └─────────┘   └─────┬─────┘  └─────────────┘
                      │
                ┌─────▼─────┐
                │   Redis   │
                │  (Cache)  │
                └───────────┘
```

---

## Tech Stack

### Backend
| Layer | Technology | Purpose |
|---|---|---|
| Framework | FastAPI + Uvicorn | Async REST API |
| Database | MongoDB Atlas + Motor | Patient & visit records |
| Vector Store | ChromaDB Cloud | RAG embeddings |
| Cache | Redis (Upstash) | RAG query cache · sessions |
| Agent State | Supabase PostgreSQL | LangGraph thread persistence |
| AI / LLM | OpenAI GPT-4o-mini | Chat + embeddings |
| Agent Framework | LangGraph 0.4 | Multi-agent orchestration |
| Auth | JWT + bcrypt | Role-based access control |
| PDF | ReportLab | Patient & visit PDF export |
| Email | aiosmtplib | Async SMTP delivery |
| Logging | structlog | Structured JSON logs |

### Frontend
| Layer | Technology |
|---|---|
| Framework | Next.js 14 App Router |
| Language | TypeScript |
| Styling | Tailwind CSS (Carbon Frost design) |
| State | Zustand (session-scoped) |
| Data Fetching | TanStack Query v5 |
| Forms | React Hook Form + Zod |
| Charts | Recharts |
| Toasts | Sonner |

---

## Core Features

### Patient Management
- Complete patient registration with personal info, allergies, chronic conditions
- Two-collection MongoDB design — `patients` (metadata) + `visits` (clinical records)
- Doctor-scoped data isolation — doctors see only their own assigned patients
- Receptionist cross-doctor search and registration
- Visit timeline with vitals (BP, weight), medications, diagnosis, follow-up tracking
- PDF export and email delivery of patient history and individual visits

### RAG — Clinical Intelligence
The RAG system answers natural language questions about a patient's medical history:

```
"What medications has this patient been on in the last 6 months?"
"Has the patient ever had a reaction to NSAIDs?"
"Summarise the last 3 visits."
```

Full pipeline:
```
Query
  → Redis cache check (SHA256 key · 1hr TTL)
  → Cache miss → HybridRetriever
      ├── ChromaDB vector search (semantic similarity)
      └── BM25 keyword search (exact term matching)
  → RRF fusion (Reciprocal Rank Fusion — merges both result sets)
  → CrossEncoder reranker (precision pass over top candidates)
  → GPT-4o-mini synthesis (answer + source citations)
  → Cache result → Return to client
```

- Pre-visit brief generation — auto-summary before a consultation
- Patient-scoped queries — doctors can restrict RAG to a single patient
- Role-enforced — receptionists have zero access to clinical RAG

### Multi-Agent System (LangGraph)

A supervisor-routed graph of 4 specialised agents, each with its own tools and responsibilities:

```
User Message
     │
     ▼
┌─────────────┐
│  Supervisor │  — classifies intent, routes to correct agent
└──────┬──────┘
       │
  ┌────┴──────────────────────────────────────┐
  │            │            │          │      │
  ▼            ▼            ▼          ▼      │
┌──────┐  ┌────────┐  ┌──────────┐  ┌──────┐  │
│ RAG  │  │ Recept │  │ Schedule │  │Notif │  │
│Agent │  │ Agent  │  │  Agent   │  │Agent │  │
└──────┘  └────────┘  └────┬─────┘  └──────┘  │
                            │                 │
                     ┌──────▼──────┐          │
                     │  Calendar   │◄─────────┘
                     │    Agent   │  (slot checks)
                     └────────────┘
```

**ReceptionistAgent** — identifies existing patients by name, registers new patients, answers clinic queries

**RAGAgent** — answers clinical history questions using the RAG pipeline (ReAct loop, max 5 tool calls)

**SchedulingAgent** — books appointments, resolves slot conflicts, delegates availability checks to CalendarAgent

**CalendarAgent** — dedicated agent node for real-time slot availability checks; routed to directly by the supervisor for calendar queries, and delegated to by SchedulingAgent during booking flows

**NotificationAgent** — composes and sends confirmation emails to patients, retries on SMTP failure

All agents share:
- **Thread-based memory** — conversation context persists across multiple messages via `thread_id`
- **PostgresSaver checkpointer** — LangGraph state stored in Supabase, survives process restarts
- **Structured state** — shared `AgentState` TypedDict passed between all nodes

### Calendar & Appointments
- Month-view calendar combining two event sources:
  - `appointments` collection (agent-booked slots)
  - `patients.metadata.pending_followup_date` (doctor-set follow-ups)
- Slot availability check before booking
- Doctor vs receptionist calendar views

### Admin Panel
- Embedding pipeline — trigger ChromaDB ingestion for pending visits
- Queue status — see embedded / pending / failed visit counts
- Sync check — detect and fix MongoDB ↔ ChromaDB status mismatches
- Agent monitoring — call counts, latency, token usage, fallback rates per agent
- Analytics — monthly patient registrations, visit trends, top diagnoses, doctor utilisation
- Audit logs — every create / update / delete action attributed to the actor

---

## Database Design

### Two-Collection MongoDB Pattern

```
patients collection                visits collection
─────────────────────              ─────────────────────────
_id                                _id
personal:                          patient_id  → FK to patients
  name, dob, sex, phone            doctor_id   → FK to users
  known_allergies                  visit_date
  chronic_conditions               bp, weight_kg
  assigned_doctor_id               chief_complaint
metadata:                          symptoms
  total_visits                     diagnosis
  last_visit_date                  medications []
  pending_followup_date            followup_date
  embedding_pending_count          embedding_status
                                   chroma_chunk_id
```

**Why split?** Patient list queries load zero visit data — fast dashboard. Embedding pipeline queries only the `visits` collection. No 16MB document size risk for high-visit patients.

---

## RAG Pipeline — Deep Dive

### Embedding (Ingestion)
```
visits (embedding_status: pending)
  → VisitChunker — builds rich text chunk with patient context
  → OpenAIEmbedder — text-embedding-3-small (1536 dims)
  → ChromaDB upsert (with metadata: patient_id, doctor_id, date, diagnosis)
  → MongoDB update: embedding_status → "embedded", chroma_chunk_id saved
```

### Retrieval
```
Query + patient_id (optional)
  ├── Vector search  — ChromaDB cosine similarity, top-10 candidates
  └── BM25 search    — keyword frequency over visit corpus
         ↓
  RRF fusion — score = Σ 1/(k + rank_i) for each result list
         ↓
  CrossEncoder reranker — BERT-based precision pass, reorders top-5
         ↓
  GPT-4o-mini — synthesises answer with inline source citations
```

---

## Role Permissions

| Feature | Doctor | Receptionist | Admin |
|---|---|---|---|
| View own patients | ✅ | — | — |
| Search all patients | — | ✅ | ✅ |
| Add / edit visits | ✅ | — | — |
| RAG clinical queries | ✅ | — | ✅ |
| Agent chatbot | — | ✅ | ✅ |
| Calendar (read) | ✅ | ✅ | — |
| Book appointments | — | ✅ | — |
| PDF export + email | ✅ | — | — |
| Embedding pipeline | — | — | ✅ |
| Audit logs | — | — | ✅ |
| Analytics | — | — | ✅ |
| User management | — | — | ✅ |

---

## API Endpoints

```
POST   /api/auth/login                    JWT authentication
POST   /api/auth/register                 Create staff account (admin)

GET    /api/patients/                     List doctor's patients
POST   /api/patients/                     Register new patient
GET    /api/patients/search               Cross-patient search
GET    /api/patients/doctors/list         List all active doctors
GET    /api/patients/{id}                 Patient detail
PATCH  /api/patients/{id}                 Update patient info
DELETE /api/patients/{id}                 Delete patient

POST   /api/patients/{id}/visit           Add visit record
GET    /api/patients/{id}/visits          Visit history
PATCH  /api/patients/{id}/visits/{vid}    Edit visit
DELETE /api/patients/{id}/visits/{vid}    Delete visit

POST   /api/rag/query                     Clinical RAG query
POST   /api/rag/chat                      RAG chat (multi-turn)
GET    /api/rag/previsit-brief/{id}       Pre-visit auto-summary

POST   /api/agents/chat                   Multi-agent conversation
POST   /api/agents/webhook               External event receiver (webhook secret auth)
GET    /api/agents/thread/{thread_id}     Conversation history

GET    /api/appointments/                 Calendar events
PATCH  /api/appointments/{id}/cancel      Cancel appointment
GET    /api/appointments/available-slots  Free slot check for a doctor + date

GET    /api/pdf/patient/{id}              Download patient PDF
GET    /api/pdf/visit/{id}               Download visit PDF
POST   /api/pdf/patient/{id}/email        Email patient PDF
POST   /api/pdf/visit/{id}/email          Email visit PDF

POST   /api/admin/embed-batch             Run embedding pipeline
GET    /api/admin/queue                   Embedding queue status
POST   /api/admin/retry-failed            Retry failed embeddings
GET    /api/admin/sync-check              MongoDB ↔ Chroma audit
POST   /api/admin/sync-fix                Fix status mismatches
GET    /api/admin/audit-logs              Action audit trail
GET    /api/admin/analytics               Usage analytics
GET    /api/admin/agent-stats             Agent performance metrics
GET    /api/admin/agent-logs              Raw agent call logs (filterable by agent/role)
GET    /api/admin/users                   List all staff users
POST   /api/admin/users                   Create new staff user
PATCH  /api/admin/users/{user_id}         Update staff user

GET    /health                            System health check
```

---

## Project Structure

```
cliniccare-v2/
├── backend/
│   ├── agents/
│   │   ├── graph.py              LangGraph graph builder
│   │   ├── supervisor.py         Intent router
│   │   ├── state.py              Shared AgentState TypedDict
│   │   ├── receptionist_agent.py Patient identification + registration
│   │   ├── rag_agent.py          Clinical history queries (ReAct)
│   │   ├── scheduling_agent.py   Appointment booking + conflicts
│   │   ├── notification_agent.py Email composition + SMTP delivery
│   │   ├── calendar_agent.py     Slot availability checks
│   │   └── drug_checker.py       Drug interaction validation tool
│   ├── api/
│   │   ├── middleware/
│   │   │   └── auth_middleware.py JWT decode + role enforcement
│   │   └── routes/
│   │       ├── auth.py           Login + register
│   │       ├── patients.py       Patient + visit CRUD
│   │       ├── rag.py            RAG query endpoints
│   │       ├── agents.py         Agent chat endpoints
│   │       ├── appointments.py   Calendar endpoints
│   │       ├── pdf.py            PDF export + email
│   │       └── admin.py          Pipeline + analytics + audit
│   ├── core/
│   │   └── config.py             Pydantic settings (env-driven)
│   ├── db/mongodb/
│   │   ├── connection.py         Async Motor client + pool
│   │   └── indexes.py            MongoDB index definitions
│   ├── models/
│   │   └── patient.py            All Pydantic models + enums
│   ├── rag/
│   │   ├── rag_service.py        Orchestrates full RAG pipeline
│   │   ├── chunking/             Visit → text chunk conversion
│   │   ├── embedding/            OpenAI embedding wrapper
│   │   └── retrieval/
│   │       ├── chroma_client.py  ChromaDB HTTP client
│   │       ├── hybrid_retriever.py Vector + BM25 retrieval
│   │       ├── bm25_retriever.py BM25 keyword search
│   │       └── reranker.py       CrossEncoder precision reranking
│   ├── services/
│   │   ├── auth/auth_service.py  Password hashing + JWT
│   │   └── patient/patient_service.py  DB transactions
│   ├── utils/
│   │   └── audit.py              Fire-and-forget audit logging
│   ├── tasks.py                  Celery scheduled tasks
│   └── main.py                   FastAPI app + lifespan
│
├── frontend/
│   └── src/
│       ├── app/
│       │   ├── (auth)/login/     Login page
│       │   └── (dashboard)/
│       │       ├── dashboard/    KPI cards + recent patients
│       │       ├── patients/     Patient list + detail + visits
│       │       ├── rag/          Cross-patient RAG chatbot
│       │       ├── agent/        Receptionist agent chat
│       │       ├── calendar/     Appointment calendar
│       │       └── admin/        Pipeline + analytics + audit
│       ├── components/ui/        Button, Card, Input, Modal, Badge
│       ├── lib/api.ts            Axios client + all API functions
│       ├── store/auth.ts         Zustand auth (sessionStorage)
│       └── types/index.ts        Shared TypeScript types
│
└── tests/
    ├── conftest.py               Test DB, HTTP client, JWT fixtures
    ├── test_auth.py              Auth service + login + register
    ├── test_patients.py          Patient CRUD + role isolation
    ├── test_visits.py            Visit lifecycle + metadata sync
    ├── test_rag.py               RAG endpoints + access control
    ├── test_admin.py             Pipeline + queue endpoints
    ├── test_models.py            Pydantic schema validation
    ├── test_rrf.py               RRF algorithm unit tests
    ├── test_chroma_client.py     ChromaDB client (mocked)
    ├── test_health.py            Health + router import checks
    └── TEST_REPORT.md            Full test run results + analysis
```

---

## Test Suite

**195 tests · 192 passed · 98.5% pass rate**

```bash
# Run all tests
pytest tests/ -v

# Unit tests only (no DB needed)
pytest tests/ -m unit -v

# Integration tests
pytest tests/ -m integration -v
```

Tests run against a separate `cliniccare_test` database — production data is never touched. The test DB is dropped after every session.

See [tests/TEST_REPORT.md](tests/TEST_REPORT.md) for the full breakdown.

---

## Local Setup

### Prerequisites
- Python 3.11+
- Node.js 20+
- MongoDB Atlas account
- ChromaDB Cloud account
- Supabase project
- Redis (Upstash or local)
- OpenAI API key (or EURI)

### Backend

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/cliniccare-v2.git
cd cliniccare-v2

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Start backend
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000`

---

## Deployment

| Service | Platform |
|---|---|
| Backend (FastAPI) | Render — Python runtime, `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |
| Frontend (Next.js) | Vercel — root directory: `frontend` |
| Database | MongoDB Atlas |
| Vectors | ChromaDB Cloud |
| Cache | Upstash Redis |
| Agent state | Supabase PostgreSQL |

---

## Key Design Decisions

**Two MongoDB collections** — `patients` and `visits` are separate. Patient list queries never load visit data. Embedding pipeline queries only visits. No embedded-array size limit risk.

**Hybrid retrieval** — pure vector search misses exact drug names and diagnosis codes. Pure BM25 misses semantic variants. RRF fusion of both outperforms either alone.

**LangGraph over plain functions** — thread-based memory means the receptionist agent remembers the patient's name across multiple turns without re-asking. Supabase checkpointing survives server restarts.

**sessionStorage for auth** — each browser tab holds an independent session. You can be logged in as doctor in one tab and receptionist in another simultaneously.

**Fire-and-forget audit logging** — audit writes never block or fail the original request. Errors are logged but swallowed.

**Graceful degradation** — if Redis or Supabase are misconfigured, the app starts normally. Only RAG cache and agent endpoints degrade. Core patient management always works.

---

<div align="center">

Built with FastAPI · LangGraph · Next.js · MongoDB · ChromaDB

</div>
