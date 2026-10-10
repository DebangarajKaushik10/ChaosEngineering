"""Live dashboard:  streamlit run dashboard/app.py

Read-only view of results/ (refreshes on demand) plus an emergency STOP button that calls the same
`undo-all` as the CLI. The static results/report.html needs none of this and always works.
"""
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))
import data  # noqa: E402

RESULTS = Path(os.getenv("RESULTS_DIR", ROOT / "results"))
st.set_page_config(page_title="ResiliAI", layout="wide")
st.title("ResiliAI: AI-driven chaos engineering for Hotel Reservation")
res = data.load_results(RESULTS)

if data.is_simulated(res):
    st.warning("SIMULATED data: some experiments ran on the built-in simulator, not the real app.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Experiments", len(res["experiments"]))
c2.metric("Kill switch fired", sum(1 for e in res["experiments"] if e.get("aborted")))
c3.metric("Active faults now", len(res["active"]))
if res["baseline"]:
    o = res["baseline"].get("overall", {})
    c4.metric("Baseline p95 (ms)", round(o.get("p95_ms", 0), 1))

with st.sidebar:
    st.header("Emergency")
    if st.button("STOP EVERYTHING (undo-all)", type="primary"):
        r = subprocess.run([sys.executable, str(ROOT / "run.py"), "undo-all"], capture_output=True, text=True,
                           cwd=ROOT)
        st.code(r.stdout + r.stderr)
    if st.button("Refresh"):
        st.rerun()

st.subheader("Experiments")
rows = [{"id": e.get("id"), "tool": e["plan"]["tool"], "target": e["plan"]["target"],
         "kill switch": "FIRED" if e.get("aborted") else "no", "recovery s": e.get("recovery_time_s"),
         "impact": "; ".join(e.get("impact_summary", [])[:2])} for e in res["experiments"]]
st.dataframe(rows, use_container_width=True)

if res["experiments"]:
    pick = st.selectbox("Timeline of", [e["id"] for e in res["experiments"]])
    rec = next(e for e in res["experiments"] if e["id"] == pick)
    tl = data.timeline_rows(rec)
    if tl:
        import pandas as pd
        import plotly.express as px
        df = pd.DataFrame(tl, columns=["t", "error_rate", "p95_ms"])
        a, b = st.columns(2)
        a.plotly_chart(px.line(df, x="t", y="error_rate", title="Error rate"), use_container_width=True)
        b.plotly_chart(px.line(df, x="t", y="p95_ms", title="p95 latency (ms)"), use_container_width=True)
    pred = rec.get("prediction_score")
    if pred:
        st.json(pred)

st.subheader("Safety log")
st.dataframe(list(reversed(res["events"]))[:200], use_container_width=True)
