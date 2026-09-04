from __future__ import annotations

import argparse
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings, load_settings

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id BIGSERIAL PRIMARY KEY,
    blob_name TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    etag TEXT,
    last_modified TIMESTAMPTZ,
    size_bytes BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector(__EMBEDDING_DIMENSIONS__) NOT NULL,
    token_count INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw_idx
ON rag_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS rag_documents_blob_name_idx ON rag_documents(blob_name);
CREATE INDEX IF NOT EXISTS rag_chunks_document_id_idx ON rag_chunks(document_id);
"""


def connect(settings: Settings):
    return psycopg.connect(settings.postgres_dsn, row_factory=dict_row)


def init_db(settings: Settings) -> None:
    with connect(settings) as conn:
        conn.execute(SCHEMA_SQL.replace("__EMBEDDING_DIMENSIONS__", str(settings.embedding_dimensions)))
        conn.commit()


def count_documents(settings: Settings) -> int:
    """Number of ingested documents. Returns 0 if the schema is not present yet."""
    try:
        with connect(settings) as conn:
            row = conn.execute("SELECT count(*) AS n FROM rag_documents").fetchone()
            return int(row["n"]) if row else 0
    except psycopg.errors.UndefinedTable:
        return 0


def get_document_by_blob(conn, blob_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM rag_documents WHERE blob_name = %s",
        (blob_name,),
    ).fetchone()
    return dict(row) if row else None


def upsert_document(
    conn,
    *,
    blob_name: str,
    content_hash: str,
    etag: str | None,
    last_modified,
    size_bytes: int | None,
    metadata: dict[str, Any],
) -> int:
    row = conn.execute(
        """
        INSERT INTO rag_documents (blob_name, content_hash, etag, last_modified, size_bytes, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (blob_name) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            etag = EXCLUDED.etag,
            last_modified = EXCLUDED.last_modified,
            size_bytes = EXCLUDED.size_bytes,
            metadata = EXCLUDED.metadata,
            updated_at = now()
        RETURNING id
        """,
        (blob_name, content_hash, etag, last_modified, size_bytes, Jsonb(metadata)),
    ).fetchone()
    return int(row["id"])


def replace_chunks(conn, document_id: int, chunks: list, embeddings: list[list[float]]) -> None:
    """Replace a document's chunks. Accepts ``Chunk`` objects or bare strings."""
    conn.execute("DELETE FROM rag_chunks WHERE document_id = %s", (document_id,))
    rows = []
    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        content = chunk if isinstance(chunk, str) else chunk.content
        metadata = {} if isinstance(chunk, str) else dict(chunk.metadata)
        rows.append(
            (
                document_id,
                index,
                content,
                _vector_literal(embedding),
                len(content.split()),
                Jsonb(metadata),
            )
        )
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rag_chunks (document_id, chunk_index, content, embedding, token_count, metadata)
            VALUES (%s, %s, %s, %s::vector, %s, %s)
            """,
            rows,
        )


def search_chunks(conn, query_embedding: list[float], limit: int = 8) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT
                c.content,
                c.chunk_index,
                c.metadata,
                d.blob_name,
                1 - (c.embedding <=> %s::vector) AS similarity
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (_vector_literal(query_embedding), _vector_literal(query_embedding), limit),
        ).fetchall()
    )


def citation(row: dict[str, Any]) -> str:
    """Human-readable source locator: page and heading when the chunk carries them."""
    metadata = row.get("metadata") or {}
    parts = [row["blob_name"]]
    page = metadata.get("page")
    if page:
        parts.append(f"p.{page}")
    heading = metadata.get("heading_path")
    if heading:
        parts.append(heading)
    if len(parts) == 1:
        parts.append(f"chunk-{row['chunk_index']}")
    return " | ".join(parts)


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "count"])
    args = parser.parse_args()
    settings = load_settings()
    if args.command == "init":
        init_db(settings)
        print("Database initialized")
    elif args.command == "count":
        # Prints just the integer so launchers can parse it directly.
        print(count_documents(settings))


if __name__ == "__main__":
    main()

