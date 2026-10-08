import json
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree


def run(args, check=True, stdin=None):
    proc = subprocess.run(
        args, capture_output=True, text=True, input=stdin, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{args[0]} failed")
    return proc.stdout or ""


def normalize(path, src_root):
    path = path.replace("\\", "/").lstrip("./")
    root = src_root.strip("/")
    if not root:
        return path
    marker = f"/{root}/"
    idx = path.find(marker)
    if idx >= 0:
        return path[idx + 1 :]
    if path == root or path.startswith(f"{root}/"):
        return path
    return f"{root}/{path}"


def parse_xml(path, src_root):
    root = ElementTree.parse(path).getroot()
    covered = int(root.get("lines-covered") or 0)
    total = int(root.get("lines-valid") or 0)
    files = {}
    for klass in root.iter("class"):
        name = klass.get("filename")
        if not name:
            continue
        files[normalize(name, src_root)] = {
            int(line.get("number")): int(line.get("hits") or 0) > 0
            for line in klass.iter("line")
            if line.get("number")
        }
    return covered, total, files


def parse_lcov(path, src_root):
    files = {}
    covered = total = 0
    current = None
    for raw in Path(path).read_text(errors="replace").splitlines():
        if raw.startswith("SF:"):
            current = normalize(raw[3:].strip(), src_root)
            files[current] = {}
        elif raw.startswith("DA:") and current is not None:
            parts = raw[3:].split(",")
            if len(parts) >= 2:
                files[current][int(parts[0])] = int(parts[1]) > 0
        elif raw.startswith("LF:"):
            total += int(raw[3:] or 0)
        elif raw.startswith("LH:"):
            covered += int(raw[3:] or 0)
    return covered, total, files


def parse_report(path, src_root):
    if path.endswith(".info") or path.endswith(".lcov"):
        return parse_lcov(path, src_root)
    return parse_xml(path, src_root)


def parse_target(value):
    if value is None:
        return None
    text = str(value).strip().rstrip("%").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def patch_lines(patch):
    lines = set()
    if not patch:
        return lines
    for match in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch, re.M):
        start = int(match.group(1))
        count = int(match.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


def pr_files(repo, pr):
    raw = run(["gh", "api", f"repos/{repo}/pulls/{pr}/files", "--paginate", "--slurp"])
    pages = json.loads(raw) if raw.strip() else []
    files = []
    for page in pages:
        files.extend(page)
    return files


def resolve_pr(repo, pr, head_sha):
    if pr and pr.isdigit():
        return pr
    if not head_sha:
        return ""
    raw = run(
        [
            "gh",
            "api",
            f"repos/{repo}/commits/{head_sha}/pulls",
            "--jq",
            '[.[] | select(.state == "open")][0].number // empty',
        ],
        check=False,
    ).strip()
    return raw if raw.isdigit() else ""


def rate(covered, total):
    return (100.0 * covered / total) if total else None


def pct(covered, total):
    value = rate(covered, total)
    return "n/a" if value is None else f"{value:.2f}%"


def verdict(covered, total, target):
    value = rate(covered, total)
    if target is None or value is None:
        return "—"
    return "✅" if value + 1e-9 >= target else "❌"


def target_label(target):
    return "—" if target is None else f"{target:.2f}%"


def compact(lines):
    lines = sorted(set(lines))
    if not lines:
        return ""
    ranges = []
    start = prev = lines[0]
    for number in lines[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = number
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


def truncate(text, limit=800):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def test_name(case):
    classname = (case.get("classname") or "").strip()
    name = (case.get("name") or "").strip()
    return "::".join(part for part in (classname, name) if part) or "(unnamed)"


def parse_junit(paths):
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    failed = []
    slowest = []
    for path in paths:
        if not path or not Path(path).exists():
            continue
        root = ElementTree.parse(path).getroot()
        for case in root.iter("testcase"):
            name = test_name(case)
            try:
                duration = float(case.get("time") or 0.0)
            except ValueError:
                duration = 0.0
            summary["tests"] += 1
            summary["time"] += duration
            slowest.append((name, duration))
            node = case.find("failure")
            kind = "failure"
            if node is None:
                node = case.find("error")
                kind = "error"
            if node is not None:
                summary["failures" if kind == "failure" else "errors"] += 1
                detail = (node.get("message") or "").strip()
                body = (node.text or "").strip()
                if body and detail and not body.startswith(detail):
                    detail = f"{detail}\n{body}"
                elif body and not detail:
                    detail = body
                failed.append((name, kind, truncate(detail)))
            elif case.find("skipped") is not None:
                summary["skipped"] += 1
    slowest.sort(key=lambda item: item[1], reverse=True)
    return summary, failed, slowest


def coverage_section(report, src_root, repo, pr, project_target, patch_target):
    covered, total, files = parse_report(report, src_root)
    patch_covered = patch_total = 0
    rows = []
    for item in pr_files(repo, pr):
        path = normalize(item.get("filename", ""), src_root)
        hits = files.get(path)
        if not hits:
            continue
        changed = patch_lines(item.get("patch"))
        executable = sorted(number for number in changed if number in hits)
        if not executable:
            continue
        missing = [number for number in executable if not hits[number]]
        patch_total += len(executable)
        patch_covered += len(executable) - len(missing)
        file_covered = sum(1 for value in hits.values() if value)
        rows.append((path, pct(file_covered, len(hits)), compact(missing)))

    lines = ["### 覆盖率", ""]
    lines.append("| 指标 | 覆盖率 | 明细 | 目标 | 结论 |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(
        f"| 项目 | {pct(covered, total)} | {covered}/{total} 行 | "
        f"{target_label(project_target)} | {verdict(covered, total, project_target)} |"
    )
    lines.append(
        f"| 补丁 | {pct(patch_covered, patch_total)} | {patch_covered}/{patch_total} 变更行 | "
        f"{target_label(patch_target)} | {verdict(patch_covered, patch_total, patch_target)} |"
    )
    lines.append("")
    if rows:
        lines.append(f"<details><summary>变更文件覆盖情况（{len(rows)}）</summary>")
        lines.append("")
        lines.append("| 文件 | 文件覆盖率 | 未覆盖变更行 |")
        lines.append("| --- | --- | --- |")
        for path, file_pct, missing in rows:
            lines.append(f"| `{path}` | {file_pct} | {missing or '—'} |")
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)


def tests_section(summary, failed, slowest):
    passed = summary["tests"] - summary["failures"] - summary["errors"] - summary["skipped"]
    lines = ["### 测试", ""]
    lines.append("| 用例 | 通过 | 失败 | 错误 | 跳过 | 耗时 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    lines.append(
        f"| {summary['tests']} | {passed} | {summary['failures']} | "
        f"{summary['errors']} | {summary['skipped']} | {summary['time']:.1f}s |"
    )
    lines.append("")
    if failed:
        lines.append(f"<details><summary>失败用例（{len(failed)}）</summary>")
        lines.append("")
        for name, kind, message in failed[:10]:
            lines.append(f"**`{name}`** — {kind}")
            if message:
                lines.append("")
                lines.append(f"~~~text\n{message}\n~~~")
            lines.append("")
        if len(failed) > 10:
            lines.append(f"其余 {len(failed) - 10} 个失败用例见 run logs。")
            lines.append("")
        lines.append("</details>")
        lines.append("")
    else:
        lines.append("✅ 全部用例通过。")
        lines.append("")
    if summary["tests"]:
        lines.append("<details><summary>最慢用例</summary>")
        lines.append("")
        lines.append("| 用例 | 耗时 |")
        lines.append("| --- | --- |")
        for name, duration in slowest[:8]:
            lines.append(f"| `{name}` | {duration:.2f}s |")
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)


def build_body(title, report, src_root, junit_paths, repo, pr, run_url, project_target, patch_target):
    sections = []
    if report and Path(report).exists():
        sections.append(coverage_section(report, src_root, repo, pr, project_target, patch_target))
    summary, failed, slowest = parse_junit(junit_paths)
    if summary["tests"]:
        sections.append(tests_section(summary, failed, slowest))
    if not sections:
        return None
    footer = "由 CI 本地生成，不依赖 Codecov 上传额度"
    if run_url:
        footer += f" · [run logs]({run_url})"
    head = f"## CI 报告 · {title}"
    tail = f"<sub>{footer}</sub>"
    return "\n\n".join([head, "\n\n".join(sections), tail]).strip()


def upsert_comment(repo, pr, marker, body):
    raw = run(["gh", "api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp"])
    pages = json.loads(raw) if raw.strip() else []
    comment_id = None
    for page in pages:
        for comment in page:
            if marker in (comment.get("body") or ""):
                comment_id = comment.get("id")
                break
        if comment_id:
            break
    if comment_id:
        run(
            ["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "-F", "body=@-"],
            stdin=body,
        )
        return f"updated {comment_id}"
    run(
        ["gh", "api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments", "-F", "body=@-"],
        stdin=body,
    )
    return "created"


def main():
    repo = os.environ.get("THPF_REPO", "")
    report = os.environ.get("COVERAGE_REPORT", "")
    src_root = os.environ.get("COVERAGE_SRC_ROOT", "")
    junit_paths = os.environ.get("JUNIT_REPORTS", "").split()
    title = os.environ.get("REPORT_TITLE", "ci")
    marker = os.environ.get("REPORT_MARKER", "ci-report")
    run_url = os.environ.get("RUN_URL", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    project_target = parse_target(os.environ.get("COVERAGE_PROJECT_TARGET"))
    patch_target = parse_target(os.environ.get("COVERAGE_PATCH_TARGET"))
    dry_run = os.environ.get("DRY_RUN") == "1"

    if not repo:
        print("skip: THPF_REPO not set")
        return 0
    pr = resolve_pr(repo, os.environ.get("PR_NUMBER", ""), head_sha)
    if not pr:
        print("skip: no open PR for this commit")
        return 0
    body = build_body(title, report, src_root, junit_paths, repo, pr, run_url, project_target, patch_target)
    if body is None:
        print("skip: no coverage report or test results found")
        return 0
    body = f"{body}\n\n<!-- {marker} -->\n"
    if dry_run:
        print(body)
        return 0
    print(upsert_comment(repo, pr, marker, body))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ci report failed: {exc}", file=sys.stderr)
        sys.exit(1)
