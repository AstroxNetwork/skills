"""Internal runtime package; public interfaces are the CLI and MCP protocols."""
import importlib
from .exports import OWNERS


def owner(name):
    module = OWNERS.get(name)
    if module is None:
        raise AttributeError(name)
    return importlib.import_module('.' + module, __name__)


def __getattr__(name):
    return getattr(owner(name), name)
