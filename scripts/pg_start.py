import pathlib
import re

import pgserver

db = pgserver.get_server("D:/PROJECT 4RTH YR/data/pgdata_e2e", cleanup_mode=None)
port = db.get_uri().split(":")[-1].split("/")[0]
p = pathlib.Path("D:/PROJECT 4RTH YR/data/run/env.sh")
s = p.read_text()
s = re.sub(r"127\.0\.0\.1:\d+/kalpa_e2e", f"127.0.0.1:{port}/kalpa_e2e", s)
p.write_text(s)
print("pg on", port)
