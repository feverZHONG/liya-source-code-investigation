# 站点镜像重建（把「原本的样子」复原成能打开的一份）

> 用在：查完一个私服／民间站点后，用户要「这网站原来长什么样」「做个网页版，壳就行」——
> 站点可能已下架、改版、或只是换了端口，而本地有（或能补）抓取件。
> 产出是**静态可打开的一份**，不是新做的资料页。

## 一、盘点抓取件

历史抓取通常**平铺命名**：原路径的 `/` 被替换成 `_`。

| 原路径 | 抓取件名 |
|:--|:--|
| `assets/css/style.css` | `assets_css_style.css` |
| `assets/img/char_01.png` | `assets_img_char_01.png` |
| `index.html` | `index.html`（HTML 一般保持原名） |

先 `ls` 数一遍文件，再和 HTML 里的引用清单对一次，能立刻看出「抓漏了哪些」。

## 二、还原目录结构

```
mirror-<卷标>/
  index.html  register.html  bind.html  tutorial.html  feedback.html
  favicon.svg  manifest.json
  assets/css/*.css
  assets/js/*.js
  assets/img/*
```

`assets_<dir>_<name>.<ext>` → `assets/<dir>/<name>.<ext>`。

**HTML 正文一个字都不要改**——它是证据。要改路径就另存一份 `*.local.html`，原件留着。

## 三、扫引用、查缺（定版脚本）

```bash
python3 scripts/mirror_refcheck.py <mirror目录>
```

三类引用来源，缺一类就会漏：

1. HTML 的 `href=` / `src=`
2. **CSS 的 `url(...)`** ——相对**该 CSS 文件**解析（`../img/x.png`），不是相对根目录；背景图漏了整页少底图
3. **JS 里拼出来的路径**——`assets/img/char_0'+i+'.png'` 这类脚本抓不到，要手 grep 一次确认上下限（如 `char_01`～`char_14`）

## 四、缺件回头补抓

站点只要还有**任一端口活着**，静态资源往往整套都在。逐条打状态码，别静默：

```bash
for f in hero-bg.jpg story-bg.png feature-bg.png; do
  printf '%-22s' "$f"
  curl -s -m 15 -o "assets/img/$f" -w 'HTTP %{http_code} %{size_download}B\n' "http://<host>:<port>/assets/img/$f"
done
```

补完再跑一次查缺，直到 **0 缺失**。

> 📌 **补抓全中本身就是证据**：能整套补回 ⇒ 站点**没改内容，只换了入口／端口**。
> 这比端口体检更硬（端口只说明有没有人听，补抓说明文件是不是同一套）。

## 五、本地起服验证（别用 `file://`）

```bash
cd <mirror目录> && exec python3 -m http.server 8899 --bind 127.0.0.1   # 后台起
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8899/index.html
```

- 重页面（十几 MB 图）走 `file://` 容易被渲染进程杀掉；`http://127.0.0.1` 稳。
- 截图：小窗口 ＋ `--force-device-scale-factor=0.6 --disable-dev-shm-usage`，先截首屏确认无破图再放大。

## 六、交付时写清三件事

1. **引用检查结果**：缺 0 处 / 23 张图全在（可给数字）
2. **哪些点不动**：表单提交、答题、登录走服务器接口，本地壳点不动——这是壳的正常边界，不是破图
3. **文件来源**：哪一次抓取作主体 ＋ 哪一次补抓补了哪些件

## 七、常见坑

| 坑 | 机制 | 对策 |
|:--|:--|:--|
| CSS 背景图全缺 | HTML 扫描看不到 `url()` | 单独扫 CSS，按 CSS 位置解析相对路径 |
| 本地打开一片空白 | 原站用绝对路径 `/assets/...` | 起 server 挂根，或批量改相对（改动另存） |
| 图鉴/公告区是空的 | JS 渲染，依赖接口 | 提前说明「壳」的边界；别当破图修 |
| 补抓后仍有缺 | 文件名大小写、多语言目录 | 回原站目录页确认实际路径 |
| 把抓取件当原件改 | 动了就不是证据了 | 原件只读，改件另存 |
