# xhs-studio · 小红书图文生产线

**一段内容 → 九张 1080×1440 图文 + 一个手机发布页。图能一键进相册。**

```bash
python3 cli.py --file 我的稿子.md      # 出 9 张图 + 发布页 + zip
```

大多数人做这类工具，做到"把图存到 `output/` 就结束了"。
我们多做了最后 10 厘米：**图怎么进手机相册、怎么直接变成能发的素材。**

为此多写了一个十几 KB 的安卓壳（`android/`），点一次按钮把 9 张图整批写进系统相册。
因为纯网页做不到这件事——浏览器安全模型不允许网页静默写相册，这不是实现偷懒。

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  一段文字    │ ──▶ │  9 张图文     │ ──▶ │  发布页（手机打开）│
│ 选题/稿子/链接│     │ 1080×1440   │     │ 存相册 / 复制 / 发 │
└─────────────┘     └──────────────┘     └──────────────────┘
```

---

## 为什么值得用

| 常见做法 | 这里 |
|---|---|
| 生成图后自己长按另存 9 次 | 点一次，整批进相册指定文件夹 |
| 让小模型自由发挥，编出"我坚持了 21 天" | **事实墙**：改写稿里出现原稿没有的数字/时间，自动拦截重写 |
| 每条都要重新想结构 | 固定九页结构（结果前置 → 钩子 → 判断 → 依据 → 取舍 → 作品墙 → 金句 → 记住一点 → 我在做什么） |
| 只能"生成"，不能"发" | 发布页把标题/正文/标签做成单击复制，配合 APK 一条链路走完 |

第 2 条是重点。这类工具最大的风险不是画得丑，是**替你编经历**——模型太懂"怎样更像人写的"，于是会给你编一个"卡了三天"的故事。`guard.py` 会把你原稿里没出现过的"三次 / 两周 / 十几版"全部揪出来。

实跑一次的输出（真实日志）：

```
[rewrite] 正文 351 字 / 标题：重读三遍才反应过来，我在替别人评价自己 | ...
[check] 改写稿：拦截，硬伤 1，编造嫌疑数字 1
[fix] 带着问题清单重改一轮：改写稿里出现了原稿没有的数字/时间（等于替你编经历）：两遍
[fix] 重改后：通过，硬伤 0
```

---

## 30 秒上手

```bash
# 1. 依赖：只有一个 —— Pillow
pip install -r requirements.txt
# 中文字体（没有会报错并提示你装什么）
sudo apt install fonts-noto-cjk        # Ubuntu/Debian
# macOS / Windows 一般自带可用的中文字体，无需安装

# 2. 配置模型（任意 OpenAI 兼容接口）
cp .env.example .env && vi .env        # 填 XHS_LLM_KEY

# 3. 出图
python3 cli.py --file examples/demo.txt
open out/*/index.html                  # 手机扫码打开更爽
```

**一个 key 都没有？** 也能跑通整条链路（规则拆页，质量一般，但路径完整）：

```bash
python3 cli.py --file examples/demo.txt --offline
```

三种输入：

```bash
python3 cli.py --topic "读完《被讨厌的勇气》" --facts "27期;读了三遍"   # 给选题，模型写
python3 cli.py --file 稿子.md --title "我想强调的方向"                 # 给稿子，只做小红书化改写
python3 cli.py --url https://mp.weixin.qq.com/s/xxxx                  # 给链接，抓正文改写
```

输出：

```
out/<slug>/
├── 01.jpg … 09.jpg     # 九张，1080×1440，第 1 张是封面
├── index.html          # 手机发布页（同一局域网/公网可直接打开）
├── note.json           # 中间产物，改完可单独重渲染
└── <slug>.zip          # 打包下载
```

---

## 「一键存相册」到底怎么做的

发布页里按设备能力自动选一条路，页面上只出现当前真正能用的按钮：

| 路径 | 触发条件 | 行为 |
|---|---|---|
| ① 原生批量 | 装了配套 APK | `AndroidBridge.saveImages()` → 原生下载 9 张 → 写 MediaStore 到 `Pictures/<相册名>/` |
| ② 系统分享 | 手机浏览器支持 Web Share Level 2 | `navigator.share({files})`，一个系统弹层里"保存图片"或直接发给 App |
| ③ 逐个下载 | 其它浏览器 | 逐张下载 + 打包 zip |

APK 里就三件事值得看（`android/MainActivity.java`）：

```java
// Android 10+ 用 RELATIVE_PATH 指定相册目录，用 IS_PENDING 做原子写入，
// 全程不需要存储权限；10 以下才要 WRITE_EXTERNAL_STORAGE。
cv.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/" + dir);
cv.put(MediaStore.Images.Media.IS_PENDING, 1);
Uri uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
// ...写完之后
cv.clear(); cv.put(MediaStore.Images.Media.IS_PENDING, 0);
resolver.update(uri, cv, null, null);
```

**构建 APK**（不需要 Gradle，只要 Android SDK 的 build-tools）：

```bash
cd android
ANDROID_SDK=$HOME/Android/Sdk bash build.sh     # 产出 xhs-post.apk
```

装好后首次启动会问你要发布页地址（填你自己部署的，例 `http://192.168.1.10:8000/xhs/`），存在本地，之后随时改。

---

## 九页结构

固定的页序是这个工具真正的"经验"部分，改之前先想清楚为什么每一页在那儿。

| # | kind | 作用 | 关键字段 |
|---|---|---|---|
| 1 | `result` | 结果前置，封面决定点击率 | `num` `unit` `title` `sub` `visual` |
| 2 | `hook` | 只留人，一句具体处境 | `big` `lines[]` |
| 3 | `opinion` | 我的一个判断 | `title` `body` `note` |
| 4 | `detail` | 依据（引用原文要真） | `quote` `source` `mine` |
| 5 | `behind` | 取舍：我删了什么、卡在哪 | `no` `title` `body` |
| 6 | `grid` | 作品墙，证明真在做 | `title` `sub` `n` `cols` |
| 7 | `quote` | 一句我自己的话（深底大字） | `big` `sub` |
| 8 | `note` | 这一期最想让人记住的一点 | `badge` `title` `body` |
| 9 | `follow` | 我在做什么（**不承诺给读者东西**） | `head` `promise[]` `name` `tag` |

细节想调 → 改 `prompts/persona-line.md`（九页规则）、`prompts/rewrite.md`（改写）、`prompts/layout.md`（拆页）。
渲染版式在 `render.py` 的 `p_*` 函数里，一个 kind 一个函数，互不干扰。

---

## 配置

全部走环境变量或 `.env`：

| 变量 | 说明 | 默认 |
|---|---|---|
| `XHS_LLM_BASE` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `XHS_LLM_KEY` | API key | 空（为空则只能 `--offline`） |
| `XHS_LLM_MODEL` | 模型名 | `deepseek-chat` |
| `XHS_BRAND` | 署名，画在页脚与第 9 页 | `我的图文账号` |
| `XHS_ALBUM` | APK 存相册的文件夹名 | 同 `XHS_BRAND` |
| `XHS_FONT_FILE` | 手动指定中文字体文件 | 自动探测 |

示例图放在 `assets/samples/`（第 1、6 页的图片墙用），换成你自己的作品截图，效果立刻不一样。

---

## 部署到自己的服务器，用手机打开

```bash
python3 cli.py --file 稿子.md --out /var/www/html/xhs
# 手机浏览器访问 http://<你的地址>/xhs/<slug>/
```

想要二维码可以直接扫？

```bash
python3 -c "import qrcode,sys;qrcode.make(sys.argv[1]).save('qr.png')" http://192.168.1.10:8000/xhs/
```

nginx 一行即可：

```nginx
location /xhs/ { root /var/www/html; autoindex on; }
```

> 同源部署时"系统分享"这条路径最顺（`fetch` 读图不会被 CORS 拦）。

---

## 目录

```
cli.py            主入口（三种输入 + 离线兜底）
render.py         九页渲染（PIL，一个 kind 一个函数）
guard.py          合规闸门 + 事实墙（防编造经历）
publish.py        手机发布页（三条保存路径）
fetch_url.py      链接抓正文（公众号/知乎/头条/简书特判）
prompts/          三段提示词，可直接改
assets/samples/   图片墙示例图（换成你的作品）
android/          安卓壳（唯一能干"静默写相册"的东西）
docs/             为什么这么设计 / 踩坑记录
```

---

## 已知的坑（都踩过，写在这里省你的时间）

1. **`lines` / `promise` 写成字符串会被逐字拆成一行一个字。** 渲染前统一过 `_clean_page()` 拆分，手写 `note.json` 也安全。
2. **`fit()` 只是截断，长标题会丢掉后半句。** 想让标题完整，就自己控制在 14 字内——这也是好标题的长度。
3. **中文折行要禁则。** 标点不能落行首、括号书名号不能落行尾、末行不能只剩 1–2 个字，否则单看一页会觉得"很业余"。`wrap_cn()` 里都有。
4. **字体 index。** `NotoSansCJK-*.ttc` 里 index=2 才是简体中文，用 0 会变成日文字形。
5. **Web Share 需要 https 或 localhost。** 局域网 http 下 `navigator.share` 可能不可用，此时自动落到逐个下载。
6. **Ubuntu 上 `python3 -m venv` 报错**是缺 `python3-venv`，装一下即可。
7. **APK 的 `usesCleartextTraffic`。** 用 http 内网地址必须开，否则 WebView 白屏。

更多见 `docs/pitfalls.md`。

---

## English

**xhs-studio turns one piece of text into a 9-image Xiaohongshu (RED) carousel — 1080×1440 each — plus a mobile publish page that can push all images straight into the phone's gallery.**

The gallery part is why this exists. Generating images is easy; getting nine of them into your album, ready to post, is the annoying 10 centimeters everyone skips. A ~17 KB Android shell (`android/`) does it via `MediaStore`, no gallery permissions needed on Android 10+.

```bash
pip install -r requirements.txt
cp .env.example .env      # set XHS_LLM_KEY (any OpenAI-compatible endpoint)
python3 cli.py --file my_draft.md        # or --topic / --url / --offline
```

Also included: a **fact wall** (`guard.py`) that rejects any number or duration the model invented but your draft never mentioned — the most common failure mode of AI writing tools.

MIT licensed. Issues and PRs welcome.

---

## License

MIT © 2026
