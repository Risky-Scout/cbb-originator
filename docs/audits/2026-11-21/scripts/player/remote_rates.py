# READ-ONLY: per market-listed player, season-to-date (2027) and prior-season (2026, 2025, 2024) finals from the latest
# run's PIT feature DB. Writes only /tmp/cbb_forensics_rates.parquet.
import duckdb, pandas as pd, sys
F=sys.argv[1]
ids=[l.strip() for l in open('/tmp/cbb_forensics_mkt_players.csv') if l.strip()]
con=duckdb.connect(F,read_only=True)
cols=[r[0] for r in con.execute("describe feat.player_box_state").fetchall()]
print(cols, file=sys.stderr)
con.register('ids',pd.DataFrame({'canonical_player_id':ids}))
df=con.execute("""SELECT canonical_player_id, season, available_at, source_game_id, s_min, n_app, s_pts, s_ast, s_orb, s_drb, s_tpm
  FROM feat.player_box_state WHERE canonical_player_id IN (SELECT canonical_player_id FROM ids)""").df()
df.to_parquet('/tmp/cbb_forensics_rates.parquet'); print(len(df), df.season.value_counts().to_dict(), file=sys.stderr)
