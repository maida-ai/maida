"""CLI command implementation: version."""

from typing import Annotated

import typer

from maida import __version__


def _version_callback(value: bool) -> None:
    if value:
        print(f"Maida {__version__}")
        raise typer.Exit()


def version_callback(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
            show_default=False,
        ),
    ] = None,
):
    """Show Maida version."""
