import json
import sqlite3

DB = "/app/data/langbot.db"
con = sqlite3.connect(DB, timeout=30)
row = con.execute("select uuid, config from legacy_pipelines").fetchone()
cfg = json.loads(row[1])
runner_keys = [k for k in cfg["ai"]["runner_config"] if k.startswith("plugin:")]
changed = []
for k in runner_keys:
    rc = cfg["ai"]["runner_config"][k]
    if rc.get("timeout") != 0:
        rc["timeout"] = 0  # 0 = 不设本地 wall-clock 上限，交给宿主 deadline
        changed.append(k)
con.execute("update legacy_pipelines set config=? where uuid=?", (json.dumps(cfg, ensure_ascii=False), row[0]))
con.commit()
print("已设置 timeout=0 的 runner:", changed)
cfg2 = json.loads(con.execute("select config from legacy_pipelines").fetchone()[0])
for k in runner_keys:
    print("  现在:", k, "timeout =", cfg2["ai"]["runner_config"][k].get("timeout"))
con.close()
