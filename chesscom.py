import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE_URL = "https://api.chess.com/pub"
USER_AGENT = os.environ.get(
    "CHESSCOM_USER_AGENT",
    "Insight8x8/1.0 (AI-powered chess analysis companion; Python standard library)",
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$")
HEADER_RE = re.compile(r'^\[([A-Za-z0-9_]+)\s+"((?:\\.|[^"\\])*)"\]\s*$')
SAN_RE = re.compile(
    r"^(?:(?:O-O(?:-O)?|0-0(?:-0)?)[+#]?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)$"
)
TIME_CLASSES = (
    ("chess_bullet", "Bullet"),
    ("chess_blitz", "Blitz"),
    ("chess_rapid", "Rapid"),
    ("chess_daily", "Daily"),
)


class ChessComError(Exception):
    pass


class UsernameNotFoundError(ChessComError):
    pass


def validate_username(username):
    value = str(username or "").strip()
    if not USERNAME_RE.match(value):
        raise ChessComError(
            "Enter a valid Chess.com username (letters, numbers, hyphens or underscores)."
        )
    return value.lower()


def _epoch_to_date(value):
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for date_format in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except (TypeError, ValueError):
            continue
    return text


def _status_for(last_online):
    if not isinstance(last_online, (int, float)):
        return "Status unknown"
    days = max(0, (time.time() - float(last_online)) / 86400)
    if days < 7:
        return "Active this week"
    if days < 30:
        return "Recently active"
    if days < 365:
        return "Away from Chess.com"
    return "Long-term inactive"


def _relative_last_online(last_online):
    date = _epoch_to_date(last_online)
    if not date:
        return "Unknown"
    days = int(max(0, (time.time() - float(last_online)) / 86400))
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 30:
        return "{days} days ago".format(days=days)
    if days < 365:
        return "{months} months ago".format(months=max(1, days // 30))
    return "{years} years ago".format(years=max(1, days // 365))


def parse_headers(pgn):
    headers = {}
    for line in str(pgn or "").splitlines():
        match = HEADER_RE.match(line.strip())
        if match:
            headers[match.group(1)] = match.group(2).replace('\\"', '"')
        elif headers:
            break
    return headers


def _strip_braces(text):
    result = []
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif depth == 0:
            result.append(char)
    return "".join(result)


def _strip_variations(text):
    result = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif depth == 0:
            result.append(char)
    return "".join(result)


def move_sans(pgn):
    text = re.sub(r"^\[[^\n]*\]\s*", "", str(pgn or ""), flags=re.MULTILINE)
    text = _strip_braces(text)
    text = "\n".join(line.split(";")[0] for line in text.splitlines())
    text = _strip_variations(text)
    text = re.sub(r"\$[0-9]+", " ", text)
    text = re.sub(r"\b\d+\s*\.\.\.?", " ", text)
    text = re.sub(r"\b\d+\s*\.", " ", text)
    text = re.sub(r"\b(?:1-0|0-1|1/2-1/2|\*)\b", " ", text)
    moves = []
    for token in text.split():
        token = re.sub(r"[!?]+$", "", token.strip())
        if SAN_RE.match(token):
            moves.append(token)
    return moves


def _slug_to_opening(url):
    slug = urllib.parse.unquote(str(url or "").rstrip("/").split("/")[-1])
    if not slug:
        return ""
    parts = slug.split("-")
    title = []
    for part in parts:
        if re.match(r"^\d", part):
            title.append(part)
        elif part.isupper() and len(part) <= 3:
            title.append(part)
        else:
            title.append(part[:1].upper() + part[1:])
    return " ".join(title)


def _infer_time_class(time_control, fallback=None):
    if fallback in {"bullet", "blitz", "rapid", "daily"}:
        return fallback
    value = str(time_control or "")
    if "/" in value or "d" in value.lower():
        return "daily"
    match = re.match(r"(\d+)", value)
    if not match:
        return "rapid"
    seconds = int(match.group(1))
    if seconds < 180:
        return "bullet"
    if seconds < 600:
        return "blitz"
    return "rapid"


def _result_from_pgn(headers, white_result, black_result):
    code = headers.get("Result", "*")
    if code == "1-0":
        return "white"
    if code == "0-1":
        return "black"
    if code == "1/2-1/2":
        return "draw"
    losing = {"checkmated", "resigned", "timeout", "abandoned", "disconnected"}
    if white_result in losing and black_result not in losing:
        return "black"
    if black_result in losing and white_result not in losing:
        return "white"
    if white_result == black_result and white_result in {"draw", "repetition", "stalemate", "insufficient material", "50-move rule", "agreement", "time vs insufficient material"}:
        return "draw"
    return None


def _accuracy_map(game):
    values = game.get("accuracies") or game.get("accurate_moves") or {}
    result = {}
    for key, value in values.items():
        normalized = str(key).lower()
        if normalized in {"white", "black"} and isinstance(value, (int, float)):
            result[normalized] = round(float(value), 2)
    return result


def _duration_seconds(headers, end_time):
    date = headers.get("UTCDate") or headers.get("Date")
    clock = headers.get("UTCTime")
    if not date or not clock or not isinstance(end_time, (int, float)):
        return None
    for date_format in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            started = datetime.strptime(date + " " + clock, date_format + " %H:%M:%S")
            started = started.replace(tzinfo=timezone.utc).timestamp()
            seconds = float(end_time) - started
            if 0 <= seconds <= 172800:
                return int(seconds)
        except (TypeError, ValueError):
            continue
    return None


def game_id(game):
    value = game.get("uuid")
    if value:
        return str(value)
    seed = str(game.get("url") or "") + str(game.get("pgn") or "")
    return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:20]


def _is_supported(game, headers, pgn):
    rules = str(game.get("rules") or "chess").lower()
    variant = str(headers.get("Variant") or "").lower()
    if headers.get("FEN") or headers.get("SetUp") == "1":
        return False
    if rules not in {"chess", ""}:
        return False
    if variant not in {"", "standard", "chess"}:
        return False
    blob = (str(pgn)[:2000] + " " + str(headers.get("Event") or "")).lower()
    return not any(
        token in blob
        for token in ("chess960", "crazyhouse", "atomic", "antichess", "king of the hill", "threefold", "horde", "racing kings")
    )


def parse_game(game, username, include_pgn=False):
    pgn = str(game.get("pgn") or "")
    headers = parse_headers(pgn)
    player = str(username or "").lower()
    white_side = game.get("white") or {}
    black_side = game.get("black") or {}
    white_name = headers.get("White") or white_side.get("username") or ""
    black_name = headers.get("Black") or black_side.get("username") or ""
    color = "white" if white_name.lower() == player else "black"
    player_side = white_side if color == "white" else black_side
    opponent_side = black_side if color == "white" else white_side
    opponent_name = black_name if color == "white" else white_name
    winner = _result_from_pgn(
        headers, white_side.get("result") or "", black_side.get("result") or ""
    )
    if winner == "draw":
        result = "draw"
    elif winner is None:
        result = "unknown"
    else:
        result = "win" if winner == color else "loss"
    time_control = headers.get("TimeControl") or game.get("time_control") or ""
    time_class = _infer_time_class(
        time_control, game.get("time_class") or headers.get("TimeClass")
    )
    end_time = game.get("end_time")
    date = _epoch_to_date(end_time) or _normalize_date(headers.get("UTCDate") or headers.get("Date"))
    accuracies = _accuracy_map(game)
    eco = headers.get("ECO") or ""
    opening = headers.get("Opening") or ""
    if not opening:
        opening = _slug_to_opening(headers.get("ECOUrl") or game.get("eco"))
    ply = len(move_sans(pgn))
    duration = _duration_seconds(headers, end_time)
    record = {
        "id": game_id(game),
        "url": game.get("url") or headers.get("Site") or "",
        "date": date,
        "timestamp": end_time if isinstance(end_time, (int, float)) else None,
        "time": headers.get("UTCTime") or "",
        "rated": bool(game.get("rated", True)),
        "timeClass": time_class,
        "timeControl": time_control,
        "color": color,
        "white": white_name,
        "black": black_name,
        "playerRating": _safe_int(headers.get("WhiteElo" if color == "white" else "BlackElo"), player_side.get("rating")),
        "opponentRating": _safe_int(headers.get("BlackElo" if color == "white" else "WhiteElo"), opponent_side.get("rating")),
        "opponent": opponent_name or str(opponent_side.get("username") or "Unknown"),
        "opponentResult": opponent_side.get("result") or "",
        "result": result,
        "resultCode": headers.get("Result") or "*",
        "termination": headers.get("Termination") or "",
        "opening": opening,
        "eco": eco,
        "openingUrl": headers.get("ECOUrl") or game.get("eco") or "",
        "ply": ply,
        "moves": (ply + 1) // 2,
        "durationSeconds": duration,
        "accuracy": accuracies.get(color),
        "opponentAccuracy": accuracies.get("black" if color == "white" else "white"),
        "whiteAccuracy": accuracies.get("white"),
        "blackAccuracy": accuracies.get("black"),
        "accuracySource": "Chess.com" if color in accuracies else None,
        "supported": _is_supported(game, headers, pgn),
        "event": headers.get("Event") or "",
    }
    if include_pgn:
        record["pgn"] = pgn
    return record


def _safe_int(primary, fallback=None):
    for value in (primary, fallback):
        try:
            if value is not None and str(value).strip():
                return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def normalize_stats(raw):
    time_classes = []
    rating_map = {}
    totals = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
    best_overall = None
    for key, label in TIME_CLASSES:
        block = raw.get(key) or {}
        last = block.get("last") or {}
        best = block.get("best") or {}
        record = block.get("record") or {}
        wins = int(record.get("win") or 0)
        losses = int(record.get("loss") or 0)
        draws = int(record.get("draw") or 0)
        games = wins + losses + draws
        rating = _safe_int(last.get("rating"))
        best_rating = _safe_int(best.get("rating"))
        rating_map[key.replace("chess_", "")] = rating
        if best_rating is not None and (best_overall is None or best_rating > best_overall["rating"]):
            best_overall = {
                "rating": best_rating,
                "timeClass": key.replace("chess_", ""),
                "timeClassName": label,
                "date": _epoch_to_date(best.get("date")),
                "game": best.get("game"),
            }
        totals["games"] += games
        totals["wins"] += wins
        totals["losses"] += losses
        totals["draws"] += draws
        time_classes.append(
            {
                "key": key.replace("chess_", ""),
                "name": label,
                "rating": rating,
                "best": best_rating,
                "bestDate": _epoch_to_date(best.get("date")),
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "games": games,
                "winPercentage": round(wins / games * 100, 1) if games else None,
            }
        )
    totals["winPercentage"] = (
        round(totals["wins"] / totals["games"] * 100, 1) if totals["games"] else None
    )
    return {
        "ratings": rating_map,
        "highest": best_overall or {},
        "totals": totals,
        "timeClasses": time_classes,
    }


class ChessComClient:
    def __init__(self, store):
        self.store = store
        self._rate_lock = threading.Lock()
        self._last_request = 0.0
        self.min_interval = float(os.environ.get("CHESSCOM_MIN_INTERVAL", "0.14"))
        self.max_workers = int(os.environ.get("CHESSCOM_MAX_WORKERS", "5"))

    def _ttl_for_url(self, url):
        if "/games/archives" in url:
            return 6 * 3600
        if "/games/" in url:
            match = re.search(r"/games/(\d{4})/(\d{2})$", url)
            if match:
                now = datetime.now(timezone.utc)
                current = (now.year, now.month)
                if (int(match.group(1)), int(match.group(2))) == current:
                    return 10 * 60
            return 30 * 86400
        if "/country/" in url:
            return 7 * 86400
        return 6 * 3600

    def _wait_for_slot(self):
        with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def fetch_json(self, url, force=False, ttl=None):
        cache_key = "http:" + url
        if not force:
            cached = self.store.get_cache(cache_key)
            if cached is not None:
                return cached
        ttl = self._ttl_for_url(url) if ttl is None else ttl
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        delay = 1.0
        for attempt in range(5):
            self._wait_for_slot()
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.store.set_cache(cache_key, payload, ttl)
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise UsernameNotFoundError("Chess.com player not found.") from exc
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < 4:
                        retry_after = exc.headers.get("Retry-After")
                        try:
                            delay = min(30.0, max(delay, float(retry_after)))
                        except (TypeError, ValueError):
                            pass
                        time.sleep(delay)
                        delay = min(16.0, delay * 2)
                        continue
                    raise ChessComError("Chess.com is rate limiting requests. Try again shortly.") from exc
                raise ChessComError("Chess.com returned HTTP {code}.".format(code=exc.code)) from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < 4:
                    time.sleep(delay)
                    delay = min(16.0, delay * 2)
                    continue
                raise ChessComError("Could not reach the Chess.com API. Check your connection.") from exc
        raise ChessComError("Chess.com request failed.")

    def profile(self, username):
        name = validate_username(username)
        raw = self.fetch_json("{base}/player/{name}".format(base=BASE_URL, name=name))
        country_name = None
        country_url = raw.get("country") or ""
        if country_url.startswith(BASE_URL):
            try:
                country_name = (self.fetch_json(country_url).get("name") or "").strip() or None
            except ChessComError:
                country_name = None
        code = country_url.rstrip("/").split("/")[-1] if country_url else ""
        return {
            "username": raw.get("username") or name,
            "name": raw.get("name") or "",
            "title": raw.get("title") or "",
            "avatar": raw.get("avatar") or "",
            "country": country_name or code.upper() or "Unknown",
            "countryCode": code.upper(),
            "countryUrl": country_url,
            "joined": _epoch_to_date(raw.get("joined")),
            "lastOnline": raw.get("last_online"),
            "lastOnlineLabel": _relative_last_online(raw.get("last_online")),
            "status": _status_for(raw.get("last_online")),
            "followers": raw.get("followers") or 0,
            "isStreamer": bool(raw.get("is_streamer")),
            "isVerified": bool(raw.get("verified")),
            "league": raw.get("league") or "",
            "profileUrl": "https://www.chess.com/member/{name}".format(name=raw.get("username") or name),
        }

    def stats(self, username):
        name = validate_username(username)
        raw = self.fetch_json("{base}/player/{name}/stats".format(base=BASE_URL, name=name))
        return normalize_stats(raw)

    def connect(self, username):
        name = validate_username(username)
        profile = self.profile(name)
        stats = self.stats(name)
        archives = self.archive_urls(name)
        return {
            "username": name,
            "profile": profile,
            "stats": stats,
            "archiveCount": len(archives),
        }

    def archive_urls(self, username, force=False):
        name = validate_username(username)
        raw = self.fetch_json(
            "{base}/player/{name}/games/archives".format(base=BASE_URL, name=name),
            force=force,
        )
        urls = [str(url) for url in raw.get("archives", []) if str(url).startswith(BASE_URL)]
        return sorted(urls)

    def load_games(self, username, force=False, progress=None):
        name = validate_username(username)
        urls = self.archive_urls(name, force) if force else self.archive_urls(name)
        total = len(urls)
        results = [None] * total
        errors = []
        completed = 0
        if progress:
            progress(completed, total)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.fetch_json, url, force): index
                for index, url in enumerate(urls)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    payload = future.result()
                    results[index] = payload.get("games") or []
                except ChessComError as exc:
                    errors.append(str(exc))
                except Exception as exc:
                    errors.append("Unexpected archive error: {exc}".format(exc=exc))
                completed += 1
                if progress:
                    progress(completed, total)
        raw_games = []
        for batch in results:
            if batch:
                raw_games.extend(batch)
        summaries = [parse_game(game, name) for game in raw_games]
        summaries.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        if errors:
            cached = self.store.get_cache("games:" + name)
            if cached is not None:
                summaries = cached
        elif urls:
            self.store.set_cache("games:" + name, summaries, 3600)
        return {
            "archiveCount": total,
            "games": summaries,
            "errors": errors[:10],
        }

    def cached_archive_urls(self, username):
        name = validate_username(username)
        url = "{base}/player/{name}/games/archives".format(base=BASE_URL, name=name)
        payload = self.store.get_cache("http:" + url)
        return [str(item) for item in (payload or {}).get("archives", [])]

    def cached_games(self, username):
        name = validate_username(username)
        cached = self.store.get_cache("games:" + name)
        if cached is not None:
            return cached
        urls = self.cached_archive_urls(name)
        if not urls:
            return []
        raw_games = []
        available = 0
        for url in urls:
            payload = self.store.get_cache("http:" + url)
            if payload is None:
                continue
            available += 1
            raw_games.extend(payload.get("games") or [])
        summaries = [parse_game(game, name) for game in raw_games]
        summaries.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        if available == len(urls):
            self.store.set_cache("games:" + name, summaries, 3600)
        return summaries

    def cached_game_detail(self, username, identifier):
        name = validate_username(username)
        for url in self.cached_archive_urls(name):
            payload = self.store.get_cache("http:" + url) or {}
            for raw in payload.get("games") or []:
                if game_id(raw) == identifier:
                    return parse_game(raw, name, include_pgn=True)
        return None

    def all_game_details(self, username):
        name = validate_username(username)
        details = []
        for url in self.cached_archive_urls(name):
            payload = self.store.get_cache("http:" + url) or {}
            for raw in payload.get("games") or []:
                details.append(parse_game(raw, name, include_pgn=True))
        details.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
        return details
