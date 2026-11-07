# P5b 扩展接口验收（L3）

> 状态：2026-11-08 完成。验收脚本 `ops/l3/acceptance_extensions.py`，9/9 通过。

## 1. 验收矩阵

| 扩展点 | 方式 | 验收 | 结果 |
|---|---|---|---|
| 自定义模型/画笔 | `workflows/models.json` 增加条目 | `GET /api/models` 识别 | ✅ |
| 自定义角色卡 | `data/characters/*.json` 放入卡片 | `GET /api/characters` 识别 | ✅ |
| 自定义工作流 | `workflows/*.json` 放入文件 | `GET /api/workflows` 识别（新增接口） | ✅ |
| 本地组件覆盖 | `components.local.json` 新增组件 + 下载器安装 | marker 文件生效、可卸载 | ✅ |
| MCP / 插件 | LangBot MCP（comfyui/memory）+ 插件目录 | 既有证据（L2/P2 日志） | ✅ |
| 回滚 | 删除扩展后 API 不再返回 | 模型/角色/工作流均验证 | ✅ |

## 2. 新增接口

`GET /api/workflows`（Media Gateway，扩展接口）：

```json
{
  "ok": true,
  "dir": "D:\\AI\\comic\\workflows",
  "workflows": [
    {"name": "portrait", "file": "comic-portrait.json", "source": "builtin", "exists": true},
    {"name": "comic-flux3.json", "file": "comic-flux3.json", "source": "custom", "exists": true}
  ]
}
```

用途：控制台/第三方可发现内置与自定义工作流，无需读文件系统。

## 3. 运行验收

```powershell
python ops\l3\acceptance_extensions.py
# 9/9 通过  → exit 0
```

脚本特性：
- 修改前备份、结束后恢复（模型注册表/角色卡/工作流文件均回滚）
- 本地组件测试使用临时目录 + 自造 7z 包，不污染真机 `runtime`
- 不修改 LangBot 数据库（MCP 用既有证据说明）

## 4. 结论

「放入扩展目录即可生效，无需重装」在以下四类扩展点上成立：
模型注册表、角色卡、工作流、本地组件清单。
MCP/插件为 LangBot 侧能力（目录/数据库管理），已验证加载链路。

## 5. 待办

- [ ] 控制台 UI 增加工作流/能力页（消费 `/api/workflows`、manifest）
- [ ] 自定义 MCP 的安装向导（写入 LangBot 配置/DB）
- [ ] 扩展点版本化（`api_version`）与兼容性检查
