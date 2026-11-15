#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
链接正文抓取（2026-11-15 新增，自定义文案支持贴链接）

为什么不用 hf-line 那版正则：`(.*?)</div>` 是非贪婪，遇到公众号正文里成堆的嵌套
<div>/<section> 会在第一个闭合标签处截断，抓到的字往往不到原文一半。
这里用 HTMLParser 按「同名标签深度计数」提取容器，抓到不完整再回退整页提取。

用法：
  from fetch_url import fetch_article
  title, text = fetch_article("https://mp.weixin.qq.com/s/xxx")
"""
import gzip
import re
import urllib.request
import zlib
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 站点 → 正文容器候选（id 或 class 片段）
SITES = [
    ("mp.weixin.qq.com", {"ids": ["js_content"], "cls": ["rich_media_content"]}),
    ("zhuanlan.zhihu.com", {"ids": [], "cls": ["Post-RichText", "RichText"]}),
    ("zhihu.com", {"ids": [], "cls": ["Post-RichText", "RichText"]}),
    ("toutiao.com", {"ids": ["article-content"], "cls": ["article-content"]}),
    ("36kr.com", {"ids": [], "cls": ["articleDetailContent"]}),
    ("jianshu.com", {"ids": [], "cls": ["show-content", "article"]}),
    ("xiaohongshu.com", {"ids": [], "cls": []}),      # 基本抓不到，走报错提示
    ("douyin.com", {"ids": [], "cls": []}),
]
GENERIC = {"ids": ["article", "content", "main", "artibody"],
           "cls": ["article-content", "post-content", "entry-content", "markdown-body",
                   "rich_media_content", "article-body", "content-body"]}

BLOCK = {"p", "div", "section", "article", "li", "tr", "blockquote", "h1", "h2", "h3",
         "h4", "h5", "h6", "br", "hr", "figure", "figcaption", "dd", "dt"}
SKIP = {"script", "style", "noscript", "svg", "iframe", "form", "button", "select",
        "nav", "header", "footer", "aside", "template"}
# 通用提取会把导航条混进来，这些整行直接丢
NAV = set("""首页 新闻 体育 财经 娱乐 科技 视频 图片 更多 登录 注册 无障碍 听全文 评论 分享 收藏
返回顶部 客户端 下载 打开APP 打开app 广告 举报 相关推荐 热门推荐 猜你喜欢 上一页 下一页 展开全文""".split())

BAD_HINTS = ["环境异常", "访问过于频繁", "请输入验证码", "完成验证", "请在微信客户端打开",
             "该内容已被发布者删除", "此内容因违规无法查看", "参数错误", "页面不存在"]


class _Ex(HTMLParser):
    """提取 HTML 正文：优先命中容器，否则整页（跳过 nav/footer 等）"""

    def __init__(self, ids, cls):
        super().__init__(convert_charrefs=True)
        self.ids = set(ids)
        self.cls = [c.lower() for c in cls]
        self.mode = "generic" if not (self.ids or self.cls) else "search"
        self.capture = False
        self.tagname = ""
        self.cnt = 0
        self.skip = 0
        self.parts = []
        self.title = ""
        self._t = 0

    # ---- 开始标签
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._t += 1
            return
        if tag in SKIP and not self.capture:
            self.skip += 1
            return
        if self.skip:
            return

        if self.mode == "search":
            hit = False
            if a.get("id") in self.ids:
                hit = True
            else:
                c = (a.get("class") or "").lower()
                hit = any(k in c for k in self.cls) if c else False
            if hit:
                self.mode = "capture"
                self.capture = True
                self.tagname = tag
                self.cnt = 1
                self._nl()
                return

        if self.mode == "capture":
            if tag == self.tagname:
                self.cnt += 1
            if tag in BLOCK:
                self._nl()
        elif tag in BLOCK:
            self._nl()

    def handle_startendtag(self, tag, attrs):
        if self.capture and tag in ("br", "hr"):
            self._nl()

    # ---- 结束标签
    def handle_endtag(self, tag):
        if tag == "title":
            self._t = max(0, self._t - 1)
            return
        if tag in SKIP and self.skip and not self.capture:
            self.skip -= 1
            return
        if self.skip:
            return
        if self.mode == "capture" and tag == self.tagname:
            self.cnt -= 1
            if self.cnt <= 0:
                self.capture = False
                self.mode = "done"
                return
        if tag in BLOCK:
            self._nl()

    # ---- 文本
    def handle_data(self, data):
        if self._t:
            self.title += data
            return
        if self.skip:
            return
        if self.capture or (self.mode == "generic"):
            s = data.strip()
            if s:
                self.parts.append(s)

    def _nl(self):
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def text(self, keep_nav=False):
        raw = ""
        for p in self.parts:
            if p == "\n":
                raw += "\n"
            else:
                raw += p + (" " if not raw.endswith("\n") else "")
        lines = []
        for ln in raw.split("\n"):
            ln = re.sub(r"[ \t\u00a0\u3000]+", " ", ln).strip()
            if len(ln) < 2 or ln in NAV:
                continue
            lines.append(ln)
        return "\n".join(lines)


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://mp.weixin.qq.com/" if "weixin" in url else url,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        cs = r.headers.get_content_charset()
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif "deflate" in enc:
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            pass
    for c in [cs, "utf-8", "gbk", "gb18030"]:
        if not c:
            continue
        try:
            return raw.decode(c, "ignore")
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def fetch_article(url):
    """返回 (title, text)。抓不到就抛 ValueError，带人话原因。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("链接必须以 http:// 或 https:// 开头")
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()

    cfg = dict(GENERIC)
    for h, c in SITES:
        if host.endswith(h):
            cfg = c if (c["ids"] or c["cls"]) else {"ids": [], "cls": []}
            break

    try:
        html = _get(url)
    except Exception as e:
        raise ValueError("抓不到这个链接（%s）。如果页面需要登录，请直接把正文复制进来。" % e)

    if not html or len(html) < 500:
        raise ValueError("这个链接返回的内容太少（可能被拦截或需要登录），请把正文复制进来。")

    for h in BAD_HINTS:
        if h in html[:6000]:
            raise ValueError("这个页面打不开：出现「%s」。请在浏览器里确认能正常看，或直接贴正文。" % h)

    # 1) 容器提取
    ex = _Ex(cfg["ids"], cfg["cls"])
    txt = ""
    title = ""
    try:
        ex.feed(html)
        txt = ex.text()
        title = (ex.title or "").strip()
    except Exception:
        pass

    # 2) 不够长就整页提取
    if len(txt) < 200:
        ex2 = _Ex([], [])
        try:
            ex2.feed(html)
            t2 = ex2.text()
            if len(t2) > len(txt):
                txt = t2
                title = (ex2.title or title).strip()
        except Exception:
            pass

    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    if len(txt) < 200:
        raise ValueError("只抓到 %d 个字，多半是动态渲染或被拦。请把正文复制进文本框。" % len(txt))

    if len(txt) > 12000:
        txt = txt[:12000]

    title = re.sub(r"\s+", " ", title)[:80]
    if not title:
        first = [x for x in txt.split("\n") if len(x) >= 6]
        title = first[0][:80] if first else host
    return title, txt


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        try:
            t, b = fetch_article(u)
            print("== %s\n标题：%s\n字数：%d\n---\n%s\n" % (u, t, len(b), b[:600]))
        except Exception as e:
            print("== %s\nERR: %s\n" % (u, e))
