"""Unified diff parsing."""

from tools.diff import changed_files, parse_added_lines

DIFF = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,3 +10,5 @@ def login(user, password):
     user = load(user)
-    return token
+    token = sign(user)
+    print(password)
     return token
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 # Title
+More docs
"""


def test_added_lines_carry_file_and_new_line_numbers():
    added = parse_added_lines(DIFF)
    assert [(a.file_path, a.line_no, a.text) for a in added] == [
        ("src/auth.py", 11, "    token = sign(user)"),
        ("src/auth.py", 12, "    print(password)"),
        ("README.md", 2, "More docs"),
    ]


def test_removed_and_header_lines_are_ignored():
    added = parse_added_lines(DIFF)
    assert all("return token" not in a.text for a in added)
    assert all(not a.text.startswith("++") for a in added)


def test_bare_added_lines_without_headers_still_parse():
    added = parse_added_lines("+ print('hello')\n")
    assert len(added) == 1
    assert added[0].file_path is None
    assert added[0].text == " print('hello')"


def test_changed_files():
    assert changed_files(DIFF) == ["src/auth.py", "README.md"]
