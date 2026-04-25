# hermes-img-skill

一个给 Hermes 用的图片技能与脚本封装：**不需要切会话模型，不需要为图片任务额外适配执行流；默认仍可直接读取本机主配置里的 `base_url` 和 `api_key`，也支持图片链路的可选独立覆盖；默认用 `gpt-image-2` 调用 OpenAI-compatible Images API。**

## 这个技能的初衷

这个仓库的核心目标不是做一个“通用图片工具箱”，而是解决 Hermes 里最常见的实际问题：

- 用户只是随口说一句“帮我生成个图”；
- 不希望先切模型、改 provider、改命令习惯；
- 不希望每次图片任务都临时写一套接口适配；
- 希望代理能直接读取现有 Hermes 配置，然后稳定调用图片接口。

所以这里把图片生成/编辑统一收口到 `~/.hermes/skills/img/scripts/remote_image.py`：

1. 保持文字主对话继续走当前模型；
2. 图片生成/编辑时单独调用图片接口，不污染普通聊天链路；
3. 生成图片时优先按 transport 自动选择协议（默认先试 `/v1/images/generations`，如果该站点生图链路不可用，再回退 `/v1/responses`）；
4. 纯编辑/局部修改走 `/v1/images/edits`；如果是“参考这张图生成 / 以这张图为参考 / 同风格来一张”这类参考图生成，则走生成链路并优先使用 `/v1/responses`；
5. 图片相关 base_url / key / model 可独立覆盖，但默认仍可复用 Hermes 主配置。


## 它解决什么问题

### 1. 不用切模型

即使当前对话主模型不是图片模型，这个脚本也会单独发起图片 API 请求。

也就是说，Hermes 处理图片请求时不用先把整场会话切到图片模型，只需要调用：

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --prompt "..."
```

### 2. 不用专门适配图片接口

脚本已经把以下事情封装好了：

- 读取 `~/.hermes/config.yaml`
- 回退读取 `~/.hermes/.env`
- 自动组装 OpenAI-compatible 请求
- 生成模式支持 `auto / responses / images / chat` transport，默认 `auto`，其中 `auto` 会先试 `/images/generations`，再回退 `/responses`
- 编辑模式走 `/images/edits`
- 自动把返回的 base64 或 URL 落盘

注意：如果 `~/.hermes/skills/img/` 目录里还有历史遗留的 `config.yaml` 或备份文件，不要默认把它当成当前生效配置。当前 `remote_image.py` 的运行时来源仍以 `--base-url` / `--api-key`、环境变量、`~/.hermes/.env`、`~/.hermes/config.yaml` 为准；排障时应先用 `--dry-run --show-resolved` 看实际解析结果。

上层技能只需要决定：

- 用户是要生成还是编辑
- 要不要传 `--input`
- 图片尺寸、提示词、返回格式怎么说

### 3. 统一 Hermes 图片任务入口

仓库里的 `SKILL.md` 约束了图片任务的默认行为：

- 普通自然语言“生成图片”请求直接走这个技能
- 默认 `n=1`
- 默认模型是 `gpt-image-2`
- 不静默切换到别的图片后端
- Telegram 原图需求要走 zip 打包
- 局部文字修复不要整图重绘
- 用户明确说“快速来一张 / 先来一版 / 先看方向”时，应优先走 `fast`，避免默认落到 `official-like + quality=high`

## 仓库结构

```text
.
├── SKILL.md
├── README.md
├── requirements.txt
├── scripts/
│   └── remote_image.py
└── fix-localized-text-in-attached-image-with-crop-and-composite/
    └── SKILL.md
```

## 关键文件说明

### `scripts/remote_image.py`
统一图片生成/编辑入口。

关键行为：

- 默认模型：`gpt-image-2`
- 默认尺寸：普通生成如果没有显式 `--size`，默认省略 size 字段，让 provider 自然决定；显式尺寸仍优先保留
- 生成接口：默认按 transport 自动选择，优先 `<base_url>/images/generations`，失败再回退 `<base_url>/responses`
- 编辑接口：`<base_url>/images/edits`
- 输出：时间戳目录下的图片文件
- 可选 profile：`official-like` / `fast` / `anime-poster` / `photo-real`
- 可选 profile：`official-like` / `fast` / `anime-poster` / `photo-real` / `social-cover`
- 支持 `--show-resolved` 查看解析后的 profile / size / quality / prompt
- 支持 `--best-of`、`--record-run`、`--archive`、`--negative-hints`、`--variant-of`

配置读取顺序：

- `--api-key`
- `OPENAI_API_KEY_IMAGE`
- `~/.hermes/.env` 里的 `OPENAI_API_KEY_IMAGE`
- `OPENAI_API_KEY`
- `~/.hermes/.env` 里的 `OPENAI_API_KEY`
- `~/.hermes/config.yaml` 中的 `model.api_key`

`base_url` 默认读取：

- `--base-url`
- `OPENAI_BASE_URL_IMAGE`
- `~/.hermes/.env` 里的 `OPENAI_BASE_URL_IMAGE`
- `OPENAI_BASE_URL`
- `~/.hermes/.env` 里的 `OPENAI_BASE_URL`
- `~/.hermes/config.yaml` 中的 `model.base_url`
- 若缺失则回退 `https://api.openai.com/v1`

`model` 默认读取：

- `--model`
- `OPENAI_IMAGE_MODEL`
- `~/.hermes/.env` 里的 `OPENAI_IMAGE_MODEL`
- 默认 `gpt-image-2`

这意味着：

- **`base_url` 不是必须单独配置的**
- 如果没有设置图片专用 `base_url`，脚本会继续回退到 Hermes 主配置中的 `model.base_url`
- 只有显式设置图片专用覆盖时，图片链路才会与主链路分开

### `SKILL.md`
Hermes 主技能说明。

重点不是介绍接口参数，而是规定：

- 哪些请求应该自动走图片技能
- 默认如何控制成本与变体数量
- 如何向用户回报真实使用的模型
- 什么时候不能静默降级
- Telegram 图片如何无损交付

### `fix-localized-text-in-attached-image-with-crop-and-composite/SKILL.md`
局部修图子技能。

用于“只改一小块字、其他地方别动”的场景，避免整张图被模型重绘。

## 快速使用

先安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

查看帮助：

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --help
```

查看可用 profile：

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --list-profiles --prompt x
```

查看成熟归档与多候选：

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --prompt "做一张 AI 产品发布社媒封面" \
  --profile social-cover \
  --negative-hints "avoid watermark, avoid unreadable tiny text, avoid busy background behind title area" \
  --best-of 3 \
  --record-run \
  --archive \
  --show-resolved
```

### 文生图

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --prompt "Create a vertical AI illustration of a livestream host in a neon studio" \
  --profile official-like \
  --n 1
```

这会默认使用 `gpt-image-2`，并调用：

```text
<base_url>/v1/images/generations
```

> 说明：脚本内部传入的是 `/images/generations`，而 `base_url` 通常已经是 `.../v1`，所以组合后的完整地址就是 `<base_url>/images/generations`，也就是常见的 `/v1/images/generations`。

### 基于本地图片编辑

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --input /path/to/input.png \
  --prompt "Only fix the text in the top-right badge; keep everything else unchanged." \
  --mode localized-fix \
  --profile photo-real \
  --show-resolved
```

### 复用上一次结果做变体

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py \
  --prompt "同风格再来一个版本，更有科技感" \
  --profile official-like \
  --variant-of /abs/path/to/run-record.json \
  --record-run
```

### 只在需要时给图片单独指定 base_url

如果你想让图片单独走别的上游，可以显式设置：

```bash
OPENAI_BASE_URL_IMAGE=https://example.com/v1 \
OPENAI_API_KEY_IMAGE=sk-xxx \
python3 ~/.hermes/skills/img/scripts/remote_image.py --prompt "做一张海报"
```

如果你**不设置** `OPENAI_BASE_URL_IMAGE`，脚本会继续使用主配置中的：

```yaml
model:
  base_url: ...
```

也就是：**图片独立 `base_url` 是可选增强，不是必填项。**

## 接入 Hermes

推荐作为 Hermes skill 放在：

```text
~/.hermes/skills/img/
├── SKILL.md
├── scripts/
│   └── remote_image.py
└── fix-localized-text-in-attached-image-with-crop-and-composite/
    └── SKILL.md
```

这样代理在收到图片相关请求时，可以：

- 不切模型；
- 不重写接口适配；
- 直接读取本地配置；
- 默认用 `gpt-image-2` 发图像请求；
- 再把结果回传到 Telegram 或其他渠道。

## 新增能力概览

### P1
- `--best-of N`：显式多候选，不默认开启
- `--record-run`：保存 `run-record.json`
- 更细尺寸策略：支持 banner / wallpaper / social-cover 倾向
- 错误分类：stderr JSON 会附带 `error_type`

### P2
- `--mode generate|edit|localized-fix`
- `--variant-of /path/to/run-record.json`
- `--compat-fallback`：仅丢弃可选字段做一次兼容性重试
- 局部修图提示增强

### P3
- `templates/profiles.yaml`
- `templates/prompt-templates.yaml`
- `social-cover` profile
- `--negative-hints`
- `--archive`：输出 `result/` + `meta/` + `outputs.zip`

### 目录管理（适配 Hermes CLI + Telegram）
- `--list-runs`
- `--cleanup-days N`
- `--cleanup-keep N`
- `--cleanup-all`
- 管理模式下不要求 `--prompt`

## 归档结构

启用 `--archive` 后，每次运行目录会更成熟：

```text
generated-images/<timestamp>/
├── result/
│   └── image-01.png
├── meta/
│   └── run-record.json
└── outputs.zip
```

后续如果继续扩展，也建议保持 `result/` 与 `meta/` 分层。

## 图片输出目录在哪

默认输出根目录是：

```text
$HERMES_HOME/generated-images
```

如果没有单独设置 `HERMES_HOME`，通常就是：

```text
~/.hermes/generated-images
```

在当前这台机器上，按前面的 dry-run 示例，对应路径通常会像：

```text
/data/data/com.termux/files/home/.hermes/generated-images/20260423-211855
```

## 目录管理命令

### 查看最近的图片 run 目录

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --list-runs
```

### 只看最近 10 个 run

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --list-runs --list-runs-limit 10
```

### 预演：看看 7 天前的 run 会删哪些

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-days 7 --dry-run
```

### 真正删除 7 天前的 run

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-days 7
```

### 只保留最近 20 个 run

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-keep 20
```

### 清空整个图片输出目录（高风险）

```bash
python3 ~/.hermes/skills/img/scripts/remote_image.py --cleanup-all
```

> 建议在 Telegram 里先用自然语言让 Hermes 做 `--dry-run` 预演，再决定是否真的删除。

## Telegram / Hermes 自然语言示例

你在 Telegram 机器人里可以直接这样说：

- “帮我看下图片缓存目录”
- “列出最近 10 个出图目录”
- “删掉 7 天前的图片”
- “先看看会删哪些旧图”
- “只保留最近 20 次生成”
- “清空图片缓存”

skill 会自动路由到 `remote_image.py` 的目录管理模式，而不是要求你手工记 CLI 参数。

目录管理返回 JSON 中还会带一个 `data.summary`，适合 Hermes/TG 先用它生成第一句自然语言回复，再补充细节列表。

## 适合哪些场景

- Telegram 里临时说一句“帮我生成个图”
- 要求明确使用 `gpt-image-2`
- Hermes 当前主模型不是图像模型，但仍需发图
- 需要读取现有配置而不是单独再配一套图片客户端
- 需要局部修图、原图打包、稳定返回本地路径

## License

MIT
