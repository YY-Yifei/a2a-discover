"""发布面卫生 —— 版本库里只该有「读者需要的东西」。

这一层不测行为，测的是**仓库本身**：哪些文件进了版本库、它们是不是都该发布、
文档里的相对链接是不是还指向存在的文件。这三件事都是「清理一次就忘了」的那一类，
所以写成机检 —— 而不是发布前靠人眼扫一遍目录。

界定的基准是 `git ls-files`，不是目录名：目录名会猜错，版本库不会。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 允许发布的顶层条目。要发布新东西，先把它加到这里 —— 让「多出来一个目录」必须经过一次决定。
ALLOWED_TOP_LEVEL = {"README.md", "LICENSE", ".gitignore", "spec", "registry", "tests"}

#: 过程产物：工作区、开发稿、缓存、运行时数据、备份残留。
DEV_ARTIFACT_PATTERNS = (
    r"(^|/)\.openthree/",
    r"(^|/)design/",
    r"(^|/)__pycache__/",
    r"(^|/)\.pytest_cache/",
    r"(^|/)\.mypy_cache/",
    r"(^|/)\.ruff_cache/",
    r"\.pyc$",
    r"\.orig$",
    r"\.rej$",
    r"\.bak$",
    r"\.db$",
    r"\.db-wal$",
    r"\.db-shm$",
    r"\.sqlite3$",
    r"\.log$",
)

#: Markdown 图片 / 链接的目标（不含锚点、外链）。
LINK_TARGET = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")


def _tracked() -> list[str]:
    """返回版本库里的文件清单（相对仓库根、正斜杠）。"""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:  # pragma: no cover - 无 git 的环境
        pytest.skip("没有 git，无法界定发布面")
    if result.returncode != 0:  # pragma: no cover
        pytest.skip(f"git ls-files 失败：{result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_tracked_files_contain_no_dev_artifacts():
    """版本库里不许有过程产物 —— 尤其不许有工作日志与开发稿。"""
    tracked = _tracked()
    assert tracked, "git ls-files 为空：发布面无从界定"
    offenders = [
        f"{path}（匹配 {pattern}）"
        for path in tracked
        for pattern in DEV_ARTIFACT_PATTERNS
        if re.search(pattern, path)
    ]
    assert not offenders, "版本库里混进了不该发布的过程产物：\n" + "\n".join(offenders)


def test_tracked_files_stay_in_publish_allowlist():
    """顶层条目必须都在白名单里 —— 多出来一个目录要经过一次决定，而不是顺手提交。"""
    tracked = _tracked()
    tops = {path.split("/")[0] for path in tracked}
    outsiders = sorted(tops - ALLOWED_TOP_LEVEL)
    assert not outsiders, (
        f"发布面外出现顶层条目：{outsiders}。"
        f"确实要发布，就把它们加进 ALLOWED_TOP_LEVEL；否则它们不该进版本库。"
    )


def test_relative_markdown_links_resolve():
    """文档里的相对链接必须指向真实存在的文件 —— 删掉设计稿之后最容易断在这里。"""
    broken: list[str] = []
    for rel in _tracked():
        if not rel.endswith((".md", ".yaml", ".toml")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if not rel.endswith(".md"):
            continue
        for target in LINK_TARGET.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (ROOT / rel).parent / target.split("#", 1)[0]
            if not resolved.exists():
                broken.append(f"{rel} → {target}")
    assert not broken, "文档链接指向不存在的文件：\n" + "\n".join(broken)
