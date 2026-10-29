"""Tests for the dependency-free edit verification helper."""

from __future__ import annotations

from services import chat_verify


def _write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_python_syntax_error_is_an_error(tmp_path) -> None:
    path = _write(tmp_path, "broken.py", "def f(:\n    pass\n")
    issues = chat_verify.verify_file(path)
    assert issues is not None
    assert [i.severity for i in issues] == ["error"]
    assert "Python syntax error" in issues[0].message


def test_python_argparse_warns_for_undeclared_attribute(tmp_path) -> None:
    path = _write(
        tmp_path,
        "train.py",
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--max-trees', type=int)\n"
        "args = p.parse_args()\n"
        "print(args.max_trees, args.algorithm)\n",
    )
    issues = chat_verify.verify_file(path)
    assert issues is not None
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "algorithm" in issues[0].message


def test_python_argparse_does_not_warn_for_declared_attribute(tmp_path) -> None:
    path = _write(
        tmp_path,
        "ok.py",
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--algorithm')\n"
        "args = p.parse_args()\n"
        "print(args.algorithm)\n",
    )
    assert chat_verify.verify_file(path) == []


def test_yaml_json_and_shell_parse_errors(tmp_path) -> None:
    yaml_path = _write(tmp_path, "bad.yml", "root:\n  - a\n   b: c\n")
    json_path = _write(tmp_path, "bad.json", "{")
    shell_path = _write(tmp_path, "bad.sh", "if then\n")

    yaml_issues = chat_verify.verify_file(yaml_path)
    json_issues = chat_verify.verify_file(json_path)
    shell_issues = chat_verify.verify_file(shell_path)

    assert yaml_issues and yaml_issues[0].severity == "error"
    assert json_issues and json_issues[0].severity == "error"
    assert shell_issues and shell_issues[0].severity == "error"


def test_unsupported_file_returns_none(tmp_path) -> None:
    path = _write(tmp_path, "notes.md", "# notes\n")
    assert chat_verify.verify_file(path) is None


def test_format_verification_reports_ok_and_failures() -> None:
    assert chat_verify.format_verification("a.py", []) == "\nVerification: OK"
    text = chat_verify.format_verification(
        "a.py", [chat_verify.VerificationIssue("error", "boom")]
    )
    assert "Verification failed for a.py" in text
    assert "boom" in text
