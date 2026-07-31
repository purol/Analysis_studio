from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import glob
import os
import re
from typing import Iterable


TOKEN_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOKEN_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NOT_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")


@dataclass(frozen=True)
class SourceField:
    key: str
    label: str
    preview: str = ""


@dataclass(frozen=True)
class TokenDefinition:
    name: str
    source: str
    source_label: str
    preview: str = ""

    @property
    def expression(self) -> str:
        return "{" + self.name + "}"


def sanitize_token_name(value: str, fallback: str = "variable") -> str:
    cleaned = _NOT_IDENTIFIER.sub("_", str(value).strip()).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def extract_tokens(value: object) -> list[str]:
    """Return explicit {name} references while respecting {{ and }} escapes."""
    text = str(value or "")
    left = "\x00AS_LEFT_BRACE\x00"
    right = "\x00AS_RIGHT_BRACE\x00"
    text = text.replace("{{", left).replace("}}", right)
    found: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return found


def token_bindings(properties: dict[str, object]) -> list[dict[str, str]]:
    raw = properties.get("tokens", [])
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": str(item.get("name", "")).strip(),
                "source": str(item.get("source", "")).strip(),
            }
        )
    return result


def _expand_preview_template(
    value: object, inherited_context: dict[str, object] | None = None
) -> str:
    context = inherited_context or {}
    text = str(value or "")
    left = "\x00AS_PREVIEW_LEFT\x00"
    right = "\x00AS_PREVIEW_RIGHT\x00"
    text = text.replace("{{", left).replace("}}", right)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(context.get(name, "{" + name + "}"))

    return TOKEN_PATTERN.sub(replace, text).replace(left, "{").replace(right, "}")


def _resolve_path(
    value: object,
    base_directory: str | Path | None,
    inherited_context: dict[str, object] | None = None,
) -> Path:
    rendered = _expand_preview_template(value, inherited_context)
    expanded = Path(os.path.expandvars(os.path.expanduser(rendered)))
    if expanded.is_absolute() or base_directory is None:
        return expanded
    return Path(base_directory) / expanded


def _csv_configuration(properties: dict[str, object]) -> tuple[Path, str, bool]:
    path = Path(str(properties.get("csv_file", "")))
    delimiter = str(properties.get("delimiter", ","))
    if delimiter == r"\t":
        delimiter = "\t"
    if len(delimiter) != 1:
        delimiter = ","
    return path, delimiter, bool(properties.get("has_header", True))


def source_fields(
    properties: dict[str, object],
    base_directory: str | Path | None = None,
    inherited_context: dict[str, object] | None = None,
) -> list[SourceField]:
    """Fields the user may explicitly expose as For Each variables."""
    mode = str(properties.get("source_mode", "root_files"))
    if mode == "root_files":
        directory = _resolve_path(
            properties.get("directory", ""), base_directory, inherited_context
        )
        pattern = _expand_preview_template(
            properties.get("pattern", "*.root"), inherited_context
        )
        first_path = next(iter(sorted(glob.glob(str(directory / pattern)))), "")
        path = Path(first_path) if first_path else None
        return [
            SourceField("path", "Full file path", str(path) if path else "<matching file path>"),
            SourceField(
                "directory",
                "Parent directory",
                str(path.parent) if path else str(directory),
            ),
            SourceField("filename", "File name", path.name if path else "example.root"),
            SourceField("stem", "File stem", path.stem if path else "example"),
            SourceField("suffix", "File suffix", path.suffix if path else ".root"),
            SourceField("index", "Loop index", "0"),
        ]

    if mode == "values":
        values = [
            _expand_preview_template(line.strip(), inherited_context)
            for line in str(properties.get("values", "")).splitlines()
            if line.strip()
        ]
        return [
            SourceField("value", "Current value", values[0] if values else "<first value>"),
            SourceField("index", "Loop index", "0"),
        ]

    if mode == "csv_rows":
        raw_path, delimiter, has_header = _csv_configuration(properties)
        path = _resolve_path(raw_path, base_directory, inherited_context)
        fields: list[SourceField] = [
            SourceField("index", "Row index", "0"),
            SourceField("csv_file", "CSV file path", str(path)),
        ]
        if not path.is_file():
            if has_header:
                fields.append(
                    SourceField(
                        "column_sanitized:column",
                        "CSV column (file not readable yet)",
                        "<column value>",
                    )
                )
            else:
                fields.append(SourceField("column_index:0", "CSV column 1", "<column 1>"))
            return fields

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                if has_header:
                    reader = csv.reader(stream, delimiter=delimiter)
                    headers = next(reader, [])
                    first_row = next(reader, [])
                    for index, header in enumerate(headers):
                        preview = first_row[index] if index < len(first_row) else ""
                        fields.append(
                            SourceField(
                                f"column:{header}",
                                f"CSV column: {header}",
                                preview,
                            )
                        )
                else:
                    reader = csv.reader(stream, delimiter=delimiter)
                    first_row = next(reader, [])
                    for index, preview in enumerate(first_row):
                        fields.append(
                            SourceField(
                                f"column_index:{index}",
                                f"CSV column {index + 1}",
                                preview,
                            )
                        )
        except (OSError, csv.Error, UnicodeError):
            # Keep the editor usable; runtime validation/execution reports the
            # exact read failure.
            pass
        return fields

    return []


def token_definitions(
    properties: dict[str, object],
    base_directory: str | Path | None = None,
    inherited_context: dict[str, object] | None = None,
) -> list[TokenDefinition]:
    fields = {
        field.key: field
        for field in source_fields(properties, base_directory, inherited_context)
    }
    definitions: list[TokenDefinition] = []
    for binding in token_bindings(properties):
        source = binding["source"]
        field = fields.get(source)
        definitions.append(
            TokenDefinition(
                name=binding["name"],
                source=source,
                source_label=field.label if field else source or "<not selected>",
                preview=field.preview if field else "<unavailable>",
            )
        )
    return definitions


def _lookup_raw_value(source: str, raw_item: dict[str, object]) -> object:
    if source.startswith("column_sanitized:"):
        wanted = source.split(":", 1)[1]
        columns = raw_item.get("columns", {})
        if isinstance(columns, dict):
            for raw_name, value in columns.items():
                if sanitize_token_name(str(raw_name), "column") == wanted:
                    return value
        raise KeyError(source)
    if source.startswith("column:"):
        raw_name = source.split(":", 1)[1]
        columns = raw_item.get("columns", {})
        if isinstance(columns, dict) and raw_name in columns:
            return columns[raw_name]
        raise KeyError(source)
    if source.startswith("column_index:"):
        index = int(source.split(":", 1)[1])
        columns = raw_item.get("column_values", [])
        if isinstance(columns, list) and 0 <= index < len(columns):
            return columns[index]
        raise KeyError(source)
    if source in raw_item:
        return raw_item[source]
    raise KeyError(source)


def bind_tokens(
    properties: dict[str, object],
    raw_item: dict[str, object],
) -> dict[str, object]:
    context: dict[str, object] = {}
    for binding in token_bindings(properties):
        name = binding["name"]
        source = binding["source"]
        if not name or not source:
            continue
        try:
            context[name] = _lookup_raw_value(source, raw_item)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(
                f"For Each variable '{name}' cannot read source '{source}'."
            ) from exc
    return context


def valid_source_key(mode: str, source: str) -> bool:
    if mode == "root_files":
        return source in {"path", "directory", "filename", "stem", "suffix", "index"}
    if mode == "values":
        return source in {"value", "index"}
    if mode == "csv_rows":
        return (
            source in {"index", "csv_file"}
            or source.startswith("column:")
            or source.startswith("column_index:")
            or source.startswith("column_sanitized:")
        )
    return False


def replace_token_references(value: object, replacements: dict[str, str]) -> object:
    if not isinstance(value, str):
        return value
    text = value
    for old, new in replacements.items():
        text = text.replace("{" + old + "}", "{" + new + "}")
    return text


def unique_names(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
