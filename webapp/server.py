import csv
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

import chess
from chesscom import ChessComClient, validate_username
from coaching import LLMCoach, build_coaching_data, improvement_summary, load_env_file, progress_history, record_progress, training_report
from engine import StockfishAnalyzer, validate_pgn
from store import Store
from utils import csv_safe, game_matches_filters

STATIC = Path(__file__).resolve().parent / "static"
CONFIG_PATH = ROOT / "data" / "web-config.json"
DEFAULTS = {
    "onboardingComplete": False,
    "username": "",
    "enginePath": "",
    "depth": 12,
    "movetime": 150,
    "llmUrl": "",
    "llmModel": "",
}

load_env_file()


def load_config():
    config = dict(DEFAULTS)
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update({key: value for key, value in loaded.items() if key in DEFAULTS})
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = {key: config.get(key) for key in DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(safe, indent=2), encoding="utf-8")


def compute_insights(games):
    totals = {"win": 0, "loss": 0, "draw": 0}
    by_class = {}
    by_color = {"white": {"win": 0, "loss": 0, "draw": 0}, "black": {"win": 0, "loss": 0, "draw": 0}}
    by_opening = {}
    by_opponent = {}
    by_month = {}
    rating_history = {}
    rating_timestamps = {}
    for game in games:
        result = game.get("result")
        if result in totals:
            totals[result] += 1
        time_class = game.get("timeClass") or "rapid"
        bucket = by_class.setdefault(time_class, {"games": 0, "win": 0, "loss": 0, "draw": 0})
        bucket["games"] += 1
        if result in bucket:
            bucket[result] += 1
        color = game.get("color")
        if color in by_color and result in by_color[color]:
            by_color[color][result] += 1
        opening = game.get("opening") or "Unknown opening"
        record = by_opening.setdefault(opening, {
            "name": opening, "eco": game.get("eco") or "",
            "games": 0, "win": 0, "loss": 0, "draw": 0,
        })
        record["games"] += 1
        if result in record:
            record[result] += 1
        opponent = game.get("opponent") or "Unknown"
        by_opponent[opponent] = by_opponent.get(opponent, 0) + 1
        month = str(game.get("date") or "")[:7]
        if month:
            by_month[month] = by_month.get(month, 0) + 1
        if month and game.get("playerRating"):
            key = (time_class, month)
            timestamp = game.get("timestamp") or 0
            if key not in rating_timestamps or timestamp >= rating_timestamps[key]:
                rating_timestamps[key] = timestamp
                rating_history.setdefault(time_class, {})[month] = game["playerRating"]
    decided = sum(totals.values())
    return {
        "totals": totals,
        "total": len(games),
        "winPercentage": round(totals["win"] / decided * 100, 1) if decided else None,
        "byClass": by_class,
        "byColor": by_color,
        "byOpening": by_opening,
        "byOpponent": by_opponent,
        "byMonth": by_month,
        "ratingHistory": rating_history,
    }


class AppState:
    def __init__(self):
        self.config = load_config()
        db_path = os.environ.get("INSIGHT8X8_DB", str(ROOT / "data" / "insight8x8.db"))
        self.store = Store(db_path)
        self.client = ChessComClient(self.store)
        self.analyzer = StockfishAnalyzer(self.config.get("enginePath") or None)
        self.username = ""
        self.connection = None
        self.games = []
        self.errors = []
        self.favorites = set()
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.coaching_data = None
        self.coaching_dirty = True

    def start_job(self, kind, func, *args):
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "kind": kind, "status": "running", "stage": "Starting…",
            "done": 0, "total": 1, "result": None, "error": "",
        }
        with self.job_lock:
            self.jobs[job_id] = job
            if len(self.jobs) > 60:
                for old in list(self.jobs)[:len(self.jobs) - 60]:
                    if self.jobs[old]["status"] != "running":
                        self.jobs.pop(old, None)

        def runner():
            def progress(done, total, stage=None):
                job["done"] = max(0, int(done or 0))
                job["total"] = max(1, int(total or 1))
                if stage:
                    job["stage"] = stage
            try:
                job["result"] = func(progress, *args)
                job["status"] = "done"
                job["stage"] = "Ready"
            except Exception as exc:
                job["status"] = "error"
                job["error"] = str(exc) or type(exc).__name__
                job["stage"] = "Failed"

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def job(self, job_id):
        with self.job_lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def connect(self, progress, username, force=False):
        name = validate_username(username)
        self.username = name
        self.connection = None
        self.games = []
        self.errors = []
        self.favorites = set()
        self.coaching_data = None
        self.coaching_dirty = True
        self.config["username"] = name
        save_config(self.config)
        progress(0, 2, "Loading profile…")
        connection = self.client.connect(name)
        self.connection = connection
        progress(1, 2, "Loading game archives…")

        def archive_progress(done, total):
            progress(1 + (done / total if total else 0), 2, f"Loading archives {done}/{total}…")

        result = self.client.load_games(name, force=force, progress=archive_progress)
        self.games = self.client.cached_games(name)
        self.errors = list(result.get("errors") or [])
        self.favorites = set(self.store.favorites(name))
        progress(2, 2, "Ready")
        return {
            "username": name,
            "archiveCount": connection.get("archiveCount", 0),
            "gameCount": len(self.games),
            "errors": self.errors[:10],
        }

    def require_account(self):
        if not self.username or not self.connection:
            raise ValueError("Connect a Chess.com username first.")

    def filtered_games(self, filters, favorites_only=False):
        self.require_account()
        games = self.games
        if favorites_only:
            games = [game for game in games if game.get("id") in self.favorites]
        return [game for game in games if game_matches_filters(game, filters)]

    def prepare_game(self, game_id):
        self.require_account()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id):
            raise ValueError("Invalid game id.")
        detail = self.client.cached_game_detail(self.username, game_id)
        if not detail:
            raise ValueError("Game not found in cached archives.")
        payload = {
            "detail": detail,
            "moves": [],
            "fens": [],
            "headers": {},
            "pgnError": "",
            "analysis": self.saved_analysis(game_id),
            "favorite": game_id in self.favorites,
        }
        try:
            _, parsed = validate_pgn(detail.get("pgn") or "")
        except ValueError as exc:
            payload["pgnError"] = str(exc)
            return payload
        board = parsed.board()
        payload["fens"].append(board.fen())
        payload["headers"] = dict(parsed.headers)
        for move in parsed.mainline_moves():
            san = board.san(move)
            captured = board.piece_at(move.to_square)
            payload["moves"].append({
                "san": san,
                "from": chess.square_name(move.from_square),
                "to": chess.square_name(move.to_square),
                "uci": move.uci(),
                "captured": captured.symbol().lower() if captured else "",
                "color": "w" if len(payload["moves"]) % 2 == 0 else "b",
            })
            board.push(move)
            payload["fens"].append(board.fen())
        return payload

    def saved_analysis(self, game_id):
        value = self.store.get_training(self.username, f"analysis:{game_id}")
        return value.get("analysis") if isinstance(value, dict) else None

    def analyze_game(self, progress, game_id, depth, movetime):
        prepared = self.prepare_game(game_id)
        if not prepared["moves"]:
            raise ValueError(prepared["pgnError"] or "This game has no moves to analyze.")
        if not self.analyzer.available:
            raise ValueError("Stockfish is not configured on this device.")
        result = self.analyzer.analyze_game(
            prepared["fens"], prepared["moves"], depth=depth, movetime=movetime,
            progress=lambda done, total: progress(done, total, f"Analyzed {done}/{total} positions…"),
        )
        game = next((item for item in self.games if item.get("id") == game_id), {})
        self.store.set_training(self.username, f"analysis:{game_id}", {
            "game_id": game_id,
            "date": game.get("date"),
            "color": game.get("color"),
            "opponent": game.get("opponent"),
            "timeClass": game.get("timeClass"),
            "playerRating": game.get("playerRating"),
            "analysis": result,
        })
        self.coaching_dirty = True
        return result

    def collect_analyses(self):
        return [
            value for key, value in self.store.all_training(self.username).items()
            if key.startswith("analysis:") and isinstance(value, dict) and value.get("analysis")
        ]

    def coaching(self):
        self.require_account()
        if self.coaching_data is None or self.coaching_dirty:
            llm = LLMCoach(
                url=self.config.get("llmUrl") or None,
                model=self.config.get("llmModel") or None,
            )
            self.coaching_data = build_coaching_data(
                self.collect_analyses(), self.store, self.username,
                llm=llm if llm.available else None,
            )
            self.coaching_dirty = False
        return self.coaching_data

    def compare(self, progress, other_name):
        self.require_account()
        other = validate_username(other_name)
        if other == self.username:
            raise ValueError("Choose a different Chess.com username.")
        progress(0, 4, "Loading players…")
        first_profile = self.client.profile(self.username)
        progress(1, 4, f"Loading {other}…")
        second_profile = self.client.profile(other)
        progress(2, 4, "Loading statistics…")
        first_stats = self.client.stats(self.username)
        second_stats = self.client.stats(other)
        progress(3, 4, "Finding direct encounters…")
        head_to_head = [
            game for game in self.client.cached_games(self.username)
            if str(game.get("opponent") or "").lower() == other
        ]
        progress(4, 4, "Ready")
        return {
            "players": [
                {"username": self.username, "profile": first_profile, "stats": first_stats},
                {"username": other, "profile": second_profile, "stats": second_stats},
            ],
            "headToHead": head_to_head,
        }

    def export_text(self, kind, filters, favorites_only=False):
        self.require_account()
        if kind == "training_report":
            data = self.coaching()
            return training_report(
                self.store, self.username,
                data["lessons"], data["exercises"], data["history"],
            )
        if kind not in ("pgn", "csv"):
            raise ValueError("Unknown export type.")
        selected_ids = {game["id"] for game in self.filtered_games(filters, favorites_only)}
        details = [
            game for game in self.client.all_game_details(self.username)
            if game.get("id") in selected_ids
        ]
        if kind == "pgn":
            return "\n\n".join(game.get("pgn", "").strip() for game in details if game.get("pgn"))
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "Date", "Result", "Color", "Opponent", "Opponent Rating", "Player Rating",
            "Time Class", "Time Control", "Opening", "ECO", "Moves", "Accuracy (%)",
            "Accuracy Source", "Termination", "URL",
        ])
        for game in details:
            row = [
                game.get("date"), game.get("result"), game.get("color"), game.get("opponent"),
                game.get("opponentRating"), game.get("playerRating"), game.get("timeClass"),
                game.get("timeControl"), game.get("opening"), game.get("eco"), game.get("moves"),
                game.get("accuracy"), game.get("accuracySource"), game.get("termination"), game.get("url"),
            ]
            writer.writerow([csv_safe(value) for value in row])
        return output.getvalue()

    def public_config(self):
        return {
            "onboardingComplete": bool(self.config.get("onboardingComplete")),
            "username": self.config.get("username", ""),
            "enginePath": self.config.get("enginePath", ""),
            "engineAvailable": bool(self.analyzer.available),
            "depth": int(self.config.get("depth", 12)),
            "movetime": int(self.config.get("movetime", 150)),
            "llmUrl": self.config.get("llmUrl", ""),
            "llmModel": self.config.get("llmModel", ""),
            "llmAvailable": bool(LLMCoach(
                url=self.config.get("llmUrl") or None,
                model=self.config.get("llmModel") or None,
            ).available),
        }

    def state(self):
        data = {
            "connected": bool(self.connection),
            "username": self.username,
            "config": self.public_config(),
            "database": self.store.path,
        }
        if self.connection:
            data.update({
                "profile": self.connection.get("profile", {}),
                "stats": self.connection.get("stats", {}),
                "archiveCount": self.connection.get("archiveCount", 0),
                "gameCount": len(self.games),
                "favorites": sorted(self.favorites),
                "errors": self.errors[:10],
                "insights": compute_insights(self.games),
            })
        return data

    def update_config(self, values):
        allowed = {"onboardingComplete": bool, "enginePath": str, "depth": int, "movetime": int, "llmUrl": str, "llmModel": str}
        for key, value in values.items():
            if key not in allowed or value is None:
                continue
            try:
                converted = allowed[key](value)
            except (TypeError, ValueError):
                continue
            self.config[key] = converted
        self.config["depth"] = min(20, max(4, int(self.config.get("depth", 12))))
        self.config["movetime"] = min(3000, max(50, int(self.config.get("movetime", 150))))
        self.analyzer.set_path(self.config.get("enginePath") or None)
        key = str(values.get("llmKey") or "")
        if key:
            os.environ["INSIGHT8X8_LLM_KEY"] = key
        save_config(self.config)
        self.coaching_dirty = True
        return self.public_config()


class Handler(BaseHTTPRequestHandler):
    server_version = "Insight8x8Web/1.1"
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, value, status=200):
        self._send(status, json.dumps(value, ensure_ascii=False, default=str))

    def _error(self, message, status=400):
        self._json({"error": str(message)}, status)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("Request body too large.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("Invalid JSON body.")
        if not isinstance(value, dict):
            raise ValueError("JSON object expected.")
        return value

    def _static(self, relative):
        base = STATIC.resolve()
        path = (base / relative).resolve()
        if path != base and base not in path.parents:
            self._error("Forbidden", 403)
            return
        if not path.is_file():
            self._error("Not found", 404)
            return
        suffixes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        self._send(200, path.read_bytes(), suffixes.get(path.suffix, "application/octet-stream"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        try:
            if path == "/":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/health":
                return self._json({"ok": True})
            if path == "/api/config":
                return self._json(self.state.public_config())
            if path == "/api/state":
                return self._json(self.state.state())
            if path == "/api/games":
                favorites_only = query.get("favorites") in ("1", "true", "yes")
                games = self.state.filtered_games(query, favorites_only)
                page = max(1, int(query.get("page", "1") or 1))
                page_size = min(100, max(5, int(query.get("pageSize", "25") or 25)))
                start = (page - 1) * page_size
                return self._json({
                    "games": games[start:start + page_size],
                    "total": len(games),
                    "page": page,
                    "pageSize": page_size,
                })
            if path.startswith("/api/game/"):
                return self._json(self.state.prepare_game(path[len("/api/game/"):]))
            if path.startswith("/api/job/"):
                job = self.state.job(path[len("/api/job/"):])
                return self._json(job) if job else self._error("Job not found", 404)
            if path == "/api/coaching":
                return self._json(self.state.coaching())
            if path == "/api/progress":
                history = progress_history(self.state.store, self.state.username)
                return self._json({"history": history, "summary": improvement_summary(history)})
            if path.startswith("/api/export/"):
                kind = path[len("/api/export/"):]
                text = self.state.export_text(kind, query, query.get("favorites") in ("1", "true", "yes"))
                suffix = "pgn" if kind == "pgn" else "csv"
                name = f"{self.state.username or 'insight8x8'}-{kind}.{suffix}"
                return self._send(200, text, "text/plain; charset=utf-8", {
                    "Content-Disposition": f'attachment; filename="{name}"',
                })
            self._error("Not found", 404)
        except (ValueError, KeyError) as exc:
            self._error(str(exc) or "Invalid request")
        except Exception as exc:
            self._error(f"Request failed: {type(exc).__name__}", 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            body = self._body()
            state = self.state
            if path == "/api/config":
                return self._json(state.update_config(body))
            if path == "/api/connect":
                username = str(body.get("username") or "")
                force = bool(body.get("force"))
                job_id = state.start_job("connect", state.connect, username, force)
                return self._json({"job": job_id}, 202)
            if path == "/api/refresh":
                if not state.username:
                    raise ValueError("Connect a Chess.com username first.")
                job_id = state.start_job("refresh", state.connect, state.username, True)
                return self._json({"job": job_id}, 202)
            if path.startswith("/api/analyze/"):
                game_id = path[len("/api/analyze/"):]
                depth = int(body.get("depth", state.config.get("depth", 12)) or 12)
                movetime = int(body.get("movetime", state.config.get("movetime", 150)) or 150)
                job_id = state.start_job("analyze", state.analyze_game, game_id, depth, movetime)
                return self._json({"job": job_id}, 202)
            if path.startswith("/api/favorite/"):
                game_id = path[len("/api/favorite/"):]
                state.require_account()
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id):
                    raise ValueError("Invalid game id.")
                if game_id in state.favorites:
                    state.store.remove_favorite(state.username, game_id)
                    state.favorites.discard(game_id)
                else:
                    state.store.add_favorite(state.username, game_id)
                    state.favorites.add(game_id)
                return self._json({"favorite": game_id in state.favorites})
            if path == "/api/compare":
                job_id = state.start_job("compare", state.compare, str(body.get("username") or ""))
                return self._json({"job": job_id}, 202)
            if path == "/api/coaching/finish":
                state.require_account()
                session = {
                    "focus": str(body.get("focus") or "Balanced"),
                    "correct": max(0, int(body.get("correct", 0) or 0)),
                    "total": max(1, int(body.get("total", 1) or 1)),
                }
                history = record_progress(state.store, state.username, session)
                return self._json({"history": history, "summary": improvement_summary(history)})
            self._error("Not found", 404)
        except (ValueError, KeyError) as exc:
            self._error(str(exc) or "Invalid request")
        except Exception as exc:
            self._error(f"Request failed: {type(exc).__name__}", 500)


def main():
    state = AppState()
    Handler.state = state
    port = int(os.environ.get("INSIGHT8X8_WEB_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"Insight8x8 web app: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.store.close()


if __name__ == "__main__":
    main()
