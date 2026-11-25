import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import chess
import chess.pgn

from chesscom import ChessComError
from intelligence import (
    classify_loss_for_rating,
    explain_positional_mistake,
    move_lesson,
    phase_for_ply,
    tactical_motifs,
    why_is_better,
)

MATE_SCORE = 10000
MAX_FEN_LENGTH = 200
MAX_PGN_CHARS = 200000
_EOF = object()


def _timeout(name, default):
    try:
        return max(0.05, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def validate_fen(fen):
    text = str(fen or "").strip()
    if not text:
        raise ValueError("FEN is empty.")
    if len(text) > MAX_FEN_LENGTH:
        raise ValueError("FEN is too long.")
    try:
        board = chess.Board(text)
    except (TypeError, ValueError) as error:
        raise ValueError("FEN is not valid.") from error
    if not board.is_valid():
        raise ValueError("FEN describes an illegal position.")
    return text


def validate_pgn(pgn):
    text = str(pgn or "").strip()
    if not text:
        raise ValueError("PGN is empty.")
    if len(text) > MAX_PGN_CHARS:
        raise ValueError("PGN is too large to replay.")
    try:
        game = chess.pgn.read_game(io.StringIO(text))
    except Exception as error:
        raise ValueError("PGN could not be parsed.") from error
    if game is None:
        raise ValueError("PGN does not contain a game.")
    if not any(game.mainline_moves()):
        raise ValueError("PGN does not contain any moves.")
    return text, game


def classify_loss(loss):
    if loss is None:
        return None
    if loss <= 25:
        return "best or good"
    if loss <= 50:
        return "inaccuracy"
    if loss <= 100:
        return "mistake"
    return "blunder"


def score_value(score):
    if not score:
        return None
    mate = score.get("mate")
    if mate is not None:
        mate = int(mate)
        return (MATE_SCORE - abs(mate) * 10) * (1 if mate > 0 else -1)
    cp = score.get("cp")
    return int(cp) if cp is not None else None


def format_score(score):
    if not score:
        return "—"
    mate = score.get("mate")
    if mate is not None:
        mate = int(mate)
        return f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}"
    cp = score.get("cp")
    return f"{int(cp) / 100:+.2f}" if cp is not None else "—"


def _score_for_player(score, side_to_move, player):
    if not score:
        return {"cp": None, "mate": None}
    cp = score.get("cp")
    mate = score.get("mate")
    if side_to_move != player:
        cp = -cp if cp is not None else None
        mate = -mate if mate is not None else None
    return {"cp": cp, "mate": mate}


def _fen_side(fen):
    parts = str(fen or "").split()
    return parts[1] if len(parts) > 1 and parts[1] in ("w", "b") else "w"


def _san_sequence(fen, ucis):
    try:
        board = chess.Board(fen)
    except Exception:
        return []
    result = []
    for uci in ucis or []:
        try:
            move = board.parse_uci(str(uci))
        except (ValueError, TypeError, AssertionError):
            break
        result.append(board.san(move))
        board.push(move)
    return result


def _first_san(fen, uci):
    sans = _san_sequence(fen, [uci]) if uci else []
    return sans[0] if sans else (str(uci) if uci else "—")


def _move_flags(fen, uci):
    try:
        board = chess.Board(fen)
        move = board.parse_uci(str(uci))
        return {"capture": board.is_capture(move), "check": board.gives_check(move)}
    except Exception:
        return {"capture": False, "check": False}


def _parse_info(line):
    if not line.startswith("info ") or " score " not in line or " string " in line:
        return None
    cp_match = re.search(r"score cp (-?\d+)", line)
    mate_match = re.search(r"score mate (-?\d+)", line)
    if not cp_match and not mate_match:
        return None
    depth_match = re.search(r"\bdepth (\d+)", line)
    index_match = re.search(r"\bmultipv (\d+)", line)
    pv_match = re.search(r"\bpv\b(.*)$", line)
    return {
        "index": int(index_match.group(1)) if index_match else 1,
        "depth": int(depth_match.group(1)) if depth_match else 0,
        "score": {
            "cp": int(cp_match.group(1)) if cp_match else None,
            "mate": int(mate_match.group(1)) if mate_match else None,
        },
        "pv": pv_match.group(1).split() if pv_match else None,
    }


def _find_opportunities(before, after, best_san, best_candidate, reply_score, reply_san):
    items = []
    best_value = score_value(before)
    played_value = score_value(after)
    mate = before.get("mate") if before else None
    if mate is not None and mate > 0 and not (after.get("mate") or 0) > 0:
        items.append(f"Missed a forced mate in {abs(mate)} ({best_san})")
    elif best_value is not None and played_value is not None and best_value >= 300 and played_value < 100:
        items.append(f"Missed a winning advantage ({best_san}, {format_score(before)})")
    if best_candidate.get("capture") or best_candidate.get("check"):
        if best_value is None or played_value is None or best_value - played_value >= 150:
            tactic = "check" if best_candidate.get("check") else "capturing move"
            items.append(f"Tactical shot available: {best_san} ({tactic})")
    reply_value = score_value(reply_score)
    if reply_value is not None and reply_value >= 300 and played_value is not None and played_value < 0:
        items.append(f"Allowed a strong reply: {reply_san}")
    return items


def build_game_analysis(
    fens,
    moves,
    positions,
    multipv=3,
    player_rating=None,
    opponent_rating=None,
    player_color=None,
):
    entries = {}
    opportunities = []
    summary = {
        "moves": len(moves),
        "classified": 0,
        "bestOrGood": 0,
        "inaccuracies": 0,
        "mistakes": 0,
        "blunders": 0,
        "byColor": {
            "w": {"bestOrGood": 0, "inaccuracies": 0, "mistakes": 0, "blunders": 0},
            "b": {"bestOrGood": 0, "inaccuracies": 0, "mistakes": 0, "blunders": 0},
        },
    }
    for ply, move in enumerate(moves, start=1):
        before_fen = fens[ply - 1]
        after_fen = fens[ply]
        mover = move.get("color") or ("w" if ply % 2 == 1 else "b")
        before_position = positions[ply - 1]
        after_position = positions[ply]
        raw_candidates = before_position.get("candidates") or []
        best_raw = raw_candidates[0] if raw_candidates else {}
        best_uci = best_raw.get("move") or before_position.get("bestMove")
        best_san = _first_san(before_fen, best_uci)
        eval_before = _score_for_player(best_raw.get("score"), _fen_side(before_fen), mover)
        reply_raw = (after_position.get("candidates") or [{}])[0]
        eval_after = _score_for_player(reply_raw.get("score"), _fen_side(after_fen), mover)
        best_value = score_value(eval_before)
        played_value = score_value(eval_after)
        loss = max(0, best_value - played_value) if best_value is not None and played_value is not None else None
        phase = phase_for_ply(ply - 1, chess.Board(before_fen) if before_fen else None)
        mover_rating = player_rating if mover == player_color else opponent_rating
        classification = (
            classify_loss_for_rating(loss, mover_rating, phase)
            if mover_rating is not None
            else classify_loss(loss)
        )
        best_motifs = tactical_motifs(before_fen, best_uci) if best_uci else []
        played_motifs = tactical_motifs(before_fen, move.get("uci")) if move.get("uci") else []
        candidates = []
        for index, candidate in enumerate(raw_candidates[:multipv], start=1):
            uci = candidate.get("move")
            pv_san = _san_sequence(before_fen, candidate.get("pv") or ([uci] if uci else []))
            flags = _move_flags(before_fen, uci) if uci else {"capture": False, "check": False}
            score = _score_for_player(candidate.get("score"), _fen_side(before_fen), mover)
            candidates.append(
                {
                    "rank": index,
                    "move": uci,
                    "san": pv_san[0] if pv_san else (uci or "—"),
                    "score": score,
                    "centipawns": score_value(score),
                    "pv": pv_san,
                    "pvUci": [str(value) for value in (candidate.get("pv") or []) if value],
                    "capture": flags["capture"],
                    "check": flags["check"],
                }
            )
        reply_uci = reply_raw.get("move") or after_position.get("bestMove")
        reply_san = _first_san(after_fen, reply_uci)
        move_opportunities = _find_opportunities(
            eval_before,
            eval_after,
            best_san,
            candidates[0] if candidates else {},
            reply_raw.get("score"),
            reply_san,
        )
        if classification in ("mistake", "blunder") and best_motifs:
            themes = ", ".join(best_motifs[:2])
            move_opportunities.insert(0, f"{best_san} available: {themes}")
        entry = {
            "ply": ply,
            "move": move.get("san"),
            "color": mover,
            "phase": phase,
            "bestMove": best_uci,
            "bestMoveSan": best_san,
            "playedMove": move.get("uci"),
            "fen": before_fen,
            "evalBefore": eval_before,
            "evalAfter": eval_after,
            "centipawnLoss": loss,
            "classification": classification,
            "candidates": candidates,
            "replySan": reply_san,
            "motifs": played_motifs,
            "bestMotifs": best_motifs,
            "tacticalOpportunities": move_opportunities,
            "explanation": explain_positional_mistake(before_fen, after_fen, move.get("uci")),
        }
        entry["whyBetter"] = why_is_better(entry)
        entry["lesson"] = move_lesson(entry)
        entries[ply] = entry
        for text in move_opportunities:
            opportunities.append({"ply": ply, "move": move.get("san"), "color": mover, "text": text})
        if classification:
            key = {
                "best or good": "bestOrGood",
                "inaccuracy": "inaccuracies",
                "mistake": "mistakes",
                "blunder": "blunders",
            }[classification]
            summary["classified"] += 1
            summary[key] += 1
            summary["byColor"][mover][key] += 1
    return {
        "engine": "Stockfish",
        "moves": entries,
        "summary": summary,
        "opportunities": opportunities,
    }


def rebuild_analysis_summary(entries):
    summary = {
        "moves": len(entries),
        "classified": 0,
        "bestOrGood": 0,
        "inaccuracies": 0,
        "mistakes": 0,
        "blunders": 0,
        "byColor": {
            "w": {"bestOrGood": 0, "inaccuracies": 0, "mistakes": 0, "blunders": 0},
            "b": {"bestOrGood": 0, "inaccuracies": 0, "mistakes": 0, "blunders": 0},
        },
    }
    keys = {
        "best or good": "bestOrGood",
        "inaccuracy": "inaccuracies",
        "mistake": "mistakes",
        "blunder": "blunders",
    }
    for entry in (entries or {}).values():
        key = keys.get(entry.get("classification"))
        if not key:
            continue
        summary["classified"] += 1
        summary[key] += 1
        color = entry.get("color") or "w"
        summary["byColor"][color][key] += 1
    return summary


class _EngineSession:
    def __init__(self, process):
        self.process = process
        self.lines = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        stream = self.process.stdout
        try:
            if stream is not None:
                for raw in iter(stream.readline, b""):
                    self.lines.put(raw.decode("utf-8", "replace").strip())
        except (OSError, ValueError):
            pass
        finally:
            self.lines.put(_EOF)

    def send(self, command):
        stream = self.process.stdin
        if stream is None or stream.closed:
            raise ChessComError("Stockfish stopped responding.")
        try:
            stream.write((str(command) + "\n").encode("utf-8"))
            stream.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise ChessComError("Stockfish stopped responding.") from error

    def read_line(self, timeout):
        try:
            return self.lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def close(self):
        self._reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


class StockfishAnalyzer:
    def __init__(self, path=None):
        self.path_error = ""
        self.set_path(path if path is not None else os.environ.get("STOCKFISH_PATH"))

    def set_path(self, path):
        configured = str(path or "").strip()
        self.path_error = ""
        self.path = ""
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                self.path = str(candidate)
            else:
                self.path_error = "The selected Stockfish file is not executable."
        if not self.path:
            self.path = shutil.which("stockfish") or ""
        self.available = bool(self.path)

    def _start_process(self):
        try:
            options = {}
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if flags:
                    options["creationflags"] = flags
            process = subprocess.Popen(
                [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **options,
            )
        except (OSError, ValueError) as error:
            raise ChessComError("Could not start Stockfish: {error}".format(error=error)) from error
        return _EngineSession(process)

    @staticmethod
    def _send(session, command):
        session.send(command)

    def _analyze_position(self, session, fen, depth, movetime, multipv):
        self._send(session, "position fen " + fen)
        self._send(
            session,
            "go depth {depth} movetime {movetime}".format(
                depth=depth, movetime=movetime
            ),
        )
        candidates = {}
        best_move = None
        reached_depth = 0
        deadline = time.monotonic() + (movetime / 1000) + _timeout("STOCKFISH_RESPONSE_TIMEOUT", 10)
        stopping = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if stopping:
                    break
                try:
                    self._send(session, "stop")
                except ChessComError:
                    break
                stopping = True
                deadline = time.monotonic() + _timeout("STOCKFISH_STOP_TIMEOUT", 2)
                continue
            line = session.read_line(remaining)
            if line is _EOF:
                break
            if line is None:
                continue
            if line.startswith("bestmove"):
                parts = line.split()
                best_move = parts[1] if len(parts) > 1 and parts[1] != "(none)" else None
                break
            info = _parse_info(line)
            if not info:
                continue
            previous = candidates.get(info["index"], {"move": None, "score": {}, "pv": [], "depth": 0})
            if info["depth"] and info["depth"] < previous["depth"]:
                continue
            reached_depth = max(reached_depth, info["depth"])
            pv = info["pv"] or previous["pv"]
            score = info["score"] if (info["score"].get("cp") is not None or info["score"].get("mate") is not None) else previous["score"]
            candidates[info["index"]] = {
                "move": pv[0] if pv else previous["move"],
                "score": score,
                "pv": pv,
                "depth": info["depth"] or previous["depth"],
            }
        ordered = [candidates[key] for key in sorted(candidates)][:multipv]
        if not ordered and best_move:
            ordered = [{"move": best_move, "score": {}, "pv": [best_move], "depth": reached_depth}]
        elif ordered and not ordered[0].get("move") and best_move:
            ordered[0]["move"] = best_move
            if not ordered[0]["pv"]:
                ordered[0]["pv"] = [best_move]
        return {"fen": fen, "depth": reached_depth, "bestMove": best_move, "candidates": ordered}

    @staticmethod
    def _configure_engine(session, multipv):
        StockfishAnalyzer._send(session, "uci")
        StockfishAnalyzer._send(session, "isready")
        StockfishAnalyzer._send(session, "setoption name MultiPV value {multipv}".format(multipv=multipv))
        deadline = time.monotonic() + _timeout("STOCKFISH_STARTUP_TIMEOUT", 5)
        while time.monotonic() < deadline:
            line = session.read_line(deadline - time.monotonic())
            if line is _EOF or line == "readyok":
                break

    @staticmethod
    def _stop_process(session):
        if session is None:
            return
        try:
            StockfishAnalyzer._send(session, "quit")
        except ChessComError:
            pass
        process = session.process
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        session.close()

    def analyze_positions(self, fens, depth=12, movetime=150, multipv=3, progress=None):
        if not self.available:
            raise ChessComError("Stockfish is not configured on this device.")
        fens = [str(item or "").strip() for item in fens]
        if not fens:
            raise ChessComError("No positions supplied for analysis.")
        try:
            fens = [validate_fen(fen) for fen in fens]
        except ValueError as error:
            raise ChessComError(str(error)) from error
        depth = max(4, min(20, int(depth)))
        movetime = max(50, min(3000, int(movetime)))
        multipv = max(1, min(5, int(multipv)))
        session = self._start_process()
        positions = []
        try:
            self._configure_engine(session, multipv)
            for index, fen in enumerate(fens, start=1):
                result = self._analyze_position(session, fen, depth, movetime, multipv)
                positions.append(result)
                if not result.get("bestMove"):
                    self._stop_process(session)
                    session = self._start_process()
                    self._configure_engine(session, multipv)
                if progress:
                    progress(index, len(fens))
        finally:
            self._stop_process(session)
        return positions

    def analyze_game(
        self,
        fens,
        moves,
        depth=12,
        movetime=150,
        multipv=3,
        progress=None,
        player_rating=None,
        opponent_rating=None,
        player_color=None,
    ):
        if len(fens) != len(moves) + 1:
            raise ChessComError("Analysis needs one position per move plus the starting position.")
        positions = self.analyze_positions(
            fens, depth=depth, movetime=movetime, multipv=multipv, progress=progress
        )
        return build_game_analysis(
            fens,
            moves,
            positions,
            multipv=multipv,
            player_rating=player_rating,
            opponent_rating=opponent_rating,
            player_color=player_color,
        )

    def analyze(self, fen, depth=12, movetime=400):
        position = self.analyze_positions([fen], depth=depth, movetime=movetime, multipv=3)[0]
        best = position["candidates"][0] if position["candidates"] else {}
        score = best.get("score") or {}
        return {
            "engine": "Stockfish",
            "depth": position["depth"],
            "bestMove": best.get("move") or position["bestMove"],
            "score": score.get("cp"),
            "mate": score.get("mate"),
            "fen": fen,
            "centipawns": score_value(score),
            "candidates": position["candidates"],
        }

    def analyze_move(self, before_fen, after_fen, depth=12, movetime=400):
        before = self.analyze(before_fen, depth=depth, movetime=movetime)
        after = self.analyze(after_fen, depth=depth, movetime=movetime)
        before_cp = before.get("centipawns")
        after_cp = after.get("centipawns")
        result = {"before": before, "after": after, "engine": "Stockfish"}
        if before_cp is not None and after_cp is not None:
            loss = max(0, before_cp + after_cp)
            result["centipawnLoss"] = loss
            result["classification"] = classify_loss(loss)
        return result
