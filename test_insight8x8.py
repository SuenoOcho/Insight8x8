import os
import tempfile
import textwrap
import unittest
from unittest.mock import patch

os.environ.setdefault(
    "INSIGHT8X8_DB", os.path.join(tempfile.gettempdir(), "insight8x8-unittest.db")
)

from chesscom import (
    ChessComClient,
    ChessComError,
    game_id,
    move_sans,
    normalize_stats,
    parse_game,
    parse_headers,
    validate_username,
)
from coaching import (
    GROQ_MODEL,
    GROQ_URL,
    LLMCoach,
    check_opening_book_with_llm,
    explain_position_with_coach,
    generate_lessons,
    improvement_summary,
    load_env_file,
    progress_history,
    record_progress,
    softened_classification,
    training_report,
)
from insight8x8 import compute_insights
from engine import (
    StockfishAnalyzer,
    build_game_analysis,
    classify_loss,
    format_score,
    score_value,
    validate_fen,
    validate_pgn,
)
from intelligence import (
    classify_loss_for_rating,
    explain_positional_mistake,
    is_common_opening_move,
    move_lesson,
    position_factors,
    recurring_weaknesses,
    tactical_motifs,
    why_is_better,
)
from store import Store
from ui_widgets import EvalBar, captured_pieces
from utils import csv_safe, filter_game_details, fmt_date, game_matches_filters


PGN = """
[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.11.03"]
[White "Hikaru"]
[Black "Carokannlover213"]
[Result "1-0"]
[UTCTime "12:00:00"]
[ECO "A45"]
[Opening "Trompowsky Attack"]
[WhiteElo "3200"]
[BlackElo "3000"]
[TimeControl "180"]
[UTCDate "2026.11.03"]

1. d4 {book} d5 2. Bg5 (2. Nf3 Nf6) 2... h6 3. Bh4 c5 1-0
"""

RAW_GAME = {
    "uuid": "test-uuid-123",
    "url": "https://www.chess.com/game/live/1",
    "pgn": PGN,
    "end_time": 1793719200,
    "time_control": "180",
    "time_class": "blitz",
    "rated": True,
    "rules": "chess",
    "white": {"username": "Hikaru", "rating": 3200, "result": "win"},
    "black": {"username": "Carokannlover213", "rating": 3000, "result": "checkmated"},
    "accuracies": {"white": 91.12, "black": 83.37},
}


class Insight8x8Tests(unittest.TestCase):
    def test_username_validation(self):
        self.assertEqual(validate_username("  Hikaru  "), "hikaru")
        with self.assertRaises(ChessComError):
            validate_username("not valid!")

    def test_headers_and_move_sans(self):
        headers = parse_headers(PGN)
        self.assertEqual(headers["Opening"], "Trompowsky Attack")
        self.assertEqual(move_sans(PGN), ["d4", "d5", "Bg5", "h6", "Bh4", "c5"])

    def test_parse_game_summary(self):
        summary = parse_game(RAW_GAME, "hikaru")
        self.assertEqual(summary["id"], "test-uuid-123")
        self.assertEqual(summary["color"], "white")
        self.assertEqual(summary["result"], "win")
        self.assertEqual(summary["moves"], 3)
        self.assertEqual(summary["accuracy"], 91.12)
        self.assertEqual(summary["opponentAccuracy"], 83.37)
        self.assertEqual(summary["whiteAccuracy"], 91.12)
        self.assertEqual(summary["blackAccuracy"], 83.37)
        self.assertTrue(summary["supported"])

    def test_game_id_fallback_is_stable(self):
        first = {"url": RAW_GAME["url"], "pgn": PGN}
        second = {"pgn": PGN, "url": RAW_GAME["url"]}
        self.assertEqual(game_id(first), game_id(second))
        self.assertNotEqual(game_id(first), game_id({"url": "other", "pgn": PGN}))

    def test_normalize_stats(self):
        stats = normalize_stats(
            {
                "chess_rapid": {
                    "last": {"rating": 1800},
                    "best": {"rating": 1950, "date": 1700000000},
                    "record": {"win": 10, "loss": 7, "draw": 3},
                },
                "chess_blitz": {
                    "last": {"rating": 1700},
                    "best": {"rating": 1900},
                    "record": {"win": 5, "loss": 5, "draw": 2},
                },
            }
        )
        self.assertEqual(stats["ratings"]["rapid"], 1800)
        self.assertEqual(stats["highest"]["rating"], 1950)
        self.assertEqual(stats["totals"]["games"], 32)
        self.assertEqual(stats["totals"]["wins"], 15)
        self.assertEqual(stats["totals"]["winPercentage"], 46.9)
        self.assertEqual(normalize_stats({})["highest"], {})

    def test_filter_and_csv_safety(self):
        games = [
            {"result": "win", "timeClass": "blitz", "color": "white"},
            {"result": "loss", "timeClass": "rapid", "color": "black"},
        ]
        filtered = filter_game_details(
            games, {"result": "wins", "timeClass": "blitz", "color": "all"}
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(csv_safe("=formula"), "'=formula")

    def test_filter_accepts_ui_labels_search_and_dates(self):
        games = [
            {"result": "win", "timeClass": "blitz", "color": "white", "opponent": "Alice", "date": "2026-11-03"},
            {"result": "win", "timeClass": "rapid", "color": "black", "opponent": "Bob", "date": "2026-11-05"},
            {"result": "loss", "timeClass": "blitz", "color": "white", "opponent": "Carol", "date": "2026-11-06"},
        ]
        filtered = filter_game_details(
            games,
            {
                "result": "Wins",
                "timeClass": "Blitz",
                "color": "As White",
                "search": "alice",
                "dateFrom": "2026-11-01",
                "dateTo": "2026-11-30",
            },
        )
        self.assertEqual([game["opponent"] for game in filtered], ["Alice"])
        self.assertEqual(len(filter_game_details(games, {"result": "Wins"})), 2)
        self.assertEqual(fmt_date("2026.11.03"), "Nov 3, 2026")

    def test_header_date_fallback_is_normalized(self):
        raw = {key: value for key, value in RAW_GAME.items() if key != "end_time"}
        summary = parse_game(raw, "hikaru")
        self.assertEqual(summary["date"], "2026-11-03")

    def test_rating_history_keeps_latest_rating_per_month(self):
        insights = compute_insights(
            [
                {"date": "2026-11-03", "timeClass": "blitz", "playerRating": 1700, "timestamp": 1, "result": "win"},
                {"date": "2026-11-20", "timeClass": "blitz", "playerRating": 1800, "timestamp": 2, "result": "loss"},
                {"date": "2026-10-02", "timeClass": "blitz", "playerRating": 1600, "timestamp": 0, "result": "draw"},
            ]
        )
        self.assertEqual(insights["ratingHistory"]["blitz"], {"2026-11": 1800, "2026-10": 1600})

    def test_captured_pieces_use_move_history(self):
        moves = [
            {"san": "e4", "captured": "", "color": "w"},
            {"san": "xd5", "captured": "p", "color": "w"},
            {"san": "Nf6", "captured": "", "color": "b"},
            {"san": "exf6", "captured": "n", "color": "b"},
        ]
        self.assertEqual(captured_pieces(moves, 0), ("", ""))
        self.assertEqual(captured_pieces(moves, 2), ("♟", ""))
        self.assertEqual(captured_pieces(moves, 4), ("♟", "♞"))

    def test_loss_classification(self):
        self.assertEqual(classify_loss(10), "best or good")
        self.assertEqual(classify_loss(45), "inaccuracy")
        self.assertEqual(classify_loss(80), "mistake")
        self.assertEqual(classify_loss(160), "blunder")


    def test_score_formatting(self):
        self.assertEqual(format_score({"cp": 30}), "+0.30")
        self.assertEqual(format_score({"mate": 3}), "M3")
        self.assertEqual(format_score({"mate": -3}), "-M3")
        self.assertEqual(score_value({"mate": -3}), -9970)

    def test_full_game_analysis_details(self):
        fens = [
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
            "rnbqkbnr/pppp1ppp/8/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR b KQkq - 1 2",
        ]
        moves = [
            {"san": "e4", "uci": "e2e4", "color": "w"},
            {"san": "e5", "uci": "e7e5", "color": "b"},
            {"san": "Bc4", "uci": "f1c4", "color": "w"},
        ]
        positions = [
            {
                "bestMove": "e2e4",
                "candidates": [
                    {"move": "e2e4", "score": {"cp": 30, "mate": None}, "pv": ["e2e4", "e7e5", "g1f3"]},
                    {"move": "d2d4", "score": {"cp": 20, "mate": None}, "pv": ["d2d4", "d7d5"]},
                    {"move": "c2c4", "score": {"cp": 10, "mate": None}, "pv": ["c2c4"]},
                ],
            },
            {"bestMove": "e7e5", "candidates": [{"move": "e7e5", "score": {"cp": -20, "mate": None}, "pv": ["e7e5"]}]},
            {"bestMove": "g1f3", "candidates": [{"move": "g1f3", "score": {"cp": 50, "mate": None}, "pv": ["g1f3"]}]},
            {"bestMove": "d7d5", "candidates": [{"move": "d7d5", "score": {"cp": 300, "mate": None}, "pv": ["d7d5"]}]},
        ]
        result = build_game_analysis(fens, moves, positions)
        first = result["moves"][1]
        self.assertEqual(first["bestMoveSan"], "e4")
        self.assertEqual(first["evalBefore"], {"cp": 30, "mate": None})
        self.assertEqual(first["evalAfter"], {"cp": 20, "mate": None})
        self.assertEqual(first["centipawnLoss"], 10)
        self.assertEqual(first["classification"], "best or good")
        self.assertEqual([candidate["san"] for candidate in first["candidates"]], ["e4", "d4", "c4"])
        self.assertEqual(first["candidates"][0]["pv"], ["e4", "e5", "Nf3"])
        self.assertTrue(first["explanation"])
        self.assertIn("e4", first["whyBetter"])
        self.assertEqual(first["phase"], "opening")
        self.assertEqual(result["moves"][2]["classification"], "inaccuracy")
        self.assertEqual(result["moves"][3]["classification"], "blunder")
        self.assertTrue(any("Allowed a strong reply" in item["text"] for item in result["opportunities"]))
        self.assertEqual(result["summary"]["blunders"], 1)

    def test_missed_mate_opportunity(self):
        fens = [
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        ]
        moves = [{"san": "e4", "uci": "e2e4", "color": "w"}]
        positions = [
            {"bestMove": "e2e4", "candidates": [{"move": "e2e4", "score": {"cp": None, "mate": 2}, "pv": ["e2e4"]}]},
            {"bestMove": "e7e5", "candidates": [{"move": "e7e5", "score": {"cp": -100, "mate": None}, "pv": ["e7e5"]}]},
        ]
        result = build_game_analysis(fens, moves, positions)
        self.assertEqual(format_score(result["moves"][1]["evalBefore"]), "M2")
        self.assertTrue(any("Missed a forced mate" in item["text"] for item in result["opportunities"]))

    def test_analyze_game_with_fake_uci_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fake-stockfish")
            commands_path = os.path.join(directory, "commands.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    textwrap.dedent(
                        """\
                        #!/usr/bin/env python3
                        import os
                        import sys
                        with open(os.environ["STOCKFISH_LOG"], "a", encoding="utf-8") as log:
                            for line in sys.stdin:
                                command = line.strip()
                                log.write(command + "\\n")
                                log.flush()
                                if command == "uci":
                                    print("id name Fake Engine", flush=True)
                                    print("uciok", flush=True)
                                elif command == "isready":
                                    print("readyok", flush=True)
                                elif command.startswith("go"):
                                    print("info depth 6 multipv 1 score cp 25 pv e2e4 e7e5", flush=True)
                                    print("info depth 6 multipv 2 score cp 18 pv d2d4 d7d5", flush=True)
                                    print("info depth 6 multipv 3 score cp 12 pv c2c4", flush=True)
                                    print("bestmove e2e4", flush=True)
                                elif command == "quit":
                                    break
                        """
                    )
                )
            os.chmod(path, 0o755)
            with patch.dict(
                os.environ,
                {
                    "STOCKFISH_PATH": path,
                    "STOCKFISH_LOG": commands_path,
                    "STOCKFISH_STARTUP_TIMEOUT": "0.5",
                    "STOCKFISH_RESPONSE_TIMEOUT": "0.5",
                },
            ):
                analyzer = StockfishAnalyzer()
                self.assertTrue(analyzer.available)
                progress = []
                result = analyzer.analyze_game(
                    [
                        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
                    ],
                    [{"san": "e4", "uci": "e2e4", "color": "w"}],
                    depth=6,
                    movetime=100,
                    progress=lambda done, total: progress.append((done, total)),
                )
            with open(commands_path, encoding="utf-8") as handle:
                commands = [line.strip() for line in handle if line.strip()]
            self.assertIn("setoption name MultiPV value 3", commands)
            go_commands = [command for command in commands if command.startswith("go ")]
            self.assertEqual(len(go_commands), 2)
            self.assertTrue(all("multipv" not in command for command in go_commands))
            self.assertEqual(progress, [(1, 2), (2, 2)])
            entry = result["moves"][1]
            self.assertEqual(entry["bestMoveSan"], "e4")
            self.assertEqual([candidate["san"] for candidate in entry["candidates"]], ["e4", "d4", "c4"])
            self.assertEqual(entry["candidates"][0]["pv"], ["e4", "e5"])
            self.assertEqual(entry["candidates"][0]["score"]["cp"], 25)

    def test_castling_with_check_is_counted(self):
        castling_pgn = """
[Event "Live Chess"]
[Date "2026.11.03"]

1. O-O+ O-O-O+ 1-0
"""
        self.assertEqual(move_sans(castling_pgn), ["O-O+", "O-O-O+"])
        summary = parse_game(dict(RAW_GAME, pgn=castling_pgn), "hikaru")
        self.assertEqual(summary["ply"], 2)
        self.assertEqual(summary["moves"], 1)

    def test_undated_games_ignore_date_bounds(self):
        game = {"date": "", "result": "win"}
        filters = {"dateFrom": "2000-01-01", "dateTo": "2028-01-01"}
        self.assertTrue(game_matches_filters(game, filters))

    def test_partial_archive_failure_preserves_cached_games(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "test.db"))
            client = ChessComClient(store)
            previous = [{"id": "previous-game"}]
            store.set_cache("games:hikaru", previous, 3600)
            urls = [
                "https://api.chess.com/pub/player/hikaru/games/2026/10",
                "https://api.chess.com/pub/player/hikaru/games/2026/11",
            ]

            def fetch(url, force=False):
                if url.endswith("2026/10"):
                    raise ChessComError("archive unavailable")
                return {"games": [RAW_GAME]}

            with patch.object(client, "archive_urls", return_value=urls), patch.object(
                client, "fetch_json", side_effect=fetch
            ):
                result = client.load_games("hikaru")
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(result["games"], previous)
            self.assertEqual(store.get_cache("games:hikaru"), previous)
            store.close()

    def test_cached_games_does_not_cache_missing_archive_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "test.db"))
            client = ChessComClient(store)
            archive_url = "https://api.chess.com/pub/player/hikaru/games/archives"
            store.set_cache(
                "http:" + archive_url,
                {"archives": ["https://api.chess.com/pub/player/hikaru/games/2026/11"]},
                3600,
            )
            self.assertEqual(client.cached_games("hikaru"), [])
            self.assertIsNone(store.get_cache("games:hikaru"))
            store.close()

    def test_engine_restarts_after_timed_out_position(self):
        fens = [
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        ]
        analyzer = StockfishAnalyzer()
        analyzer.available = True
        with patch.object(StockfishAnalyzer, "_start_process", return_value=object()) as start, patch.object(
            StockfishAnalyzer, "_configure_engine"
        ) as configure, patch.object(StockfishAnalyzer, "_stop_process") as stop, patch.object(
            StockfishAnalyzer,
            "_analyze_position",
            side_effect=[{"bestMove": None}, {"bestMove": "e2e4"}],
        ):
            results = analyzer.analyze_positions(fens, movetime=50)
        self.assertEqual(len(results), 2)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(configure.call_count, 2)
        self.assertEqual(stop.call_count, 2)

    def test_profile_uses_country_url(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ChessComClient(Store(os.path.join(directory, "test.db")))
            country = "https://api.chess.com/pub/country/US"

            def fetch(url, force=False):
                if "country" in url:
                    return {"name": "United States"}
                return {"url": "https://www.chess.com/member/hikaru", "country": country}

            with patch.object(client, "fetch_json", side_effect=fetch):
                profile = client.profile("hikaru")
            self.assertEqual(profile["countryUrl"], country)
            self.assertEqual(profile["country"], "United States")

    def test_validate_fen_rejects_invalid_positions(self):
        start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        self.assertEqual(validate_fen("  " + start + "  "), start)
        for value in ("", "x" * 300, "not a fen", "8/8/8/8/8/8/8/8 w - - 0 1"):
            with self.assertRaises(ValueError):
                validate_fen(value)

    def test_analyze_positions_rejects_invalid_fen(self):
        analyzer = StockfishAnalyzer()
        analyzer.available = True
        with self.assertRaises(ChessComError):
            analyzer.analyze_positions(["not a fen"])

    def test_validate_pgn_rejects_empty_and_moveless_games(self):
        text, game = validate_pgn(PGN)
        self.assertEqual(text, PGN.strip())
        self.assertEqual(len(list(game.mainline_moves())), 6)
        for value in ("", "x" * 200001, '[Event "Empty"]\n\n1-0\n'):
            with self.assertRaises(ValueError):
                validate_pgn(value)

    def test_engine_reports_invalid_path_and_start_failure(self):
        analyzer = StockfishAnalyzer("/definitely/not/stockfish")
        self.assertTrue(analyzer.path_error)
        analyzer.available = True
        with patch("engine.subprocess.Popen", side_effect=OSError("missing engine")):
            with self.assertRaises(ChessComError):
                analyzer.analyze_positions(
                    ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]
                )

    def test_engine_stops_stalled_search(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "stalling-engine")
            commands_path = os.path.join(directory, "commands.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    textwrap.dedent(
                        """\
                        #!/usr/bin/env python3
                        import os
                        import sys
                        seen_go = False
                        with open(os.environ["STOCKFISH_LOG"], "a", encoding="utf-8") as log:
                            for line in sys.stdin:
                                command = line.strip()
                                log.write(command + "\\n")
                                log.flush()
                                if command == "uci":
                                    print("uciok", flush=True)
                                elif command == "isready":
                                    print("readyok", flush=True)
                                elif command.startswith("go"):
                                    if seen_go:
                                        print("info depth 4 multipv 1 score cp 10 pv e2e4", flush=True)
                                        print("bestmove e2e4", flush=True)
                                    seen_go = True
                                elif command == "stop":
                                    print("bestmove e2e4", flush=True)
                                elif command == "quit":
                                    break
                        """
                    )
                )
            os.chmod(path, 0o755)
            with patch.dict(
                os.environ,
                {
                    "STOCKFISH_PATH": path,
                    "STOCKFISH_LOG": commands_path,
                    "STOCKFISH_STARTUP_TIMEOUT": "0.3",
                    "STOCKFISH_RESPONSE_TIMEOUT": "0.2",
                    "STOCKFISH_STOP_TIMEOUT": "0.5",
                },
            ):
                analyzer = StockfishAnalyzer()
                results = analyzer.analyze_positions(
                    [
                        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
                    ],
                    movetime=50,
                )
            with open(commands_path, encoding="utf-8") as handle:
                commands = [line.strip() for line in handle if line.strip()]
            self.assertEqual(commands.count("stop"), 1, (commands, results))
            self.assertEqual(commands.count("uci"), 1, commands)
            self.assertEqual([item["bestMove"] for item in results], ["e2e4", "e2e4"])


class IntelligenceAndCoachingTests(unittest.TestCase):
    def test_rating_aware_classification(self):
        self.assertEqual(classify_loss_for_rating(30, 800), "best or good")
        self.assertEqual(classify_loss_for_rating(40, 1500), "inaccuracy")
        self.assertEqual(classify_loss_for_rating(70, 1500), "mistake")
        self.assertEqual(classify_loss_for_rating(80, 2300), "blunder")
        self.assertEqual(
            classify_loss_for_rating(70, 2300, phase="endgame"), "blunder"
        )

    def test_tactical_motifs(self):
        check = tactical_motifs("4k3/8/8/8/8/8/8/3QK3 w - - 0 1", "d1d8")
        self.assertIn("check", check)
        promotion = tactical_motifs("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8q")
        self.assertIn("promotion", promotion)
        self.assertEqual(tactical_motifs("not a fen", "e2e4"), [])

    def test_explanation_and_better_move(self):
        explanation = explain_positional_mistake(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "a2a3",
        )
        self.assertTrue(explanation)
        text = why_is_better(
            {
                "bestMoveSan": "e4",
                "centipawnLoss": 80,
                "phase": "opening",
                "replySan": "d5",
                "bestMotifs": ["fork"],
                "move": "a3",
            }
        )
        self.assertIn("e4", text)
        self.assertIn("fork", text)

    def test_recurring_weaknesses(self):
        analyses = [
            {
                "game_id": "g1",
                "analysis": {
                    "moves": {
                        1: {
                            "ply": 1,
                            "classification": "blunder",
                            "phase": "opening",
                            "motifs": [],
                            "explanation": "You gave up control of the centre",
                        }
                    }
                },
            }
        ]
        weaknesses = recurring_weaknesses(analyses)
        keys = {item["key"] for item in weaknesses}
        self.assertIn("decision_quality", keys)
        self.assertIn("centre", keys)
        self.assertEqual(weaknesses[0]["games"], 1)

    def test_progress_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "training.db"))
            try:
                record_progress(store, "tester", {"focus": "Tactics", "correct": 1, "total": 2})
                record_progress(store, "tester", {"focus": "Tactics", "correct": 2, "total": 2})
                history = progress_history(store, "tester")
                summary = improvement_summary(history)
                self.assertEqual(summary["sessions"], 2)
                self.assertEqual(summary["accuracy"], 75.0)
                self.assertEqual(summary["trend"], 50.0)
                report = training_report(store, "tester", [], [], history)
                self.assertIn("Insight8x8 training report", report)
                self.assertIn("75.0", report)
            finally:
                store.close()

    def test_llm_adapter_is_optional(self):
        with patch.dict(os.environ, {}, clear=True):
            coach = LLMCoach()
            self.assertFalse(coach.available)
            lessons = generate_lessons([], llm=coach)
            self.assertEqual(lessons[0]["id"], "lesson-start")
        llm = LLMCoach(url="https://example.test/v1/chat/completions", key="secret")
        self.assertTrue(llm.available)
        weakness = {
            "key": "decision_quality",
            "title": "Decision quality",
            "detail": "Repeated mistakes",
            "count": 4,
            "severity": "medium",
        }
        payload = '{"lessons":[{"title":"LLM lesson","summary":"s","focus":"f","steps":["a"]}]}'
        with patch.object(llm, "generate", return_value=payload):
            lessons = generate_lessons([weakness], llm=llm)
        self.assertEqual(lessons[0]["title"], "LLM lesson")
        self.assertEqual(lessons[0]["source"], "LLM")

    def test_groq_key_autodetects_endpoint_and_model(self):
        with patch.dict(os.environ, {}, clear=True):
            coach = LLMCoach(key="gsk_test")
        self.assertEqual(coach.url, GROQ_URL)
        self.assertEqual(coach.model, GROQ_MODEL)
        self.assertTrue(coach.available)

    def test_position_factors_and_book_recognition(self):
        fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
        self.assertTrue(position_factors(fen))
        book = is_common_opening_move(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "e2e4",
            "Italian Game",
            "C50",
            1,
        )
        self.assertTrue(book["book"])
        self.assertIn("e4", book["explanation"])
        entry = {"classification": "blunder", "explanation": "Original note"}
        changes = softened_classification(entry, book)
        self.assertEqual(changes["classification"], "best or good")
        self.assertEqual(changes["originalClassification"], "blunder")

    def test_move_lesson_builds_concrete_auto_explanation_and_arrows(self):
        entry = {
            "ply": 5,
            "move": "O-O",
            "playedMove": "e1g1",
            "fen": "4k3/8/8/3b4/8/8/4P3/4K2R w K - 0 1",
            "bestMove": "e2e4",
            "bestMoveSan": "e4",
            "classification": "mistake",
            "candidates": [{"pvUci": ["e2e4", "d5e6"], "san": "e4"}],
        }
        lesson = move_lesson(entry)
        self.assertIsNotNone(lesson)
        self.assertIn("Castling", lesson["headline"])
        self.assertIn("bishop", lesson["body"].lower())
        self.assertIn("e4", lesson["body"])
        self.assertEqual(lesson["arrows"][0], {"from": "e2", "to": "e4", "kind": "best"})
        self.assertEqual(lesson["arrows"][1]["kind"], "threat")
        self.assertIn("d5", lesson["focus"])

    def test_coach_explains_without_raw_scores(self):
        text = explain_position_with_coach(None, {"fen": "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"})
        self.assertNotIn("centipawn", text.lower())
        self.assertTrue(text)
        with patch.object(LLMCoach, "available", new_callable=lambda: property(lambda self: True)):
            coach = LLMCoach(url="https://example.test", key="test-key")
        with patch.object(coach, "generate", return_value="Your king needs more pawn cover."):
            text = explain_position_with_coach(coach, {"fen": "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"})
        self.assertIn("king", text)

    def test_llm_book_check_falls_back_to_local(self):
        context = {
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "uci": "e2e4",
            "san": "e4",
            "opening": "Italian Game",
            "eco": "C50",
            "ply": 1,
        }
        result = check_opening_book_with_llm(None, context)
        self.assertTrue(result["book"])
        coach = LLMCoach(url="https://example.test", key="test-key")
        with patch.object(coach, "generate", return_value='{"book": true, "name": "Open Game", "explanation": "Standard central move."}'):
            result = check_opening_book_with_llm(coach, context)
        self.assertEqual(result["source"], "LLM")

    def test_env_file_values_are_loaded_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path

            env_file = Path(directory) / ".env"
            env_file.write_text(
                "INSIGHT8X8_LLM_KEY=file-key\n# comment\nINSIGHT8X8_LLM_MODEL=file-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"INSIGHT8X8_LLM_MODEL": "shell-model"}, clear=True):
                self.assertTrue(load_env_file(env_file))
                self.assertEqual(os.environ["INSIGHT8X8_LLM_KEY"], "file-key")
                self.assertEqual(os.environ["INSIGHT8X8_LLM_MODEL"], "shell-model")


class Insight8x8GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def make_window(self):
        from insight8x8 import Insight8x8App

        with patch("insight8x8.QSettings") as settings:
            settings.return_value.value.side_effect = (
                lambda key, default="", type=None: default
            )
            return Insight8x8App()

    def test_clear_filters_resets_pagination(self):
        window = self.make_window()
        try:
            filters = window.game_filters[False]
            filters["page"] = 3
            window._clear_filters(False)
            self.assertEqual(filters["page"], 1)
            self.assertEqual(filters["search"], "")
            self.assertEqual(filters["dateFrom"], window._default_filters()["dateFrom"])
        finally:
            window.close()

    def test_export_cards_do_not_overlap_summary(self):
        window = self.make_window()
        try:
            grid = window.export_summary.parent().layout()
            self.assertIs(grid.itemAtPosition(0, 1).widget(), window.export_summary)
            for row in (1, 2):
                item = grid.itemAtPosition(row, 1)
                self.assertIsNotNone(item)
                self.assertIsNotNone(item.widget())
        finally:
            window.close()

    def test_branding_and_game_columns(self):
        window = self.make_window()
        try:
            self.assertTrue(window.windowTitle().startswith("Insight8x8"))
            tree = window.game_filters[False]["widgets"]["tree"]
            self.assertEqual(tree.columnCount(), 9)
            self.assertEqual(tree.headerItem().text(3), "Δ")
        finally:
            window.close()

    def test_sort_and_page_size_controls(self):
        window = self.make_window()
        try:
            window.games = [
                {"id": "a", "playerRating": 1000, "date": "2026-01-01", "result": "win"},
                {"id": "b", "playerRating": 1800, "date": "2026-02-01", "result": "loss"},
                {"id": "c", "playerRating": 1400, "date": "2026-03-01", "result": "draw"},
            ]
            self.assertEqual(
                [game["id"] for game in window._filtered_games(False)], ["c", "b", "a"]
            )
            window.game_filters[False]["sort"] = "rating_desc"
            self.assertEqual(
                [game["id"] for game in window._filtered_games(False)], ["b", "c", "a"]
            )
            widgets = window.game_filters[False]["widgets"]
            widgets["pageSize"].setCurrentIndex(0)
            window._on_filter_changed(False)
            self.assertEqual(window.game_filters[False]["pageSize"], 25)
            self.assertEqual(window.game_filters[False]["sort"], "recent")
        finally:
            window.close()

    def test_page_spinbox_updates_page(self):
        window = self.make_window()
        try:
            window.games = [
                {"id": str(index), "date": "2026-01-01", "result": "win"}
                for index in range(60)
            ]
            window._refresh_games(False)
            widgets = window.game_filters[False]["widgets"]
            self.assertEqual(widgets["pageSpin"].maximum(), 2)
            widgets["pageSpin"].setValue(2)
            self.assertEqual(window.game_filters[False]["page"], 2)
            self.assertEqual(widgets["pageLabel"].text(), "Page 2 of 2")
        finally:
            window.close()

    def test_export_scope_filters_favorites(self):
        window = self.make_window()
        try:
            window.games = [
                {"id": "a", "date": "2026-01-01", "result": "win", "opponent": "one"},
                {"id": "b", "date": "2026-01-02", "result": "loss", "opponent": "two"},
            ]
            window.favorites = {"b"}
            details = [
                {"id": "a", "pgn": "[Event \"A\"]\n\n1. e4 e5 1-0"},
                {"id": "b", "pgn": "[Event \"B\"]\n\n1. d4 d5 1-0"},
            ]
            with patch.object(window.client, "all_game_details", return_value=details):
                text = window._export_text("pgn", favorites_only=True)
            self.assertIn('[Event "B"]', text)
            self.assertNotIn('[Event "A"]', text)
        finally:
            window.close()

    def test_viewer_receives_analysis_settings(self):
        from viewers import GameViewerDialog

        window = self.make_window()
        try:
            detail = parse_game(RAW_GAME, "hikaru", include_pgn=True)
            viewer = GameViewerDialog(detail, window.analyzer, False, window, depth=18, movetime=200)
            try:
                self.assertEqual(viewer.depth, 18)
                self.assertEqual(viewer.movetime, 200)
                self.assertTrue(viewer.engine_button.isEnabled() or not viewer.analyzer.available)
                self.assertIsNotNone(viewer.settings_button)
            finally:
                viewer.close()
        finally:
            window.close()

    def test_training_and_progress_tabs(self):
        window = self.make_window()
        try:
            self.assertIn("training", window.nav_buttons)
            self.assertIn("progress", window.nav_buttons)
            window.show_tab("training")
            self.assertEqual(window.page_title.text(), "Training")
            window.show_tab("progress")
            self.assertEqual(window.page_title.text(), "Progress")
            report = window._export_text("training_report")
            self.assertIn("Insight8x8 training report", report)
        finally:
            window.close()

    def test_eval_bar_reflects_white_advantage(self):
        bar = EvalBar()
        bar.set_evaluation({"cp": 300}, "white")
        self.assertGreater(bar._white_share(), 0.5)
        self.assertEqual(bar.orientation, "white")
        bar.set_evaluation({"mate": -2}, "black")
        self.assertAlmostEqual(bar._white_share(), 0.03)
        self.assertEqual(bar.orientation, "black")

    def test_viewer_orients_board_to_player_and_hides_raw_scores(self):
        from viewers import GameViewerDialog

        window = self.make_window()
        try:
            detail = parse_game(RAW_GAME, "hikaru", include_pgn=True)
            detail["color"] = "black"
            viewer = GameViewerDialog(detail, window.analyzer, False, window)
            try:
                self.assertEqual(viewer.board.orientation, "black")
                self.assertEqual(viewer.eval_bar.orientation, "black")
                self.assertFalse(hasattr(viewer, "coach_button"))
                self.assertGreater(viewer.eval_bar.sizeHint().width(), 30)
                self.assertTrue(viewer.eval_label.text().startswith("Eval"))
                viewer._show_automatic_explanation(
                    {"ply": 1, "move": "e4", "classification": "mistake"},
                    {
                        "headline": "d4 is stronger",
                        "body": "d4 takes the centre.",
                        "line": "d4 d5 c4",
                        "arrows": [{"from": "d2", "to": "d4", "kind": "best"}],
                        "focus": [],
                    },
                )
                self.assertEqual(viewer.coach_headline.text(), "d4 is stronger")
                self.assertIn("d4 d5 c4", viewer.guide_label.text())
                viewer._my_side()
                self.assertEqual(viewer.board.orientation, "black")
                text = viewer._format_analysis_entry(
                    {
                        "ply": 1,
                        "move": "e4",
                        "color": "w",
                        "classification": "inaccuracy",
                        "bestMoveSan": "d4",
                        "explanation": "You gave up the centre.",
                        "whyBetter": "d4 controls the centre.",
                        "motifs": ["check"],
                    }
                )
                self.assertNotIn("Centipawn", text)
                self.assertNotIn("Evaluation before", text)
                self.assertIn("Why it matters", text)
            finally:
                viewer.close()
        finally:
            window.close()

    def test_viewer_reports_invalid_pgn(self):
        from viewers import GameViewerDialog

        window = self.make_window()
        try:
            viewer = GameViewerDialog({"pgn": "", "white": "A", "black": "B"}, window.analyzer)
            try:
                self.assertEqual(viewer.pgn_error, "PGN is empty.")
                self.assertFalse(viewer.moves)
                self.assertIn("PGN problem", viewer.analysis_progress.text())
            finally:
                viewer.close()
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
