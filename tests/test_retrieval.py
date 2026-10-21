from pathlib import Path

from app.evaluation import load_cases
from app.retrieval import KnowledgeRetriever


ROOT = Path(__file__).parents[1]


def test_bm25_finds_expected_program_criteria_and_returns_source_records():
    retriever = KnowledgeRetriever.from_default_corpus()
    matches = retriever.search("Small company in North requests 50000 PLN for an innovation prototype")
    ids = {match.document.document_id for match in matches}
    assert "eligible-regions-v1" in ids
    assert "eligible-company-sizes-v1" in ids
    assert "funding-range-v1" in ids
    assert "eligible-project-types-v1" in ids
    assert matches[0].score > 0


def test_retrieves_policy_sources_for_vague_location_and_rd_scope_wording():
    retriever = KnowledgeRetriever.from_default_corpus()
    matches = retriever.search(
        "We are somewhere in the northern area, a small business, and might need about 50000 PLN "
        "for a new product. Exact location and R&D scope are unclear."
    )
    ids = {match.document.document_id for match in matches}
    assert "eligible-regions-v1" in ids
    assert "eligible-project-types-v1" in ids
    assert "required-information-v1" in ids


def test_unrelated_query_returns_no_knowledge_match():
    retriever = KnowledgeRetriever.from_default_corpus()
    assert retriever.search("quantum submarine weather forecast") == []
    assert retriever.search("company logo") == []
    assert retriever.search("I need grant information") == []


def test_gold_citation_ids_are_backed_by_synthetic_documents():
    retriever = KnowledgeRetriever.from_default_corpus()
    document_ids = {document.document_id for document in retriever.documents}
    gold_citation_ids = {
        citation_id
        for case in load_cases(ROOT / "data" / "evaluation_cases.jsonl")
        for citation_id in case.expected_citation_ids
    }
    assert gold_citation_ids <= document_ids
