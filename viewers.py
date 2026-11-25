import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import chess
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from coaching import (
    check_opening_book_with_llm,
    explain_position_with_coach,
    softened_classification,
)
from engine import format_score, rebuild_analysis_summary, validate_pgn
from intelligence import move_lesson, position_factors
from ui_widgets import ChessBoard, EvalBar, captured_pieces
from utils import fmt_date, fmt_duration, fmt_number, result_text
from workers import AnalysisWorker, FunctionWorker


class GameViewerDialog(QWidget):
    favorite_toggled = pyqtSignal(str)
    analysis_saved = pyqtSignal(str, object)

    def __init__(
        self,
        game,
        analyzer,
        is_favorite=False,
        parent=None,
        depth=12,
        movetime=150,
        coach=None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self.game = game
        self.analyzer = analyzer
        self.coach = coach
        self.player_color = "white" if game.get("color") == "white" else "black"
        self.depth = depth
        self.movetime = movetime
        self.positions = []
        self.moves = []
        self.ply = 0
        self.playing = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.worker = None
        self.book_worker = None
        self.explain_worker = None
        self.coach_explanations = {}
        self._closed = False
        self.pgn_error = ""
        self.game_analysis = None
        self.setWindowTitle(f"{game.get('white', '')} vs {game.get('black', '')} — Insight8x8")
        self.resize(1240, 820)
        self.setMinimumSize(900, 620)
        self._parse_pgn()
        self._build_ui(is_favorite)
        self._set_ply(0)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._set_ply(self.ply - 1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._set_ply(self.ply + 1))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_play)

    def _parse_pgn(self):
        start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        positions = []
        moves = []
        try:
            _, parsed = validate_pgn(self.game.get("pgn"))
        except ValueError as error:
            self.pgn_error = str(error)
            self.positions = [start_fen]
            self.moves = moves
            return
        board = parsed.board()
        positions.append(board.fen())
        for move in parsed.mainline_moves():
            san = board.san(move)
            from_square = chess.square_name(move.from_square)
            to_square = chess.square_name(move.to_square)
            captured = board.piece_at(move.to_square)
            captured_symbol = captured.symbol().lower() if captured else ""
            if board.is_en_passant(move):
                captured_symbol = "p"
            board.push(move)
            moves.append(
                {
                    "san": san,
                    "from": from_square,
                    "to": to_square,
                    "uci": move.uci(),
                    "captured": captured_symbol,
                    "color": "w" if len(moves) % 2 == 0 else "b",
                }
            )
            positions.append(board.fen())
        self.positions = positions or [start_fen]
        self.moves = moves

    def _accuracy_for_side(self, side):
        direct = self.game.get(f"{side}Accuracy")
        if direct is not None:
            return direct
        if side == self.game.get("color"):
            return self.game.get("accuracy")
        return self.game.get("opponentAccuracy")

    def _build_ui(self, is_favorite):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        head = QFrame()
        head.setObjectName("viewerHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel(f"{self.game.get('white', '')} vs {self.game.get('black', '')}")
        title.setObjectName("viewerTitle")
        subtitle = QLabel(
            f"{fmt_date(self.game.get('date'))} · {self.game.get('timeClass', '')} · "
            f"{result_text(self.game.get('result'))} · {self.game.get('timeControl', '')}"
        )
        subtitle.setObjectName("muted")
        head_text = QVBoxLayout()
        head_text.addWidget(title)
        head_text.addWidget(subtitle)
        head_layout.addLayout(head_text)
        head_layout.addStretch()
        self.favorite_button = QPushButton("★ Saved" if is_favorite else "☆ Save")
        self.favorite_button.setObjectName("smallButton")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.link_button = QPushButton("Chess.com ↗")
        self.link_button.setObjectName("smallButton")
        self.link_button.clicked.connect(self._open_link)
        close_button = QPushButton("Close")
        close_button.setObjectName("smallButton")
        close_button.clicked.connect(self.close)
        head_layout.addWidget(self.favorite_button)
        head_layout.addWidget(self.link_button)
        head_layout.addWidget(close_button)
        root.addWidget(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        self.eval_bar = EvalBar()
        self.eval_label = QLabel("Eval —")
        self.eval_label.setObjectName("sectionLabel")
        self.eval_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.board = ChessBoard()
        self.board.orientation = self.player_color
        self.board.setMaximumWidth(620)
        eval_column = QVBoxLayout()
        eval_column.setContentsMargins(0, 0, 0, 0)
        eval_column.setSpacing(5)
        eval_column.addWidget(self.eval_label)
        eval_column.addWidget(self.eval_bar, 1)
        board_row = QHBoxLayout()
        board_row.setContentsMargins(0, 0, 0, 0)
        board_row.setSpacing(8)
        board_row.addLayout(eval_column, 0)
        board_row.addWidget(self.board, 1)
        left_layout.addLayout(board_row, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.captured_label = QLabel()
        self.captured_label.setObjectName("muted")
        self.captured_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(self.captured_label)
        controls = QHBoxLayout()
        for text, slot in (
            ("First", self._first),
            ("Previous", self._previous),
            ("Play", self.toggle_play),
            ("Next", self._next),
            ("Last", self._last),
        ):
            button = QPushButton(text)
            button.setObjectName("smallButton")
            button.clicked.connect(slot)
            if text == "Play":
                self.play_button = button
            controls.addWidget(button)
        left_layout.addLayout(controls)
        playback = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, max(0, len(self.moves)))
        self.slider.valueChanged.connect(self._set_ply)
        self.speed = QComboBox()
        self.speed.addItems(["0.5×", "1×", "1.5×", "2×"])
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self._restart_timer)
        playback.addWidget(self.slider, 1)
        playback.addWidget(self.speed)
        left_layout.addLayout(playback)
        tools = QHBoxLayout()
        flip = QPushButton("Flip board")
        flip.setObjectName("smallButton")
        flip.clicked.connect(self._flip)
        download = QPushButton("Download PGN")
        download.setObjectName("smallButton")
        download.clicked.connect(self._download_pgn)
        copy = QPushButton("Copy PGN")
        copy.setObjectName("smallButton")
        copy.clicked.connect(self._copy_pgn)
        my_side = QPushButton("My side")
        my_side.setObjectName("smallButton")
        my_side.clicked.connect(self._my_side)
        tools.addWidget(flip)
        tools.addWidget(my_side)
        tools.addWidget(download)
        tools.addWidget(copy)
        left_layout.addLayout(tools)
        splitter.addWidget(left)

        right = QScrollArea()
        right.setWidgetResizable(True)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(12)
        accuracy_frame = QFrame()
        accuracy_frame.setObjectName("callout")
        accuracy_row = QHBoxLayout(accuracy_frame)
        accuracy_row.setContentsMargins(16, 12, 16, 12)
        accuracy_row.setSpacing(16)
        for side in ("white", "black"):
            value = self._accuracy_for_side(side)
            text = f"{value:.1f}%" if isinstance(value, (int, float)) else "—"
            side_layout = QVBoxLayout()
            caption = QLabel(f"{side.upper()} ACCURACY")
            caption.setObjectName("statTitle")
            badge = QLabel(text)
            badge.setObjectName("accuracyBadge")
            source = QLabel("Chess.com · before analysis")
            source.setObjectName("muted")
            side_layout.addWidget(caption)
            side_layout.addWidget(badge)
            side_layout.addWidget(source)
            accuracy_row.addLayout(side_layout, 1)
            if side == "white":
                self.white_accuracy_label = badge
            else:
                self.black_accuracy_label = badge
        right_layout.addWidget(accuracy_frame)

        meta = QFrame()
        meta.setObjectName("panel")
        meta_grid = QGridLayout(meta)
        meta_grid.setContentsMargins(14, 12, 14, 12)
        entries = [
            ("Your rating", fmt_number(self.game.get("playerRating"))),
            ("Opponent", fmt_number(self.game.get("opponentRating"))),
            ("You played", self.game.get("color", "").title()),
            ("Result", result_text(self.game.get("result"))),
            ("Moves", fmt_number(self.game.get("moves"))),
            ("Duration", fmt_duration(self.game.get("durationSeconds"))),
        ]
        for index, (label, value) in enumerate(entries):
            row, col = divmod(index, 3)
            caption = QLabel(label.upper())
            caption.setObjectName("statTitle")
            value_label = QLabel(value)
            value_label.setObjectName("statValueSmall")
            meta_grid.addWidget(caption, row * 2, col)
            meta_grid.addWidget(value_label, row * 2 + 1, col)
        right_layout.addWidget(meta)

        moves_frame = QFrame()
        moves_frame.setObjectName("panel")
        moves_layout = QVBoxLayout(moves_frame)
        moves_layout.setContentsMargins(10, 10, 10, 10)
        moves_head = QLabel("MOVE HISTORY")
        moves_head.setObjectName("sectionLabel")
        moves_layout.addWidget(moves_head)
        self.move_list = QListWidget()
        self.move_list.setMaximumHeight(230)
        self.move_list.itemClicked.connect(self._move_clicked)
        self._fill_move_list()
        moves_layout.addWidget(self.move_list)
        right_layout.addWidget(moves_frame)

        analysis_frame = QFrame()
        analysis_frame.setObjectName("panel")
        analysis_layout = QVBoxLayout(analysis_frame)
        analysis_layout.setContentsMargins(14, 12, 14, 12)
        analysis_layout.setSpacing(9)
        analysis_head = QLabel("WHAT JUST HAPPENED")
        analysis_head.setObjectName("sectionLabel")
        analysis_layout.addWidget(analysis_head)

        coach_frame = QFrame()
        coach_frame.setObjectName("callout")
        coach_layout = QVBoxLayout(coach_frame)
        coach_layout.setContentsMargins(14, 12, 14, 12)
        coach_layout.setSpacing(6)
        self.coach_headline = QLabel("Choose a move to see the idea.")
        self.coach_headline.setObjectName("viewerTitle")
        self.coach_headline.setWordWrap(True)
        self.coach_body = QLabel(
            "Use ← and → or the slider to walk through the game. The explanation appears here automatically."
        )
        self.coach_body.setObjectName("coachText")
        self.coach_body.setWordWrap(True)
        self.guide_label = QLabel()
        self.guide_label.setObjectName("muted")
        self.guide_label.setWordWrap(True)
        coach_layout.addWidget(self.coach_headline)
        coach_layout.addWidget(self.coach_body)
        coach_layout.addWidget(self.guide_label)
        analysis_layout.addWidget(coach_frame)

        self.detail_label = QLabel(
            f"{self.game.get('opening') or 'Unknown opening'}"
            f"{' · ' + self.game.get('eco') if self.game.get('eco') else ''}"
        )
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        analysis_layout.addWidget(self.detail_label)
        self.analysis_note = QLabel(
            "Read the board first: green shows the stronger move, gold shows the follow-up threat, "
            "and the bar on the left shows who is better."
        )
        self.analysis_note.setObjectName("muted")
        self.analysis_note.setWordWrap(True)
        analysis_layout.addWidget(self.analysis_note)

        self.engine_button = QPushButton("Analyze all moves")
        self.engine_button.setProperty("primary", True)
        self.engine_button.setEnabled(bool(self.analyzer and self.analyzer.available and self.moves))
        self.engine_button.clicked.connect(self._analyze)
        analysis_layout.addWidget(self.engine_button)
        self.analysis_progress = QLabel()
        self.analysis_progress.setObjectName("muted")
        self.analysis_progress.setWordWrap(True)
        if self.pgn_error:
            self.analysis_progress.setText(f"PGN problem: {self.pgn_error}")
        elif not self.moves:
            self.analysis_progress.setText("This game has no replayable moves in the cached PGN.")
        elif not (self.analyzer and self.analyzer.available):
            path_error = getattr(self.analyzer, "path_error", "") if self.analyzer else ""
            message = path_error or "Stockfish is not configured."
            self.analysis_progress.setText(f"{message} Open Engine options to locate it.")
        else:
            self.analysis_progress.setText(
                f"Stockfish ready · depth {self.depth} · {self.movetime} ms per position"
            )
        analysis_layout.addWidget(self.analysis_progress)

        options_toggle = QPushButton("Engine options ▾")
        options_toggle.setObjectName("smallButton")
        options_toggle.setCheckable(True)
        self.advanced_frame = QFrame()
        advanced_layout = QVBoxLayout(self.advanced_frame)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)
        self.settings_button = QPushButton("Engine settings…")
        self.settings_button.setObjectName("smallButton")
        self.settings_button.setToolTip("Choose the Stockfish executable and analysis depth")
        self.settings_button.clicked.connect(self._open_engine_settings)
        advanced_layout.addWidget(self.settings_button)
        self.engine_result_label = QLabel()
        self.engine_result_label.setObjectName("engineResult")
        self.engine_result_label.setWordWrap(True)
        advanced_layout.addWidget(self.engine_result_label)
        self.advanced_frame.hide()
        options_toggle.toggled.connect(self.advanced_frame.setVisible)
        analysis_layout.addWidget(options_toggle)
        analysis_layout.addWidget(self.advanced_frame)

        self.analysis_summary_label = QLabel()
        self.analysis_summary_label.setObjectName("muted")
        self.analysis_summary_label.setWordWrap(True)
        analysis_layout.addWidget(self.analysis_summary_label)
        right_layout.addWidget(analysis_frame)

        right_layout.addStretch()
        right.setWidget(right_widget)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

    @staticmethod
    def _white_score(score, color):
        if not score:
            return {"cp": None, "mate": None}
        result = dict(score)
        if color == "b":
            if result.get("cp") is not None:
                result["cp"] = -int(result["cp"])
            if result.get("mate") is not None:
                result["mate"] = -int(result["mate"])
        return result

    def _set_ply(self, ply):
        if not self.positions:
            return
        self.ply = max(0, min(int(ply), len(self.moves)))
        entries = (self.game_analysis or {}).get("moves", {})
        entry = entries.get(self.ply)
        lesson = entry.get("lesson") if entry else None
        if not lesson and entry:
            lesson = move_lesson(entry)
        self.board.set_position(
            self.positions[self.ply],
            self.moves[self.ply - 1] if self.ply else None,
            self.board.orientation,
            lesson,
        )
        fen = self.positions[self.ply]
        turn = "White" if fen.split(" ")[1] == "w" else "Black"
        move_text = self.moves[self.ply - 1]["san"] if self.ply else "Starting position"
        self.status_label.setText(f"{self.ply}. {move_text}    {turn} to move")
        white_captured, black_captured = captured_pieces(self.moves, self.ply)
        self.captured_label.setText(
            f"White captured: {white_captured or '—'}     Black captured: {black_captured or '—'}"
        )
        self.slider.blockSignals(True)
        self.slider.setValue(self.ply)
        self.slider.blockSignals(False)
        any_selected = False
        for index in range(self.move_list.count()):
            item = self.move_list.item(index)
            selected = item.data(Qt.ItemDataRole.UserRole) == self.ply
            item.setSelected(selected)
            any_selected = any_selected or selected
        if not any_selected:
            self.move_list.clearSelection()
        if self.ply == 0:
            source = entries.get(1) or {}
            score = self._white_score(source.get("evalBefore"), source.get("color"))
        else:
            score = self._white_score(
                entry.get("evalAfter") if entry else None,
                entry.get("color") if entry else None,
            )
        self.eval_bar.set_evaluation(score, self.board.orientation)
        self.eval_label.setText(f"Eval {format_score(score)}")
        self.engine_result_label.setText(self._format_analysis_entry(entry) if entry else "")
        self._show_automatic_explanation(entry, lesson)
        self._request_auto_explanation(entry, lesson)

    def _fill_move_list(self):
        self.move_list.clear()
        markers = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}
        entries = (self.game_analysis or {}).get("moves") or {}
        for index, move in enumerate(self.moves):
            number = index // 2 + 1
            prefix = f"{number}." if index % 2 == 0 else f"{number}..."
            entry = entries.get(index + 1) or {}
            marker = markers.get(entry.get("classification"), "")
            self.move_list.addItem(f"{prefix} {move['san']}{marker}")
            self.move_list.item(self.move_list.count() - 1).setData(Qt.ItemDataRole.UserRole, index + 1)

    def _move_clicked(self, item):
        self._set_ply(int(item.data(Qt.ItemDataRole.UserRole)))

    def _first(self):
        self._set_ply(0)

    def _previous(self):
        self._set_ply(self.ply - 1)

    def _next(self):
        self._set_ply(self.ply + 1)

    def _last(self):
        self._set_ply(len(self.moves))

    def _advance(self):
        if self.ply >= len(self.moves):
            self.toggle_play()
            return
        self._set_ply(self.ply + 1)

    def toggle_play(self):
        if not self.moves:
            return
        if self.playing:
            self.playing = False
            self.timer.stop()
            self.play_button.setText("Play")
            return
        if self.ply >= len(self.moves):
            self._set_ply(0)
        self.playing = True
        self.play_button.setText("Pause")
        self._restart_timer()

    def _restart_timer(self):
        values = [0.5, 1.0, 1.5, 2.0]
        speed = values[self.speed.currentIndex()]
        self.timer.setInterval(max(150, int(1000 / speed)))
        if self.playing:
            self.timer.start()

    def _flip(self):
        self.board.orientation = "black" if self.board.orientation == "white" else "white"
        self._set_ply(self.ply)

    def _my_side(self):
        self.board.orientation = self.player_color
        self._set_ply(self.ply)

    def _show_automatic_explanation(self, entry, lesson):
        if not entry:
            self.coach_headline.setText("Pick a move to see the idea")
            self.coach_body.setText(
                "Use ← and → or the slider to walk through the game. The explanation appears here automatically."
            )
            self.guide_label.setText("Green shows the stronger move; gold shows the follow-up threat.")
            return
        lesson = lesson or {}
        classification = entry.get("classification") or "the engine line"
        self.coach_headline.setText(lesson.get("headline") or f"{entry.get('move')} · {classification}")
        text = (
            self.coach_explanations.get(self.ply)
            or lesson.get("body")
            or entry.get("explanation")
            or entry.get("whyBetter")
            or "Follow the highlighted line to see the idea."
        )
        self.coach_body.setText(text)
        line = lesson.get("line") or lesson.get("bestSan") or entry.get("bestMoveSan") or "—"
        self.guide_label.setText(f"Board line: {line} · Green = stronger move · Gold = follow-up threat")

    def _request_auto_explanation(self, entry, lesson):
        if not entry or not getattr(self.coach, "available", False):
            return
        if self.coach_explanations.get(self.ply):
            return
        if self.explain_worker is not None and self.explain_worker.isRunning():
            return
        context = {
            "fen": entry.get("fen") or (self.positions[self.ply] if self.positions else ""),
            "move": entry.get("move"),
            "best": entry.get("bestMoveSan"),
            "opening": self.game.get("opening") or "Unknown opening",
            "eco": self.game.get("eco") or "",
            "playerColor": self.game.get("color", ""),
            "classification": entry.get("classification"),
            "explanation": (lesson or {}).get("body") or entry.get("explanation"),
            "factors": position_factors(entry.get("fen") or (self.positions[self.ply] if self.positions else "")),
        }
        worker = FunctionWorker(explain_position_with_coach, self.coach, context)
        self.explain_worker = worker
        ply = self.ply

        def cleanup(current=worker):
            if self.explain_worker is current:
                self.explain_worker = None
            current.deleteLater()

        worker.succeeded.connect(lambda text: self._apply_auto_explanation(ply, text))
        worker.failed.connect(lambda message: self._apply_auto_explanation(ply, lesson.get("body") if lesson else ""))
        worker.finished.connect(cleanup)
        worker.start()

    def _apply_auto_explanation(self, ply, text):
        text = str(text or "").strip()
        if not text:
            return
        self.coach_explanations[ply] = text
        if self.ply == ply:
            self.coach_body.setText(text)

    def _toggle_favorite(self):
        self.favorite_toggled.emit(self.game["id"])

    def set_favorite(self, value):
        self.favorite_button.setText("★ Saved" if value else "☆ Save")

    def _open_link(self):
        url = self.game.get("url")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _download_pgn(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save PGN",
            f"{self.game.get('white', 'game')}-vs-{self.game.get('black', 'pgn')}.pgn",
            "PGN files (*.pgn);;All files (*)",
        )
        if filename:
            try:
                with open(filename, "w", encoding="utf-8") as handle:
                    handle.write(self.game.get("pgn") or "")
            except OSError as error:
                QMessageBox.warning(self, "Save PGN", f"Could not save PGN:\n{error}")

    def _copy_pgn(self):
        QApplication.clipboard().setText(self.game.get("pgn") or "")

    def _open_engine_settings(self):
        parent = self.parent()
        if parent is None or not hasattr(parent, "_open_settings"):
            QMessageBox.information(self, "Insight8x8", "Open Engine settings from the main window first.")
            return
        parent._open_settings()
        self.analyzer = parent.analyzer
        options = parent._analysis_options()
        self.depth = options["depth"]
        self.movetime = options["movetime"]
        self.engine_button.setEnabled(bool(self.analyzer and self.analyzer.available and self.moves))
        if self.analyzer.available:
            self.analysis_progress.setText(
                f"Stockfish ready · depth {self.depth} · {self.movetime} ms per position"
            )
        else:
            self.analysis_progress.setText("Stockfish is not configured. Use Engine settings to locate it.")

    def _analyze(self):
        if not self.analyzer or not self.analyzer.available or not self.moves:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self.engine_button.setEnabled(False)
        self.engine_button.setText("Analyzing…")
        self.analysis_progress.setText("Starting Stockfish analysis…")
        player_color = "w" if self.game.get("color") == "white" else "b"
        worker = AnalysisWorker(
            self.analyzer,
            self.positions,
            self.moves,
            depth=self.depth,
            movetime=self.movetime,
            player_rating=self.game.get("playerRating"),
            opponent_rating=self.game.get("opponentRating"),
            player_color=player_color,
        )
        self.worker = worker

        def cleanup(current=worker):
            if self.worker is current:
                self.worker = None
            current.deleteLater()

        worker.progressed.connect(self._analysis_progress)
        worker.succeeded.connect(self._analysis_ready)
        worker.failed.connect(self._analysis_failed)
        worker.finished.connect(cleanup)
        worker.start()

    def _analysis_progress(self, done, total):
        if self._closed:
            return
        self.analysis_progress.setText(f"Analyzed {done} / {total} positions…")

    def _analysis_ready(self, result):
        if self._closed:
            return
        self.game_analysis = result
        self.engine_button.setEnabled(True)
        self.engine_button.setText("Analyze all moves")
        self.analysis_progress.setText(
            f"Analysis complete · {len(result.get('moves') or {})} moves · checking opening book…"
        )
        if getattr(self.coach, "available", False):
            self._check_opening_book_async(result)
            return
        self._apply_book_check(result)
        self._analysis_finalized(result)

    def _apply_book_check(self, result):
        entries = result.get("moves") or {}
        for ply, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("phase") != "opening":
                continue
            context = {
                "fen": entry.get("fen"),
                "uci": entry.get("playedMove"),
                "bestUci": entry.get("bestMove"),
                "san": entry.get("move"),
                "opening": self.game.get("opening"),
                "eco": self.game.get("eco"),
                "ply": ply,
            }
            info = check_opening_book_with_llm(self.coach, context)
            if not info or not info.get("book"):
                continue
            changes = softened_classification(entry, info)
            if changes:
                entry.update(changes)
            else:
                entry["bookMove"] = info
        result["summary"] = rebuild_analysis_summary(entries)
        return result

    def _check_opening_book_async(self, result):
        worker = FunctionWorker(self._apply_book_check, result)
        self.book_worker = worker

        def cleanup(current=worker):
            if self.book_worker is current:
                self.book_worker = None
            current.deleteLater()

        worker.succeeded.connect(lambda updated: self._analysis_finalized(updated))
        worker.failed.connect(lambda message: self._analysis_finalized(result))
        worker.finished.connect(cleanup)
        worker.start()

    def _analysis_finalized(self, result):
        if self._closed:
            return
        self.game_analysis = result
        self.analysis_progress.setText(f"Analysis complete · {len(result.get('moves') or {})} moves")
        self.analysis_summary_label.setText(self._format_analysis_summary(result))
        self.analysis_saved.emit(self.game.get("id", ""), result)
        self._fill_move_list()
        self._set_ply(self.ply)

    def _analysis_failed(self, message):
        if self._closed:
            return
        self.engine_button.setEnabled(True)
        self.engine_button.setText("Analyze all moves")
        self.analysis_progress.setText(f"Analysis failed: {message}")

    def _format_analysis_entry(self, entry):
        if not entry:
            return ""
        color = "White" if entry.get("color") == "w" else "Black"
        suffix = "." if color == "White" else "..."
        verdict = entry.get("classification") or "unclassified"
        lines = [
            f"Move {entry['ply']}{suffix} {entry.get('move')} ({color}) · {verdict}",
            f"Best move: {entry.get('bestMoveSan') or '—'}",
        ]
        book = entry.get("bookMove")
        if book and book.get("book"):
            lines.append(f"Opening note: {book.get('explanation') or book.get('name')}")
        explanation = entry.get("explanation")
        if explanation:
            lines.append(f"Why it matters: {explanation}")
        why_better = entry.get("whyBetter")
        if why_better:
            lines.append(f"Better idea: {why_better}")
        motifs = entry.get("motifs") or []
        if motifs:
            lines.append("Themes: " + ", ".join(motifs))
        lines.append("Follow the board arrows for the stronger move and its threat.")
        return "\n".join(lines)

    def _format_analysis_summary(self, result):
        summary = result.get("summary") or {}
        lines = [
            f"Analyzed {summary.get('classified', 0)} of {summary.get('moves', 0)} moves · "
            f"Blunders {summary.get('blunders', 0)} · Mistakes {summary.get('mistakes', 0)} · "
            f"Inaccuracies {summary.get('inaccuracies', 0)} · Best/good {summary.get('bestOrGood', 0)}"
        ]
        opportunities = result.get("opportunities") or []
        if opportunities:
            lines.append(f"Tactical opportunities ({len(opportunities)}):")
            for item in opportunities[:12]:
                suffix = "." if item.get("color") == "w" else "..."
                lines.append(f"  {item['ply']}{suffix} {item.get('move')}: {item.get('text')}")
            if len(opportunities) > 12:
                lines.append(f"  ... and {len(opportunities) - 12} more")
        else:
            lines.append("No tactical opportunities detected.")
        return "\n".join(lines)

    def closeEvent(self, event):
        self.timer.stop()
        self._closed = True
        for worker in (self.worker, self.book_worker, self.explain_worker):
            if worker is not None and worker.isRunning():
                worker.wait(1000)
        event.accept()
