#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xhs-studio · 一条命令把一段内容变成小红书九图。

三个入口，一套输出（九张 1080×1440 + 手机发布页 + zip）：

  # 1. 给选题，让模型按九页结构写
  python3 cli.py --topic "读完《被讨厌的勇气》，我把课题分离用在了剪片上" --facts "27期;读了三遍"

  # 2. 给你自己写好的稿子，模型只做小红书化改写，事实一个字不动
  python3 cli.py --file 我的稿子.md --title "我想强调的方向"

  # 3. 贴一个链接，抓正文后改写
  python3 cli.py --url https://mp.weixin.qq.com/s/xxxx

  # 4. 一个 key 都没有？照样能出图（规则拆页，质量一般但通路完整）
  python3 cli.py --file 稿子.md --offline

输出：out/<slug>/{note.json, 01..09.jpg, index.html, <slug>.zip}

配置见 .env.example，或直接给环境变量。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from guard import ai_mark_tip, check, fact_check, report, sanitize  # noqa: E402

KINDS = ["result", "hook", "opinion", "detail", "behind", "grid", "quote", "note", "follow"]


# ------------------------------------------------------------------ 配置
def load_dotenv():
    for name in (".env", ".env.local"):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(*names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def llm_cfg():
    return (env("XHS_LLM_BASE", "OPENAI_BASE_URL", default="https://api.deepseek.com/v1"),
            env("XHS_LLM_KEY", "OPENAI_API_KEY"),
            env("XHS_LLM_MODEL", "OPENAI_MODEL", default="deepseek-chat"))


def prompt(name, brand):
    p = os.path.join(HERE, "prompts", name)
    if not os.path.exists(p):
        raise SystemExit("[err] 缺少提示词文件：%s" % p)
    return open(p, encoding="utf-8").read().replace("{{BRAND}}", brand)


# ------------------------------------------------------------------ LLM
def llm_chat(messages, temperature=0.8):
    base, key, model = llm_cfg()
    if not key:
        raise SystemExit("[err] 没有配置 LLM key。设置 XHS_LLM_KEY，或加 --offline 走规则拆页。")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4000,
        # DeepSeek 等支持该字段；不支持的厂商会忽略，无副作用
        "thinking": {"type": "disabled"},
    }).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:400]
        if e.code == 402:
            raise SystemExit("[err] 模型余额不足（402）。%s" % msg)
        if e.code == 401:
            raise SystemExit("[err] key 无效（401）。%s" % msg)
        raise RuntimeError("HTTP %s %s" % (e.code, msg))
    return d["choices"][0]["message"]["content"]


def parse_json(t):
    t = (t or "").strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no json: " + t[:300])
    return json.loads(m.group(0))


def llm_json(messages, temperature=0.85, tries=3, tag="llm"):
    last = ""
    for i in range(tries):
        try:
            raw = llm_chat(messages, temperature)
        except SystemExit:
            raise
        except Exception as e:
            print("[warn] %s 调用失败（第 %d 轮）：%s" % (tag, i + 1, e))
            continue
        try:
            return parse_json(raw)
        except Exception as e:
            last = str(e)
            print("[warn] %s JSON 解析失败（第 %d 轮）：%s" % (tag, i + 1, e))
            messages = messages + [
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": "上一轮不是合法 JSON，请只输出 JSON，不要解释、不要代码块围栏。"},
            ]
    print("[err] %s 三轮都失败：%s" % (tag, last[:200]))
    return None


# ------------------------------------------------------------------ 归一化
def pick_title(d):
    bad = re.compile(r"AI|ai|产线|自动|批量|模板|工作流|提示词|工具流|副业", re.I)
    opts = [x for x in (d.get("title_options") or []) if x and not bad.search(x)]
    if d.get("title") and not bad.search(d["title"]):
        return d["title"]
    return opts[0] if opts else (d.get("title") or "")


def normalize(d, brand, shots=None):
    """把模型输出规整成 render 需要的 note，kind 按位置强制对齐（少页/多页都能兜住）"""
    pages = d.get("pages") or []
    out = []
    for i, k in enumerate(KINDS):
        p = dict(pages[i]) if i < len(pages) else {}
        p = {kk: (vv if isinstance(vv, (list, dict)) else
                  ("" if vv is None or isinstance(vv, bool) else str(vv)))
             for kk, vv in p.items()}
        p["kind"] = k
        out.append(p)
    if shots:
        out[0]["visual"] = shots[0]
    elif not out[0].get("visual"):
        out[0]["visual"] = "wall"
    out[8]["name"] = out[8].get("name") or brand
    out[8]["tag"] = re.sub(r"\s*AI\s*", "", out[8].get("tag") or "").strip() or "一个人记录自己怎么读、怎么做"
    return {
        "brand": brand,
        "kicker": d.get("kicker") or "读与做",
        "title": pick_title(d),
        "pages": out,
        "caption": d.get("caption", ""),
        "tags": (d.get("tags") or [])[:7],
        "title_options": [x for x in (d.get("title_options") or []) if x][:3],
    }


# ------------------------------------------------------------------ 三种生成路径
TPL = """这一期要讲的内容：{topic}
真实素材（能用就用，不许编造其它数字）：{facts}

输出 JSON，字段：title_options(3个字符串), kicker(≤10字，一期栏目标签), pages(9项，按上面的结构，kind 必须对), caption, tags(7项)"""

REWRITE_TPL = """这是我写的原稿，请按上面的要求改写成小红书笔记：

---
@@BODY@@
---

@@TITLE_HINT@@

只输出 JSON：{"titles":[...3个],"body":"改写后的正文","tags":[...7个],"hook":"第一句"}"""

LAYOUT_TPL = """这篇笔记（标题候选：@@TITLES@@）：

---
@@NOTE@@
---

按九页结构拆成 9 张图，只输出 JSON：{"kicker":"","pages":[...9项],"tags":[...],"title_options":[...]}"""


def gen_note(topic, facts, brand, shots=None, max_try=3):
    """选题 → 九页（产线模式）"""
    sys_text = prompt("persona-line.md", brand) + "\n\n" + prompt("good.md", brand)
    facts = sanitize(facts or "") or "（无，只能用你能确证的内容，不许编数字）"
    msgs = [{"role": "system", "content": sys_text},
            {"role": "user", "content": TPL.format(topic=topic, facts=facts)}]
    last = None
    for attempt in range(1, max_try + 1):
        raw = llm_chat(msgs)
        try:
            note = normalize(parse_json(raw), brand, shots)
        except Exception as e:
            print("[warn] 第 %d 轮 JSON 解析失败：%s" % (attempt, e))
            msgs.append({"role": "user", "content": "上一轮不是合法 JSON，请只输出 JSON。"})
            continue
        ok, hard, soft, info = check(note)
        print("[check] 第 %d 轮：%s，硬伤 %d，软伤 %d"
              % (attempt, "通过" if ok else "拦截", len(hard), len(soft)))
        if hard:
            print(report(hard, [], info))
        last = note
        if ok and len(soft) <= 2:
            return note, True, hard, soft, info
        msgs.append({"role": "assistant", "content": raw})
        msgs.append({"role": "user", "content":
                     "这篇被合规自检拦下了。逐条改掉，其余结构不变，重新输出完整 JSON（不要解释）：\n"
                     + report(hard, soft, info)})
    return last, False, (check(last)[1] if last else ["无产出"]), [], {}


def rewrite_to_xhs(body, brand, title_hint="", extra=""):
    hint = ("标题我定了方向：%s，请围绕这个方向给 3 个候选。" % title_hint) if title_hint else ""
    if extra:
        hint = (hint + "\n" + extra).strip()
    tpl = REWRITE_TPL.replace("@@BODY@@", body[:8000]).replace("@@TITLE_HINT@@", hint)
    d = llm_json([{"role": "system", "content": prompt("rewrite.md", brand)},
                  {"role": "user", "content": tpl}], 0.85, 3, "改写")
    if not d or not (d.get("body") or "").strip():
        return None
    print("[rewrite] 正文 %d 字 / 标题：%s"
          % (len(d["body"]), " | ".join((d.get("titles") or [])[:3])))
    return d


def layout_pages(rw, brand, shots=None):
    tpl = (LAYOUT_TPL.replace("@@TITLES@@", "; ".join((rw.get("titles") or [])[:3]))
                     .replace("@@NOTE@@", rw.get("body") or ""))
    d = llm_json([{"role": "system", "content": prompt("layout.md", brand)},
                  {"role": "user", "content": tpl}], 0.7, 3, "分页")
    if not d:
        return None
    # 标题 / 标签 / 正文一律以改写稿为准（分页阶段只负责拆页）
    if rw.get("titles"):
        d["title_options"] = rw["titles"][:3]
    if rw.get("tags"):
        d["tags"] = rw["tags"][:7]
    d["caption"] = rw.get("body") or d.get("caption", "")
    return normalize(d, brand, shots)


def custom_note(body, brand, title_hint="", shots=None, strict=False, extra=""):
    """自定义文案：改写 → 分页 → 事实墙（编造经历是这类工具最容易犯的错）"""
    rw = rewrite_to_xhs(body, brand, title_hint, extra)
    if not rw:
        return None, False, ["改写阶段没有产出（模型没返回可用 JSON）"], [], {}
    note = layout_pages(rw, brand, shots)
    if not note:
        return None, False, ["分页阶段没有产出"], [], {}
    ok, hard, soft, info = check(note)
    bad, ninfo = fact_check(note, body)
    if bad:
        hard = list(hard) + ["改写稿里出现了原稿没有的数字/时间（等于替你编经历）："
                             + "、".join(bad[:8])]
        ok = False
    print("[check] 改写稿：%s，硬伤 %d，软伤 %d，编造嫌疑数字 %d"
          % ("通过" if ok else "拦截", len(hard), len(soft), len(bad)))
    if hard and not strict:
        print("[fix] 带着问题清单重改一轮：%s" % "；".join(hard[:3]))
        fix = ("这篇改写的稿子有问题，请重写上面那篇小红书笔记，事实一个字都不许动，"
               "逐条避开下面这些问题：\n" + report(hard, soft, info))
        if bad:
            fix += ("\n特别注意：这些数字/时间是原稿里没有的，全部删掉或换成原稿的说法——"
                    + "、".join(bad[:10]))
        rw2 = llm_json([{"role": "system", "content": prompt("rewrite.md", brand)},
                        {"role": "user", "content": REWRITE_TPL.replace("@@BODY@@", body[:8000])
                                                    .replace("@@TITLE_HINT@@",
                                                             (title_hint + "\n" + extra).strip())},
                        {"role": "user", "content": fix}], 0.8, 2, "重改")
        if rw2 and rw2.get("body"):
            n2 = layout_pages(rw2, brand, shots)
            if n2:
                ok2, hard2, soft2, info2 = check(n2)
                bad2, _ = fact_check(n2, body)
                print("[fix] 重改后：%s，硬伤 %d，编造嫌疑数字 %d"
                      % ("通过" if ok2 else "仍拦截", len(hard2), len(bad2)))
                if bad2:
                    n2["fact_warn"] = "这些数字原稿没有，建议人工核对：" + "、".join(bad2[:10])
                    print("[warn] " + n2["fact_warn"])
                return n2, ok2, hard2, soft2, info2
    return note, ok, hard, soft, info


# ------------------------------------------------------------------ 离线模式
def _sent(text):
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip(" \t\n") for p in parts if p and len(p.strip()) > 1]


def offline_note(body, brand, title=""):
    """不调用模型：按句拆九页。质量一般，但保证 clone 下来第一条命令就能出图。"""
    ss = _sent(body) or [body[:60] or "（空）"]

    def cut(s, n):
        """截断到 n 字：优先在标点处断，实在没有才用省略号（别切出半句话）"""
        s = re.sub(r"[。！？；\n]+$", "", (s or "").strip())
        if len(s) <= n:
            return s
        head = s[:n]
        pos = max(head.rfind(c) for c in "，、：；" ) if any(c in head for c in "，、：；") else -1
        if pos >= max(4, n // 2):
            return head[:pos]
        return head.rstrip("，、：；") + "…"

    def pick(i, default=""):
        """循环取句，保证任何索引都有内容（句子不够也不会出现空白页）"""
        if not ss:
            return default
        v = ss[i % len(ss)]
        return v if v else default

    if not title:
        # 有书名号时优先用书名做标题（讲一本书的稿子最常见）
        for s0 in ss[:4]:
            m = re.search(r"《([^》]{1,18})》", s0)
            if m:
                rest = re.sub(r"^.*?》", "", s0).strip("，。！？、 ")
                title = (m.group(0) + cut(rest, 10)) if rest else (m.group(0) + "，我读了三遍")
                title = title[:20]
                break
        else:
            title = cut(re.sub(r"[，。！？\n]", "", ss[0]), 14)
    pages = [
        {"kind": "result", "lead": "这一期", "num": "01", "unit": "篇",
         "title": title, "sub": cut(pick(1, title), 34), "visual": "wall", "next": "往下看"},
        {"kind": "hook", "big": cut(pick(2, "我卡了很久"), 12),
         "lines": [cut(pick(3, ""), 26), cut(pick(4, ""), 26)], "next": "接着说"},
        {"kind": "opinion", "title": cut(pick(5, "我的判断"), 12),
         "body": cut(pick(6, ""), 52), "note": cut(pick(7, ""), 16), "next": ""},
        {"kind": "detail", "title": cut(pick(8, "让我停下来的那句"), 14),
         "quote": cut(pick(9, ""), 60), "source": "原稿", "mine": cut(pick(10, ""), 40), "next": ""},
        {"kind": "behind", "no": "取舍", "title": cut(pick(11, "我改了什么"), 12),
         "body": cut(pick(12, ""), 52), "note": cut(pick(13, ""), 16), "next": ""},
        {"kind": "grid", "title": "我最近在做的几件事", "sub": cut(pick(14, "一件一件来"), 20), "next": ""},
        {"kind": "quote", "big": cut(pick(15, title), 20), "sub": cut(pick(16, ""), 26), "next": ""},
        {"kind": "note", "badge": "这一期", "title": cut(pick(17, "我最想让人记住的一点"), 14),
         "body": cut("".join(ss[17:20]) or body[:110] or pick(18), 110)},
        {"kind": "follow", "head": "我在做什么",
         "promise": ["我在读什么：", "我在做什么：", "我卡在哪："][:0] or
                    [cut(pick(i), 30) for i in (0, 1, 2)],
         "name": brand, "tag": "一个人记录自己怎么读、怎么做"},
    ]
    # follow 页三条：用「我在读什么 / 我在做什么 / 我卡在哪」做前缀，避免空列表
    pages[8]["promise"] = [
        "我在读什么 · " + cut(pick(0), 24),
        "我在做什么 · " + cut(pick(1), 24),
        "我卡在哪 · " + cut(pick(2), 24),
    ]
    return {"brand": brand, "kicker": "读与做", "title": title, "pages": pages,
            "caption": cut(body, 180), "tags": [], "title_options": [title]}


# ------------------------------------------------------------------ 输出
def run_render(note, outdir, shots_dir=None):
    jpath = os.path.join(outdir, "note.json")
    json.dump(note, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    cmd = [sys.executable, os.path.join(HERE, "render.py"), jpath, outdir]
    if shots_dir:
        cmd.append(shots_dir)
    subprocess.run(cmd, check=True)
    pub = [sys.executable, os.path.join(HERE, "publish.py"), jpath, outdir, outdir]
    if note.get("brand"):
        pub.append(note["brand"])
    if note.get("album"):
        pub.append(note["album"])
    subprocess.run(pub, check=True)
    # 打包（没装 zip 也不影响主流程）
    zpath = os.path.join(outdir, note["slug"] + ".zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    try:
        subprocess.run(["zip", "-q", "-j", zpath] +
                       [os.path.join(outdir, "%02d.jpg" % i)
                        for i in range(1, len(note["pages"]) + 1)], check=False)
    except FileNotFoundError:
        print("[warn] 系统没有 zip 命令，跳过打包（图都在，不影响发布）")
    return jpath


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="一段内容 → 小红书九图 + 手机发布页")
    ap.add_argument("topic", nargs="?", default="", help="选题（产线模式）")
    ap.add_argument("--facts", default="", help="真实素材，分号分隔（数字必须真实）")
    ap.add_argument("--file", default="", help="你自己写好的稿子（txt/md）")
    ap.add_argument("--text", default="", help="稿子正文，命令行直传")
    ap.add_argument("--url", default="", help="链接：抓正文后改写")
    ap.add_argument("--title", default="", help="指定标题 / 给改写方向的提示")
    ap.add_argument("--slug", default="", help="输出目录名")
    ap.add_argument("--out", default=os.path.join(HERE, "out"), help="输出根目录")
    ap.add_argument("--brand", default=env("XHS_BRAND", default="我的图文账号"), help="署名 / 发布页品牌")
    ap.add_argument("--album", default=env("XHS_ALBUM", default=""), help="APK 存相册时的文件夹名")
    ap.add_argument("--shots", default="", help="成片墙用的截图，逗号分隔")
    ap.add_argument("--shots-dir", default="", help="成片墙截图池目录（默认 assets/samples）")
    ap.add_argument("--offline", action="store_true", help="不调用模型，规则拆页")
    ap.add_argument("--strict", action="store_true", help="自定义模式下也按硬伤拦截")
    ap.add_argument("--no-guard", action="store_true", help="跳过合规闸门（调试用，别直接发）")
    ap.add_argument("--force", action="store_true", help="自检不过也出图（调试用）")
    a = ap.parse_args()

    custom = bool(a.file or a.text or a.url)
    shots = [s.strip() for s in a.shots.split(",") if s.strip()]
    brand = a.brand

    if custom:
        body = a.text or ""
        if a.file:
            body = open(a.file, encoding="utf-8").read()
        body = body.strip()
        extra = ""
        src_url = (a.url or "").strip()
        if src_url:
            from fetch_url import fetch_article
            try:
                atitle, abody = fetch_article(src_url)
            except Exception as e:
                print("[FAIL] 链接抓取失败：%s" % e)
                sys.exit(2)
            print("[url] 《%s》%d 字 ← %s" % (atitle, len(abody), src_url))
            body = abody
            extra = ("这段内容来自我贴的一个链接（原文标题：《%s》）。按上面【如果这段内容来自我贴的一个链接】"
                     "的规则改写。" % atitle)
            if (a.text or "").strip():
                extra += "\n我的补充要求（优先满足）：%s" % a.text.strip()[:800]
        if len(body) < 30:
            print("[FAIL] 稿子太短（至少 30 字），或者给一个链接")
            sys.exit(2)

        if a.offline:
            note, ok, hard, soft, info = offline_note(body, brand, a.title.strip()), True, [], [], {}
            print("[offline] 规则拆页（没调用模型）")
        else:
            note, ok, hard, soft, info = custom_note(body, brand, a.title.strip(), shots,
                                                     strict=a.strict, extra=extra)
            if note is None:
                print("[FAIL] 改写失败：%s" % ("；".join(hard or []) or "未知原因"))
                sys.exit(2)
        note["source"] = "custom"
        if src_url:
            note["src_url"] = src_url
    else:
        if not a.topic:
            print("[FAIL] 要么给选题（--topic），要么给稿子（--file/--text/--url）")
            print("       想先看效果：python3 cli.py --file examples/demo.txt --offline")
            sys.exit(2)
        if a.offline:
            note, ok, hard, soft, info = offline_note(a.topic, brand, a.title.strip()), True, [], [], {}
            print("[offline] 规则拆页（没调用模型）")
        else:
            note, ok, hard, soft, info = gen_note(a.topic, a.facts, brand, shots)
        note["source"] = "line"

    if note is None:
        print("[FAIL] 三轮都没出合法 JSON，没生成")
        sys.exit(2)

    if a.title and not a.title.strip() in (note.get("title_options") or []):
        note["title"] = a.title.strip()
        if not note.get("title_options"):
            note["title_options"] = [note["title"]]
    note["brand"] = brand
    if a.album:
        note["album"] = a.album

    slug = a.slug or (("c" if custom else "x") + time.strftime("%m%d%H%M"))
    note["slug"] = slug
    note["created"] = time.strftime("%Y-%m-%d %H:%M")
    if a.shots_dir:
        note["pool_dirs"] = [a.shots_dir]

    if not a.no_guard and not ok:
        print("\n[WARN] 合规自检没过（自己看一眼再发）：" if custom else
              "\n[FAIL] 合规闸门没过，这篇不建议发。问题：")
        if hard:
            print(report(hard, soft, info))
        if not custom and not a.force:
            sys.exit(2)
        if custom and hard and a.strict:
            sys.exit(2)

    outdir = os.path.join(a.out, slug)
    os.makedirs(outdir, exist_ok=True)
    run_render(note, outdir, a.shots_dir or None)

    print("\n[OK] %s" % slug)
    print("[提示] %s" % ai_mark_tip())
    print("标题：%s" % note["title"])
    print("目录：%s" % outdir)
    print("发布页：%s" % os.path.join(outdir, "index.html"))
    if note.get("fact_warn"):
        print("[事实] " + note["fact_warn"])
    if soft:
        print(report([], soft, info))


if __name__ == "__main__":
    main()
