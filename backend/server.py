"""Compatibility entry point for the HTTP API.

Application code lives in :mod:`api.views`. Existing deployments can continue
to run ``python backend/server.py`` and older tests can continue to import the
``server`` module while the project transitions to the ``api`` package.
"""

from __future__ import annotations

import sys

try:
    from .api import views as _views
except ImportError:  # Direct ``python backend/server.py`` execution.
    from api import views as _views


if __name__ == "__main__":
    _views.main()
else:
    # Return the application module itself so test/deployment monkey-patches of
    # settings such as DATA_ROOT still affect the functions that consume them.
    sys.modules[__name__] = _views
