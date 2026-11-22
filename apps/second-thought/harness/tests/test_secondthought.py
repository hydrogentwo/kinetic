"""pytest wrapper over the harness self-checks.

    pip install pytest && python -m pytest tests -q

The checks themselves live in `secondthought/checks.py` so they also run from
`python -m secondthought check`, with no pytest and no dependencies installed.
"""

import pytest

from secondthought.checks import CHECKS


@pytest.mark.parametrize("fn", CHECKS, ids=[f.__name__ for f in CHECKS])
def test_check(fn):
    detail = fn()
    assert detail is None or isinstance(detail, str)
