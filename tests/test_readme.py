"""README 的口吻与要素检查 —— 门面要像公开 repo，而不是工作日志。

行为测试与文本一致性测试都不管这件事：一份文档可以技术全对，却仍在回答
「我上轮改了什么」「为什么我选了 A 而不选 B」——读者读到的是别人的开发过程，
找不到「这是什么、怎么跑、能不能用」。所以这一层锁三件事：

1. README 具备公开 repo 的必要章节（陌生人能自己走完安装到调用）；
2. 发布文档里不出现面向过程的叙事标记（设计史、对上轮改动的辩解、内部约束）；
3. 状态与前提必须写明 —— 「能用」和「什么时候能用」是两件事。

禁令条目会含禁语本身（如「MUST NOT 声称『…』」），所以检查表是
(marker, 豁免) 二元组，而不是裸 grep。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")

#: 陌生人打开 README 要能回答的问题，各对应一个必须存在的章节标题。
REQUIRED_SECTIONS = (
    "它解决什么问题",
    "API",
    "快速开始",
    "它不做什么",
    "安全模型",
    "仓库结构",
    "状态",
    "License",
)

#: 面向过程的叙事标记。这些词出现在发布文档里，说明文字在回答「我们怎么走到这儿」，
#: 而不是「这东西是什么」——读者没有这段上下文。
#: 每项为 (marker, 豁免)：同一行含豁免子串时不算违规（禁令本身要用到禁语）。
NARRATIVE_MARKERS = (
    ("早期版本", None),
    ("上一轮", None),
    ("本轮", None),
    ("这一轮", None),
    ("是刻意的", None),
    ("自动解体", None),
    ("净效果", None),
    ("不是待修的", None),
    ("CI 会先拦下", None),
    ("谁想加回", None),
    ("复核意见", None),
    ("评审意见", None),
)


def _tracked_markdown() -> list[str]:
    """版本库里的全部 Markdown（发布面以 git 为准，不靠目录名猜）。"""
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.md"],
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


def test_readme_has_the_sections_a_stranger_needs():
    """README 必须让没读过设计的读者自己走完「是什么 → 怎么跑 → 能信什么」。"""
    headings = set(re.findall(r"^##\s+(.+?)\s*$", README, re.M))
    missing = [section for section in REQUIRED_SECTIONS if section not in headings]
    assert not missing, f"README 缺少章节：{missing}；现有章节：{sorted(headings)}"


def test_readme_states_install_and_run():
    """安装与启动命令必须在 README 里 —— 只有设计说明的 README 不是 repo 门面。"""
    assert "pip install" in README
    assert "python -m adp_registry serve" in README


def test_readme_states_status_and_python_requirement():
    """Draft 状态与 Python 版本要求必须写明：读者据此判断能不能用于生产。"""
    assert "Draft" in README, "README 必须声明协议状态（Draft / stable）"
    assert re.search(r"Python 3\.\d+\+", README), "README 必须写明 Python 版本要求"


def test_readme_license_links_to_license_file():
    """License 一节不能只是文字 —— 必须指到真实的 LICENSE 文件。"""
    assert re.search(r"\]\(LICENSE\)", README), "README 的 License 应指向 LICENSE 文件"
    assert (ROOT / "LICENSE").exists()


@pytest.mark.parametrize("rel", _tracked_markdown())
def test_tracked_markdown_has_no_narrative_markers(rel):
    """发布文档不回答「我们怎么走到这儿」——那是 ADR 与提交信息的事。"""
    text = (ROOT / rel).read_text(encoding="utf-8")
    offenders: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for marker, exempt in NARRATIVE_MARKERS:
            if marker not in line:
                continue
            if exempt and exempt in line:
                continue
            offenders.append(f"{rel}:{lineno}: {marker} —— {line.strip()}")
    assert not offenders, "发布文档里出现面向过程的叙事：\n" + "\n".join(offenders)
