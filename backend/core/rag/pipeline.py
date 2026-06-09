# core/rag/pipeline.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator, List
import uuid

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.config import settings


@dataclass
class RAGConfig:
    embedding_model:      str   = settings.OLLAMA_EMBED_MODEL
    llm_model:            str   = settings.OLLAMA_LLM_MODEL
    chunk_size:           int   = 800
    chunk_overlap:        int   = 100
    top_k:                int   = 8
    similarity_threshold: float = 0.72
    temperature:          float = 0.3
    ollama_base_url:      str   = settings.OLLAMA_URL


JARVIS_SYSTEM_PROMPT = """You are JARVIS — Anurag's personal AI assistant.
You have full access to his private knowledge base (bookmarks, notes, conversations).
Personality: witty, concise, technically precise, occasionally dry humour.
Address him as "Sir" or "Anurag" naturally. Never break character.

Rules:
- Cite sources with [source:<bookmark_id>]
- Admit uncertainty explicitly: "I don't have enough data on that, Sir."
- Prioritise recent, high-authority sources
- Connect dots across domains when relevant
- Be concise. He's busy."""


class JarvisRAG:
    def __init__(self, config: RAGConfig, crypto, db_pool):
        self.config = config
        self.crypto = crypto
        self.db_pool = db_pool

        self.embeddings = OllamaEmbeddings(
            model=config.embedding_model,
            base_url=config.ollama_base_url,
        )
        self.vector_store = Chroma(
            collection_name="jarvis_knowledge",
            embedding_function=self.embeddings,
            persist_directory="/data/chroma",
        )
        self.llm = ChatOllama(
            model=config.llm_model,
            base_url=config.ollama_base_url,
            temperature=config.temperature,
            num_ctx=16384,
            keep_alive=-1,
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        self._build_chain()

    def _build_chain(self):
        retriever = self.vector_store.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": self.config.top_k,
                "score_threshold": self.config.similarity_threshold,
            },
        )

        def format_docs(docs: list[Document]) -> str:
            return "\n\n".join(
                f"[source:{d.metadata.get('bookmark_id','?')}] {d.page_content}"
                for d in docs
            )

        prompt = ChatPromptTemplate.from_messages([
            ("system", JARVIS_SYSTEM_PROMPT),
            ("human", "Context:\n{context}\n\nQuestion: {question}"),
        ])

        self.rag_chain = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )
        self.rag_chain_stream = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | prompt
            | self.llm
        )

    # ---------- Ingestion ----------

    async def ingest_bookmark(self, bookmark_id: str, content: str, metadata: dict) -> None:
        """Chunk content and add to Chroma + pgvector."""
        docs = self.splitter.create_documents(
            [content],
            metadatas=[{"bookmark_id": bookmark_id, **metadata}],
        )
        await self.vector_store.aadd_documents(docs)
        await self._upsert_pgvector(bookmark_id, docs)

    async def _upsert_pgvector(self, bookmark_id: str, docs: list[Document]) -> None:
        """Embed first chunk and store in bookmarks.embedding column."""
        if not docs:
            return
        embedding = await self.embeddings.aembed_query(docs[0].page_content)
        # Convert list of floats to pgvector string format (e.g. '[0.1, 0.2, ...]')
        embedding_str = f"[{','.join(map(str, embedding))}]"
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE bookmarks SET embedding = $1 WHERE id = $2",
                embedding_str, uuid.UUID(bookmark_id) if isinstance(bookmark_id, str) else bookmark_id,
            )

    # ---------- Query ----------

    def _build_question(self, question: str, history: list[dict]) -> str:
        if not history:
            return question
        recent = history[-6:]
        context = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in recent)
        return f"Conversation so far:\n{context}\n\nCurrent question: {question}"

    async def query(self, question: str, history: list[dict] | None = None) -> str:
        try:
            return await self.rag_chain.ainvoke(
                self._build_question(question, history or [])
            )
        except Exception as e:
            # Fallback if vector store score threshold is too strict and returns empty, leading to no retrieval
            # Let's try direct inference if similarity threshold raises an issue or returns nothing
            try:
                # We can fallback to basic prompt without context
                prompt = ChatPromptTemplate.from_messages([
                    ("system", JARVIS_SYSTEM_PROMPT),
                    ("human", "{question}"),
                ])
                chain = prompt | self.llm | StrOutputParser()
                return await chain.ainvoke({"question": self._build_question(question, history or [])})
            except Exception as inner_e:
                return f"I encountered an error accessing my neural sub-routines, Sir: {str(inner_e)}"

    async def query_stream(
        self, question: str, history: list[dict] | None = None
    ) -> AsyncGenerator[str, None]:
        q = self._build_question(question, history or [])
        try:
            async for chunk in self.rag_chain_stream.astream(q):
                yield chunk.content
        except Exception:
            # Fallback stream if RAG chain fails
            prompt = ChatPromptTemplate.from_messages([
                ("system", JARVIS_SYSTEM_PROMPT),
                ("human", "{question}"),
            ])
            chain = prompt | self.llm
            async for chunk in chain.astream({"question": q}):
                yield chunk.content
