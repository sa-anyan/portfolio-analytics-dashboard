"""Compact reversible review of source conflicts; no financial acceptance."""
import pandas as pd
import streamlit as st

from portfolio_analytics.input_engine.review import review_view, decide, reverse_decision, confirm_review

LABELS = {"exclude_sources": "Exclude selected source files", "exclude_records": "Exclude selected records",
          "keep_both": "Keep all remaining candidates", "choose_snapshot": "Choose one snapshot",
          "choose_format": "Choose holdings or ledger", "defer": "Defer decision"}


def render_duplicate_review(batch: dict) -> None:
    view = review_view(batch)
    sources = {f["source_id"]: f for f in batch["files"]}
    parts = {p["part_id"]: (f, p) for f in batch["files"] for p in f["parts"]}
    records = {r["record_id"]: r for r in view["records"]}
    st.markdown("**Duplicate and conflict review**")
    st.caption(f"{len(view['unresolved_ids'])} unresolved conflicts · {len(view['retained_record_ids'])} retained normalised records · {len(view['excluded_record_ids'])} explicitly excluded normalised records · {len(view['excluded_source_ids'])} excluded source files")
    if view["invalidated_decisions"]:
        st.warning("Account/source corrections invalidated earlier decisions. Reverse those decisions and review the new evidence before confirming.")
    if not view["retained_record_ids"]:
        st.warning("No records are retained. Add valid sources or reject this draft.")
    blocking = [i for i in view["issues"] if i["blocking"]]
    if blocking:
        options = [i["id"] for i in blocking]
        lookup = {i["id"]: i for i in blocking}
        identifier = st.selectbox("Exception to review", options, format_func=lambda i: lookup[i]["title"], key="duplicate_review_issue")
        issue = lookup[identifier]
        st.caption(f"{issue['classification'].replace('_', ' ').title()} · Accounts: {', '.join(issue['accounts']) or 'unresolved'}")
        st.write(issue["reason"])
        if issue["recommendation"]:
            st.caption("Suggested review · " + issue["recommendation"])
        with st.expander("Affected sources and record evidence"):
            st.dataframe(pd.DataFrame([{"Source": sources[s]["filename"], "Source ID": s,
                "Fingerprint": sources[s]["fingerprint"]} for s in issue["source_ids"]]), hide_index=True, use_container_width=True)
            st.write(issue["evidence"])
            rows = [{"Record ID": r, "Account": records[r]["account_id"], "File": records[r]["source_file"],
                     "Kind": records[r]["kind"], **records[r]["data"]} for r in issue["record_ids"]]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        action = st.selectbox("Review decision", issue["actions"], format_func=lambda a: LABELS[a], key=f"review_action_{identifier}")
        targets = []
        if action == "exclude_sources":
            targets = st.multiselect("Source files to exclude", issue["source_ids"],
                format_func=lambda s: f"{sources[s]['filename']} · {s}", key=f"review_sources_{identifier}")
        elif action == "exclude_records":
            targets = st.multiselect("Record IDs to exclude", issue["record_ids"],
                format_func=lambda r: f"{records[r]['source_file']} · {records[r]['data'].get('Transaction ID') or 'no transaction ID'} · {r}", key=f"review_records_{identifier}")
        elif action == "choose_snapshot":
            targets = [st.selectbox("Snapshot to retain", issue["part_ids"],
                format_func=lambda p: f"{parts[p][0]['filename']} · {parts[p][1]['account_id']} · {issue['evidence']['snapshots'][p]}", key=f"review_snapshot_{identifier}")]
        elif action == "choose_format":
            targets = [st.selectbox("Authoritative account input", ["holdings", "ledger"], key=f"review_format_{identifier}")]
        reason = st.text_input("Reason for decision", key=f"review_reason_{identifier}")
        if st.button("Save review decision"):
            try:
                st.session_state["consolidation_batch"] = decide(batch, identifier, action, reason=reason, targets=targets)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    legitimate = [i for i in view["issues"] if not i["blocking"]]
    if legitimate:
        with st.expander("Legitimate repeated activity and source information"):
            st.dataframe(pd.DataFrame([{"Finding": i["title"], "Accounts": ", ".join(i["accounts"]), "Reason": i["reason"],
                                      "Sources": ", ".join(sources[s]["filename"] for s in i["source_ids"])} for i in legitimate]), hide_index=True, use_container_width=True)
    decisions = batch.get("review_decisions", {})
    if decisions:
        with st.expander("Decision history and reversals"):
            st.dataframe(pd.DataFrame([{"Decision ID": i, "Decision": d["action"], "Reason": d["reason"],
                "Status": "invalidated" if i in view["invalidated_decisions"] else "active", "Sources": ", ".join(d["issue"]["source_ids"]),
                "Record IDs": ", ".join(d["issue"]["record_ids"])} for i, d in decisions.items()]), hide_index=True, use_container_width=True)
            identifier = st.selectbox("Decision to reverse", list(decisions), format_func=lambda i: decisions[i]["issue"]["title"], key="review_reverse_id")
            reason = st.text_input("Reason for reversal", key="review_reverse_reason")
            if st.button("Reverse review decision"):
                try:
                    st.session_state["consolidation_batch"] = reverse_decision(batch, identifier, reason=reason)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if batch.get("review_history"):
        with st.expander("Full review audit evidence"):
            st.json(batch["review_history"])
    if view["review_confirmed"]:
        st.success("Review decisions confirmed. Financial consolidation and Trust validation await Milestone 2B-3.")
    elif view["can_progress"]:
        st.caption("No unresolved material import conflicts. Confirm the review; financial acceptance remains unavailable.")
    else:
        st.warning("Progression to consolidation is blocked until identity conflicts are resolved and at least one valid record is retained.")
    if st.button("Confirm review decisions", disabled=not view["can_progress"]):
        st.session_state["consolidation_batch"] = confirm_review(batch)
        st.rerun()
