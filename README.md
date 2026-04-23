# hermes-img-skill

一个给 Hermes 用的图片技能与脚本封装：**不需要切会话模型，不需要为图片任务额外适配执行流，直接读取本机配置里的 `base_url` 和 `api_key`，默认用 `gpt-image-2` 调用 OpenAI-compatible Images API。**

## 这个技能的初衷

这个仓库的核心目标不是做一个“通用图片工具箱”，而是解决 Hermes 里最常见的实际问题：

- 用户只是随口说一句“帮我生成个图”；
- 不希望先切模型、改 provider、改命令习惯；
- 不希望每次图片任务都临时写一套接口适配；
- 希望代理能直接读取现有 Hermes 配置，然后稳定调用图片接口。

所以这里把图片生成/编辑统一收口到 `scripts/remote_image.py`：

1. 自动读取 Hermes 配置中的 `base_url` / `api_key`；
2. 默认使用 `gpt-image-2`；
3. 生成图片时直接请求 `/v1/images/generations`；
4. 有输入图片时走 `/v1/images/edits`；
5. 输出本地文件路径，方便 Telegram / Hermes 继续分发。

## 它解决什么问题

### 1. 不用切模型

即使当前对话主模型不是图片模型，这个脚本也会单独发起图片 API 请求。

也就是说，Hermes 处理图片请求时不用先把整场会话切到图片模型，只需要调用：

```bash
python3 scripts/remote_image.py --prompt "..."
```

### 2. 不用专门适配图片接口

脚本已经把以下事情封装好了：

- 读取 `~/.hermes/config.yaml`
- 回退读取 `~/.hermes/.env`
- 自动组装 OpenAI-compatible 请求
- 生成模式走 `/images/generations`
- 编辑模式走 `/images/edits`
- 自动把返回的 base64 或 URL 落盘

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
- 默认尺寸：`1024x1024`
- 生成接口：`<base_url>/images/generations`
- 编辑接口：`<base_url>/images/edits`
- 输出：时间戳目录下的图片文件

配置读取顺序：

- `OPENAI_API_KEY_IMAGE`
- `~/.hermes/.env` 里的 `OPENAI_API_KEY_IMAGE`
- `OPENAI_API_KEY`
- `~/.hermes/.env` 里的 `OPENAI_API_KEY`
- `~/.hermes/config.yaml` 中的 `model.api_key`

`base_url` 默认读取：

- `~/.hermes/config.yaml` 中的 `model.base_url`
- 若缺失则回退 `https://api.openai.com/v1`

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
python3 scripts/remote_image.py --help
```

### 文生图

```bash
python3 scripts/remote_image.py \
  --prompt "Create a vertical AI illustration of a livestream host in a neon studio" \
  --size 1024x1792 \
  --n 1
```

这会默认使用 `gpt-image-2`，并调用：

```text
<base_url>/v1/images/generations
```

> 说明：脚本内部传入的是 `/images/generations`，而 `base_url` 通常已经是 `.../v1`，所以组合后的完整地址就是 `<base_url>/images/generations`，也就是常见的 `/v1/images/generations`。

### 基于本地图片编辑

```bash
python3 scripts/remote_image.py \
  --input /path/to/input.png \
  --prompt "Only fix the text in the top-right badge; keep everything else unchanged." \
  --size 1536x1024
```

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

## 适合哪些场景

- Telegram 里临时说一句“帮我生成个图”
- 要求明确使用 `gpt-image-2`
- Hermes 当前主模型不是图像模型，但仍需发图
- 需要读取现有配置而不是单独再配一套图片客户端
- 需要局部修图、原图打包、稳定返回本地路径

## License

MIT
