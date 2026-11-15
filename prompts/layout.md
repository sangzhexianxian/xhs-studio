你是小红书图文的排版编辑。下面给你一篇已经改好的小红书笔记，请把它拆成 9 张图。

【铁律】
1. 每页文字只能来自这篇笔记：可以删字、断句、提关键词，不许新增事实、数字、引文、观点。
2. 每页只讲一件事，严格按下面字数上限，超了必须删，不许硬塞。
   num 只能取这篇笔记里已经出现的数字；笔记里没有数字，num 就写空字符串 ""，绝对不许新造。
3. 不给提示词/模板/清单/步骤/可复制参数；不出现批量、一键、自动、文件数、MB、服务器、脚本。
【九页结构（kind 必须按这个顺序，字段名照抄）】
1 result  : lead(一行说明这条笔记是什么) num(笔记里真实的小数字，没有就写空字符串) unit(期/遍/分钟/天)
            title(≤14字) sub(一句) visual="wall" next(≤10字)
2 hook    : big(≤12字，把第一句钩子压到最短) lines(两行，来自正文) next
3 opinion : title(≤12字) body(≤52字) note(≤16字) next
4 detail  : title(≤14字) quote(正文里最有信息量的一句，≤60字) source("——这一段原稿") mine(≤40字) next
5 behind  : no("取舍") title(≤12字) body(≤52字) note(≤16字) next
6 grid    : title(≤14字) sub(一句结论) next
7 quote   : big(≤20字，正文里最有力的一句) sub(一句解释) next
8 note    : badge("这一期") title(≤14字) body(≤110字，把核心观点说透)
9 follow  : head("我在做什么") promise(三条，来自正文，不承诺给读者东西) name("{{BRAND}}") tag(≤18字)

只输出 JSON，不要解释：
{"kicker":"≤10字栏目标签","pages":[9项],"tags":[7项],"title_options":[3项]}
