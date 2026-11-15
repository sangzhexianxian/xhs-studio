#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书合规自检闸门（2026-11-13 立，起因：p_bookread 一期被判「利用 AI 进行不规范运营账号」）

平台原文红线三条：
  1. 教他人用 AI 批量生成内容（提供 AI 指令模板、展示一键生成大量内容的流程）
  2. 教他人用 AI 绕过平台审核（洗稿克隆他人内容、展示规避审核的方法）
  3. 教他人用 AI 同时操控多个账号

本模块做三件事：
  check(note)  → 返回 (ok, hard[], soft[], info{})，硬伤命中即整篇不可发
  sanitize(t)  → 给写稿素材做清洗：含违禁词的句子整句丢弃
  report(...)  → 生成人能看懂的问题清单，回灌给模型重写

用法：
  from guard import check, sanitize, report
  ok, hard, soft, info = check(note)
"""
import os
import re

# ---------------------------------------------------------------- 词表
# 硬伤：平台明文点名的行为。命中任何一条 → 整篇作废重写。
HARD = [
    # 给模板 / 教方法
    (r"提示词", "给了提示词（平台原文举例就是这条）"),
    (r"指令模板|模板化指令|咒语", "出现指令模板"),
    (r"prompt", "出现 prompt/提示词"),
    (r"直接抄|抄走|抄作业|复制就能|复制粘贴就能|拿去就能|照抄", "出现「直接抄/复制就能」类可复制资产暗示"),
    (r"手把手|保姆级|保姆式|0\s*基础也能|零基础也能", "出现教程式口吻"),
    (r"教你|教会你|教别人|教他|学会这招|学会这个", "出现「教他人」表述（红线第一条）"),
    (r"方法如下|步骤如下|三步搞定|几个步骤|操作流程如下", "出现操作流程教学"),
    # 批量 / 一键 / 自动化
    (r"批量生成|批量出|批量生|批量生产|批量剪辑|批量做|一键生成|一键出|一键成片|一键搞定|一键完成", "出现批量/一键生成（红线第一条）"),
    (r"自动跑|全自动|自动生成|自动化流程|自动出片|自动产出|无人值守|不用人盯着|不用管|挂机|后台跑", "出现自动化量产表述"),
    (r"量产|流水线量产|规模化产出|日产\s*\d|日更\s*\d+\s*条|一天出\s*\d+\s*条|一天\s*\d+\s*篇", "出现产量化指标"),
    (r"个文件|文件数|\d+\s*MB|\d+\s*GB|服务器上|服务器里|部署|脚本|代码库|接口|目录结构|工作流引擎", "出现工程/文件量表述"),
    # 绕过审核 / 搬运
    (r"洗稿|搬运|伪原创|改写别人|仿写别人|克隆别人|克隆他人|抄袭", "涉及搬运/洗稿（红线第二条）"),
    (r"绕过审核|规避审核|过审|防限流|限流破解|养号技巧|避审|躲审核", "涉及规避审核（红线第二条）"),
    # 多账号
    (r"多账号|矩阵号|号群|养号|批量发号|多个账号|账号矩阵|小号", "涉及多账号运营（红线第三条）"),
    # 导流承诺
    (r"私信我|私我|加微信|加我微信|领取|无偿分享|免费领|回复关键词|留言领取|主页领|关注领", "出现导流/领取承诺"),
    (r"明天你能拿到|后天你能看到|关注我，你会拿到|关注我给你|关注后送你", "出现承诺型关注诱导"),
]

# ---------------------------------------------------------------- 平台规则（2026-11-15 用户提供）
# 依据小红书 App「当前笔记违规点」两条官方规则：
#
# 规则一「利用AI教唆做号」：
#   传授或使用 AI 工具批量生成内容实现账号矩阵化运营 / 教授利用 AI 洗稿规避平台审核 /
#   提供 AI 批量运营指令与模板推荐 → 平台将对相关内容及账号予以处理。
#   适用范围：笔记、评论、用户资料等所有发布场景。
#
# 规则二「鼓励如实标识AI」：
#   完全由 AI 生成，或经 AI 润色、补充、生成部分素材后的再创作内容，发布时建议主动标明 AI 参与方式；
#   未主动标注的，平台识别后统一添加标识。发布路径：高级选项 → 笔记内容声明 → 笔记含 AI 合成。
#   （产线落地：发布页 pub.py 固定显示这条提示，文案本身不写"AI 做的"以免破坏调性）
PLATFORM_AI = [
    (r"做号|起号|做账号|账号矩阵|矩阵化|多开|工作室号|批量起号|带做号|搞号",
     "出现「做号/起号/矩阵」表述（规则一：禁止教唆做号）"),
    (r"AI\s*工具推荐|推荐\s*AI|AI\s*工具合集|这几款\s*AI|AI\s*指令|喂给\s*AI|AI\s*批量运营",
     "出现 AI 工具/指令推荐（规则一：禁止提供 AI 批量运营指令与模板推荐）"),
    (r"AI\s*洗稿|去重工具|一键去重|AI\s*改写规避|降重",
     "出现 AI 洗稿/去重表述（规则一：禁止教唆洗稿规避审核）"),
]

# 软伤：不一定违规，但会让内容读起来像"流程说明"。命中 ≥3 条建议重写。
SOFT = [
    (r"流程|环节|模块|节点|管线|产线|流水线", "流程/产线类词（读起来像教程）"),
    (r"效率|提效|降本|roi|投入产出", "效率口号"),
    (r"干货|必看|建议收藏|码住|收藏起来|不看后悔", "标题党词"),
    (r"内卷|焦虑|逆袭|扎心|治愈|人生意义|普通人翻身", "情绪套路词"),
    (r"大家|各位|我们", "用了「大家/我们」，要求全程第一人称"),
]

# 真人形象：跨号引用别人的真人形象有肖像与身份风险，一律不写。
# 想禁止引用某个具体系列/人物，在项目根目录放 persona_ban.txt，每行「正则<TAB>理由」。
BAN_PERSONA = [
    (r"真人出镜|真人形象|真人医生|白大褂医生|出镜医生",
     "出现真人出镜形象表述（引用他人真人形象有肖像与身份风险）"),
]


def _load_persona_ban():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_ban.txt")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            out.append((parts[0].strip(), parts[1].strip()))
    return out


BAN_PERSONA += _load_persona_ban()

# 事实墙：改写稿里出现、但原稿没有的时间/次数/数量 → 视为编造（2026-11-15 加）
# 小红书要具体，但具体必须来自作者自己写的原稿，模型不许替他编经历。
_NUM = re.compile(r"([0-9]{1,4}|[一二三四五六七八九十百千万两半]{1,3})\s*"
                  r"(个月|年|天|日|周|星期|小时|分钟|次|遍|篇|版|条|行|页|张|块|万元|万|元)")
_GENERIC = {"一个", "一句", "一点", "一条", "一件", "一样", "一次", "一段", "一步", "一层", "一面",
            "一篇", "一本", "一张", "一位", "一场", "一顿", "一遍", "一行", "一页"}


def _num_tokens(text):
    out = set()
    for m in _NUM.finditer(text or ""):
        tok = m.group(0).replace(" ", "")
        if tok in _GENERIC:
            continue
        out.add(tok)
    return out


def fact_check(note, orig):
    """返回 (bad[], info)：bad = 改写稿有、原稿没有的具体数字/时间（编造嫌疑）"""
    import json as _j
    txt = _j.dumps(note, ensure_ascii=False)
    o = _num_tokens(orig)
    mine = _num_tokens(txt)
    bad = sorted(x for x in mine if x not in o)
    return bad, {"nums_orig": len(o), "nums_note": len(mine)}


# 只要出现就提示"AI 用太多"（AI 是工具不是卖点）
AI_LIMIT_TITLE = 0          # 标题里一次都不许有
AI_LIMIT_ALL = 2            # 全文最多 2 次

# 定位词：账号标签里不要挂这些
FORBID_TAG = ["AI工具", "AI副业", "AI赚钱", "AIGC", "AI变现", "自动化", "批量", "矩阵", "产线", "工作流"]


def _walk_strings(o):
    """递归取出 note 里所有字符串（含 title_options / pages / caption / tags）"""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _walk_strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _walk_strings(v)


def _pages_text(note):
    out = []
    for p in note.get("pages", []) or []:
        out.append(json_dumps(p))
    return "\n".join(out)


def json_dumps(o):
    import json
    return json.dumps(o, ensure_ascii=False)


def check(note, persona=False):
    """返回 (ok, hard, soft, info)

    persona=True 时额外把真人形象 / persona_ban.txt 里配置的系列相关表述判为硬伤
    """
    hard, soft = [], []
    title = note.get("title", "") or ""
    for t in note.get("title_options", []) or []:
        pass
    body = "\n".join([json_dumps(note.get("pages", [])), note.get("caption", "") or "",
                      note.get("kicker", "") or ""])
    alltext = title + "\n" + body + "\n" + "\n".join(note.get("tags", []) or [])

    for pat, why in list(HARD) + list(PLATFORM_AI):
        m = re.search(pat, alltext, re.I)
        if m:
            hard.append("%s ← 命中「%s」" % (why, m.group(0)))

    for pat, why in SOFT:
        m = re.search(pat, alltext, re.I)
        if m:
            soft.append("%s ← 命中「%s」" % (why, m.group(0)))

    # 真人形象（含 persona_ban.txt 自定义）
    if persona:
        for pat, why in BAN_PERSONA:
            m = re.search(pat, alltext, re.I)
            if m:
                hard.append("%s ← 命中「%s」" % (why, m.group(0)))

    # AI 出现次数
    n_title = len(re.findall(r"AI|ai", title))
    n_all = len(re.findall(r"\bAI\b|AI", alltext))
    info = {"ai_title": n_title, "ai_all": n_all}
    if n_title > AI_LIMIT_TITLE:
        hard.append("标题里出现 AI（标题只讲内容，不讲工具）← 「%s」" % title)
    if n_all > AI_LIMIT_ALL:
        soft.append("全文 AI 出现 %d 次（上限 %d，AI 是工具不是卖点）" % (n_all, AI_LIMIT_ALL))

    # 标签
    for tg in note.get("tags", []) or []:
        for bad in FORBID_TAG:
            if bad.lower() in tg.lower():
                hard.append("标签「%s」属于工具/投放类词，容易触发账号运营判定" % tg)

    # 结构：不许出现可复制资产页（kind=asset 且 code 里像指令）
    for i, p in enumerate(note.get("pages", []) or [], 1):
        if p.get("kind") == "asset":
            hard.append("第 %d 页是「可复制资产页」（asset），这类页面正是平台点名的 AI 指令模板" % i)
        code = p.get("code") or ""
        if code and re.search(r"你是[一个]?|请你|输入[:：]|输出[:：]|角色[:：]|要求[:：]", code):
            hard.append("第 %d 页含指令式文本（角色/输入/输出/要求），等同提示词" % i)

    # 真在场感：至少 2 个"我…"具体句
    n_wo = len(re.findall(r"我(?!们)", body))
    info["wo"] = n_wo
    if n_wo < 5:
        soft.append("第一人称细节只有 %d 处（建议 ≥5，平台要「真人真事真感受」）" % n_wo)

    ok = not hard
    return ok, hard, soft, info


def sanitize(text):
    """给写稿素材做清洗：按句切开，丢掉含硬禁词/工程词/真人形象词的句子"""
    if not text:
        return ""
    parts = re.split(r"(?<=[。；;！!？?\n])", text)
    keep = []
    for s in parts:
        if not s.strip():
            continue
        bad = False
        for pat, _ in list(HARD) + list(PLATFORM_AI) + list(BAN_PERSONA):
            if re.search(pat, s, re.I):
                bad = True
                break
        if not bad:
            keep.append(s.strip())
    return "".join(keep)


AI_MARK_TIP = ("发布前标注 AI（小红书规则二「鼓励如实标识AI」）："
                "发布页 → 高级选项 → 笔记内容声明 → 笔记含 AI 合成")


def ai_mark_tip():
    """平台规则二：内容经 AI 参与就必须如实标识，否则平台会强制加标。"""
    return AI_MARK_TIP


def report(hard, soft, info=None):
    """给人/给模型看的问题清单"""
    lines = []
    if hard:
        lines.append("【必须改掉的问题（违反平台红线，逐条解决）】")
        for x in hard:
            lines.append("- " + x)
    if soft:
        lines.append("【建议改掉的问题（容易被读成教程/运营号）】")
        for x in soft:
            lines.append("- " + x)
    if info:
        lines.append("【统计】标题 AI 次数 %d，全文 AI 次数 %d，第一人称 %d 处"
                     % (info.get("ai_title", 0), info.get("ai_all", 0), info.get("wo", 0)))
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    import sys
    note = json.load(open(sys.argv[1], encoding="utf-8"))
    ok, hard, soft, info = check(note)
    print("PASS" if ok else "FAIL")
    print(report(hard, soft, info))
