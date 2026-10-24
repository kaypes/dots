#!/usr/bin/env python3

# /// script
# requires-python = ">=3.11"
# dependencies = ["tomlkit>=0.13.2,<1"]
# ///

import argparse
import math
import os
from pathlib import Path
import stat
import sys
import tempfile

if sys.version_info < (3, 11):
    sys.exit("Error: Python 3.11 or later is required.")

import tomllib


def equivalent(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def merge(files, tomlkit):
    sections = {key: {} for key in ("language-server", "language", "grammar")}
    formatted = {
        "language-server": tomlkit.table().add(tomlkit.nl()),
        "language": tomlkit.aot(),
        "grammar": tomlkit.aot(),
    }
    origins = {}
    for path in files:
        if not path.is_file():
            raise ValueError(f"{path}: not a regular file.")
        try:
            text = path.read_text(encoding="utf-8")
            document = tomllib.loads(text)
            styled = tomlkit.parse(text)
        except (ValueError, OSError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        unknown = document.keys() - sections.keys()
        if unknown:
            raise ValueError(
                f"{path}: unsupported root keys: {', '.join(sorted(unknown))}"
            )
        for section, entries in document.items():
            if section == "language-server":
                if not isinstance(entries, dict):
                    raise ValueError(f"{path}: language-server must be a table.")
                entries = (
                    (name, entry, styled[section][name])
                    for name, entry in entries.items()
                )
            else:
                if not isinstance(entries, list) or not all(
                    isinstance(e, dict) for e in entries
                ):
                    raise ValueError(f"{path}: use [[{section}]] for entries.")
                entries = (
                    (entry.get("name"), entry, styled[section][index])
                    for index, entry in enumerate(entries)
                )
            for name, entry, node in entries:
                if (
                    not isinstance(name, str)
                    or not name.strip()
                    or not isinstance(entry, dict)
                ):
                    raise ValueError(
                        f"{path}: invalid entry in {section}; check the name and table."
                    )
                key = (section, name)
                if name in sections[section]:
                    if not equivalent(sections[section][name], entry):
                        raise ValueError(
                            f"Conflicting definitions in {section}/{name}:\n"
                            f"  {origins[key]}\n  {path}"
                        )
                else:
                    sections[section][name] = entry
                    origins[key] = path
                    if section == "language-server":
                        formatted[section][name] = node
                    else:
                        if isinstance(node, tomlkit.items.InlineTable):
                            table = tomlkit.table()
                            table.update(node)
                            node = table
                        formatted[section].append(node)
    expected = {
        "language-server": sections["language-server"],
        "language": list(sections["language"].values()),
        "grammar": list(sections["grammar"].values()),
    }
    output = tomlkit.document()
    for section, entries in formatted.items():
        if entries:
            output[section] = entries
    return {key: value for key, value in expected.items() if value}, output


def destination_mode(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0o600
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path}: destination must be a regular file, not a symlink.")
    return stat.S_IMODE(info.st_mode) & 0o777


def write_atomic(path, data):
    mode = destination_mode(path)
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.chmod(temporary, mode)
            os.fsync(stream.fileno())
        destination_mode(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate languages.toml from languages.d/*.toml."
    )
    parser.add_argument(
        "directory", nargs="?", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    try:
        import tomlkit
    except ModuleNotFoundError:
        parser.exit(1, "Error: install tomlkit in your Python environment.\n")
    try:
        root = args.directory.expanduser().resolve(strict=True)
        source, target = root / "languages.d", root / "languages.toml"
        destination_mode(target)
        if not source.is_dir():
            raise ValueError(f"Directory not found: {source}")
        files = sorted(source.glob("*.toml"))
        if not files:
            raise ValueError(f"No .toml files in {source}; destination unchanged.")
        if any(path.resolve() == target for path in files):
            raise ValueError(
                "A fragment points to languages.toml; remove this circular reference."
            )
        document, formatted = merge(files, tomlkit)
        text = tomlkit.dumps(formatted)
        if not equivalent(document, tomllib.loads(text)):
            raise ValueError("Serialization changed the data; destination unchanged.")
        changed = write_atomic(target, text.encode("utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(
        f"{target}: generated from {len(files)} file(s)."
        if changed
        else f"{target}: already up to date."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
