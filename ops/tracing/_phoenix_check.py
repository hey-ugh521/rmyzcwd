import glob
import sqlite3

paths = glob.glob("/mnt/phoenix/**/*.db", recursive=True)
print("db 文件:", paths)
for p in paths[:2]:
    con = sqlite3.connect(p)
    tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    print(f"{p}: 表 {len(tabs)} 个")
    for t in ("spans", "traces", "projects"):
        if t in tabs:
            n = con.execute(f"select count(*) from {t}").fetchone()[0]
            print(f"  {t}: {n}")
    con.close()
