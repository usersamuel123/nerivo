import json, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parent
db=ROOT/"agent.db"
if not db.exists():
    print("Nessun database agente ancora presente.")
    raise SystemExit(0)
c=sqlite3.connect(db)
since=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
rows=c.execute("select ts,kind,data from events where ts>=? order by id desc limit 50",(since,)).fetchall()
print("# NERIVO — Daily Brief")
print(datetime.now().astimezone().strftime("%d/%m/%Y %H:%M"))
print()
print(f"Eventi ultime 24h: {len(rows)}")
for ts,kind,data in rows[:20]:
    try: d=json.loads(data)
    except: d=data
    print(f"- {ts} | {kind} | {d}")
