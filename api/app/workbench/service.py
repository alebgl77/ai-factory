"""Compatibility import for the installable Workbench engine."""

import sys

from ai_factory_workbench import service as _module

sys.modules[__name__] = _module
