# core/ingestion/worker.py
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import httpx
import trafilatura
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDoc
from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    title:      str
    content:    str
    summary:    str      = ""
    key_points: list[str] = field(default_factory=list)
    category:   str      = "Uncategorized"
    tags:       list[str] = field(default_factory=list)
    domain:     str      = ""
    word_count: int      = 0
    language:   str      = "en"
    embedding:  list[float] = field(default_factory=list)


class IngestionWorker:
    def __init__(self, redis_client, db_pool, rag, crypto):
        self.redis   = redis_client
        self.db_pool = db_pool
        self.rag     = rag
        self.crypto  = crypto
        self.running = False
        self.client  = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JARVIS/1.0)"},
        )

    async def start(self):
        self.running = True
        asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        await self.client.aclose()

    async def _loop(self):
        while self.running:
            try:
                item = await self.redis.brpop("ingestion_queue", timeout=5)
                if item:
                    _, job_id = item
                    await self._process(job_id)
            except Exception as exc:
                logger.error("Worker loop error: %s", exc)
                await asyncio.sleep(5)

    async def _process(self, job_id: str):
        async with self.db_pool.acquire() as conn:
            job = await conn.fetchrow(
                "SELECT * FROM ingestion_jobs WHERE id=$1 FOR UPDATE SKIP LOCKED", uuid.UUID(job_id)
            )
            if not job or job["status"] != "pending":
                return
            await conn.execute(
                "UPDATE ingestion_jobs SET status='processing', progress=0.1 WHERE id=$1", uuid.UUID(job_id)
            )

        try:
            result = await self._extract(job["source_url"], job["source_type"])
            result = await self._enrich(result)

            encrypted = self._encrypt_fields(result)
            bookmark_id = await self._store(str(job["user_id"]), job["source_url"], result, encrypted)

            # Ingest to Chroma vector store
            await self.rag.ingest_bookmark(bookmark_id, result.content, {
                "url":    job["source_url"],
                "title":  result.title,
                "domain": result.domain,
                "added_at": datetime.utcnow().isoformat(),
            })

            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE ingestion_jobs
                       SET status='completed', progress=1.0,
                           result_bookmark_id=$1, completed_at=NOW()
                       WHERE id=$2""",
                    uuid.UUID(bookmark_id), uuid.UUID(job_id),
                )
            await self.redis.publish(
                f"ingestion:{job['user_id']}",
                json.dumps({"job_id": job_id, "bookmark_id": bookmark_id, "status": "completed"}),
            )

        except Exception as exc:
            logger.error("Ingestion failed for %s: %s", job_id, exc)
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE ingestion_jobs SET status='failed', error_message=$1 WHERE id=$2",
                    str(exc), uuid.UUID(job_id),
                )

    async def _extract(self, url: str, source_type: Optional[str]) -> IngestionResult:
        domain = url.split("/")[2].replace("www.", "") if "//" in url else url
        if source_type == "youtube" or "youtube.com" in url or "youtu.be" in url:
            return await self._extract_youtube(url, domain)
        return await self._extract_webpage(url, domain)

    async def _extract_webpage(self, url: str, domain: str) -> IngestionResult:
        resp = await self.client.get(url)
        resp.raise_for_status()
        html = resp.text

        content = trafilatura.extract(html, include_tables=True, include_formatting=False)
        if not content or len(content) < 200:
            doc     = ReadabilityDoc(html)
            content = BeautifulSoup(doc.summary(), "html.parser").get_text(separator="\n")

        soup  = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title else url

        content = " ".join(content.split())  # normalise whitespace
        return IngestionResult(
            title=title[:500],
            content=content,
            domain=domain,
            word_count=len(content.split()),
        )

    async def _extract_youtube(self, url: str, domain: str) -> IngestionResult:
        video_id = ""
        if "v=" in url:
            video_id = url.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[-1].split("?")[0]
        else:
            video_id = url.split("/")[-1]

        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            content = " ".join(t["text"] for t in transcript)
        except Exception as exc:
            content = f"Could not retrieve transcript for YouTube video {video_id}: {str(exc)}"

        return IngestionResult(
            title=f"YouTube: {video_id}",
            content=content,
            domain=domain,
            word_count=len(content.split()),
        )

    async def _enrich(self, result: IngestionResult) -> IngestionResult:
        """Call local LLM to generate summary, key points, category, tags."""
        prompt = f"""Analyse this web content and respond ONLY with valid JSON, no markdown fences.

Title:   {result.title}
Domain:  {result.domain}
Content: {result.content[:12000]}

Required JSON schema:
{{
  "summary":    "<2–3 sentence summary>",
  "key_points": ["<point>", "..."],
  "category":   "<single label>",
  "tags":       ["<tag>", "..."]
}}"""
        try:
            from langchain_core.messages import HumanMessage
            response = await self.rag.llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content.strip()
            
            # Clean markdown code blocks if Ollama wrapped it
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)
            result.summary = data.get("summary", "")
            result.key_points = data.get("key_points", [])
            result.category = data.get("category", "Uncategorized")
            result.tags = data.get("tags", [])
        except Exception as exc:
            logger.error("Enrichment failed: %s. Using fallback values.", exc)
            result.summary = result.content[:200] + "..." if len(result.content) > 200 else result.content
            result.key_points = ["Failed to extract key points automatically, Sir."]
            result.category = "Uncategorized"
            result.tags = ["auto-imported"]
        return result

    def _encrypt_fields(self, result: IngestionResult) -> dict:
        summary_dek = self.crypto.generate_dek()
        kp_dek = self.crypto.generate_dek()
        cat_dek = self.crypto.generate_dek()

        s_wrapped, s_dek_iv, s_dek_tag = self.crypto.wrap_dek(summary_dek)
        kp_wrapped, kp_dek_iv, kp_dek_tag = self.crypto.wrap_dek(kp_dek)
        cat_wrapped, cat_dek_iv, cat_dek_tag = self.crypto.wrap_dek(cat_dek)

        summary_enc, summary_iv, summary_tag = self.crypto.encrypt_field(result.summary, summary_dek)
        kp_enc, kp_iv, kp_tag = self.crypto.encrypt_field("\n".join(result.key_points), kp_dek)
        cat_enc, cat_iv, cat_tag = self.crypto.encrypt_field(result.category, cat_dek)

        return {
            "summary_encrypted": summary_enc,
            "summary_iv": summary_iv,
            "summary_tag": summary_tag,
            "summary_dek_wrapped": s_wrapped,
            "summary_dek_iv": s_dek_iv,
            "summary_dek_tag": s_dek_tag,

            "key_points_encrypted": kp_enc,
            "key_points_iv": kp_iv,
            "key_points_tag": kp_tag,
            "key_points_dek_wrapped": kp_wrapped,
            "key_points_dek_iv": kp_dek_iv,
            "key_points_dek_tag": kp_dek_tag,

            "category_encrypted": cat_enc,
            "category_iv": cat_iv,
            "category_tag": cat_tag,
            "category_dek_wrapped": cat_wrapped,
            "core_category_dek_iv": cat_dek_iv, # renaming locally
            "category_dek_iv": cat_dek_iv,
            "category_dek_tag": cat_dek_tag,
        }

    async def _store(self, user_id: str, url: str, result: IngestionResult, enc: dict) -> str:
        content_hash = hashlib.sha256(result.content.encode()).hexdigest()
        
        async with self.db_pool.acquire() as conn:
            bookmark_id = await conn.fetchval(
                """INSERT INTO bookmarks (
                    user_id, url, title, description, content_hash,
                    summary_encrypted, summary_iv, summary_tag,
                    summary_dek_wrapped, summary_dek_iv, summary_dek_tag,
                    key_points_encrypted, key_points_iv, key_points_tag,
                    key_points_dek_wrapped, key_points_dek_iv, key_points_dek_tag,
                    category_encrypted, category_iv, category_tag,
                    category_dek_wrapped, category_dek_iv, category_dek_tag,
                    domain, word_count, language, tags, read_status, source
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16, $17,
                    $18, $19, $20, $21, $22, $23,
                    $24, $25, $26, $27, $28, $29
                ) RETURNING id""",
                uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                url,
                result.title,
                result.summary,
                content_hash,
                enc["summary_encrypted"], enc["summary_iv"], enc["summary_tag"],
                enc["summary_dek_wrapped"], enc["summary_dek_iv"], enc["summary_dek_tag"],
                enc["key_points_encrypted"], enc["key_points_iv"], enc["key_points_tag"],
                enc["key_points_dek_wrapped"], enc["key_points_dek_iv"], enc["key_points_dek_tag"],
                enc["category_encrypted"], enc["category_iv"], enc["category_tag"],
                enc["category_dek_wrapped"], enc["category_dek_iv"], enc["category_dek_tag"],
                result.domain,
                result.word_count,
                result.language,
                result.tags,
                "unread",
                "manual"
            )
            return str(bookmark_id)
