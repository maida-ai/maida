import re
from pathlib import Path

try:
    from hatchling.metadata.plugin.interface import MetadataHookInterface
except ModuleNotFoundError:  # pragma: no cover - hatchling is build-time only
    MetadataHookInterface = object  # type: ignore[misc, assignment]


_MARKDOWN_PREVIEW_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Allow one level of nested brackets so badge links like
# [![alt](https://img...)](LICENSE) rewrite the outer target.
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)]+)\)")
_HTML_PREVIEW_LINK_RE = re.compile(r'src="([^"]+)"')
_HTML_LINK_RE = re.compile(r'href="([^"]+)"')


def _normalize_base(base: str) -> str:
    return base if base.endswith("/") else base + "/"


def _is_external_or_ignored(url: str) -> bool:
    return url.startswith("#") or url.startswith("//") or (re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) is not None)


def _append_raw_true(url: str) -> str:
    if "#" in url:
        main, frag = url.split("#", 1)
        suffix = f"#{frag}"
    else:
        main, suffix = url, ""

    sep = "&" if "?" in main else "?"
    return f"{main}{sep}raw=True{suffix}"


def relative_preview_links(content: str, base: str) -> str:
    """Replace relative markdown image links with absolute links with `raw=True`."""
    base = _normalize_base(base)

    def repl(m: re.Match[str]) -> str:
        alt, url = m.group(1), m.group(2)
        if _is_external_or_ignored(url):
            return m.group(0)
        return f"![{alt}]({_append_raw_true(base + url)})"

    return _MARKDOWN_PREVIEW_LINK_RE.sub(repl, content)


def relative_non_preview_links(content: str, base: str) -> str:
    """Replace relative markdown links with absolute links."""
    base = _normalize_base(base)

    def repl(m: re.Match[str]) -> str:
        text, url = m.group(1), m.group(2)
        if _is_external_or_ignored(url):
            return m.group(0)
        return f"[{text}]({base}{url})"

    return _MARKDOWN_LINK_RE.sub(repl, content)


def relative_html_preview_links(content: str, base: str) -> str:
    """Replace relative HTML image `src` URLs with absolute links with `raw=True`."""
    base = _normalize_base(base)

    def repl(m: re.Match[str]) -> str:
        url = m.group(1)
        if _is_external_or_ignored(url):
            return m.group(0)
        return f'src="{_append_raw_true(base + url)}"'

    return _HTML_PREVIEW_LINK_RE.sub(repl, content)


def relative_html_links(content: str, base: str) -> str:
    """Replace relative HTML `href` URLs with absolute links."""
    base = _normalize_base(base)

    def repl(m: re.Match[str]) -> str:
        url = m.group(1)
        if _is_external_or_ignored(url):
            return m.group(0)
        return f'href="{base}{url}"'

    return _HTML_LINK_RE.sub(repl, content)


def rewrite_readme_links(content: str, base: str) -> str:
    """Rewrite relative markdown and HTML README links for PyPI."""
    rewritten = relative_non_preview_links(content, base)
    rewritten = relative_preview_links(rewritten, base)
    rewritten = relative_html_links(rewritten, base)
    rewritten = relative_html_preview_links(rewritten, base)
    return rewritten


def readme_base_url(version: str, base_url: str = "https://github.com/maida-ai/maida/blob/") -> str:
    """Return the GitHub blob base URL for a package version."""
    ref = "main" if ".dev" in version else f"v{version}"
    return f"{_normalize_base(base_url)}{ref}/"


class ReadmeLinksRewriteMetadataHook(MetadataHookInterface):
    """Rewrite relative README links into absolute URLs for package metadata."""

    PLUGIN_NAME = "custom"
    BASE_URL = "https://github.com/maida-ai/maida/blob/"
    README_FILE = "README.md"

    def update(self, metadata: dict) -> None:
        version = metadata.get("version")
        if not version:
            raise RuntimeError("Package version must be resolved before rewriting README links.")

        readme_path = Path(self.root) / self.README_FILE
        if not readme_path.is_file():
            raise RuntimeError(f"{self.README_FILE} was not found.")

        original = readme_path.read_text(encoding="utf-8")
        rewritten = rewrite_readme_links(original, readme_base_url(version, self.BASE_URL))
        metadata["readme"] = {
            "content-type": "text/markdown",
            "text": rewritten,
        }
