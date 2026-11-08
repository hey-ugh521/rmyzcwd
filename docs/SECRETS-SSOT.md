# 密钥 SSOT（单一事实源）

> 2026-11-08 完成。secrets.json 是密钥唯一编辑源；各消费方视图由脚本生成/校验。

---

## 1. 结构

```
secrets.json（唯一编辑源，gitignore）
├─ pg.password
├─ deepseek.api_key
├─ core.api_key
├─ langbot.global_api_key / secret / recovery_key / integration_api_key
└─ napcat.http_token / ws_token / webui_token
```

路径解析（Core 与脚本同口径）：

```
MOBIUS_SECRETS_FILE              # 环境变量优先
→ config.json 顶层 secrets_file   # 本机：D:\AI\mobius\secrets.json
→ <root>/data/secrets.json       # 默认（安装版布局）
→ D:\AI\mobius\secrets.json      # 兜底
```

## 2. 代码接入

| 位置 | 行为 |
|---|---|
| `core/secrets.py` | 加载/校验、路径解析、DSN 口令替换、`apply_to_config()` |
| `core/store.py` | 配置展开后调用 overlay：core.api_key / llm.api_key / integrations key / db_dsn / gateway DSN |
| `ops/credentials.py` | 取值顺序改为：环境变量 → **secrets.json** → `.env` → config.json |
| `ops/secrets/rotate.py` | init / show / check / sync / rotate |

## 3. 命令

```powershell
python ops/secrets/rotate.py init            # 从现有视图反推生成（幂等，已存在不动）
python ops/secrets/rotate.py show            # 只显示掩码 + drift 摘要
python ops/secrets/rotate.py check           # 检查所有视图是否与 SSOT 一致（exit 1=drift）
python ops/secrets/rotate.py sync            # 写回所有消费方（备份+原子写，内容相同不写）
python ops/secrets/rotate.py rotate --key <name> [--value X] [--sync]
```

`--key` 支持：`pg` / `deepseek` / `core` / `langbot-global` / `langbot-secret` /
`langbot-recovery` / `langbot-integration` / `napcat-http` / `napcat-ws` / `napcat-webui`。
`deepseek` 无生成规则，需 `--value`。

## 4. 消费方视图

| 视图 | 字段 |
|---|---|
| `.env` | PG_PASSWORD、DEEPSEEK_API_KEY、NAPCAT_HTTP_TOKEN、LANGBOT_API_KEY |
| `config.json` | core.api_key、llm.api_key、integrations.langbot.api_key、db_dsn、gateway DSN |
| LangBot `data/config.yaml` | global_api_key、secret、recovery_key、database.password |
| LangBot PG | `model_providers` DeepSeek api_keys；`bots` onebot access-token |
| NapCat | `onebot11_*.json` http/ws token；`webui.json` token |

## 5. 验收证据（真实环境）

| 测试 | 结果 |
|---|---|
| `init` | ✅ 从 10 个视图字段反推成功，写入 `D:\AI\mobius\secrets.json` |
| `show` | ✅ 全掩码，drift 0 |
| `check` | ✅ OK=17 DRIFT=0 SKIP=1（可选 LANGBOT_API_KEY 未配置） |
| `sync` 幂等 | ✅ 内容相同不写；首次补写可选 `.env LANGBOT_API_KEY` 后稳定 |
| 人为 drift | ✅ 篡改 `.env DEEPSEEK_API_KEY` → `check` 报 DRIFT=1 且 exit 1 |
| `sync` 修复 | ✅ 复检 DRIFT=0 exit 0 |
| 低风险轮换 | ✅ `rotate --key langbot-recovery --sync` → check 0 |
| 单测 | ✅ `tests/test_secrets_rotate.py` 136/136、`tests/test_secrets_loader.py` 14/14 |

## 6. 安全注意

- `secrets.json` 已在 `.gitignore`（含 `**/secrets.json`），不要提交
- `rotate.py` 输出永远掩码；备份在 `backups/security/<时间戳>/`（gitignore）
- 安装版首次运行若没有 secrets.json，仍回退 `config.json`/`.env` 旧行为；建议向导后续生成 secrets.json
- 轮换后需重启对应服务才生效（PG/NapCat 需重启；Core/Gateway/LangBot 视改动字段）

## 7. 仍待办

- [ ] 向导在首次运行生成 secrets.json
- [ ] `rotate --restart`（按 key 自动重启受影响服务）
- [ ] 密钥文件 ACL 收紧（仅当前用户）
