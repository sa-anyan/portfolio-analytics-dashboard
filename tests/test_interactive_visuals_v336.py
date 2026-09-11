from pathlib import Path


def test_plotly_interactive_visuals_are_used():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    req = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "import plotly.express as px" in app
    assert "import plotly.graph_objects as go" in app
    assert "st.plotly_chart" in app
    assert '"scrollZoom": True' in app
    assert 'hole=0.58' in app
    assert 'height=330' in app
    assert "plotly>=5.20" in req


def test_time_charts_have_range_controls_and_locked_ranges():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "_apply_time_range_buttons" in app
    assert 'label="1Y"' in app
    assert 'label="3Y"' in app
    assert 'label="5Y"' in app
    assert 'label="All"' in app
    assert "range=[clean.index.min(), clean.index.max()]" in app
