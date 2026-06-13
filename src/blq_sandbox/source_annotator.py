"""Source context annotator plugin.

Enriches error events with enclosing function/class definitions by reading
source files and scanning backwards from the error line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from blq.ext.annotator import Annotation, RunContext

# ---------------------------------------------------------------------------
# Definition scanning
# ---------------------------------------------------------------------------


@dataclass
class Definition:
    """An enclosing function or class definition."""

    kind: str  # "function", "class", "method"
    name: str  # e.g. "authenticate"
    signature: str  # e.g. "def authenticate(user, password):"
    line: int  # 1-based line number of the definition


# Python patterns
_PY_DEF = re.compile(r"^(\s*)(async\s+)?def\s+(\w+)")
_PY_CLASS = re.compile(r"^(\s*)class\s+(\w+)")

# C-family / Rust / Go patterns: return_type name(
_C_FUNC = re.compile(
    r"^\s*(?:(?:pub(?:lic)?|priv(?:ate)?|prot(?:ected)?|static|virtual|"
    r"override|async|fn|func|void|int|bool|char|auto|const|unsigned|"
    r"signed|long|short|double|float|inline|extern|struct)\s+)*"
    r"(\w+)\s*\("
)


def find_enclosing_definition(file_path: Path, line_number: int) -> Definition | None:
    """Find the enclosing function/class definition for a given line.

    Scans backwards from *line_number* (1-based) looking for ``def``,
    ``class``, or C-style function signatures.  Returns ``None`` when no
    definition is found or the file cannot be read.
    """
    try:
        text = file_path.read_text(errors="replace")
    except (OSError, PermissionError):
        return None

    lines = text.splitlines()
    if not lines or line_number < 1 or line_number > len(lines):
        return None

    # Determine language heuristic from extension
    suffix = file_path.suffix.lower()
    is_python = suffix in (".py", ".pyi")

    # For Python, track indentation at the target line to find the
    # *innermost* enclosing scope.
    if is_python:
        return _find_python_definition(lines, line_number)
    else:
        return _find_c_style_definition(lines, line_number)


def _find_python_definition(lines: list[str], line_number: int) -> Definition | None:
    """Scan backwards for Python def/class enclosing *line_number*."""
    target_line = lines[line_number - 1]
    target_indent = len(target_line) - len(target_line.lstrip())

    # Track whether the innermost def is inside a class (-> method)
    found_def: Definition | None = None

    for idx in range(line_number - 1, -1, -1):
        raw = lines[idx]
        stripped = raw.lstrip()
        if not stripped:
            continue
        indent = len(raw) - len(stripped)

        # Must be at a *lesser* indentation than the target to be enclosing
        if indent >= target_indent:
            continue

        m_def = _PY_DEF.match(raw)
        m_cls = _PY_CLASS.match(raw)

        if m_def and found_def is None:
            name = m_def.group(3)
            # Capture full signature line (stripped of trailing whitespace)
            found_def = Definition(
                kind="function",
                name=name,
                signature=raw.rstrip(),
                line=idx + 1,
            )
            # Update target indent to look for an enclosing class
            target_indent = indent

        elif m_cls:
            cls_name = m_cls.group(2)
            if found_def is not None and indent < found_def.line:
                # The def we already found is inside this class -> method
                found_def.kind = "method"
                found_def.name = f"{cls_name}.{found_def.name}"
                return found_def
            elif found_def is None:
                return Definition(
                    kind="class",
                    name=cls_name,
                    signature=raw.rstrip(),
                    line=idx + 1,
                )

        # If we already have a def and hit something at lower indent that
        # isn't a class, we're done.
        if found_def is not None and indent < target_indent:
            break

    return found_def


def _find_c_style_definition(lines: list[str], line_number: int) -> Definition | None:
    """Scan backwards for C/Rust/Go function enclosing *line_number*."""
    target_line = lines[line_number - 1]
    target_indent = len(target_line) - len(target_line.lstrip())

    for idx in range(line_number - 1, -1, -1):
        raw = lines[idx]
        stripped = raw.lstrip()
        if not stripped:
            continue
        indent = len(raw) - len(stripped)

        # Must be at strictly less indentation to be enclosing
        if indent >= target_indent:
            continue

        m = _C_FUNC.match(raw)
        if m:
            name = m.group(1)
            # Skip common keywords that aren't function names
            if name in (
                "if",
                "else",
                "for",
                "while",
                "switch",
                "return",
                "case",
                "catch",
                "sizeof",
                "typeof",
                "alignof",
            ):
                continue
            return Definition(
                kind="function",
                name=name,
                signature=raw.rstrip(),
                line=idx + 1,
            )
    return None


# ---------------------------------------------------------------------------
# Annotator plugin
# ---------------------------------------------------------------------------


class SourceContextAnnotator:
    """Annotator that adds enclosing source definitions to error events."""

    name = "source_context"
    eager = True  # Run during blq run

    def should_annotate(self, context: RunContext) -> bool:
        """Return True when there are error events with file references."""
        return any(e["severity"] == "error" and e.get("ref_file") for e in context.events)

    def annotate(self, context: RunContext) -> None:
        """Add source context annotations to qualifying error events."""
        for event in context.events:
            if event["severity"] != "error" or not event.get("ref_file"):
                continue

            ref_file: str = event["ref_file"]
            ref_line = event.get("ref_line")
            if ref_line is None:
                continue

            # Resolve file path relative to source root
            file_path = context.source_root / ref_file
            if not file_path.exists():
                continue

            definition = find_enclosing_definition(file_path, int(ref_line))
            if definition is None:
                continue

            context.add_annotation(
                event["id"],
                Annotation(
                    annotator=self.name,
                    type="source",
                    display="inline",
                    data={
                        "definition": definition.signature,
                        "kind": definition.kind,
                        "name": definition.name,
                        "file": ref_file,
                        "def_line": definition.line,
                    },
                ),
            )
