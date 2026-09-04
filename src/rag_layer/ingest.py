from __future__ import annotations

import argparse
import hashlib
from itertools import islice
from pathlib import Path
from typing import Iterable

from .blob_store import BlobInfo, get_blob_store
from .chunking import chunk_sections
from .config import load_settings
from .extractors import extract_document, is_supported


def batched(values: list, size: int) -> Iterable[list]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Azure Blob documents into Postgres/pgvector")
    parser.add_argument("--prefix", action="append", help="Blob prefix to ingest. Can be repeated.")
    parser.add_argument(
        "--blob-name",
        action="append",
        help="Ingest one exact blob path. Can be repeated. Bypasses prefix listing.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Skip blobs whose path contains this substring. Can be repeated.",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of blobs to inspect")
    parser.add_argument("--dry-run", action="store_true", help="List candidate blobs without downloading or embedding")
    parser.add_argument("--force-reindex", action="store_true", help="Re-embed blobs even if the content hash is unchanged")
    args = parser.parse_args()

    settings = load_settings()
    prefixes = [p.strip("/") for p in args.prefix] if args.prefix else settings.azure_blob_prefixes

    blob_store = get_blob_store(settings)
    if args.blob_name:
        # Exact paths, so skip listing entirely -- --prefix appends a trailing slash
        # and can only ever match a folder.
        blobs = iter([BlobInfo(name=name) for name in args.blob_name])
    else:
        blobs = blob_store.iter_blobs(prefixes)
    if args.exclude:
        patterns = [p.lower() for p in args.exclude]
        blobs = (b for b in blobs if not any(p in b.name.lower() for p in patterns))
    if args.limit:
        blobs = islice(blobs, args.limit)

    if args.dry_run:
        for blob in blobs:
            marker = "ok" if is_supported(blob.name) else "skip"
            print(f"{marker}\t{blob.name}")
        return

    from .db import connect, get_document_by_blob, init_db, replace_chunks, upsert_document
    from .embeddings import embed_texts, get_openai_client

    init_db(settings)
    client = get_openai_client(settings)
    force_reindex = args.force_reindex or settings.force_reindex

    inspected = embedded = skipped = failed = 0
    with connect(settings) as conn:
        for blob in blobs:
            blob_name = blob.name
            inspected += 1
            if not is_supported(blob_name):
                skipped += 1
                print(f"skip unsupported\t{blob_name}")
                continue

            try:
                content = blob_store.download_blob(blob_name)
                content_hash = hashlib.sha256(content).hexdigest()

                existing = get_document_by_blob(conn, blob_name)
                if existing and existing["content_hash"] == content_hash and not force_reindex:
                    skipped += 1
                    print(f"skip unchanged\t{blob_name}")
                    continue

                sections = extract_document(blob_name, content)
                chunks = chunk_sections(sections, settings.chunk_size, settings.chunk_overlap)
                if not chunks:
                    skipped += 1
                    print(f"skip empty\t{blob_name}")
                    continue

                embeddings: list[list[float]] = []
                for chunk_batch in batched(chunks, settings.batch_size):
                    texts = [chunk.content for chunk in chunk_batch]
                    embeddings.extend(embed_texts(client, settings, texts))

                document_id = upsert_document(
                    conn,
                    blob_name=blob_name,
                    content_hash=content_hash,
                    etag=blob.etag,
                    last_modified=blob.last_modified,
                    # --blob-name bypasses listing, so size is not known up front.
                    size_bytes=blob.size if blob.size is not None else len(content),
                    metadata={
                        "source": "azure_blob",
                        "container": settings.azure_storage_container,
                        "extension": Path(blob_name).suffix.lower(),
                    },
                )
                replace_chunks(conn, document_id, chunks, embeddings)
                conn.commit()
                embedded += 1
                print(f"embedded {len(chunks)} chunks\t{blob_name}")
            except Exception as exc:
                conn.rollback()
                failed += 1
                print(f"failed\t{blob_name}\t{type(exc).__name__}: {exc}")

    print(f"Done. inspected={inspected} embedded={embedded} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()


