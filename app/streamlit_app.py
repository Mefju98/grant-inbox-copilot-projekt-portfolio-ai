"""Optional Streamlit presentation layer for the synthetic triage API."""

from __future__ import annotations

import os
import sqlite3
from uuid import uuid4

import httpx
import streamlit as st
from pydantic import ValidationError

from app.review_queue import ReviewQueue
from app.schemas import TriageAPIResponse


DEFAULT_API_URL = os.getenv("TRIAGE_API_URL", "http://127.0.0.1:8000/triage")
API_TIMEOUT_SECONDS = 120.0
EXAMPLES = {
    "No knowledge match (human review)": "Can you fund a quantum submarine weather forecast?",
    "Evidence match (fictional prototype)": (
        "We are a small company in North and need 50000 PLN for an R&D prototype."
    ),
}


def main() -> None:
    st.set_page_config(page_title="Grant Inbox Copilot", page_icon="📨", layout="wide")
    st.title("Grant Inbox Copilot")
    st.caption(
        "Portfolio demo using fictional inquiries and fictional program rules. "
        "This is preliminary triage, not a funding decision."
    )

    api_url = st.text_input("Triage API URL", value=DEFAULT_API_URL)
    selected_example = st.selectbox("Synthetic example", options=list(EXAMPLES))
    message = st.text_area(
        "Inquiry message",
        value=EXAMPLES[selected_example],
        key=f"message_{selected_example}",
        height=120,
    )
    st.caption("The message is sent to the configured API and held in this active session; use synthetic data only.")
    synthetic_confirmed = st.checkbox(
        "I confirm this is synthetic demo data and may be stored locally if it needs review."
    )

    if st.button(
        "Run triage",
        type="primary",
        disabled=not message.strip() or not api_url.strip() or not synthetic_confirmed,
    ):
        with st.spinner("Submitting synthetic inquiry to the local API…"):
            try:
                response = httpx.post(
                    api_url.strip(),
                    json={"inquiry_id": f"streamlit-demo-{uuid4().hex[:12]}", "message": message},
                    timeout=API_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                st.error(f"Could not reach the triage API ({type(exc).__name__}). Check the API URL and server.")
                return

        if response.is_error:
            _render_api_error(response)
            return

        try:
            payload = TriageAPIResponse.model_validate(response.json())
        except (ValueError, ValidationError):
            st.error(f"The API returned an invalid response (HTTP {response.status_code}).")
            return

        if payload.result.escalate:
            try:
                ReviewQueue().enqueue(
                    inquiry_id=payload.result.inquiry_id,
                    message=message,
                    eligibility=payload.result.eligibility.value,
                    rationale=payload.result.rationale,
                    escalation_reasons=[reason.value for reason in payload.result.escalation_reasons],
                    citations=[
                        {
                            "document_id": citation.document_id,
                            "title": citation.title,
                            "excerpt": citation.excerpt,
                            "source_version": citation.source_version,
                        }
                        for citation in payload.result.citations
                    ],
                )
            except (OSError, ValueError, sqlite3.Error):
                st.warning("The result requires review, but it could not be added to the local review queue.")

        _render_result(payload)

    _render_review_queue()


def _render_api_error(response: httpx.Response) -> None:
    try:
        body = response.json()
    except ValueError:
        body = {}

    code = body.get("code") if isinstance(body, dict) else None
    suffix = f" ({code})" if isinstance(code, str) else ""
    st.error(f"Triage API returned HTTP {response.status_code}{suffix}.")

    diagnostics = {
        key: body[key]
        for key in ("request_id", "attempts", "failure_kind", "attempt_summaries")
        if isinstance(body, dict) and key in body
    }
    if diagnostics:
        with st.expander("Provider diagnostics"):
            st.json(diagnostics)


def _render_result(payload: TriageAPIResponse) -> None:
    result = payload.result
    llm = payload.llm

    st.subheader("Triage result")
    eligibility_col, escalation_col = st.columns(2)
    eligibility_col.metric("Eligibility", result.eligibility.value)
    escalation_col.metric("Human review", "Required" if result.escalate else "Not required")

    if result.escalate:
        st.warning("Route this inquiry to a human reviewer before taking action.")
    else:
        st.success("The deterministic policy checks passed. This is still not a funding decision.")

    if result.recommendation:
        st.markdown(f"**Next step:** {result.recommendation}")
    st.markdown(f"**Rationale:** {result.rationale}")

    if result.escalation_reasons:
        st.markdown("**Escalation reasons:** " + ", ".join(reason.value for reason in result.escalation_reasons))

    with st.expander("Extracted facts"):
        st.json(result.extracted_facts.model_dump(mode="json"))

    st.markdown("**Policy evidence**")
    if result.citations:
        for citation in result.citations:
            with st.expander(f"{citation.title} · {citation.document_id}"):
                st.write(citation.excerpt)
                st.caption(f"Source version: {citation.source_version}")
    else:
        st.caption("No policy citations were returned.")

    st.caption(
        f"Model: {llm.model or 'not called'} · Prompt: {llm.prompt_version or 'n/a'} · "
        f"Attempts: {llm.attempts} · Latency: {llm.latency_ms} ms · "
        f"Cost: {llm.cost_usd if llm.cost_usd is not None else 'unknown'}"
    )


def _render_review_queue() -> None:
    st.divider()
    st.subheader("Local human review queue")
    st.caption("Demo-only SQLite queue. It is local and has no access controls; never enter real applicant data.")
    try:
        queue = ReviewQueue()
        pending = queue.list_items(status="pending")
        reviewed = queue.list_items(status="reviewed")
    except (OSError, ValueError, sqlite3.Error):
        st.error("The local review queue could not be opened.")
        return

    pending_tab, reviewed_tab = st.tabs([f"Pending ({len(pending)})", f"Reviewed ({len(reviewed)})"])
    with pending_tab:
        if not pending:
            st.caption("No inquiries are waiting for review.")
        for item in pending:
            with st.container(border=True):
                st.markdown(f"**{item['inquiry_id']}** · `{item['eligibility']}`")
                st.text(item["message"])
                st.text(f"Reason: {', '.join(item['escalation_reasons']) or 'human review requested'}")
                st.text(item["rationale"])
                if item["citations"]:
                    with st.expander("Policy evidence"):
                        for citation in item["citations"]:
                            st.markdown(f"**{citation['title']}** · `{citation['document_id']}`")
                            st.text(citation["excerpt"])
                if st.button("Mark reviewed", key=f"review_{item['inquiry_id']}"):
                    queue.mark_reviewed(item["inquiry_id"])
                    st.rerun()

    with reviewed_tab:
        if not reviewed:
            st.caption("No inquiries have been marked reviewed.")
        for item in reviewed:
            with st.expander(f"{item['inquiry_id']} · reviewed {item['reviewed_at']}"):
                st.text(item["message"])
                st.text(f"Reason: {', '.join(item['escalation_reasons']) or 'human review requested'}")
                st.text(item["rationale"])


if __name__ == "__main__":
    main()
