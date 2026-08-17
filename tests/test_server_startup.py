from __future__ import annotations

import importlib
import warnings

import dsh_bing_search.server as server


def test_fastmcp_lifespan_warning_is_filtered() -> None:
    """Re-importing server.py must register a filter that silences fastmcp's
    `lifespan` IncompleteFieldDefinitionWarning, which would otherwise leak
    into the DSH MCP server's stderr on every launch.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(server)
        warnings.warn(
            "Field 'lifespan' has an incomplete definition: regression probe",
            UserWarning,
        )
    assert not caught, [str(w.message) for w in caught]
