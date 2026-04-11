import duckdb
db = duckdb.connect("data/db/cricket.duckdb", read_only=True)

print("=== players table ===")
print(db.execute("SELECT name FROM players WHERE LOWER(name) LIKE '%suryavanshi%'").fetchall())

print("\n=== player_profiles ===")
print(db.execute("SELECT display_name, full_name FROM player_profiles WHERE LOWER(display_name) LIKE '%suryavanshi%' OR LOWER(full_name) LIKE '%suryavanshi%'").fetchall())

print("\n=== deliveries (batter) ===")
print(db.execute("SELECT DISTINCT batter FROM deliveries WHERE LOWER(batter) LIKE '%suryavanshi%' OR LOWER(batter) LIKE '%vaibhav%'").fetchall())

print("\n=== IPL 2025 season check ===")
print(db.execute("SELECT season, COUNT(*) FROM matches WHERE event_name = 'Indian Premier League' GROUP BY season ORDER BY season DESC LIMIT 5").fetchall())

print("\n=== Latest Cricsheet data date ===")
print(db.execute("SELECT MAX(date_start) FROM matches").fetchall())

print("\n=== V Suryavanshi IPL matches ===")
print(db.execute("SELECT COUNT(DISTINCT m.match_id) FROM deliveries d JOIN matches m ON d.match_id=m.match_id WHERE d.batter='V Suryavanshi' AND m.event_name='Indian Premier League'").fetchall())

print("\n=== V Suryavanshi all matches ===")
print(db.execute("SELECT m.event_name, COUNT(DISTINCT m.match_id) FROM deliveries d JOIN matches m ON d.match_id=m.match_id WHERE d.batter='V Suryavanshi' GROUP BY m.event_name").fetchall())
