"""Analyze the gap between Kaggle and Cricsheet Test match data."""
import duckdb

con = duckdb.connect("data/db/cricket.duckdb", read_only=True)

print("=== Kaggle Test Matches ===")
r = con.execute("SELECT COUNT(*), MIN(\"Match Start Date\"), MAX(\"Match Start Date\") FROM kaggle_matches").fetchone()
print(f"  Count: {r[0]}, Range: {r[1]} to {r[2]}")

print("\n=== Cricsheet Test Matches ===")
r = con.execute("SELECT COUNT(*), MIN(date_start), MAX(date_start) FROM matches WHERE match_type='Test'").fetchone()
print(f"  Count: {r[0]}, Range: {r[1]} to {r[2]}")

print("\n=== Kaggle matches by year (last 5 years) ===")
rows = con.execute("""
    SELECT EXTRACT(YEAR FROM "Match Start Date")::INT as yr, COUNT(*)
    FROM kaggle_matches
    GROUP BY yr ORDER BY yr DESC LIMIT 10
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} matches")

print("\n=== Cricsheet Test matches by year (last 5 years) ===")
rows = con.execute("""
    SELECT EXTRACT(YEAR FROM date_start)::INT as yr, COUNT(*)
    FROM matches WHERE match_type='Test'
    GROUP BY yr ORDER BY yr DESC LIMIT 10
""").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} matches")

# Check what the latest Kaggle match ID looks like vs Cricsheet
print("\n=== Kaggle Match ID format (sample) ===")
rows = con.execute("""SELECT "Match ID", "Match Name", "Match Start Date" FROM kaggle_matches ORDER BY "Match Start Date" DESC LIMIT 5""").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]}")

print("\n=== Cricsheet Test matches (recent, sample) ===")
rows = con.execute("""SELECT match_id, team1, team2, date_start FROM matches WHERE match_type='Test' ORDER BY date_start DESC LIMIT 5""").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} vs {r[2]} | {r[3]}")

# Check overlap - matches in both sources
print("\n=== Overlap check: Cricsheet match_ids that match Kaggle Match IDs ===")
r = con.execute("""
    SELECT COUNT(*) FROM matches m
    JOIN kaggle_matches km ON CAST(m.match_id AS BIGINT) = km."Match ID"
    WHERE m.match_type='Test'
""").fetchone()
print(f"  Exact match_id overlap: {r[0]}")

# Player mapping check
print("\n=== kaggle_players sample (Tendulkar) ===")
rows = con.execute("SELECT player_id, player_name FROM kaggle_players WHERE player_name ILIKE '%tendulkar%'").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]}")

print("\n=== Cricsheet players sample (Tendulkar) ===")
rows = con.execute("SELECT cricsheet_id, name, key_cricinfo FROM players WHERE name ILIKE '%tendulkar%'").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | key_cricinfo={r[2]}")

# Check if kaggle batsman IDs match kaggle_players.player_id
print("\n=== Kaggle batting: do batsman IDs map to kaggle_players? ===")
r = con.execute("""
    SELECT COUNT(*) as total,
           COUNT(CASE WHEN p.player_id IS NOT NULL THEN 1 END) as mapped
    FROM kaggle_batting b
    LEFT JOIN kaggle_players p ON b.batsman = p.player_id
""").fetchone()
print(f"  Total batting rows: {r[0]}, Mapped to player: {r[1]}")

# Check key_cricinfo in cricsheet players vs kaggle player_object_id
print("\n=== Do cricsheet key_cricinfo match kaggle player_object_id? ===")
r = con.execute("""
    SELECT COUNT(*) FROM players p
    JOIN kaggle_players kp ON CAST(p.key_cricinfo AS BIGINT) = kp.player_object_id
    WHERE p.key_cricinfo IS NOT NULL AND p.key_cricinfo != ''
""").fetchone()
print(f"  Matches via key_cricinfo <-> player_object_id: {r[0]}")

# Also check player_id direct match
print("\n=== Do cricsheet key_cricinfo match kaggle player_id? ===")
r = con.execute("""
    SELECT COUNT(*) FROM players p
    JOIN kaggle_players kp ON CAST(p.key_cricinfo AS BIGINT) = kp.player_id
    WHERE p.key_cricinfo IS NOT NULL AND p.key_cricinfo != ''
""").fetchone()
print(f"  Matches via key_cricinfo <-> player_id: {r[0]}")

con.close()
