"""Ingestion review workspace for Phase 2B-1; accepted analytics stay separate."""
import pandas as pd
import streamlit as st

from portfolio_analytics.input_engine.batch import ingest_files, apply_account_assignments


def render_ingestion() -> None:
    st.caption("2B-1 · Upload → inspect sources → correct accounts. Duplicate review and canonical acceptance will follow in approved milestones. These files do not replace the accepted portfolio.")
    uploaded = st.file_uploader("Upload account files (CSV/XLSX)", type=["csv", "xlsx"], accept_multiple_files=True)
    st.caption("CSV and the first XLSX worksheet use the existing parser. Account IDs come from an explicit account/portfolio column; otherwise assign them below.")
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
    summary = batch["summary"]
    st.markdown("**Staged import review**")
    st.caption(f"{summary['files']} files · {len(summary['accounts'])} identified accounts · {summary['holdings']} holdings · {summary['transactions']} transactions · {summary['cashflows']} cash flows · {summary['unresolved_assignments']} unresolved account assignments")
    st.caption(f"Import session: {batch['session_id']} · {batch['imported_at']}")
    st.info("Duplicate decisions, snapshot/ledger conflicts, financial reconciliation and Trust validation are pending. Nothing is consolidated or accepted in this increment.")
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
        if assignments and st.button("Save account assignments"):
            st.session_state["consolidation_batch"] = apply_account_assignments(batch, assignments)
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
    if st.button("Reject staged import"):
        st.session_state["consolidation_batch"] = None
        st.rerun()
