"""Toy repo fixtures: a small multi-file Python repo with several latent bugs.

The agent gets a task prompt naming the files; tools let it read files, run
pytest, and edit. Multiple turns of tool use produce a request stream long
enough to exhibit per-turn request growth (the thing prefix caching cares
about).
"""

TOY_REPO_FILES = {
    "calc.py": '''\
def add(a, b):
    return a - b  # BUG 1: should be a + b


def mul(a, b):
    result = 0
    for _ in range(b):
        result = add(result, a)
    return result


def div(a, b):
    if b = 0:  # BUG 2: syntax, should be ==
        raise ValueError("division by zero")
    return a / b
''',
    "shapes.py": '''\
import math


class Circle:
    def __init__(self, r):
        self.r = r

    def area(self):
        return math.pi * self.r  # BUG 3: should be ** 2


class Rect:
    def __init__(self, w, h):
        self.w = w
        self.h = h

    def area(self):
        return self.w + self.h  # BUG 4: should be *
''',
    "textutil.py": '''\
def word_count(text):
    return len(text)  # BUG 5: should split on whitespace


def reverse_words(text):
    return " ".join(reversed(text.split()))


def capitalize_first(text):
    if not text:
        return text
    return text[0].upper() + text[1:]
''',
    "test_calc.py": '''\
from calc import add, mul, div
import pytest


def test_add():
    assert add(2, 3) == 5


def test_mul():
    assert mul(3, 4) == 12


def test_div():
    assert div(10, 2) == 5
    with pytest.raises(ValueError):
        div(1, 0)
''',
    "test_shapes.py": '''\
import math
import pytest
from shapes import Circle, Rect


def test_circle_area():
    assert math.isclose(Circle(2).area(), math.pi * 4, rel_tol=1e-9)


def test_rect_area():
    assert Rect(3, 4).area() == 12
''',
    "test_textutil.py": '''\
from textutil import word_count, reverse_words


def test_word_count():
    assert word_count("hello world foo") == 3


def test_reverse_words():
    assert reverse_words("a b c") == "c b a"
''',
    # long stable content: exercises prefix volume, like real AGENTS.md/rules.
    # Scaled so stable regions (tools+system) sit far above the documented
    # >=1024-token minimum cacheable prefix; real CLI harnesses carry 10-20k
    # tokens of system prompt + tool schemas.
    "README.md": (
        "# toy-repo\n\nA small Python project used as a fixture for the\n"
        "mini-journal replication harness.\n\n"
        + ("## Notes\n\nA stable README section used purely for prefix volume. "
           "It documents module layout, conventions, and the test protocol that "
           "agents must follow when editing this repository.\n\n" * 260)
    ),
}

TASK_PROMPT = (
    "This repository has several failing tests. Work through them one file at a "
    "time: run the tests, read the failing module, fix the bug, and re-run to "
    "confirm. Fix calc.py, shapes.py, and textutil.py in that order."
)

AGENT_FILES_HINT = "Files: " + ", ".join(sorted(TOY_REPO_FILES))


# --- Second fixture: a web-service repository (Round-3 W7: task-family breadth).
# Same shape class as the toy repo (three seeded-bug modules + tests + padded
# README) but different domain, file names, bug kinds, and README volume, so
# conclusions are not idiosyncratic to one fixture's content.
REPO2_FILES = {
    "users.py": '''\
def normalize_email(email):
    return email.strip()  # BUG 1: should also lowercase the domain


def full_name(first, last):
    if not first or not last:
        raise ValueError("names must be non-empty")
    return first + " " + last


def initials(first, last):
    return (first[0] + last[0]).upper()
''',
    "orders.py": '''\
def order_total(items):
    # items: list of (price, quantity)
    total = 0
    for price, qty in items:
        total += price * qty
    return total  # BUG 2: no tax applied; should add 8% tax


def format_order_id(order_id):
    return f"ORD-{order_id:04d}"


def split_order(items, n):
    k = -(-len(items) // n)  # ceil division
    return [items[i:i + k] for i in range(0, len(items), k)]
''',
    "inventory.py": '''\
def in_stock(stock, sku):
    return stock.get(sku, 0) > 0


def reserve(stock, sku, qty):
    if stock.get(sku, 0) < qty:
        return False
    stock[sku] = stock[sku] - qty  # BUG 3: should also log the reservation
    return True


def reorder_point(history, lead_time):
    avg = sum(history) / len(history)
    return int(avg * lead_time * 1.5)
''',
    "test_users.py": '''\
from users import normalize_email, full_name


def test_normalize_email():
    assert normalize_email("User@Example.COM ") == "user@example.com"


def test_full_name():
    assert full_name("Ada", "Lovelace") == "Ada Lovelace"
''',
    "test_orders.py": '''\
from orders import order_total, format_order_id


def test_order_total_with_tax():
    assert abs(order_total([(10.0, 2)]) - 21.6) < 1e-9


def test_format_order_id():
    assert format_order_id(42) == "ORD-0042"
''',
    "test_inventory.py": '''\
from inventory import reserve, reorder_point


def test_reserve():
    stock = {"a": 5}
    assert reserve(stock, "a", 3) is True
    assert stock["a"] == 2


def test_reorder_point():
    assert reorder_point([2, 4, 6], 3) == 6
''',
    # long stable content, smaller than repo1's README so the two fixtures
    # differ in stable-front volume (~5k vs ~12k tokens)
    "README.md": (
        "# orderservice\n\nA small web-service repository used as the second "
        "fixture for the mini-journal replication harness.\n\n"
        + ("## Conventions\n\nA stable README section used purely for prefix "
           "volume. It documents the service's module layout, error-handling "
           "conventions, and the test protocol that agents must follow when "
           "editing this repository.\n\n" * 110)
    ),
}

REPO2_TASK_PROMPT = (
    "This service has several failing tests. Work through them one file at a "
    "time: run the tests, read the failing module, fix the bug, and re-run to "
    "confirm. Fix users.py, orders.py, and inventory.py in that order."
)

REPO2_AGENT_FILES_HINT = "Files: " + ", ".join(sorted(REPO2_FILES))


def _repo2_turn(step: int) -> dict:
    """Scripted traffic for the second fixture (bugfix shape)."""
    files = ["users.py", "orders.py", "inventory.py"]
    if step == 0:
        return {"tool": "read", "input": {"path": "README.md"},
                "result": "1-" + str(REPO2_FILES["README.md"].count("\n")) + "\n"
                          + REPO2_FILES["README.md"]}
    s = step - 1
    phase = s % 4
    f = files[min(s // 4, len(files) - 1)]
    if phase == 0:
        return {"tool": "read", "input": {"path": f},
                "result": "1-" + str(REPO2_FILES[f].count("\n")) + "\n"
                          + REPO2_FILES[f]}
    if phase == 1:
        return {"tool": "run_tests", "input": {},
                "result": "FAILED test_users.py::test_normalize_email\n"
                          "FAILED test_orders.py::test_order_total_with_tax\n"
                          "FAILED test_inventory.py::test_reserve"}
    if phase == 2:
        return {"tool": "edit", "input": {"path": f, "old_str": "a - b",
                                          "new_str": "a + b"},
                "result": f"edited {f}"}
    return {"tool": "run_tests", "input": {}, "result": "2 failed, 1 passed"}


REPO2_TURN_FN = _repo2_turn
