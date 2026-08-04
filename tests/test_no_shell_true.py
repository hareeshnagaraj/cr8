from pathlib import Path
import re


def test_no_shell_true():
    source_root = Path(__file__).parents[1] / "cr8"
    offenders = []
    pattern = re.compile(r"shell\s*=\s*True")
    for path in source_root.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(source_root.parent).as_posix())
    assert offenders == []
