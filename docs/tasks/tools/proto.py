"""从 A1 原型中取出指定页面、抽屉或弹窗的 HTML。

用法：python3 docs/tasks/tools/proto.py v-users [--text]
  id 见原型中的 #v-xxx（页面）、#d-xxx（抽屉）、#m-xxx（弹窗）
  --text 只输出文字（去掉标签），用来快速看字段和文案
原型有 200KB+，不要整份读，按需取页面。
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

PROTO = Path(__file__).resolve().parents[2] / "prd/prototype/style-a1-antd.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Finder(HTMLParser):
    def __init__(self, target):
        super().__init__(convert_charrefs=False)
        self.target, self.depth, self.start, self.end = target, 0, None, None

    def handle_starttag(self, tag, attrs):
        if self.end is not None or tag in VOID:
            return
        if self.start is None:
            if dict(attrs).get("id") == self.target:
                self.start, self.depth = self.getpos(), 1
        else:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.start is None or self.end is not None or tag in VOID:
            return
        self.depth -= 1
        if self.depth == 0:
            self.end = self.getpos()


def offset(lines, pos):
    line, col = pos
    return sum(len(x) for x in lines[: line - 1]) + col


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    target, text_only = sys.argv[1].lstrip("#"), "--text" in sys.argv
    src = PROTO.read_text(encoding="utf-8")
    f = Finder(target)
    f.feed(src)
    if f.start is None:
        sys.exit(f"原型中没有 id={target}")
    lines = src.splitlines(keepends=True)
    a = offset(lines, f.start)
    b = src.index(">", offset(lines, f.end)) + 1 if f.end else len(src)
    out = src[a:b]
    out = re.sub(r"<svg\b.*?</svg>", "<svg/>", out, flags=re.S)
    if text_only:
        out = re.sub(r"<(br|/p|/div|/tr|/li|/h\d|/label)\b[^>]*>", "\n", out)
        out = re.sub(r"<(td|th)\b[^>]*>", " | ", out)
        out = re.sub(r"<[^>]+>", " ", out)
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r"\n\s*\n+", "\n", out)
    else:
        out = re.sub(r">\s*<", ">\n<", out)
    print(out.strip())


if __name__ == "__main__":
    main()
