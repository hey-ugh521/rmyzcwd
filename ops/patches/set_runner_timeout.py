import json
import sqlite3
import sys

# 运行期限：必须是一个正数（0 会让宿主侧拿不到 deadline，回落到默认 300s！）
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 900

con = sqlite3.connect("/app/data/langbot.db", timeout=30)
row = con.execute("select uuid, config from legacy_pipelines").fetchone()
cfg = json.loads(row[1])
changed = []
for k, rc in cfg["ai"]["runner_config"].items():
    if k.startswith("plugin:") and rc.get("timeout") != TIMEOUT:
        rc["timeout"] = TIMEOUT
        changed.append(k)
con.execute("update legacy_pipelines set config=? where uuid=?", (json.dumps(cfg, ensure_ascii=False), row[0]))
con.commit()
cfg2 = json.loads(con.execute("select config from legacy_pipelines").fetchone()[0])
print("修改的 runner:", changed)
for k, rc in cfg2["ai"]["runner_config"].items():
    print("  ", k, "timeout =", rc.get("timeout"))
con.close()
