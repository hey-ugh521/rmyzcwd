# Möbius 系统提示词回归测试（promptfoo）

用途：在改系统提示词之前，跑一组固定用例，确认人格、口吻、长度、中文等基线没被改坏。

## 跑法

```powershell
# API key 从 LangBot 库里读，不要写进仓库
$env:DEEPSEEK_API_KEY = (从 langbot.db model_providers 读)

cd tests/promptfoo
promptfoo eval -c promptfooconfig.yaml
promptfoo view          # 浏览器里看结果对比
```

## 加新提示词

1. 把新版本存成 `prompts/system_new.txt`
2. 在 `promptfooconfig.yaml` 的 tests 里加一组 `systemFile: prompts/system_new.txt`
3. 跑 eval，对比两列的通过率与输出

## 注意

- 每个用例都会真实调用 DeepSeek，注意成本；当前 5 个用例 × 2 个提示词 = 10 次调用
- `system_current.txt` 是从 LangBot 数据库导出的当前线上提示词，改提示词后重新导出
