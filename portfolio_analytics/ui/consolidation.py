"""Ingestion review workspace for Phase 2B-1; accepted analytics stay separate."""
from copy import deepcopy
import pandas as pd
import streamlit as st

from portfolio_analytics.input_engine.batch import ingest_files, apply_account_assignments, append_files
from portfolio_analytics.ui.duplicate_review import render_duplicate_review
from portfolio_analytics.input_engine.review import review_view


def render_ingestion(prepare=None, *, use_live_prices=True, history_period="3y", policy=None) -> None:
    st.caption("Upload → review exceptions → confirm → consolidate → analyse. Original records remain intact. The accepted portfolio changes only after financial validation and explicit acceptance.")
    uploaded = st.file_uploader("Upload account files (CSV/XLSX)", type=["csv", "xlsx"], accept_multiple_files=True)
    st.caption("CSV and the first XLSX worksheet use the existing parser. Account IDs come from an explicit account/portfolio column; otherwise assign them below.")
    archived = st.session_state.get("rejected_consolidation_drafts", [])
    if not st.session_state.get("consolidation_batch") and archived and st.button("Restore most recently rejected draft"):
        restored = deepcopy(archived[-1])
        restored.pop("review_confirmation", None)
        restored["duplicate_review"] = "in_review"
        rejection = next(e for e in reversed(restored["review_history"]) if e["event"] == "draft_rejection")
        restored.setdefault("review_history", []).append({"event": "draft_restoration", "reason": "User restored the rejected draft for further review.",
                                                         "sources": rejection["sources"], "record_ids": rejection["record_ids"]})
        st.session_state["consolidation_batch"] = restored
        st.rerun()
    if st.button("Stage account files", type="primary"):
        if not uploaded:
            st.warning("Choose at least one account file.")
        else:
            batch = ingest_files([(f.name, f.getvalue()) for f in uploaded])
            st.session_state["consolidation_batch"] = batch
            st.rerun()
    batch = st.session_state.get("consolidation_batch")
    if not batch:
        return
    st.caption("Stage account files starts a new draft. Add files preserves the current draft and its decision history.")
    if st.button("Add files to staged import"):
        if not uploaded:
            st.warning("Choose account files to add.")
        else:
            st.session_state["consolidation_batch"] = append_files(batch, [(f.name, f.getvalue()) for f in uploaded])
            st.rerun()
    summary = batch["summary"]
    st.markdown("**Staged import review**")
    st.caption(f"{summary['files']} files · {len(summary['accounts'])} identified accounts · {summary['holdings']} holdings · {summary['transactions']} transactions · {summary['cashflows']} cash flows · {summary['unresolved_assignments']} unresolved account assignments")
    st.caption(f"Import session: {batch['session_id']} · {batch['imported_at']}")
    st.info("Nothing is consolidated or accepted until you confirm review, validate financial reconciliation and Trust diagnostics, and explicitly accept. Review decisions are reversible.")
    rows, issues = [], []
    for item in batch["files"]:
        issues.extend({"File": item["filename"], **i} for i in item["issues"])
        if not item["parts"]:
            rows.append({"File": item["filename"], "Source ID": item["source_id"], "Account": "Unresolved", "Format": "Unsupported", "Rows": item["row_count"], "Status": item["status"]})
        for part in item["parts"]:
            rows.append({"File": item["filename"], "Source ID": item["source_id"], "Account": part["account_id"] or "Unresolved", "Format": part["parsed"]["classification"], "Rows": len(part["source_rows"]), "Status": item["status"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if issues:
        st.warning(f"{summary['blocked_files']} files have parsing errors. They remain staged for inspection and cannot become an accepted portfolio.")
        with st.expander("Parsing exceptions and unsupported records"):
            st.dataframe(pd.DataFrame(issues), hide_index=True, use_container_width=True)
            for item in batch["files"]:
                if item["status"] == "blocked" and item["raw_records"]:
                    st.caption(f"Original rows retained for {item['filename']}; unsupported rows are not silently discarded.")
                    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != "_provenance"} for r in item["raw_records"]]), hide_index=True, use_container_width=True)
    with st.expander("Correct account assignments", expanded=summary["unresolved_assignments"] > 0):
        assignments = {}
        for item in batch["files"]:
            for part in item["parts"]:
                assignments[part["part_id"]] = st.text_input(
                    f"Account for {item['filename']} · {part['part_id'].rsplit(':', 1)[-1]}",
                    value=part["account_id"] or "", key=f"staged_account_{part['part_id']}")
        account_reason = st.text_input("Account correction reason (optional)", key="review_account_reason")
        if assignments and st.button("Save account assignments"):
            st.session_state["consolidation_batch"] = apply_account_assignments(batch, assignments, reason=account_reason or "User supplied or corrected the account assignment; no identity was inferred.")
            st.rerun()
    with st.expander("Source evidence and parsed records"):
        for item in batch["files"]:
            st.caption(f"{item['filename']} · {item['source_type']} · SHA-256: {item['fingerprint']}")
            for part in item["parts"]:
                st.caption(f"Account: {part['account_id'] or 'unresolved'} · Original account: {part['original_account_id'] or 'none'}")
                for kind, records in part["parsed"]["normalised_dataset"].items():
                    if records:
                        st.caption(kind.title())
                        st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != "_provenance"} for r in records]), hide_index=True, use_container_width=True)
    render_duplicate_review(batch)
    if prepare is not None:
        from portfolio_analytics.ui.consolidation_acceptance import render_acceptance
        render_acceptance(batch, prepare, use_live_prices=use_live_prices, history_period=history_period, policy=policy)
    if st.button("Reject staged import"):
        rejected = deepcopy(batch)
        rejected.setdefault("review_history", []).append({"event": "draft_rejection", "reason": "User rejected the entire staged draft.",
            "record_ids": [r["record_id"] for r in review_view(batch)["records"]],
            "sources": [{"source_file": f["filename"], "source_id": f["source_id"], "source_fingerprint": f["fingerprint"],
                         "accounts": [p["account_id"] for p in f["parts"]]} for f in batch["files"]]})
        st.session_state.setdefault("rejected_consolidation_drafts", []).append(rejected)
        st.session_state["consolidation_batch"] = None
        st.rerun()
