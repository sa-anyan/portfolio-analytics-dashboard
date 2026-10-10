"""Never render an empty chart shell; zero is valid evidence."""
import math
import streamlit as st


def has_chart_evidence(figure):
    for trace in figure.data:
        # Pie, XY, heatmap and treemap trace evidence.
        fields = ('x', 'y', 'values', 'z') if getattr(trace, 'orientation', None) == 'h' else ('y', 'values', 'z')
        for field in fields:
            values = getattr(trace, field, None)
            if values is None:
                continue
            def valid(items):
                for item in items:
                    if isinstance(item, (list, tuple)) or hasattr(item, '__iter__') and not isinstance(item, str):
                        if valid(item):
                            return True
                    else:
                        try:
                            if math.isfinite(float(item)):
                                return True
                        except (TypeError, ValueError):
                            pass
                return False
            if valid(values):
                return True
    return False


def render_chart(figure, **kwargs):
    if has_chart_evidence(figure):
        return st.plotly_chart(figure, **kwargs)
    title = str(figure.layout.title.text or 'Analysis').replace('<b>', '').replace('</b>', '')
    st.caption(f'{title} unavailable · add sufficient dated prices or transactions to investigate.')
    return None
