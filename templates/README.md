# Templates

这个目录只放**可变但低风险的模板层**，不放 runtime、鉴权、接口路径或优先级控制逻辑。

## 文件

- `profiles.yaml`
  - 允许外置各 profile 的静态预设：
    - `description`
    - `quality`
    - `size`
    - `prompt_suffix`
- `prompt-templates.yaml`
  - 允许外置少量通用 prompt 片段：
    - `localized_fix_suffix`
    - `preserve_unrelated_suffix`
    - `variant_suffix`

## 设计原则

- Python 内置默认值始终存在
- YAML 只是覆盖层，不是必需层
- YAML 解析失败、字段类型不对、字段缺失时，自动回退到内置默认
- 只做白名单字段 merge

## 不要外置的内容

以下逻辑仍应保留在 Python：

- `base_url` / `api_key` / `model` 的解析优先级
- mode / profile 推断逻辑
- 尺寸推断逻辑
- API 请求结构与 endpoint
- 错误分类
- 字段兼容性回退
- 输出目录与归档控制逻辑

## 目标

让 prompt/profile 调优更方便，但不破坏 skill 的稳定性与可解释性。
