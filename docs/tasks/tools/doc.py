"""按章节号取出需求文档或技术方案中的一节，输出纯文字。

用法：python3 docs/tasks/tools/doc.py <文档> <章节号>
  文档：prd（需求文档 v0.3）、tech（技术方案 v0.1）、kb（知识库技术方案）、app（应用技术方案）
  章节号：如 8.1、4.1.3、13；取一节时包含它的全部下级小节
例：python3 docs/tasks/tools/doc.py tech 8.1
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2]
FILES = {
    "prd": DOCS / "prd/智能体开发平台-需求文档-v0.3.html",
    "tech": DOCS / "tech/智能体开发平台-技术方案-v0.1.html",
    "kb": DOCS / "tech/智能体开发平台-知识库技术方案-v0.1.html",
    "app": DOCS / "tech/智能体开发平台-应用技术方案-v0.1.html",
}
BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "pre", "table", "section", "br"}


class Section(HTMLParser):
    def __init__(self, sid):
        super().__init__()
        self.sid, self.level, self.on, self.done, self.skip, self.out = sid, None, False, False, 0, []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag in ("style", "script", "nav"):
            self.skip += 1
        if re.fullmatch(r"h[1-5]", tag):
            lv = int(tag[1])
            if dict(attrs).get("id") == self.sid:
                self.on, self.level = True, lv
            elif self.on and lv <= self.level:
                self.on, self.done = False, True
                return
            if self.on:
                self.out.append("\n" + "#" * lv + " ")
                return
        if not self.on:
            return
        if tag in BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "nav"):
            self.skip -= 1

    def handle_data(self, data):
        if self.on and not self.skip:
            self.out.append(data)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in FILES:
        sys.exit(__doc__)
    sid = "s" + sys.argv[2].strip().replace(".", "-")
    p = Section(sid)
    p.feed(FILES[sys.argv[1]].read_text(encoding="utf-8"))
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if not text:
        sys.exit(f"{sys.argv[1]} 中没有第 {sys.argv[2]} 节（id={sid}）")
    print(text)


if __name__ == "__main__":
    main()
