"""Packaging checks that stay valid across version bumps and README edits."""

import importlib.util
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_HOOK_PATH = ROOT / "scripts" / "hatch_hooks" / "readme_links_rewrite.py"
_SPEC = importlib.util.spec_from_file_location("readme_links_rewrite", _HOOK_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_HOOK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HOOK)


def _relative_readme_urls(readme: str) -> list[str]:
    urls: list[str] = []
    for match in _HOOK._MARKDOWN_PREVIEW_LINK_RE.finditer(readme):
        urls.append(match.group(2))
    for match in _HOOK._MARKDOWN_LINK_RE.finditer(readme):
        urls.append(match.group(2))
    for match in _HOOK._HTML_PREVIEW_LINK_RE.finditer(readme):
        urls.append(match.group(1))
    for match in _HOOK._HTML_LINK_RE.finditer(readme):
        urls.append(match.group(1))
    return [url for url in urls if not _HOOK._is_external_or_ignored(url)]


def test_built_wheel_contains_importable_package_and_cli(tmp_path):
    out_dir = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        check=False,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    wheels = sorted(out_dir.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        entry_points_path = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        entry_points = wheel.read(entry_points_path).decode()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(metadata_path).decode()

    assert "maida/__init__.py" in names
    assert "maida/cli.py" in names
    assert "maida/drift.py" in names
    assert "maida/extract.py" in names
    assert "maida/server.py" in names
    assert "maida/ui_static/index.html" in names
    assert "maida = maida.cli:main" in entry_points

    # PyPI cannot resolve relative README links; METADATA must carry the rewrite.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    version_match = re.search(r"^Version:\s*(\S+)", metadata, flags=re.MULTILINE)
    assert version_match is not None
    expected = _HOOK.rewrite_readme_links(readme, _HOOK.readme_base_url(version_match.group(1)))
    assert expected in metadata

    relative_urls = _relative_readme_urls(readme)
    assert relative_urls, "README is expected to keep some repo-relative links"
    for url in relative_urls:
        assert f"]({url})" not in metadata
        assert f'src="{url}"' not in metadata
        assert f'href="{url}"' not in metadata
