#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书图文生产线 · 渲染引擎
录入 note.json → 输出 1080x1440 的 9 张图

设计原则（2026-11-13 v2，已按平台红线调整）：
  1. 首图必须有真实视觉（成片墙/截图），禁止纯文字海报
  2. 每页底部有「进度 + 下一张预告」→ 制造滑动动力
  3. 每页只讲一件事，字数硬上限，超了自动缩字号
  4. 第 8 页固定是「这一期我最想让人记住的一点」→ 讲判断，不给可复制资产/提示词
  5. 第 9 页固定说明「我在做什么」→ 不承诺给读者东西、不导流

页面 kind 顺序（9 页）：result / hook / opinion / detail / behind / grid / quote / note / follow
"""
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1440
BRAND = os.environ.get("XHS_BRAND") or "我的图文账号"
M = 88                       # 左右安全边距
TOP = 150                    # 内容区上界
BOT = 1244                   # 内容区下界

BG      = (253, 252, 250)
INK     = (22, 24, 28)
SUB     = (112, 114, 120)
FAINT   = (168, 170, 176)
LINE    = (229, 227, 222)
DEEP    = (14, 59, 51)       # 墨绿
ACC     = (232, 84, 58)      # 朱红
DARK_BG = (18, 22, 26)
DARK_FG = (240, 238, 232)

NO_HEAD = "，。、；：？！%）】》」』·,.;:?!)]}"
NO_TAIL = "（【《「『([{“"


def _font_candidates(bold):
    """按平台铺候选字体。(路径, ttc内的索引)。可用 XHS_FONT_DIR / XHS_FONT_FILE 覆盖。"""
    env_file = os.environ.get("XHS_FONT_FILE")
    if env_file and os.path.exists(env_file):
        return [(env_file, int(os.environ.get("XHS_FONT_INDEX", "0")))]
    d = os.environ.get("XHS_FONT_DIR")
    names = ["NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc",
             "NotoSansSC-Bold.otf" if bold else "NotoSansSC-Regular.otf",
             "SourceHanSansSC-Bold.otf" if bold else "SourceHanSansSC-Regular.otf",
             "msyhbd.ttc" if bold else "msyh.ttc"]
    dirs = [d] if d else []
    dirs += ["/usr/share/fonts/opentype/noto",
             "/usr/share/fonts/opentype/noto-cjk",
             "/usr/share/fonts/truetype/noto",
             "/usr/share/fonts/truetype/noto-cjk",
             "/usr/share/fonts/opentype/source-han-sans",
             "/System/Library/Fonts", "/Library/Fonts",
             "C:/Windows/Fonts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets/fonts")]
    out = []
    for dd in dirs:
        if not dd:
            continue
        for n in names:
            p = os.path.join(dd, n)
            if os.path.exists(p):
                # Noto CJK 的 ttc 里 index=2 才是简体中文（SC）
                idx = 2 if "CJK" in n else 0
                out.append((p, idx))
    return out


_FONT_CACHE = {}


def F(size, bold=False):
    if bold not in _FONT_CACHE:
        cands = _font_candidates(bold)
        if not cands:
            raise SystemExit(
                "[font] 找不到中文字体。装一个即可，例如：\n"
                "  Ubuntu/Debian: sudo apt install fonts-noto-cjk\n"
                "  CentOS/RHEL : sudo yum install google-noto-sans-cjk-ttc-fonts\n"
                "或把任意中文字体放到 assets/fonts/ 并设置 XHS_FONT_FILE=<路径>")
        err = None
        for p, idx in cands:
            try:
                _FONT_CACHE[bold] = (p, idx, ImageFont.truetype(p, size, index=idx))
                break
            except Exception as e:  # 该字体索引不可用时换下一个
                err = e
        else:
            raise SystemExit("[font] 字体加载失败：%s" % err)
    p, idx, _ = _FONT_CACHE[bold]
    return ImageFont.truetype(p, size, index=idx)


def tw(d, s, f):
    return d.textlength(s, font=f)


def _s(v):
    """字段容错：模型偶尔把 next 之类写成 true/false/数字，这里统一转成可渲染的字符串"""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return " ".join(_s(x) for x in v)
    return str(v)


def wrap_cn(d, text, font, maxw):
    """中文换行：按字贪心 + 禁则（标点不落行首），支持 \n 硬换行"""
    out = []
    text = _s(text)
    for seg in text.split("\n"):
        cur = ""
        for ch in seg:
            cand = cur + ch
            if tw(d, cand, font) <= maxw:
                cur = cand
            elif ch in NO_HEAD and cur:
                cur = cand                     # 标点挂在行末，允许轻微溢出
            else:
                if cur and cur[-1] in NO_TAIL:
                    out.append(cur[:-1])
                    cur = cur[-1] + ch
                else:
                    out.append(cur)
                    cur = ch
        if cur:
            out.append(cur)
    # 末行孤字回退
    if len(out) >= 2 and len(out[-1]) <= 2 and len(out[-2]) > 3:
        out[-1] = out[-2][-1] + out[-1]
        out[-2] = out[-2][:-1]
    return out


def fit(d, text, maxw, max_lines, smax, smin, bold=True):
    """从 smax 递减到 smin，取第一个不超过 max_lines 的尺寸"""
    text = _s(text)
    if not text:
        return F(smin, bold), []
    size = smax
    while size > smin:
        f = F(size, bold)
        lines = wrap_cn(d, text, f, maxw)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = F(smin, bold)
    lines = wrap_cn(d, text, f, maxw)
    return f, lines[:max_lines]


def draw_lines(d, lines, f, x, y, fill, lh=1.42, align="left", maxw=None):
    size = f.size
    step = int(size * lh)
    for ln in lines:
        if align == "center":
            w = tw(d, ln, f)
            d.text(((W - w) / 2 if maxw is None else x + (maxw - w) / 2, y), ln, font=f, fill=fill)
        else:
            d.text((x, y), ln, font=f, fill=fill)
        y += step
    return y


# ---------------------------------------------------------------- 视觉素材

class Pool:
    """真实成片封面池"""

    def __init__(self, dirs):
        self.items = []
        for dd in dirs:
            if not os.path.isdir(dd):
                continue
            for fn in os.listdir(dd):
                if fn.lower().endswith(("-cover.jpg", ".jpg", ".jpeg", ".png")):
                    p = os.path.join(dd, fn)
                    try:
                        if os.path.getsize(p) > 20000:
                            self.items.append(p)
                    except OSError:
                        pass
        self.items.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    def pick(self, n, spread=True):
        if not self.items:
            return []
        if len(self.items) <= n:
            return self.items
        if spread:                             # 均匀取样，画面不雷同
            k = len(self.items) / n
            return [self.items[min(int(i * k), len(self.items) - 1)] for i in range(n)]
        return self.items[:n]


def cover_crop(im, w, h, bias=0.10):
    r = max(w / im.width, h / im.height)
    im = im.resize((max(w, int(im.width * r + 0.5)), max(h, int(im.height * r + 0.5))), Image.LANCZOS)
    l = (im.width - w) // 2
    t = int((im.height - h) * bias)            # 偏上取，保住封面标题
    return im.crop((l, t, l + w, t + h))


def draw_wall(im, d, paths, x0, y0, maxw, maxh, cols=3, gap=18, radius=14,
              ratio=1.0, bias=0.08):
    """按列宽定格子尺寸（比例固定），整体居中，绝不压扁"""
    if not paths:
        d.rounded_rectangle((x0, y0, x0 + maxw, y0 + maxh), radius=22, fill=(243, 241, 237))
        d.text((x0 + 40, y0 + maxh / 2 - 26), "此处放真实截图", font=F(38), fill=FAINT)
        return
    n = len(paths)
    rows = (n + cols - 1) // cols
    cw = (maxw - gap * (cols - 1)) // cols
    ch = int(cw * ratio)
    if rows * ch + gap * (rows - 1) > maxh:          # 超高才等比缩
        ch = (maxh - gap * (rows - 1)) // rows
        cw = int(ch / ratio)
    total_w = cols * cw + gap * (cols - 1)
    total_h = rows * ch + gap * (rows - 1)
    ox = x0 + (maxw - total_w) // 2
    oy = y0 + (maxh - total_h) // 2
    for i, p in enumerate(paths):
        r, c = divmod(i, cols)
        cx = ox + c * (cw + gap)
        cy = oy + r * (ch + gap)
        try:
            src = cover_crop(Image.open(p).convert("RGB"), cw, ch, bias)
        except Exception:
            continue
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=radius, fill=255)
        im.paste(src, (cx, cy), mask)
        d.rounded_rectangle((cx, cy, cx + cw - 1, cy + ch - 1), radius=radius,
                            outline=(232, 230, 225), width=2)


def draw_shot(im, d, path, box, radius=22):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if not path or not os.path.exists(path):
        d.rounded_rectangle(box, radius=radius, fill=(243, 241, 237))
        d.text((x0 + 40, (y0 + y1) / 2 - 26), "此处放真实截图", font=F(38), fill=FAINT)
        return
    im.paste(cover_crop(Image.open(path).convert("RGB"), w, h), (x0, y0))
    d.rounded_rectangle(box, radius=radius, outline=(228, 226, 221), width=3)


# ---------------------------------------------------------------- 框架

def frame(im, d, page, idx, total, kicker):
    if kicker:
        fk, lk = fit(d, kicker, W - M * 2 - 180, 1, 32, 24, bold=False)
        d.text((M, 74), lk[0] if lk else "", font=fk, fill=FAINT)
    tag = f"{idx:02d} / {total:02d}"
    d.text((W - M - tw(d, tag, F(32)), 74), tag, font=F(32), fill=FAINT)

    # 底部进度 + 下一张预告
    d.rounded_rectangle((M, 1346, W - M, 1350), radius=2, fill=LINE)
    p = (W - M - M) * idx / total
    if p > 4:
        d.rounded_rectangle((M, 1346, M + p, 1350), radius=2, fill=DEEP)
    nxt = page.get("next")
    if nxt:
        d.text((M, 1282), "↓", font=F(34, True), fill=ACC)
        f, ls = fit(d, nxt, W - M * 2 - 56, 1, 40, 28, bold=False)
        d.text((M + 46, 1280), ls[0] if ls else "", font=f, fill=SUB)


# ---------------------------------------------------------------- 版式

def p_result(im, d, page, pool):
    """首图：真实成片墙打底 + 主标题 + 数字"""
    y = TOP - 6
    lead = page.get("lead", "")
    if lead:
        d.text((M, y), lead, font=F(44, False), fill=SUB)
        y += 60

    num = str(page.get("num", ""))
    unit = page.get("unit", "")
    if num:
        fn = F(148, True)
        d.text((M - 4, y - 26), num, font=fn, fill=DEEP)
        nw = tw(d, num, fn)
        fu, lu = fit(d, unit, W - M * 2 - nw - 30, 1, 44, 26, bold=True)
        d.text((M + nw + 16, y + 66), lu[0] if lu else "", font=fu, fill=DEEP)
        y += 158
    else:
        y += 10

    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2 - 20, 2, 94, 58, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.20)
    y += 30

    sub = page.get("sub", "")
    wall_bottom = BOT - 6
    if sub:
        wall_bottom -= 104
    vis = page.get("visual", "wall")
    if vis == "wall":
        draw_wall(im, d, pool.pick(6), M, y, W - M * 2, wall_bottom - y, cols=3, bias=0.05)
    elif vis and vis != "none":
        draw_shot(im, d, vis, (M, y, W - M, max(y + 240, wall_bottom)))

    if sub:
        f2, ls2 = fit(d, sub, W - M * 2, 2, 46, 32, bold=False)
        draw_lines(d, ls2, f2, M, BOT - 84, SUB, lh=1.34)


def p_hook(im, d, page):
    """第二页：只留人，字极少，留白极大"""
    big = page.get("big", "")
    f, ls = fit(d, big, W - M * 2, 3, 122, 68, bold=True)
    total_h = len(ls) * int(f.size * 1.3)
    y = (H - total_h) // 2 - 90
    d.rounded_rectangle((M, y - 92, M + 92, y - 84), radius=4, fill=ACC)
    draw_lines(d, ls, f, M, y, INK, lh=1.3)

    lines = page.get("lines") or []
    if lines:
        fy = y + total_h + 78
        step = 118
        for ln in lines[:4]:
            d.rounded_rectangle((M, fy + 22, M + 56, fy + 78), radius=8, fill=(235, 232, 226))
            d.rounded_rectangle((M, fy + 40, M + 20, fy + 60), radius=4, fill=DEEP)
            f2, ls2 = fit(d, ln, W - M * 2 - 84, 2, 50, 34, bold=False)
            draw_lines(d, ls2, f2, M + 84, fy + 18, INK, lh=1.4)
            fy += step + (len(ls2) - 1) * 46
        if fy < BOT - 60:
            d.rounded_rectangle((M, BOT - 8, W - M, BOT - 2), radius=3, fill=LINE)


def p_step(im, d, page):
    """过程页：序号 + 标题 + 正文 (+ 可选小截图)"""
    y = TOP
    no = str(page.get("no", "01"))
    fz = F(46, True)
    if len(no) <= 2:                                    # 01 / 02 —— 圆形
        d.ellipse((M, y, M + 96, y + 96), fill=DEEP)
        d.text((M + 48 - tw(d, no, fz) / 2, y + 20), no, font=fz, fill=(255, 255, 255))
        y += 148
    else:                                               # 「取舍」这类二字标签 —— 胶囊
        fz = F(38, True)
        pw = tw(d, no, fz) + 64
        d.rounded_rectangle((M, y + 8, M + pw, y + 84), radius=38, fill=DEEP)
        d.text((M + 32, y + 20), no, font=fz, fill=(255, 255, 255))
        y += 128

    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2, 2, 84, 54, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.26)
    y += 46

    body = page.get("body", "")
    shot = page.get("visual")
    has_shot = bool(shot) and shot not in ("none", "wall")
    body_bottom = BOT - (620 if has_shot else 0)
    if body:
        f2, ls2 = fit(d, body, W - M * 2, 6, 50, 34, bold=False)
        draw_lines(d, ls2, f2, M, y, SUB, lh=1.5)

    if has_shot:
        draw_shot(im, d, shot, (M, BOT - 560, W - M, BOT))

    note = page.get("note")
    if note:
        f3, ls3 = fit(d, note, W - M * 2, 2, 40, 30, bold=True)
        draw_lines(d, ls3, f3, M, BOT - 66, ACC, lh=1.34)


def p_grid(im, d, page, pool):
    """成品墙页：一行标题 + 九宫格"""
    y = TOP
    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2, 2, 76, 52, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.26)
    y += 50
    n = int(page.get("n", 9))
    cols = int(page.get("cols", 3))
    draw_wall(im, d, pool.pick(n), M, y, W - M * 2,
              BOT - y - (92 if page.get("sub") else 0),
              cols=cols, gap=14, bias=0.04)
    if page.get("sub"):
        f2, ls2 = fit(d, page["sub"], W - M * 2, 2, 46, 32, bold=False)
        draw_lines(d, ls2, f2, M, BOT - 84, SUB, lh=1.34)


def p_quote(im, d, page):
    """金句页：浅底 + 超大字 + 朱红竖条（与第8页深色拉开节奏）"""
    big = page.get("big", "")
    f, ls = fit(d, big, W - M * 2 - 40, 4, 104, 60, bold=True)
    th = len(ls) * int(f.size * 1.34)
    y = (H - th) // 2 - 70
    d.rounded_rectangle((M, y + 12, M + 9, y + th - 12), radius=5, fill=ACC)
    draw_lines(d, ls, f, M + 44, y, INK, lh=1.34)
    sub = page.get("sub")
    if sub:
        y2 = y + th + 76
        d.rounded_rectangle((M + 44, y2 - 34, M + 44 + 88, y2 - 30), radius=2, fill=DEEP)
        f2, ls2 = fit(d, sub, W - M * 2 - 44, 3, 44, 30, bold=False)
        draw_lines(d, ls2, f2, M + 44, y2, SUB, lh=1.46)
    return False


def p_opinion(im, d, page):
    """判断页：我的一个看法（不是步骤、不是教程）"""
    y = TOP
    d.rounded_rectangle((M, y + 10, M + 84, y + 16), radius=3, fill=ACC)
    y += 56
    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2, 3, 92, 56, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.26)
    y += 52
    d.rounded_rectangle((M, y - 26, W - M, y - 24), radius=1, fill=LINE)
    y += 20

    body = page.get("body", "")
    if body:
        f2, ls2 = fit(d, body, W - M * 2, 6, 54, 36, bold=False)
        draw_lines(d, ls2, f2, M, y, SUB, lh=1.56)

    note = page.get("note")
    if note:
        f3, ls3 = fit(d, note, W - M * 2, 2, 42, 30, bold=True)
        draw_lines(d, ls3, f3, M, BOT - 66, ACC, lh=1.34)


def p_detail(im, d, page):
    """摘录页：原文一段 + 出处 + 我的理解"""
    y = TOP - 10
    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2, 2, 66, 46, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.24)
    y += 46

    quote = page.get("quote", "")
    src = page.get("source", "")
    mine = page.get("mine", "")

    if quote:
        fq, lsq = fit(d, quote, W - M * 2 - 96, 6, 62, 40, bold=False)
        boxh = len(lsq) * int(fq.size * 1.6) + 150
        d.rounded_rectangle((M, y, W - M, y + boxh), radius=20, fill=(246, 244, 238))
        d.rounded_rectangle((M, y, M + 7, y + boxh), radius=4, fill=DEEP)
        d.text((M + 46, y + 26), "“", font=F(96, True), fill=(206, 200, 188))
        yy = y + 88
        yy = draw_lines(d, lsq, fq, M + 48, yy, (36, 38, 42), lh=1.6)
        if src:
            fs, _ = fit(d, "—— " + src, W - M * 2 - 96, 1, 36, 28, bold=False)
            d.text((M + 48, yy + 18), "—— " + src, font=fs, fill=SUB)
        y += boxh + 56

    if mine:
        d.ellipse((M, y + 14, M + 18, y + 32), fill=ACC)
        fm, lsm = fit(d, mine, W - M * 2 - 44, 4, 46, 32, bold=False)
        draw_lines(d, lsm, fm, M + 44, y, INK, lh=1.5)


def p_note(im, d, page):
    """收尾观点页：深底 + 这一期最想让人记住的一点（原资产页，已去掉可复制内容）"""
    d.rectangle((0, 0, W, H), fill=DARK_BG)
    y = TOP - 24
    badge = page.get("badge", "这一期")
    bw = tw(d, badge, F(32, True)) + 44
    d.rounded_rectangle((M, y, M + bw, y + 62), radius=31, fill=ACC)
    d.text((M + 22, y + 13), badge, font=F(32, True), fill=(255, 255, 255))
    y += 104

    title = page.get("title", "")
    f, ls = fit(d, title, W - M * 2, 2, 76, 50, bold=True)
    y = draw_lines(d, ls, f, M, y, DARK_FG, lh=1.28)
    y += 52

    body = page.get("body") or page.get("code") or ""
    if body:
        f2 = F(40, False)
        ls2 = wrap_cn(d, body, f2, W - M * 2 - 56)
        boxh = len(ls2) * int(40 * 1.62) + 64
        d.rounded_rectangle((M, y, W - M, min(y + boxh, BOT)), radius=18,
                            fill=(30, 36, 42), outline=(58, 66, 76), width=2)
        d.rounded_rectangle((M + 26, y + 30, M + 32, min(y + boxh - 30, BOT - 30)), radius=3, fill=DEEP)
        draw_lines(d, ls2, f2, M + 56, y + 34, (214, 220, 226), lh=1.62)


p_asset = p_note          # 兼容旧 json


def p_follow(im, d, page):
    """收尾页：我在做什么（不承诺给读者东西）"""
    y = TOP
    head = page.get("head", "我在做什么")
    f, ls = fit(d, head, W - M * 2, 2, 78, 54, bold=True)
    y = draw_lines(d, ls, f, M, y, INK, lh=1.26)
    y += 56

    for i, item in enumerate(page.get("promise") or []):
        d.ellipse((M, y + 14, M + 22, y + 36), fill=ACC)
        f2, ls2 = fit(d, item, W - M * 2 - 52, 2, 48, 32, bold=False)
        draw_lines(d, ls2, f2, M + 52, y, INK, lh=1.4)
        y += max(2, len(ls2)) * int(f2.size * 1.4) + 40

    y += 20
    d.rounded_rectangle((M, y, W - M, y + 4), radius=2, fill=LINE)
    y += 52

    name = page.get("name", "")
    if name:
        f3 = F(54, True)
        d.text((M, y), name, font=f3, fill=DEEP)
        y += 84
    tag = page.get("tag", "")
    if tag:
        f4, ls4 = fit(d, tag, W - M * 2, 2, 42, 30, bold=False)
        draw_lines(d, ls4, f4, M, y, SUB, lh=1.4)

    d.rectangle((M, H - 268, M + 76, H - 261), fill=ACC)
    d.text((M, H - 232), page.get("foot") or (BRAND + " · 记录与分享"),
           font=F(36, False), fill=FAINT)


# ---------------------------------------------------------------- 主流程

LIST_FIELDS = ("lines", "promise", "cols", "items", "points", "list")


def _clean_page(p):
    """把每页字段洗一遍：bool→空、数字→字符串，list/dict 原样保留。

    另外把"本该是列表"的字段兜住——模型（或手写 note.json）常把
    lines / promise 写成一段字符串，若不拆开，渲染时会被逐字拆成一行一个字。
    """
    out = {}
    for k, v in (p or {}).items():
        out[k] = v if isinstance(v, (list, dict)) else _s(v)
    for k in LIST_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            out[k] = [x.strip() for x in re.split(r"[\n;；|]", v) if x.strip()]
    return out


def render(note, outdir, pool):
    os.makedirs(outdir, exist_ok=True)
    pages = [_clean_page(p) for p in note["pages"]]
    total = len(pages)
    kicker = note.get("kicker", "")
    brand = note.get("brand") or BRAND
    for _p in pages:
        if not _p.get("foot"):
            _p["foot"] = brand + " · 记录与分享"
    outs = []
    for i, page in enumerate(pages, 1):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        kind = page.get("kind", "step")
        dark = False
        if kind == "result":
            p_result(im, d, page, pool)
        elif kind == "hook":
            p_hook(im, d, page)
        elif kind == "step":
            p_step(im, d, page)
        elif kind == "grid":
            p_grid(im, d, page, pool)
        elif kind == "quote":
            dark = p_quote(im, d, page) or True
        elif kind in ("note", "asset"):
            p_note(im, d, page)
            dark = True
        elif kind == "opinion":
            p_opinion(im, d, page)
        elif kind == "detail":
            p_detail(im, d, page)
        elif kind == "follow":
            p_follow(im, d, page)
        else:
            p_step(im, d, page)

        if not dark:
            frame(im, d, page, i, total, kicker)
        p = os.path.join(outdir, f"{i:02d}.jpg")
        im.save(p, quality=92, subsampling=1)
        outs.append(p)
    return outs


def main():
    if len(sys.argv) < 3:
        print("usage: xhs_render.py note.json outdir")
        sys.exit(1)
    note = json.load(open(sys.argv[1], encoding="utf-8"))
    # 成片墙用的截图池：优先 note.pool_dirs → --shots-dir → assets/samples
    dirs = note.get("pool_dirs") or []
    if not dirs and len(sys.argv) > 3:
        dirs = [sys.argv[3]]
    if not dirs:
        here = os.path.dirname(os.path.abspath(__file__))
        dirs = [os.path.join(here, "assets/samples")]
    pool = Pool(dirs)
    outs = render(note, sys.argv[2], pool)
    for p in outs:
        print(p)
    print(f"[ok] {len(outs)} pages, pool={len(pool.items)}")


if __name__ == "__main__":
    main()
