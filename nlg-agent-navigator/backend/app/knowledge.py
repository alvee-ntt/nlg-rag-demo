from typing import Any

from app.models import Citation, CitationSpan


def _field(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            found = getter(key)
        except TypeError:
            found = None
        if found is not None:
            return found
    return getattr(value, key, None)


def _annotation_index(annotation: Any, additional: Any) -> int | None:
    index = _field(additional, "annotation_index")
    if isinstance(index, int):
        return index
    index = _field(annotation, "annotation_index")
    return index if isinstance(index, int) else None


def _annotation_spans(annotation: Any) -> list[CitationSpan]:
    spans: list[CitationSpan] = []
    for region in _field(annotation, "annotated_regions") or []:
        start = _field(region, "start_index")
        end = _field(region, "end_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end:
            spans.append(CitationSpan(start_index=start, end_index=end))
    if spans:
        return spans

    raw = _field(annotation, "raw_representation")
    start = _field(raw, "start_index") if raw is not None else _field(annotation, "start_index")
    end = _field(raw, "end_index") if raw is not None else _field(annotation, "end_index")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end:
        return [CitationSpan(start_index=start, end_index=end)]
    return []


def _merge_spans(current: list[CitationSpan], incoming: list[CitationSpan]) -> list[CitationSpan]:
    seen = {(span.start_index, span.end_index) for span in current}
    merged = list(current)
    for span in incoming:
        key = (span.start_index, span.end_index)
        if key in seen:
            continue
        seen.add(key)
        merged.append(span)
    return merged


def extract_citations(response: Any) -> list[Citation]:
    citations: list[Citation] = []
    seen: dict[tuple[str, str | None], int] = {}

    for message in response.messages:
        for content in message.contents:
            for annotation in content.annotations or []:
                if _field(annotation, "type") != "citation":
                    continue
                additional = _field(annotation, "additional_properties") or {}
                uri = _field(annotation, "url") or _field(additional, "get_url")
                title = _field(annotation, "title") or "FlexLife source"
                key = (str(title), str(uri) if uri else None)
                index = _annotation_index(annotation, additional)
                spans = _annotation_spans(annotation)
                existing = seen.get(key)
                if existing is not None:
                    current = citations[existing]
                    indexes = list(current.annotation_indexes)
                    if index is not None and index not in indexes:
                        indexes.append(index)
                    citations[existing] = current.model_copy(
                        update={
                            "annotation_indexes": indexes,
                            "spans": _merge_spans(current.spans, spans),
                        }
                    )
                    continue
                seen[key] = len(citations)
                citations.append(
                    Citation(
                        citation_id=f"C{len(citations) + 1}",
                        source_title=str(title),
                        excerpt=_field(annotation, "text"),
                        uri=str(uri) if uri else None,
                        document_id=_field(additional, "document_id"),
                        annotation_indexes=[index] if index is not None else [],
                        spans=spans,
                    )
                )
    return citations


def merge_citations(*citation_groups: list[Citation]) -> list[Citation]:
    merged: list[Citation] = []
    seen: dict[tuple[str | None, str | None, str], int] = {}
    for citation in (item for group in citation_groups for item in group):
        key = (citation.document_id, citation.uri, citation.source_title)
        existing = seen.get(key)
        if existing is not None:
            current = merged[existing]
            indexes = list(current.annotation_indexes)
            for index in citation.annotation_indexes:
                if index not in indexes:
                    indexes.append(index)
            merged[existing] = current.model_copy(
                update={
                    "annotation_indexes": indexes,
                    "spans": _merge_spans(current.spans, citation.spans),
                }
            )
            continue
        seen[key] = len(merged)
        merged.append(citation.model_copy(update={"citation_id": f"C{len(merged) + 1}"}))
    return merged


