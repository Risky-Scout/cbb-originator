"""CBB_PUBLIC_DATA_CONTRACT_V1: frozen fields, sportsbook sign conventions, allow-list, row-for-row QA, mobile page."""
import pytest

from cbb_platform.prod import game_exports as GE, market_exports as ME, public_contract as PCT


def _line(**kw):
    base = {"GAME": "Away U @ Home U", "GAME_ID": "G-1", "TIP_ET": "2026-11-25 19:00", "HOME_TEAM": "Home U",
            "AWAY_TEAM": "Away U", "MODEL_HOME_SCORE": 78.24, "MODEL_AWAY_SCORE": 70.11, "MODEL_MARGIN": 8.13,
            "MODEL_TOTAL": 148.35, "FORECAST_ID": "F-x", "PMF_SHA256": "abc", "QUOTE_ID": "q", "POLICY": "p"}
    base.update(kw)
    return [base.get(k) for k in GE.LINE_COLS]


def _prop(**kw):
    base = {"Game": "Away U @ Home U", "Tip Time ET": "2026-11-25 19:00", "Player": "P One", "Team": "Home U",
            "Opponent": "Away U", "Stat": "PTS", "Model Projection": 14.234, "Best Market Line": 13.5,
            "Preferred Side": "OVER", "Model Probability": 0.581, "Fair Market Probability": 0.52, "Probability Edge": 0.061,
            "Best Book": "bk", "Best American Odds": -110, "Preferred EV": 0.04, "Is Actionable": False,
            "Reporting Tier": "PASS", "Engine ID": "E", "Forecast ID": "F"}
    base.update(kw)
    return [base.get(k) for k in ME.BOARD_COLS]


def test_model_line_conventions():
    rows = [_line(MARKET_TYPE="SPREAD", SIDE="HOME", LINE=-6.5, PICK="Home U -6.5", BOOK="bk", ODDS=-110, **{"MODEL%": 0.6, "MARKET%": 0.5}),
            _line(MARKET_TYPE="SPREAD", SIDE="AWAY", LINE=6.5, PICK="NO_POSITIVE_EV", **{"MODEL%": 0.4}),
            _line(MARKET_TYPE="GAME_TOTAL", SIDE="OVER", LINE=146.5), _line(MARKET_TYPE="HOME_TEAM_TOTAL", SIDE="OVER", LINE=77.5),
            _line(MARKET_TYPE="AWAY_TEAM_TOTAL", SIDE="UNDER", LINE=71.5), _line(MARKET_TYPE="MONEYLINE", SIDE="HOME", **{"MODEL%": 0.75})]
    g = PCT.game_records(rows, GE.LINE_COLS)
    assert [r["model_line"] for r in g] == [-8.1, 8.1, round(148.35, 1), 78.2, 70.1, -300]     # HOME = -margin, AWAY = +margin
    assert g[0]["model_side"] == "Home U -6.5" and g[1]["model_side"] is None
    assert PCT.fair_american(0.25) == 300 and PCT.fair_american(0.5) == -100


def test_payload_allow_list_and_no_wager_labels():
    p = PCT.payload(PCT.game_records([_line(MARKET_TYPE="SPREAD", SIDE="HOME", LINE=-6.5)], GE.LINE_COLS),
                    PCT.prop_records([_prop()], ME.BOARD_COLS), "2026-11-25T18:00:00Z", "2026-11-25")
    assert p["default_tab"] == "Game Lines & Totals" and p["actionability_validated"] is False
    assert tuple(p["tabs"]["Game Lines & Totals"][0]) == PCT.GAME_FIELDS and tuple(p["tabs"]["Player Props"][0]) == PCT.PROP_FIELDS
    flat = str(p)
    for t in ("abc", "F-x", "Engine", "PASS", "Is Actionable"):
        assert t not in flat
    bad = dict(p); bad["tabs"] = {**p["tabs"], "Player Props": [{**p["tabs"]["Player Props"][0], "engine_id": "E"}]}
    with pytest.raises(ValueError):
        PCT.validate(bad)


def test_row_for_row_qa_and_model_only_games():
    lines = [_line(MARKET_TYPE="SPREAD", SIDE="HOME", LINE=-6.5), _line(GAME="C @ D", MARKET_TYPE="ALL", SIDE="HOME_WIN_PROB"),
             _line(GAME="C @ D", MARKET_TYPE="ALL")]
    props = [_prop(), _prop(Player="P Two", **{"Preferred Side": "NO_POSITIVE_EV"})]
    p = PCT.payload(PCT.game_records(lines, GE.LINE_COLS), PCT.prop_records(props, ME.BOARD_COLS), "t", "2026-11-25")
    assert len(p["tabs"]["Game Lines & Totals"]) == 2                           # one model-only card per game
    assert p["tabs"]["Player Props"][1]["model_side"] is None
    assert PCT.qa_rows(p, lines, GE.LINE_COLS, props, ME.BOARD_COLS)["WIZARDOFODDS_ROW_QA"] == "PASS"
    p["tabs"]["Player Props"][0]["model_probability"] = 0.9
    assert PCT.qa_rows(p, lines, GE.LINE_COLS, props, ME.BOARD_COLS)["WIZARDOFODDS_ROW_QA"] == "FAIL"


def test_mobile_page_has_two_tabs_default_game_lines_and_no_external_requests():
    p = PCT.payload(PCT.game_records([_line(MARKET_TYPE="GAME_TOTAL", SIDE="OVER", LINE=146.5)], GE.LINE_COLS),
                    PCT.prop_records([_prop()], ME.BOARD_COLS), "t", "2026-11-25")
    h = PCT.render_html(p)
    assert "width=device-width" in h and "Game Lines &amp; Totals" in h or "Game Lines & Totals" in h
    assert "<section id='p' class='grid' hidden>" in h and "<section id='g' class='grid'>" in h
    assert "http://" not in h and "https://" not in h and "<script src" not in h
