"""Second Thought: read the conversation, work out what you mean, offer it.

The brain behind an Android overlay that reads the messaging app you are looking
at, works out what the person is trying to do, offers replies in their own
voice, and quietens the drafts they would regret.
"""

__version__ = "0.1.0"

from .config import Config
from .guard import review
from .reading import read_conversation
from .transcript import ScreenRead, parse, render

__all__ = ["Config", "ScreenRead", "parse", "render", "review", "read_conversation", "__version__"]
