"""Who holds each line that keeps a packet from being ready (src/holders.py): every producer declares a holder, the lines keep their words, and the plan says who holds each.

A producer is a function that makes packet-problem lines: every `def problems` in src/, the few others holders.OTHER_PRODUCERS names, and the lines packet.plan itself
makes. This test finds them in the code (not from a list kept here), so a producer added later without a holder fails it. Everyone here is made up."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

import holders
from holders import ATTORNEY, CLIENT, OFFICE, held, holder_of

SRC = Path(__file__).resolve().parents[1] / "src"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _decorated(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "producer":
            return True
    return False


def found_producers() -> list[tuple[str, str, bool]]:
    """(module, function, declares a holder) for every `def problems` in src/ and every function holders.OTHER_PRODUCERS names."""
    wanted = {tuple(n.rsplit(".", 1)) for n in holders.OTHER_PRODUCERS}
    out = []
    for path in sorted(SRC.rglob("*.py")):
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and (node.name == "problems" or (module, node.name) in wanted):
                out.append((module, node.name, _decorated(node)))
    return out


def test_every_producer_in_the_code_declares_a_holder():
    producers = found_producers()
    assert len(producers) >= 30, "the producers were not found: the test's own search is broken"
    undeclared = [f"{m}.{f}" for m, f, ok in producers if not ok]
    assert not undeclared, "these make packet-problem lines and declare no holder (@holders.producer(holder)): " + ", ".join(undeclared)
    named = {tuple(n.rsplit(".", 1)) for n in holders.OTHER_PRODUCERS}
    assert named <= {(m, f) for m, f, _ in producers}, "holders.OTHER_PRODUCERS names a function that is not there"


def test_every_declared_producer_is_registered_with_a_valid_holder_when_its_module_is_loaded():
    for module, function, _ in found_producers():
        importlib.import_module(module)
        assert holders.PRODUCERS.get(f"{module}.{function}") in holders.HOLDERS, f"{module}.{function} is not registered"


def _plan_lines() -> list[tuple[str, ast.AST]]:
    """Each line packet.plan makes itself: the argument of problems.append(...) and the elements of problems += [...]."""
    tree = ast.parse((SRC / "packet.py").read_text(encoding="utf-8"))
    plan = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "plan")
    out = []
    for node in ast.walk(plan):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append" and getattr(node.func.value, "id", "") == "problems":
            out.append(("append", node.args[0]))
        if isinstance(node, ast.AugAssign) and getattr(node.target, "id", "") == "problems":
            out.append(("extend", node.value))
    return out


def _is_held(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "held"


def _is_producer_call(node: ast.AST) -> bool:
    """problems += module.problems(...) (declared above) or a sum of such calls."""
    if isinstance(node, ast.BinOp):
        return _is_producer_call(node.left) and _is_producer_call(node.right)
    if isinstance(node, ast.Subscript):  # cover_letter's priority(...)[1] / mail_to(...)[1]
        return _is_producer_call(node.value)
    return isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in {f.rsplit(".", 1)[1] for f in holders.OTHER_PRODUCERS} | {"problems"}


def test_every_line_packet_plan_makes_itself_is_labelled_where_it_is_made():
    lines = _plan_lines()
    assert len(lines) >= 8
    for how, node in lines:
        ok = _is_held(node) or (isinstance(node, ast.IfExp) and _is_held(node.body)) or _is_producer_call(node) or (
            how == "extend" and isinstance(node, ast.ListComp) and _is_held(node.elt)) or (how == "extend" and isinstance(node, ast.BinOp))
        assert ok, f"packet.plan makes a line with no holder: {ast.unparse(node)[:100]}"
        if isinstance(node, ast.BinOp):  # problems += letter_problems + mail_to(...)[1]: the names stand for producers' own lists
            assert all(isinstance(x, (ast.Name, ast.Subscript, ast.Call)) for x in (node.left, node.right))


def test_a_labelled_line_is_the_same_string_everywhere_a_plain_one_is():
    plain = "Missing: Passport."
    line = held(CLIENT, plain)
    assert line == plain and hash(line) == hash(plain) and str(line) == plain and f"{line}" == plain and json.dumps([line]) == json.dumps([plain])
    assert holder_of(line) == CLIENT and holder_of(plain) == OFFICE and holder_of(None) == OFFICE  # a line no producer labelled is the office's
    assert holders.label(ATTORNEY, [line, "x"])[0].holder == CLIENT and holders.label(ATTORNEY, [line, "x"])[1].holder == ATTORNEY
    with pytest.raises(ValueError):
        held("the judge", plain)
    with pytest.raises(ValueError):
        holders.producer("the judge")


def test_the_decorator_labels_what_a_producer_leaves_and_keeps_what_it_labelled():
    @holders.producer(CLIENT)
    def made(*, both: bool = True):
        return ["a paper", held(ATTORNEY, "a sign-off")] if both else []

    got = made()
    assert [holder_of(x) for x in got] == [CLIENT, ATTORNEY] and made(both=False) == []

    @holders.producer(OFFICE, part=1)
    def pair():
        return {"month": None}, ["a line"]

    assert pair()[0] == {"month": None} and holder_of(pair()[1][0]) == OFFICE
    assert holders.counts(got) == {CLIENT: 1, OFFICE: 0, ATTORNEY: 1}


def test_who_a_question_is_for_names_its_holder():
    assert holders.of_who("the client") == CLIENT and holders.of_who("the petitioner") == CLIENT and holders.of_who("the attorney") == ATTORNEY
    assert holders.of_who("the paralegal") == OFFICE and holders.of_who("the office") == OFFICE and holders.of_who(None) == OFFICE
    qs = [{"required": True, "value": "x", "who": "the attorney"}, {"required": True, "value": None, "who": "the client"}, {"required": True, "value": None, "who": "the attorney"}]
    assert holders.of_first(qs) == CLIENT and holders.of_first([]) == OFFICE
