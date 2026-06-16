"""Tests integrations imports."""

import sys
import importlib
import pytest

import maida.integrations as integrations
from maida.integrations._error import MissingOptionalDependencyError


def has_module(name):
    return importlib.util.find_spec(name) is not None


INTEGRATIONS = [  # name, dependency
    ("crewai", "crewai"),
    ("langchain", "langchain_core"),
    ("openai_agents", "agents"),
]


@pytest.fixture
def reload_integrations():
    global integrations

    integrations = importlib.import_module("maida.integrations")
    sys.modules.pop("maida.integrations.crewai", None)
    sys.modules.pop("maida.integrations.langchain", None)
    sys.modules.pop("maida.integrations.openai_agents", None)

    integrations.__dict__.pop("crewai", None)
    integrations.__dict__.pop("langchain", None)
    integrations.__dict__.pop("openai_agents", None)
    integrations.__dict__.pop("LangChainCallbackHandler", None)

    integrations = importlib.reload(integrations)


def test_no_eager_imports(reload_integrations):
    assert "maida.integrations.crewai" not in sys.modules
    assert "maida.integrations.langchain" not in sys.modules
    assert "LangChainCallbackHandler" not in sys.modules


@pytest.mark.parametrize("name, dependency", INTEGRATIONS)
def test_lazy_module_imports(reload_integrations, name, dependency):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"{dependency} not installed")

    mod = getattr(integrations, name)

    assert f"maida.integrations.{name}" in sys.modules
    assert mod is sys.modules[f"maida.integrations.{name}"]


@pytest.mark.skipif(
    importlib.util.find_spec("langchain_core") is None, reason="langchain not installed"
)
def test_lazy_attribute_imports(reload_integrations):
    cls = integrations.LangChainCallbackHandler
    assert "maida.integrations.langchain" in sys.modules
    assert cls.__name__ == "LangChainCallbackHandler"
    assert cls is sys.modules["maida.integrations.langchain"].LangChainCallbackHandler


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError):
        integrations.this_does_not_exist


def test_dir_includes_all_attributes():
    dir_ = dir(integrations)
    for name in integrations.__all__:
        assert name in dir_


def test_missing_dependency_raises(monkeypatch):
    integrations.__dict__.pop("LangChainCallbackHandler", None)
    integrations.__dict__.pop("langchain", None)
    sys.modules.pop("maida.integrations.langchain", None)

    real_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "maida.integrations.langchain":
            raise MissingOptionalDependencyError("langchain_core not installed")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with pytest.raises(
        MissingOptionalDependencyError, match="langchain_core not installed"
    ):
        _ = integrations.LangChainCallbackHandler
