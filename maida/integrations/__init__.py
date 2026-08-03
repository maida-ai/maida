"""
Optional framework integrations. No heavy imports at package load.
"""

import importlib

__lazy_imports__ = {
    "crewai": ("maida.integrations.crewai", None),
    "langchain": ("maida.integrations.langchain", None),
    "langfuse": ("maida.integrations.langfuse", None),
    "openai_agents": ("maida.integrations.openai_agents", None),
    "LangChainCallbackHandler": (
        "maida.integrations.langchain",
        "LangChainCallbackHandler",
    ),
}

__all__ = [
    *__lazy_imports__.keys(),
]


def __getattr__(name: str):
    """Lazy load optional integrations so heavy deps are not required at import time."""
    if name not in __lazy_imports__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = __lazy_imports__[name]
    module = importlib.import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)

    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
