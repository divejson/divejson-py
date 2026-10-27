"""What every reader's finding carries beside its sentence, and what the sentence may say.

A finding reaches a diver through an application's import screen, so its sentence speaks of
the dive's values and never of the machinery that read them: no section of the
specification, no member written in code, no readers, channels or axes. Its `code` is the
stable name an application keys on, one of `NOTE_CODES`, and its `member` the document path
it is about.

Two checks hold the wording, because neither alone is enough. The corpus run reads only the
branches its fixtures reach. The static one reads the source of every sentence a reader can
raise — a literal passed to a `note`, the helpers that assemble one from parts, the
constants, tables and locals a sentence is built from, and everything raised as a refusal —
whether or not any fixture reaches it.
"""

from __future__ import annotations

import ast
import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from divejson import NOTE_CODES, PRODUCER_KEY, Note, NoteGroup, convert
from divejson.converter import grouped
from helpers import EXPORTED_AT, FIXTURES, ROOT

# The modules a conversion runs through, whose sentences and refusals reach a diver. Not
# `validate.py`, `conform.py`, `cli.py` or `uddf_write.py`: their messages address a
# document's author, and a writer's notes carry no code.
CONVERSION_PATH = [
    ROOT / "divejson" / name
    for name in (
        "converter.py",
        "series.py",
        "registry.py",
        "xmlsource.py",
        "fit.py",
        "shearwater_db.py",
        "shearwater_xml.py",
        "ssrf.py",
        "suunto_json.py",
        "suunto_xml.py",
        "uddf.py",
    )
]

READER_INPUTS = sorted(
    path
    for format_id, pattern in (
        ("fit", "*.fit"),
        ("shearwater_db", "*.db"),
        ("shearwater_xml", "*.xml"),
        ("ssrf", "*.ssrf"),
        ("suunto_json", "*.json"),
        ("suunto_xml", "*.xml"),
        ("uddf", "*.uddf"),
    )
    for path in (FIXTURES / format_id).glob(pattern)
)

# Words that speak of the machinery rather than the dive, case folded.
BANNED = (
    "§",
    "this reader",
    "convert",
    "the generator",
    "writers emit",
    "the format",
    "this format",
    "channel",
    "time axis",
    "readout",
    "member",
    "namespace",
)
SPEC = re.compile(r"\bspec\b", re.IGNORECASE)
SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


# What a file itself says, quoted where a sentence interpolates it — an id, a tag, a site's
# own name — which is the file's text rather than the sentence's and may say anything.
QUOTED = re.compile(r"'[^']*'")


# A source element named as the file names it, which the finding is about.
ELEMENT = re.compile(r"<[^<>]*>")


def banned_in(text: str) -> list[str]:
    text = ELEMENT.sub("<>", text)
    folded = text.casefold()
    found = [word for word in BANNED if word in folded]
    found += SPEC.findall(text)
    found += SNAKE_CASE.findall(text)
    return found


# -- the shape of a note ----------------------------------------------------------------


def test_a_note_built_positionally_has_no_code_and_no_member() -> None:
    """The constructor an application already calls with three arguments keeps working."""
    note = Note("dive/0", "Something.", "absent")
    assert (note.code, note.member) == ("", None)


def test_a_note_takes_its_code_and_member_by_keyword_only() -> None:
    note = Note("dive/0", "Something.", "absent", code="gas_missing", member="dives/0/cylinders/0/oxygen")
    assert (note.code, note.member) == ("gas_missing", "dives/0/cylinders/0/oxygen")
    with pytest.raises(TypeError):
        Note("dive/0", "Something.", "absent", "gas_missing")  # type: ignore[misc]


def test_a_group_carries_the_code_its_notes_share() -> None:
    notes = (
        Note("dive/0", "Same.", "absent", code="gas_missing"),
        Note("dive/1", "Same.", "absent", code="gas_missing"),
    )
    assert grouped(notes) == [NoteGroup("absent", "Same.", ["dive/0", "dive/1"], "gas_missing")]


# -- the corpus -------------------------------------------------------------------------


def _converted() -> Iterator[tuple[Path, object]]:
    for path in READER_INPUTS:
        yield path, convert(path.read_bytes(), exported_at=EXPORTED_AT)


@pytest.mark.parametrize("source", READER_INPUTS, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_every_finding_carries_a_catalogued_code(source: Path) -> None:
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert [note for note in conversion.notes if note.code not in NOTE_CODES] == []


@pytest.mark.parametrize("source", READER_INPUTS, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_no_finding_speaks_of_the_machinery(source: Path) -> None:
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    said = [QUOTED.sub("''", note.message) for note in conversion.notes]
    assert [(message, banned_in(message)) for message in said if banned_in(message)] == []


@pytest.mark.parametrize("source", READER_INPUTS, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_an_inferred_finding_names_exactly_the_members_the_document_lists(source: Path) -> None:
    """The spec couples the two: every `inferred` finding is about a listed member and every
    listed member has one. `member` is what makes the coupling checkable."""
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    named = [note.member for note in conversion.notes if note.kind == "inferred"]
    listed = conversion.document["extensions"][PRODUCER_KEY].get("inferred", [])
    assert sorted(named) == sorted(listed)


@pytest.mark.parametrize("source", READER_INPUTS, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_a_member_names_a_record_the_document_carries(source: Path) -> None:
    """A finding about a dive points into that dive, not past the end of the list."""
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    for note in conversion.notes:
        if note.member is None:
            continue
        collection, _, rest = note.member.partition("/")
        index = rest.partition("/")[0]
        if index.isdigit():
            assert int(index) < len(conversion.document.get(collection, ())), note


def test_the_fit_pair_that_infers_both_depths_says_so_of_each() -> None:
    conversion = convert((FIXTURES / "fit" / "suunto-ocean-2026.fit").read_bytes(), exported_at=EXPORTED_AT)
    inferred = [(note.code, note.member) for note in conversion.notes if note.kind == "inferred"]
    assert inferred == [
        ("dive_time_worked_out", "dives/0/submerged_time"),
        ("avg_depth_worked_out", "dives/0/avg_depth"),
    ]


def test_under_an_archive_a_member_moves_with_the_dive_it_names() -> None:
    """The second file's dive is the merged document's second, and its findings say so — the
    re-indexing the inferred list gets."""
    data = (FIXTURES / "fit" / "suunto-ocean-2026.fit").read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.fit", data)
        archive.writestr("b.fit", data)
    conversion = convert(buffer.getvalue(), exported_at=EXPORTED_AT)
    later = [note.member for note in conversion.notes if note.where.startswith("b.fit/") and note.kind == "inferred"]
    assert later == ["dives/1/submerged_time", "dives/1/avg_depth"]
    listed = conversion.document["extensions"][PRODUCER_KEY]["inferred"]
    assert sorted(note.member for note in conversion.notes if note.kind == "inferred") == sorted(listed)


# -- the source --------------------------------------------------------------------------


class _Source:
    """The conversion path's modules, parsed, with each function's definition by name."""

    def __init__(self) -> None:
        self.trees = {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in CONVERSION_PATH}
        self.constants: dict[str, ast.expr] = {}
        self.functions: dict[str, list[ast.FunctionDef]] = {}
        self.parents: dict[ast.AST, ast.AST] = {}
        self.all_calls: list[ast.Call] = []
        self.module: dict[ast.AST, str] = {}
        # What a name or a parameter has been worked out to, and an empty answer while it is
        # being worked out, which is what ends a cycle through a recursive helper.
        self.cache: dict[tuple[str, int, str], frozenset[str]] = {}
        for module, tree in self.trees.items():
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    self.constants[node.targets[0].id] = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    self.constants[node.target.id] = node.value
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    self.parents[child] = node
                if isinstance(node, ast.FunctionDef):
                    self.functions.setdefault(node.name, []).append(node)
                    self.module[node] = module
                if isinstance(node, ast.Call):
                    self.all_calls.append(node)
                    self.module[node] = module

    def definitions(self, name: str, module: str | None) -> list[ast.FunctionDef]:
        """The functions a name calls: the caller's own module's, else the shared helpers'.

        Two readers can each define a method of one name with its parameters in another
        order, so a call is matched to its own module's definition first.
        """
        found = self.functions.get(name, [])
        own = [definition for definition in found if self.module[definition] == module]
        return own or [definition for definition in found if self.module[definition] in SHARED]

    def calls(self) -> Iterator[ast.Call]:
        yield from self.all_calls

    def enclosing(self, node: ast.AST) -> ast.FunctionDef | None:
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, ast.FunctionDef):
                return node
        return None

    def fragments(self, expr: ast.AST | None, scope: ast.FunctionDef | None) -> set[str]:
        """Every literal piece of text `expr` can contribute to a sentence.

        Followed through the names a sentence is built from — a local the function assigns,
        a module constant or table, a parameter back to the arguments its callers pass, a
        module function's returns — far enough to reach every literal at the conversion
        path's own sites. A dictionary contributes its values, never its keys, which are
        what a table is looked up by.
        """
        if expr is None:
            return set()
        if isinstance(expr, ast.Constant):
            return {expr.value} if isinstance(expr.value, str) else set()
        if isinstance(expr, ast.JoinedStr):
            return {text for value in expr.values for text in self.fragments(value, scope)}
        if isinstance(expr, ast.FormattedValue):
            return self.fragments(expr.value, scope)
        if isinstance(expr, (ast.BinOp, ast.BoolOp, ast.IfExp, ast.Tuple, ast.List)):
            return {
                text for child in ast.iter_child_nodes(expr) if isinstance(child, ast.expr)
                for text in self.fragments(child, scope)
            }
        if isinstance(expr, ast.Dict):
            # A table's values, and not a record being built: a value read off the file is
            # the file's text, not the sentence's.
            return {
                text for value in expr.values if not isinstance(value, ast.Call)
                for text in self.fragments(value, scope)
            }
        if isinstance(expr, ast.Subscript):
            return self.fragments(expr.value, scope)
        if isinstance(expr, ast.Name):
            return self.cached(("name", id(scope), expr.id), lambda: self.named(expr.id, scope))
        if isinstance(expr, ast.Call):
            return self.called(expr, scope)
        return set()

    def cached(self, key: tuple[str, int, str], work) -> set[str]:
        if key not in self.cache:
            self.cache[key] = frozenset()
            self.cache[key] = frozenset(work())
        return set(self.cache[key])

    def called(self, call: ast.Call, scope: ast.FunctionDef | None) -> set[str]:
        func = call.func
        found: set[str] = set()
        if isinstance(func, ast.Attribute) and func.attr == "get":
            # A table's lookup, with the default beside it.
            found |= self.fragments(func.value, scope)
            for arg in call.args[1:]:
                found |= self.fragments(arg, scope)
            return found
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in ("told", "join", "replace", "strip", "removesuffix", "removeprefix"):
            for arg in call.args:
                found |= self.fragments(arg, scope)
            if isinstance(func, ast.Attribute):
                found |= self.fragments(func.value, scope)
            return found
        for definition in self.definitions(name, self.module_of(call)):
            found |= self.cached(("returns", id(definition), ""), lambda d=definition: self.returned(d))
        return found

    def module_of(self, node: ast.AST) -> str | None:
        while node not in self.module and node in self.parents:
            node = self.parents[node]
        return self.module.get(node)

    def returned(self, definition: ast.FunctionDef) -> set[str]:
        return {
            text for node in ast.walk(definition) if isinstance(node, ast.Return)
            for text in self.fragments(node.value, definition)
        }

    def named(self, name: str, scope: ast.FunctionDef | None) -> set[str]:
        found: set[str] = set()
        if scope is not None:
            parameters = [arg.arg for arg in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs)]
            assigned = name in parameters
            if assigned:
                found |= self.cached(("passed", id(scope), name), lambda: self.passed(scope, name))
                for default in (*scope.args.defaults, *(d for d in scope.args.kw_defaults if d is not None)):
                    found |= self.fragments(default, None)
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            assigned = True
                            found |= self.fragments(node.value, scope)
                        elif isinstance(target, ast.Tuple) and name in _names(target):
                            assigned = True
                            found |= self.unpacked(node.value, _names(target).index(name), scope)
                elif isinstance(node, ast.For):
                    if isinstance(node.target, ast.Name) and node.target.id == name:
                        assigned = True
                        found |= self.fragments(node.iter, scope)
                    elif isinstance(node.target, ast.Tuple) and name in _names(node.target):
                        assigned = True
                        position = _names(node.target).index(name)
                        if isinstance(node.iter, (ast.Tuple, ast.List)):
                            for row in node.iter.elts:
                                if isinstance(row, ast.Tuple) and position < len(row.elts):
                                    found |= self.fragments(row.elts[position], scope)
            if assigned:
                return found
        if name in self.constants:
            found |= self.fragments(self.constants[name], None)
        return found

    def unpacked(self, value: ast.expr, position: int, scope: ast.FunctionDef) -> set[str]:
        if isinstance(value, ast.Tuple):
            return self.fragments(value.elts[position], scope) if position < len(value.elts) else set()
        rows = self.rows(value, scope)
        if rows is not None:
            # A table of tuples, looked up and unpacked: the same place in every row.
            return {
                text for row in rows if position < len(row.elts) for text in self.fragments(row.elts[position], None)
            }
        if isinstance(value, ast.Call):
            # A function returning a tuple: the same place in each tuple it returns.
            func = value.func
            called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            found: set[str] = set()
            for definition in self.definitions(called, self.module_of(value)):
                for node in ast.walk(definition):
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                        if position < len(node.value.elts):
                            found |= self.fragments(node.value.elts[position], definition)
            return found
        # A table's row, unpacked: every row of the table it was looked up in.
        return self.fragments(value, scope)

    def rows(self, value: ast.expr, scope: ast.FunctionDef) -> list[ast.Tuple] | None:
        """The rows of a module table of tuples that `value` is a lookup into, if it is one."""
        if isinstance(value, ast.Name):
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == value.id for target in node.targets
                ):
                    return self.rows(node.value, scope)
            return None
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get":
            table = value.func.value
            if isinstance(table, ast.Name) and isinstance(self.constants.get(table.id), ast.Dict):
                rows = self.constants[table.id].values  # type: ignore[union-attr]
                if all(isinstance(row, ast.Tuple) for row in rows):
                    return rows  # type: ignore[return-value]
        return None

    def passed(self, function: ast.FunctionDef, parameter: str) -> set[str]:
        """What the callers of `function` pass for `parameter`, by position or by name."""
        names = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
        offset = 1 if names and names[0] == "self" else 0
        found: set[str] = set()
        shared = self.module[function] in SHARED
        for call in self.all_calls:
            func = call.func
            called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if called != function.name or not (shared or self.module[call] == self.module[function]):
                continue
            scope = self.enclosing(call)
            for keyword in call.keywords:
                if keyword.arg == parameter:
                    found |= self.fragments(keyword.value, scope)
            if parameter in names:
                position = names.index(parameter) - offset
                if 0 <= position < len(call.args):
                    found |= self.fragments(call.args[position], scope)
        return found


def _names(target: ast.Tuple) -> list[str | None]:
    return [element.id if isinstance(element, ast.Name) else None for element in target.elts]


# The modules whose helpers every reader calls, matched to callers in any module.
SHARED = ("converter.py", "series.py")

SOURCE = _Source()
NOTE_CALLS = ("note", "_note", "Note")


def _note_calls() -> Iterator[ast.Call]:
    """Every call raising a finding, but not the readers' own `note` methods passing one on:
    their callers are the sites, and each is read here already."""
    for call in SOURCE.calls():
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        scope = SOURCE.enclosing(call)
        if name in NOTE_CALLS and not (scope is not None and scope.name in NOTE_CALLS):
            yield call


def _message(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "message":
            return keyword.value
    return call.args[1] if len(call.args) > 1 else None


def test_every_note_a_reader_raises_passes_its_code() -> None:
    """The `Reporter` makes `code` required for a type checker; this makes it so for the suite."""
    missing = [
        f"{call.lineno}: {ast.unparse(call)[:80]}"
        for call in _note_calls()
        if not any(keyword.arg == "code" for keyword in call.keywords)
    ]
    assert missing == []


def test_the_catalogue_is_exactly_the_codes_the_readers_raise() -> None:
    """A code nothing raises is a promise about nothing; one missing from the catalogue is a
    finding an application cannot look up."""
    literals = {
        node.value
        for tree in SOURCE.trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in NOTE_CODES
    }
    raised = {
        keyword.value.value
        for call in SOURCE.calls()
        for keyword in call.keywords
        if keyword.arg == "code" and isinstance(keyword.value, ast.Constant)
    }
    assert raised <= set(NOTE_CODES)
    assert set(NOTE_CODES) - literals == set()


def test_every_code_is_a_snake_case_name_with_a_description() -> None:
    for code, description in NOTE_CODES.items():
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", code), code
        assert description and description == description.strip(), code


def test_no_sentence_a_reader_can_raise_speaks_of_the_machinery() -> None:
    """Every literal a note's message can be built from, and every refusal's."""
    offending: list[tuple[int, str, list[str]]] = []
    for call in _note_calls():
        for text in SOURCE.fragments(_message(call), SOURCE.enclosing(call)):
            if banned_in(text):
                offending.append((call.lineno, text, banned_in(text)))
    for tree in SOURCE.trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                for arg in node.exc.args:
                    for text in SOURCE.fragments(arg, SOURCE.enclosing(node)):
                        if banned_in(text):
                            offending.append((node.lineno, text, banned_in(text)))
    assert offending == []


def test_the_static_check_reaches_the_sentences_built_away_from_the_call() -> None:
    """The check above is only as good as what it follows, so it is held to the sites that
    pass a sentence by name: a module constant, a table, a sentence a helper returns, a
    local the method assembles, and a label a caller hands a helper."""
    reached = {text for call in _note_calls() for text in SOURCE.fragments(_message(call), SOURCE.enclosing(call))}
    for expected in (
        "The application that wrote this file gives gradient factors in whole percent where the UDDF "
        "documentation's examples show a fraction; read as percent.",
        "UDDF's <current> has six steps and the logbook five; 'very-mild-current' is read as light, beside "
        "'mild-current'.",
        " records no seconds; read as :00.",
        "The application that wrote this file marks the local time with a 'Z', the sign for UTC; read as a "
        "local time without a time zone.",
        "The file records no maximum depth, so it was taken from the deepest reading of the depth profile.",
        "the site name",
        "the low gradient factor",
        "water temperature",
    ):
        assert expected in reached, expected
