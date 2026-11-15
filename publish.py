#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成手机端发布页。

它解决的不是"生成图片"，而是**最后一公里**：图怎么进手机相册、怎么变成可发布的素材。

三条保存路径（按能力自动降级，页面上只出现当前设备真正能用的那条）：
  1. 装了配套 APK  → 调 AndroidBridge.saveImages()，原生写 MediaStore，
                     一次点按把 9 张图整批存进相册指定相册目录（真·一键）
  2. 手机浏览器支持 Web Share Level 2（Android Chrome / Edge 等）
                  → navigator.share({files})，一个系统弹层里选"保存图片"或直接分享到 App
  3. 其它浏览器  → 逐张下载 + 打包 zip，最差路径也只是一次点击一张

诚实说明：纯网页做不到静默写相册，这是浏览器安全模型的硬限制，不是实现偷懒。
所以路径 1 才有存在价值——这也是为什么本仓库连带开源那个十几 KB 的安卓壳。

用法：
  python3 publish.py <note.json> <图片目录> <输出目录> [品牌名] [相册名]
"""
import html
import json
import os
import sys

CSS = """
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#F6F4F0;font-family:-apple-system,"PingFang SC","Noto Sans SC",sans-serif;color:#16181C}
.wrap{max-width:560px;margin:0 auto;padding:18px 16px 70px}
.hd{display:flex;align-items:center;gap:8px;margin-bottom:14px}
.dot{width:8px;height:8px;border-radius:2px;background:#E8543A}
.hd b{font-size:15px;letter-spacing:.5px}
.hd span{font-size:12px;color:#9a9ca0;margin-left:auto}
.sec{background:#fff;border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.lb{font-size:11px;color:#9a9ca0;letter-spacing:1px;margin-bottom:10px}
.opt{border:1px solid #E8E6E1;border-radius:12px;padding:12px 14px;margin-bottom:9px;font-size:15px;
line-height:1.5;cursor:pointer;position:relative;padding-right:44px}
.opt:last-child{margin-bottom:0}
.opt:active{background:#F1F7F4;border-color:#0E3B33}
.opt em{position:absolute;right:12px;top:11px;font-style:normal;font-size:11px;color:#ABADB2}
.opt.on{border-color:#0E3B33;background:#F1F7F4}
.cp{display:inline-block;margin-top:12px;background:#0E3B33;color:#fff;border:0;border-radius:10px;
padding:10px 20px;font-size:14px;font-weight:600}
.cp:active{opacity:.8}
.body{font-size:14.5px;line-height:1.85;white-space:pre-wrap;color:#2A2C30}
.tags{font-size:13px;line-height:1.9;color:#0E3B33}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.grid img{width:100%;border-radius:10px;display:block;border:1px solid #EDEBE6}
.tip{font-size:12px;color:#9a9ca0;line-height:1.7;margin-top:8px}
.dl{display:flex;gap:10px}
.dl a{flex:1;text-align:center;background:#fff;border:1px solid #E8E6E1;color:#16181C;
border-radius:12px;padding:13px;font-size:13.5px;text-decoration:none;font-weight:600}
.dl a.p{background:#E8543A;color:#fff;border-color:#E8543A}
.savebar{position:fixed;left:0;right:0;bottom:0;background:#0E3B33;color:#fff;padding:14px 16px 18px;
display:none;align-items:center;justify-content:center;gap:10px;font-size:15px;font-weight:600;
box-shadow:0 -4px 16px rgba(0,0,0,.14);z-index:50;cursor:pointer}
.savebar.on{display:flex}
body.hasbar{padding-bottom:86px}
.toast{position:fixed;left:50%;bottom:76px;transform:translateX(-50%);background:#16181C;color:#fff;
padding:10px 20px;border-radius:20px;font-size:13px;opacity:0;transition:.25s;pointer-events:none;z-index:60}
.toast.on{opacity:1}
"""

JS = """
function cp(txt,el){
  var ta=document.createElement('textarea');ta.value=txt;ta.style.position='fixed';
  ta.style.top='-9999px';document.body.appendChild(ta);ta.select();
  try{document.execCommand('copy')}catch(e){}
  document.body.removeChild(ta);toast('已复制');
  if(el){document.querySelectorAll('.opt').forEach(function(x){x.classList.remove('on')});
  el.classList.add('on')}
}
function toast(m){var t=document.getElementById('tt');if(!t)return;t.textContent=m;t.classList.add('on');
clearTimeout(window._tt);window._tt=setTimeout(function(){t.classList.remove('on')},1600)}

var ALBUM='@@ALBUM@@';
var CAPTION=document.getElementById('cap-txt')?document.getElementById('cap-txt').textContent:'';
var NATIVE=!!(window.AndroidBridge&&window.AndroidBridge.saveImages);
var CANSHARE=!!(navigator.canShare&&navigator.share);

function imgUrls(){
  var a=[];document.querySelectorAll('.grid img').forEach(function(im){
    a.push(new URL(im.getAttribute('src'),location.href).href)});
  return a;
}

/* 路径1：原生批量写相册 */
function nativeSave(){
  var urls=imgUrls(),bar=document.getElementById('sb');
  window.__saveTotal=urls.length;
  bar.innerHTML='<span>⏳</span> 正在存入相册 0/'+urls.length;
  AndroidBridge.saveImages(JSON.stringify(urls),ALBUM);
}
window.__saveProgress=function(n,t){
  var bar=document.getElementById('sb');
  if(bar)bar.innerHTML='<span>⏳</span> 正在存入相册 '+n+'/'+t;
};
window.__saveDone=function(ok,fail){
  var bar=document.getElementById('sb');
  if(bar)bar.innerHTML='<span>✅</span> 已存入相册 '+ok+' 张'+(fail?'，失败 '+fail:''); 
  toast('去相册看看，文件夹「'+ALBUM+'」');
  setTimeout(function(){if(bar)bar.innerHTML='<span>📥</span> 全部存入相册（可重复点击）'},2800);
};

/* 路径2：系统分享（带文件），可保存到相册或直接发到 App */
async function shareAll(){
  var bar=document.getElementById('sb');
  try{
    bar.innerHTML='<span>⏳</span> 正在准备图片…';
    var files=[];
    var urls=imgUrls();
    for(var i=0;i<urls.length;i++){
      var r=await fetch(urls[i],{cache:'force-cache'});
      var b=await r.blob();
      files.push(new File([b],urls[i].split('/').pop(),{type:b.type||'image/jpeg'}));
    }
    if(navigator.canShare&&navigator.canShare({files:files})){
      await navigator.share({files:files,text:CAPTION});
      bar.innerHTML='<span>📤</span> 再次分享 / 保存';
    }else{
      bar.innerHTML='<span>⬇️</span> 逐张下载';
      downloadAll();
    }
  }catch(e){
    bar.innerHTML='<span>⬇️</span> 已取消，可改用逐张下载';
  }
}

/* 路径3：逐张下载（最差兜底，任何浏览器都有按钮，不会让人以为功能没做） */
function downloadAll(){
  var urls=imgUrls();
  urls.forEach(function(u,i){setTimeout(function(){
    var a=document.createElement('a');a.href=u;a.download=u.split('/').pop();
    a.target='_blank';a.rel='noopener';document.body.appendChild(a);a.click();
    document.body.removeChild(a);
  },i*300)});
  toast('已开始逐张下载（共 '+urls.length+' 张）');
}

function initSave(){
  if(window.__saveInit)return;
  window.__saveInit=1;
  document.body.classList.add('hasbar');
  var bar=document.getElementById('sb');
  bar.classList.add('on');
  if(NATIVE){
    bar.innerHTML='<span>📥</span> 全部存入相册';
    bar.onclick=nativeSave;
  }else if(CANSHARE){
    bar.innerHTML='<span>📤</span> 一键保存 / 分享这 @@N@@ 张';
    bar.onclick=shareAll;
  }else{
    bar.innerHTML='<span>⬇️</span> 逐张下载这 @@N@@ 张';
    bar.onclick=downloadAll;
  }
}
document.addEventListener('DOMContentLoaded',initSave);
window.addEventListener('load',initSave);
setTimeout(initSave,300);
"""


def build(note, imgdir, outdir, brand=None, album=None, rel_imgs=None):
    brand = brand or note.get("brand") or os.environ.get("XHS_BRAND") or "图文成稿"
    album = album or note.get("album") or brand

    opts = note.get("title_options") or [note.get("title") or ""]
    o_html = ""
    for i, t in enumerate(opts, 1):
        o_html += ('<div class="opt" onclick="cp(this.dataset.t,this)" data-t="%s">%s'
                   '<em>%d</em></div>') % (html.escape(t), html.escape(t), i)

    rel_imgs = rel_imgs or ["%02d.jpg" % i for i in range(1, len(note.get("pages") or []) + 1)]
    imgs = "".join('<img src="%s" loading="lazy" alt="%d">' % (p, i + 1) for i, p in enumerate(rel_imgs))
    tags = " ".join("#" + t for t in note.get("tags", []))
    caption = note.get("caption") or ""
    zip_name = note.get("slug", "note") + ".zip"

    page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="robots" content="noindex">
<title>@@TITLE@@</title><style>@@CSS@@</style></head><body>
<div class="wrap">
  <div class="hd"><div class="dot"></div><b>@@BRAND@@</b>
  <span>@@N@@ 张 · @@DATE@@</span></div>

  <div class="sec" style="border:1px solid #F1CDBD;background:#FFF8F4">
    <div class="lb" style="color:#C25A3C">第一步 · 存图</div>
    <div class="grid">@@IMGS@@</div>
    <div class="tip" id="save-tip">@@SAVE_TIP@@</div>
    <div class="dl" style="margin-top:12px">
      <a href="@@ZIP@@" download class="p">打包下载 zip</a>
      <a href="@@FIRST@@" download>只下封面</a>
    </div>
  </div>

  <div class="sec">
    <div class="lb">第二步 · 标题（点一条复制）</div>
    @@OPTS@@
  </div>

  <div class="sec">
    <div class="lb">第三步 · 正文</div>
    <div class="body" id="cap-txt">@@CAPTION@@</div>
    <button class="cp" onclick="cp(document.getElementById('cap-txt').textContent,this)">复制正文</button>
  </div>

  <div class="sec">
    <div class="lb">话题标签</div>
    <div class="tags" id="tag-txt">@@TAGS@@</div>
    <button class="cp" onclick="cp(document.getElementById('tag-txt').textContent,this)">复制标签</button>
  </div>

  <div class="sec">
    <div class="lb">发布前自查</div>
    <div class="tip" style="margin-top:0">
      · 图片顺序不能乱：第 1 张是封面，决定了点击率<br>
      · 标签用满 7 个，前 3 个最重要<br>
      · 第一篇别带任何外链，也别在正文写"关注我"<br>
      · 如果内容由 AI 参与生成，按平台规则如实标注
    </div>
  </div>
</div>

<div class="savebar" id="sb">📥 全部存入相册</div>
<div class="toast" id="tt"></div>
<script>@@JS@@</script>
</body></html>"""

    # 首屏给一句"当前设备能怎么存"，避免用户找不到按钮
    if note.get("_native_hint", True):
        save_tip = ("<b>一键存相册</b>：装了配套 APK，点底部按钮整批进相册，"
                    "不用长按另存九次。<br>纯网页做不到静默写相册，所以提供打包下载兜底。")
    else:
        save_tip = ""

    rep = {
        "@@CSS@@": CSS,
        "@@JS@@": JS.replace("@@ALBUM@@", album).replace("@@N@@", str(len(rel_imgs))),
        "@@TITLE@@": html.escape(opts[0] if opts else ""),
        "@@BRAND@@": html.escape(brand),
        "@@DATE@@": html.escape(note.get("created") or ""),
        "@@N@@": str(len(rel_imgs)),
        "@@IMGS@@": imgs,
        "@@OPTS@@": o_html,
        "@@CAPTION@@": html.escape(caption),
        "@@TAGS@@": html.escape(tags),
        "@@ZIP@@": html.escape(zip_name),
        "@@FIRST@@": html.escape(rel_imgs[0] if rel_imgs else ""),
        "@@SAVE_TIP@@": save_tip,
    }
    for k, v in rep.items():
        page = page.replace(k, v)

    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    note = json.load(open(sys.argv[1], encoding="utf-8"))
    imgdir, outdir = sys.argv[2], sys.argv[3]
    brand = sys.argv[4] if len(sys.argv) > 4 else None
    album = sys.argv[5] if len(sys.argv) > 5 else None
    imgs = sorted([f for f in os.listdir(imgdir) if f.lower().endswith((".jpg", ".jpeg", ".png"))])
    print(build(note, imgdir, outdir, brand, album, imgs))


if __name__ == "__main__":
    main()
