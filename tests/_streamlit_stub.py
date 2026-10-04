"""Module « streamlit » simulé pour exécuter l'application de bout en bout sans Streamlit.

Les widgets renvoient des valeurs scriptées (``values`` par libellé ou clé) ;
``clicks`` contient les boutons à « cliquer » (libellé ou clé). Les appels
d'affichage sont enregistrés dans ``calls`` pour les assertions.
"""

from __future__ import annotations

import types
from typing import Any


class StopApp(Exception):
    pass


class RerunApp(Exception):
    pass


class _State(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc

    def __setattr__(self, k, v):
        self[k] = v


def make_stub(values: dict[str, Any] | None = None, clicks: set[str] | None = None,
              state: _State | None = None) -> types.ModuleType:
    values = values or {}
    clicks = clicks or set()
    st = types.ModuleType("streamlit")
    st.calls = []
    st.session_state = state if state is not None else _State()

    def rec(name):
        def f(*a, **k):
            st.calls.append((name, a, k))
        return f

    class Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __getattr__(self, name):
            return getattr(st, name)

    def pick(label, key, default):
        if key is not None and key in values:
            return values[key]
        return values.get(label, default)

    for name in ("set_page_config", "title", "header", "subheader", "caption", "markdown", "write", "info",
                 "success", "warning", "error", "metric", "dataframe", "table", "json", "divider", "code"):
        setattr(st, name, rec(name))

    st.columns = lambda spec, **k: [Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.tabs = lambda labels: [Ctx() for _ in labels]
    st.expander = lambda *a, **k: Ctx()
    st.spinner = lambda *a, **k: Ctx()
    st.sidebar = Ctx()

    def selectbox(label, options, index=0, key=None, format_func=None, help=None, **k):
        options = list(options)
        if format_func:
            for o in options:
                format_func(o)
        return pick(label, key, options[index] if options else None)

    st.selectbox = selectbox
    st.multiselect = lambda label, options, default=None, key=None, **k: pick(label, key, list(default or []))
    st.radio = lambda label, options, key=None, **k: pick(label, key, list(options)[0])
    st.text_input = lambda label, value="", key=None, **k: pick(label, key, value)
    st.text_area = lambda label, value="", key=None, **k: pick(label, key, value)
    st.checkbox = lambda label, value=False, key=None, **k: pick(label, key, value)
    st.file_uploader = lambda label, key=None, **k: pick(label, key, None)
    st.button = lambda label, key=None, **k: (key in clicks) or (label in clicks)
    st.download_button = lambda label, data, **k: (st.calls.append(("download_button", (label,), k)), False)[1]

    def stop():
        raise StopApp()

    def rerun():
        raise RerunApp()

    st.stop, st.rerun = stop, rerun
    return st
