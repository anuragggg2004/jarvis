# main.py
from __future__ import annotations
import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import List, Optional

import asyncpg
import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, HttpUrl

from core.config import settings
from core.db.pool import init_db
from core.ingestion.worker import IngestionWorker
from core.rag.pipeline import JarvisRAG, RAGConfig, JARVIS_SYSTEM_PROMPT
from core.security.auth import create_access_token, get_current_user
from core.security.crypto import JarvisCrypto, hash_password, verify_password

# ── Globals ─────────────────────────────────────────────────────────────────
crypto: JarvisCrypto | None = None
rag:    JarvisRAG    | None = None
redis_client: redis.Redis    | None = None
db_pool:      asyncpg.Pool   | None = None
ingestion_worker: IngestionWorker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global crypto, rag, redis_client, db_pool, ingestion_worker

    db_pool      = await init_db(settings.DATABASE_URL)
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

    master_passphrase = settings.MASTER_PASSPHRASE.encode()
    crypto = JarvisCrypto(master_passphrase)

    rag = JarvisRAG(RAGConfig(), crypto, db_pool)

    ingestion_worker = IngestionWorker(redis_client, db_pool, rag, crypto)
    await ingestion_worker.start()
    await _warmup_ollama()

    yield

    await ingestion_worker.stop()
    await db_pool.close()
    await redis_client.close()


app = FastAPI(
    title="JARVIS — Anurag's Personal AI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this if needed, frontend runs on settings.FRONTEND_URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── Pydantic models ──────────────────────────────────────────────────────────

class BookmarkCreate(BaseModel):
    url:            HttpUrl
    title:          Optional[str]       = None
    tags:           List[str]           = []
    collection_ids: List[str]           = []
    source:         str                 = "manual"

class BookmarkResponse(BaseModel):
    id:          str
    url:         str
    title:       str
    summary:     str
    key_points:  List[str]
    category:    str
    tags:        List[str]
    domain:      str
    read_status: str
    is_favorite: bool
    added_at:    str

class ChatRequest(BaseModel):
    message:         str
    conversation_id: Optional[str] = None
    stream:          bool           = True
    use_rag:         bool           = True

class ChatResponse(BaseModel):
    response:        str
    conversation_id: str
    citations:       List[str]
    tokens_used:     int


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM jarvis_user WHERE username = $1", form_data.username
        )
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")

    # Re-derive crypto from login password (password IS the passphrase in this design)
    global crypto
    crypto = JarvisCrypto(form_data.password.encode(), bytes(user["master_key_salt"]))

    if not crypto.verify_passphrase(
        form_data.password.encode(),
        bytes(user["master_key_salt"]),
        bytes(user["encrypted_master_key"]),
    ):
        raise HTTPException(401, "Master key verification failed")

    token = create_access_token(
        data={"sub": user["username"], "user_id": str(user["id"])},
        expires_delta=timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS),
    )
    await _log_audit(user["id"], "login", "session", None, request.client.host)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/verify")
async def verify_token(current_user: dict = Depends(get_current_user)):
    return {"valid": True, "user": current_user}


# ── Bookmarks ─────────────────────────────────────────────────────────────────

@app.post("/bookmarks", response_model=BookmarkResponse, status_code=202)
async def create_bookmark(
    bookmark: BookmarkCreate,
    current_user: dict = Depends(get_current_user),
):
    """Queue a URL for async ingestion. Returns immediately with job ID."""
    url_str = str(bookmark.url)
    async with db_pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM bookmarks WHERE user_id=$1 AND url=$2 AND is_archived=FALSE",
            uuid.UUID(current_user["user_id"]), url_str,
        )
        if existing:
            raise HTTPException(409, "Bookmark already exists")

        job_id = await conn.fetchval(
            """INSERT INTO ingestion_jobs (user_id, source_url, source_type, status)
               VALUES ($1, $2, 'url', 'pending') RETURNING id""",
            uuid.UUID(current_user["user_id"]), url_str,
        )

    await redis_client.lpush("ingestion_queue", str(job_id))

    domain = url_str.split("/")[2].replace("www.", "") if "//" in url_str else url_str
    return BookmarkResponse(
        id=str(job_id),
        url=url_str,
        title="Processing...",
        summary="JARVIS is analysing this link, Sir.",
        key_points=[],
        category="pending",
        tags=bookmark.tags,
        domain=domain,
        read_status="unread",
        is_favorite=False,
        added_at="now",
    )


@app.get("/bookmarks", response_model=List[BookmarkResponse])
async def list_bookmarks(
    query:         Optional[str] = None,
    tags:          Optional[str] = None,
    collection_id: Optional[str] = None,
    status:        Optional[str] = None,
    limit:         int           = 50,
    offset:        int           = 0,
    current_user:  dict          = Depends(get_current_user),
):
    conditions = ["b.user_id=$1", "b.is_archived=FALSE"]
    params: list = [uuid.UUID(current_user["user_id"])]
    p = 2

    if query:
        conditions.append(f"(b.title ILIKE ${p} OR b.description ILIKE ${p})")
        params.append(f"%{query}%"); p += 1
    if tags:
        conditions.append(f"b.tags && ${p}")
        params.append(tags.split(",")); p += 1
    if collection_id:
        conditions.append(f"${p} = ANY(b.collection_ids)")
        params.append(uuid.UUID(collection_id) if isinstance(collection_id, str) else collection_id); p += 1
    if status:
        conditions.append(f"b.read_status=${p}")
        params.append(status); p += 1

    sql = (
        f"SELECT * FROM bookmarks b WHERE {' AND '.join(conditions)} "
        f"ORDER BY b.added_at DESC LIMIT ${p} OFFSET ${p+1}"
    )
    params.extend([limit, offset])

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    results = []
    for row in rows:
        try:
            dek = crypto.unwrap_dek(
                bytes(row["summary_dek_wrapped"]),
                bytes(row["summary_dek_iv"]),
                bytes(row["summary_dek_tag"]),
            )
            summary = crypto.decrypt_field(
                bytes(row["summary_encrypted"]),
                bytes(row["summary_iv"]),
                bytes(row["summary_tag"]),
                dek,
            )
            kp_dek = crypto.unwrap_dek(
                bytes(row["key_points_dek_wrapped"]),
                bytes(row["key_points_dek_iv"]),
                bytes(row["key_points_dek_tag"]),
            )
            key_points_raw = crypto.decrypt_field(
                bytes(row["key_points_encrypted"]),
                bytes(row["key_points_iv"]),
                bytes(row["key_points_tag"]),
                kp_dek,
            )
            cat_dek = crypto.unwrap_dek(
                bytes(row["category_dek_wrapped"]),
                bytes(row["category_dek_iv"]),
                bytes(row["category_dek_tag"]),
            )
            category = crypto.decrypt_field(
                bytes(row["category_encrypted"]),
                bytes(row["category_iv"]),
                bytes(row["category_tag"]),
                cat_dek,
            )
        except Exception:
            summary = row["description"] or "Failed to decrypt summary, Sir."
            key_points_raw = "Failed to decrypt key points."
            category = "Decryption Failed"

        results.append(BookmarkResponse(
            id=str(row["id"]),
            url=row["url"],
            title=row["title"] or "",
            summary=summary,
            key_points=key_points_raw.splitlines(),
            category=category,
            tags=list(row["tags"] or []),
            domain=row["domain"] or "",
            read_status=row["read_status"],
            is_favorite=row["is_favorite"],
            added_at=row["added_at"].isoformat(),
        ))
    return results


@app.get("/bookmarks/{bookmark_id}", response_model=BookmarkResponse)
async def get_bookmark(
    bookmark_id: str,
    current_user: dict = Depends(get_current_user),
):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM bookmarks WHERE id=$1 AND user_id=$2",
            uuid.UUID(bookmark_id), uuid.UUID(current_user["user_id"]),
        )
    if not row:
        raise HTTPException(404, "Bookmark not found")

    try:
        dek = crypto.unwrap_dek(
            bytes(row["summary_dek_wrapped"]),
            bytes(row["summary_dek_iv"]),
            bytes(row["summary_dek_tag"]),
        )
        summary = crypto.decrypt_field(
            bytes(row["summary_encrypted"]), bytes(row["summary_iv"]),
            bytes(row["summary_tag"]), dek,
        )
        kp_dek = crypto.unwrap_dek(
            bytes(row["key_points_dek_wrapped"]),
            bytes(row["key_points_dek_iv"]),
            bytes(row["key_points_dek_tag"]),
        )
        key_points_raw = crypto.decrypt_field(
            bytes(row["key_points_encrypted"]), bytes(row["key_points_iv"]),
            bytes(row["key_points_tag"]), kp_dek,
        )
        cat_dek = crypto.unwrap_dek(
            bytes(row["category_dek_wrapped"]),
            bytes(row["category_dek_iv"]),
            bytes(row["category_dek_tag"]),
        )
        category = crypto.decrypt_field(
            bytes(row["category_encrypted"]), bytes(row["category_iv"]),
            bytes(row["category_tag"]), cat_dek,
        )
    except Exception:
        summary = row["description"] or "Failed to decrypt summary, Sir."
        key_points_raw = "Failed to decrypt key points."
        category = "Decryption Failed"

    return BookmarkResponse(
        id=str(row["id"]),
        url=row["url"],
        title=row["title"] or "",
        summary=summary,
        key_points=key_points_raw.splitlines(),
        category=category,
        tags=list(row["tags"] or []),
        domain=row["domain"] or "",
        read_status=row["read_status"],
        is_favorite=row["is_favorite"],
        added_at=row["added_at"].isoformat(),
    )


@app.patch("/bookmarks/{bookmark_id}", status_code=204)
async def update_bookmark(
    bookmark_id: str,
    updates: dict,
    current_user: dict = Depends(get_current_user),
):
    """Update mutable fields: tags, collection_ids, read_status, is_favorite, is_archived."""
    allowed = {"tags", "collection_ids", "read_status", "is_favorite", "is_archived"}
    safe = {k: v for k, v in updates.items() if k in allowed}
    if not safe:
        raise HTTPException(400, "No valid fields to update")

    # Map collection_ids to UUID array if present
    if "collection_ids" in safe:
        safe["collection_ids"] = [uuid.UUID(cid) if isinstance(cid, str) else cid for cid in safe["collection_ids"]]

    set_clauses = [f"{k}=${i+2}" for i, k in enumerate(safe)]
    params = [uuid.UUID(bookmark_id)] + list(safe.values())
    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE bookmarks SET {', '.join(set_clauses)}, updated_at=NOW() WHERE id=$1",
            *params,
        )


@app.delete("/bookmarks/{bookmark_id}", status_code=204)
async def archive_bookmark(
    bookmark_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete: sets is_archived=TRUE."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookmarks SET is_archived=TRUE, archived_at=NOW() WHERE id=$1 AND user_id=$2",
            uuid.UUID(bookmark_id), uuid.UUID(current_user["user_id"]),
        )


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    if not rag:
        raise HTTPException(503, "RAG not initialised")

    async with db_pool.acquire() as conn:
        if req.conversation_id:
            exists = await conn.fetchval(
                "SELECT id FROM conversations WHERE id=$1 AND user_id=$2",
                uuid.UUID(req.conversation_id), uuid.UUID(current_user["user_id"]),
            )
            if not exists:
                raise HTTPException(404, "Conversation not found")
            conv_id = uuid.UUID(req.conversation_id)
        else:
            conv_id = await conn.fetchval(
                """INSERT INTO conversations (user_id, title, model_used)
                   VALUES ($1,$2,$3) RETURNING id""",
                uuid.UUID(current_user["user_id"]),
                req.message[:80],
                settings.OLLAMA_LLM_MODEL,
            )

        raw_history = await conn.fetch(
            """SELECT role, content_encrypted, content_iv, content_tag,
                      content_dek_wrapped, content_dek_iv, content_dek_tag
               FROM messages WHERE conversation_id=$1 ORDER BY created_at""",
            conv_id,
        )

    history = []
    for m in raw_history:
        try:
            dek = crypto.unwrap_dek(
                bytes(m["content_dek_wrapped"]),
                bytes(m["content_dek_iv"]),
                bytes(m["content_dek_tag"]),
            )
            content = crypto.decrypt_field(
                bytes(m["content_encrypted"]),
                bytes(m["content_iv"]),
                bytes(m["content_tag"]),
                dek,
            )
            history.append({"role": m["role"], "content": content})
        except Exception:
            history.append({"role": m["role"], "content": "Failed to decrypt message."})

    await _store_message(conv_id, "user", req.message)

    if req.use_rag:
        response = await rag.query(req.message, history)
    else:
        from langchain_core.messages import HumanMessage, SystemMessage
        msgs = [SystemMessage(content=JARVIS_SYSTEM_PROMPT)]
        for h in history:
            # Create proper langchain AI or Human messages
            from langchain_core.messages import AIMessage
            cls = HumanMessage if h["role"] == "user" else AIMessage
            msgs.append(cls(content=h["content"]))
        msgs.append(HumanMessage(content=req.message))
        result = await rag.llm.ainvoke(msgs)
        response = result.content

    citations = _extract_citations(response)
    await _store_message(conv_id, "assistant", response, citations=citations)

    return ChatResponse(
        response=response,
        conversation_id=str(conv_id),
        citations=citations,
        tokens_used=len(response.split()),  # rough estimate
    )


@app.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket, token: str):
    """Streaming WebSocket endpoint for real-time JARVIS responses."""
    from core.security.auth import decode_access_token
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    user_id = payload["user_id"]

    try:
        data = await websocket.receive_json()
        question    = data.get("message", "")
        conv_id_raw = data.get("conversation_id")

        async with db_pool.acquire() as conn:
            if conv_id_raw:
                conv_id = uuid.UUID(conv_id_raw)
            else:
                conv_id = await conn.fetchval(
                    "INSERT INTO conversations (user_id,title,model_used) VALUES ($1,$2,$3) RETURNING id",
                    uuid.UUID(user_id), question[:80], settings.OLLAMA_LLM_MODEL,
                )

        await _store_message(conv_id, "user", question)

        full_response = ""
        # Get query stream history
        async with db_pool.acquire() as conn:
            raw_history = await conn.fetch(
                """SELECT role, content_encrypted, content_iv, content_tag,
                        content_dek_wrapped, content_dek_iv, content_dek_tag
                FROM messages WHERE conversation_id=$1 ORDER BY created_at""",
                conv_id,
            )
        
        history = []
        for m in raw_history:
            try:
                dek = crypto.unwrap_dek(
                    bytes(m["content_dek_wrapped"]),
                    bytes(m["content_dek_iv"]),
                    bytes(m["content_dek_tag"]),
                )
                content = crypto.decrypt_field(
                    bytes(m["content_encrypted"]),
                    bytes(m["content_iv"]),
                    bytes(m["content_tag"]),
                    dek,
                )
                history.append({"role": m["role"], "content": content})
            except Exception:
                history.append({"role": m["role"], "content": "Failed to decrypt message."})

        async for chunk in rag.query_stream(question, history):
            full_response += chunk
            await websocket.send_json({"content": chunk})

        citations = _extract_citations(full_response)
        await _store_message(conv_id, "assistant", full_response, citations=citations)
        await websocket.send_json({"done": True, "conversation_id": str(conv_id), "citations": citations})
    except Exception as e:
        logger.error("WebSocket stream error: %s", e)
        await websocket.send_json({"error": str(e)})
    finally:
        await websocket.close()


# ── Collections ───────────────────────────────────────────────────────────────

@app.get("/collections")
async def list_collections(current_user: dict = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM collections WHERE user_id=$1 ORDER BY sort_order, name",
            uuid.UUID(current_user["user_id"]),
        )
    return [dict(r) for r in rows]


@app.post("/collections", status_code=201)
async def create_collection(data: dict, current_user: dict = Depends(get_current_user)):
    parent_id_raw = data.get("parent_id")
    parent_id = uuid.UUID(parent_id_raw) if parent_id_raw else None
    async with db_pool.acquire() as conn:
        cid = await conn.fetchval(
            """INSERT INTO collections (user_id, name, description, icon, color, parent_id)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            uuid.UUID(current_user["user_id"]),
            data.get("name"),
            data.get("description"),
            data.get("icon"),
            data.get("color"),
            parent_id,
        )
    return {"id": str(cid)}


# ── Search ────────────────────────────────────────────────────────────────────

@app.get("/search")
async def hybrid_search(
    q:            str,
    limit:        int  = 20,
    current_user: dict = Depends(get_current_user),
):
    """Vector search (Chroma) merged with full-text search (PostgreSQL)."""
    if not rag:
        raise HTTPException(503, "Search unavailable")

    vector_docs = []
    try:
        vector_docs = await rag.vector_store.asimilarity_search_with_score(q, k=limit)
    except Exception as e:
        logger.error("Vector search failed: %s", e)

    vector_ids  = {d.metadata.get("bookmark_id") for d, _ in vector_docs}

    async with db_pool.acquire() as conn:
        ft_rows = await conn.fetch(
            """SELECT id, title, url, domain, added_at,
                      ts_rank_cd(
                          to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(description,'')),
                          plainto_tsquery('english',$1)
                      ) AS rank
               FROM bookmarks
               WHERE user_id=$2 AND is_archived=FALSE
               ORDER BY rank DESC LIMIT $3""",
            q, uuid.UUID(current_user["user_id"]), limit,
        )

    # Merge: vector results first, then FT results not already included
    seen  = set(vector_ids)
    merged = [
        {"bookmark_id": d.metadata.get("bookmark_id"), "score": float(s), "source": "vector"}
        for d, s in vector_docs if d.metadata.get("bookmark_id")
    ]
    for row in ft_rows:
        rid = str(row["id"])
        if rid not in seen:
            merged.append({"bookmark_id": rid, "score": float(row["rank"]), "source": "fts"})
            seen.add(rid)

    return {"query": q, "results": merged[:limit]}


# ── Ingestion status ──────────────────────────────────────────────────────────

@app.get("/ingestion/{job_id}")
async def ingestion_status(job_id: str, current_user: dict = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT * FROM ingestion_jobs WHERE id=$1 AND user_id=$2",
            uuid.UUID(job_id), uuid.UUID(current_user["user_id"]),
        )
    if not job:
        raise HTTPException(404, "Job not found")
    return dict(job)


# ── Stats / Health ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":   "operational",
        "database": "connected" if db_pool  else "disconnected",
        "redis":    "connected" if redis_client else "disconnected",
    }


@app.get("/stats")
async def stats(current_user: dict = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COUNT(*)                            AS total_bookmarks,
                 COUNT(*) FILTER (WHERE is_favorite) AS favorites,
                 COUNT(*) FILTER (WHERE read_status='unread') AS unread,
                 COUNT(DISTINCT domain)              AS domains
               FROM bookmarks
               WHERE user_id=$1 AND is_archived=FALSE""",
            uuid.UUID(current_user["user_id"]),
        )
        # Fetch tags separately since UNNEST on empty arrays can be tricky
        tag_rows = await conn.fetch(
            "SELECT UNNEST(tags) as tag FROM bookmarks WHERE user_id=$1 AND is_archived=FALSE",
            uuid.UUID(current_user["user_id"]),
        )
    unique_tags = len(set(r["tag"] for r in tag_rows if r["tag"]))
    
    res = dict(row)
    res["unique_tags"] = unique_tags
    return res


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _warmup_ollama():
    import httpx
    async with httpx.AsyncClient(timeout=90) as client:
        for model in [settings.OLLAMA_LLM_MODEL, settings.OLLAMA_EMBED_MODEL]:
            try:
                await client.post(
                    f"{settings.OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": "warmup", "stream": False, "keep_alive": -1},
                )
            except Exception:
                pass  # non-fatal; Ollama may still be starting


async def _store_message(
    conv_id: uuid.UUID,
    role:    str,
    content: str,
    citations: list[str] | None = None,
):
    dek = crypto.generate_dek()
    wrapped, dek_iv, dek_tag = crypto.wrap_dek(dek)
    enc, iv, tag = crypto.encrypt_field(content, dek)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO messages
               (conversation_id, role,
                content_encrypted, content_iv, content_tag,
                content_dek_wrapped, content_dek_iv, content_dek_tag,
                citations)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            conv_id, role, enc, iv, tag, wrapped, dek_iv, dek_tag,
            [uuid.UUID(c) for c in (citations or [])],
        )


def _extract_citations(text: str) -> list[str]:
    import re
    return list(set(re.findall(r"\[source:([a-f0-9\-]+)\]", text)))


async def _log_audit(user_id: uuid.UUID, action, resource_type, resource_id, ip, user_agent=""):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log
               (user_id, action, resource_type, resource_id, ip_address, user_agent)
               VALUES ($1,$2,$3,$4,$5,$6)""",
            user_id, action, resource_type, resource_id, ip, user_agent,
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
