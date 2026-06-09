# ANURAG'S JARVIS — Technical Specification v2.0

JARVIS is a self-hosted, single-user, privacy-first AI knowledge base with bookmark management, RAG-powered chat, automated summaries, and a futuristic, Tony Stark-inspired interface. All data remains strictly on your local infrastructure.

---

## Key Features
- **Zero-Trust Envelope Encryption**: Bookmark summaries, categories, and chat messages are encrypted at rest using AES-256-GCM. Decryption keys are derived from your login passphrase and are never stored in plaintext.
- **Air-Gapped AI Integration**: Utilizes local LLMs via Ollama (`llama3.2` for chat and text summarization, `mxbai-embed-large` for generating 1024-dimension embeddings).
- **Hybrid Vector + Full-Text Search**: Integrates semantic vector search (ChromaDB) with traditional full-text keyword indexing (PostgreSQL's HNSW vector matching and tsvector index).
- **Automated Web & YouTube Scraper**: Background async ingestion workers crawl text content, fetch YouTube transcripts, and run automated analysis pipelines.
- **JARVIS Persona Console**: Interactive, real-time streaming WebSocket chat panel featuring Tony Stark's assistant persona.

---

## Project Structure
```
jarvis/
├── backend/
│   ├── main.py             # FastAPI Server & WebSocket stream
│   ├── schema.sql           # Database tables, pgvector HNSW indexing
│   ├── db_init.py          # Automatic migrations and admin seeding
│   ├── requirements.txt     # Python backend dependencies
│   ├── Dockerfile           # Backend container
│   ├── .env.example         # System configuration template
│   └── core/
│       ├── config.py       # Pydantic Settings management
│       ├── db/
│       │   └── pool.py     # asyncpg Connection Pooler
│       ├── security/
│       │   ├── crypto.py   # AES-256-GCM Envelope cryptography
│       │   └── auth.py     # JWT Token validation
│       ├── rag/
│       │   └── pipeline.py # LangChain RAG & Chroma configurations
│       └── ingestion/
│           └── worker.py   # Async Web scrapers and LLM enricher
├── frontend/
│   ├── index.html          # Hologram core dashboard
│   ├── login.html          # Authentication terminal console
│   ├── style.css           # Futuristic HUD design layout
│   ├── app.js              # State engine, websocket streams, and controllers
│   └── auth.js             # Operator session headers
└── docker-compose.yml       # Entire container orchestration
```

---

## Installation & Deployment

You can deploy the complete stack with a single command. The system automatically launches PostgreSQL + pgvector, Redis, Ollama, pulls the required models, runs schema migrations, seeds the default user, and starts Nginx to serve the dashboard.

### Prerequisites
- Docker and Docker Compose installed.

### Start the Stack
1. Clone this repository or copy the code.
2. Navigate to the project root directory.
3. Run the following command:
   ```bash
   docker compose up --build -d
   ```
4. The services will spin up:
   - **Frontend Console**: [http://localhost](http://localhost) (Nginx serving on port 80)
   - **FastAPI Core Backend**: [http://localhost:8000](http://localhost:8000)
   - **Ollama Engine**: [http://localhost:11434](http://localhost:11434)
   - **PostgreSQL Database**: Port 5432
   - **Redis Cache**: Port 6379

*Note: On the first boot, the `jarvis-ollama-pull` service will pull `llama3.2` and `mxbai-embed-large` models in the background. Depending on your internet speed, this may take a few minutes. Check progress with `docker logs -f jarvis-ollama-pull`.*

---

## Authentication & Default Operator Credentials
Access the login terminal at [http://localhost](http://localhost). The default developer credentials seeded by migrations are:
- **Operator Name**: `anurag`
- **Decryption Passphrase**: `password123` *(We recommend changing this in production by setting the `MASTER_PASSPHRASE` variable in your compose environment)*.

Upon submission, the passphrase is used to derive the database master key. Decrypted tokens are cached in the secure session buffer.

---

## System Workflows

### 1. Link Ingestion
```
[URL Submitted] -> [FastAPI Server] -> [Redis Queue] -> [Ingestion Worker]
                                                               │
    ┌──────────────────────────────────────────────────────────┘
    ▼
[Scraper / readability-lxml] -> [Ollama Enrichment] -> [Envelope Encrypt]
                                                               │
    ┌──────────────────────────────────────────────────────────┘
    ▼
[Save pgvector Embeddings (ChromaDB) + Encrypted PostgreSQL Records]
```

### 2. Conversational RAG
When you chat with JARVIS:
1. Your question is sent over WebSockets.
2. The system embeds your query using `mxbai-embed-large`.
3. It performs a similarity check on ChromaDB (Cosine similarity matching).
4. The matched context is injected into the JARVIS system prompt.
5. The local LLM streams replies containing clickable citations matching resource IDs.
