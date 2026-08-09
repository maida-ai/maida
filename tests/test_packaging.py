import subprocess
import zipfile
from pathlib import Path


def test_built_wheel_contains_importable_package_and_cli(tmp_path):
    out_dir = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    wheels = sorted(out_dir.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        entry_points_path = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = wheel.read(entry_points_path).decode()

    assert "maida/__init__.py" in names
    assert "maida/cli.py" in names
    assert "maida/drift.py" in names
    assert "maida/extract.py" in names
    assert "maida/server.py" in names
    assert "maida/ui_static/index.html" in names
    assert "maida = maida.cli:main" in entry_points
