from __future__ import annotations

import argparse

from .config import load_settings
from .db import citation, connect, search_chunks
from .embeddings import answer_with_context, embed_texts, factcheck_claim, get_openai_client


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the local RAG index")
    parser.add_argument("question", help="A question, or a claim to verify with --fact-check")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--no-answer", action="store_true", help="Only print retrieved chunks")
    parser.add_argument(
        "--fact-check",
        action="store_true",
        help="Verify the input as a claim (SUPPORTED / CONTRADICTED / NOT ADDRESSED) against the documents",
    )
    args = parser.parse_args()

    settings = load_settings()
    client = get_openai_client(settings)
    query_embedding = embed_texts(client, settings, [args.question])[0]

    with connect(settings) as conn:
        contexts = search_chunks(conn, query_embedding, limit=args.limit)

    print("Retrieved chunks:")
    for item in contexts:
        print(f"- {item['similarity']:.3f} {citation(item)}")

    if args.no_answer:
        return

    if args.fact_check:
        print("\nFact check:")
        print(factcheck_claim(client, settings, args.question, contexts))
        return

    print("\nAnswer:")
    print(answer_with_context(client, settings, args.question, contexts))


if __name__ == "__main__":
    main()
