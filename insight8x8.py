import csv
import io
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "vendor"))

from PyQt6.QtCore import QDate, QSettings, QSignalBlocker, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chesscom import ChessComClient, validate_username
from coaching import (
    LLMCoach,
    build_coaching_data,
    load_env_file,
    improvement_summary,
    progress_history,
    record_progress,
    training_report,
)
from engine import StockfishAnalyzer
from store import Store
from ui_widgets import (
    AvatarLabel,
    BarChart,
    ChessBoard,
    DonutChart,
    LineChart,
    StatCard,
)
from utils import (
    csv_safe,
    fmt_date,
    game_matches_filters,
    fmt_duration,
    fmt_number,
    result_letter,
    result_text,
)
from viewers import GameViewerDialog
from workers import ArchiveWorker, FunctionWorker

DEMO_FEN = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 4"


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def make_panel(title=None):
    panel = QFrame()
    panel.setObjectName("panel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    if title:
        label = QLabel(title.upper())
        label.setObjectName("sectionLabel")
        layout.addWidget(label)
    return panel, layout


def section_label(text):
    label = QLabel(text.upper())
    label.setObjectName("sectionLabel")
    return label


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
        tc = game.get("timeClass") or "rapid"
        bucket = by_class.setdefault(tc, {"games": 0, "win": 0, "loss": 0, "draw": 0})
        bucket["games"] += 1
        if result in bucket:
            bucket[result] += 1
        color = game.get("color")
        if color in by_color and result in by_color[color]:
            by_color[color][result] += 1
        opening = game.get("opening") or "Unknown opening"
        record = by_opening.setdefault(opening, {"name": opening, "eco": game.get("eco") or "", "games": 0, "win": 0, "loss": 0, "draw": 0})
        record["games"] += 1
        if result in record:
            record[result] += 1
        opponent = game.get("opponent") or "Unknown"
        by_opponent[opponent] = by_opponent.get(opponent, 0) + 1
        month = str(game.get("date") or "")[:7]
        if month:
            by_month[month] = by_month.get(month, 0) + 1
        if month and game.get("playerRating"):
            key = (tc, month)
            timestamp = game.get("timestamp") or 0
            if key not in rating_timestamps or timestamp >= rating_timestamps[key]:
                rating_timestamps[key] = timestamp
                rating_history.setdefault(tc, {})[month] = game["playerRating"]
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


def stylesheet():
    return """
    QMainWindow, QWidget, QDialog { background: #0a0e0c; color: #edf5ef; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
    QLabel#heroTitle { font-size: 44px; font-weight: 750; color: #edf5ef; }
    QLabel#heroAccent { color: #5fd39a; font-size: 16px; font-weight: 600; }
    QLabel#lead { color: #a9b9ad; font-size: 14px; line-height: 150%; }
    QLabel#muted, QLabel#statHint { color: #8ba092; font-size: 11px; }
    QLabel#sectionLabel { color: #8ba092; font-size: 11px; font-weight: 700; letter-spacing: 1.4px; }
    QLabel#pageTitle { font-size: 25px; font-weight: 700; }
    QLabel#statTitle { color: #789083; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; }
    QLabel#statValue { color: #edf5ef; font-size: 27px; font-weight: 750; }
    QLabel#statValueSmall { color: #edf5ef; font-size: 17px; font-weight: 650; }
    QLabel#viewerTitle { font-size: 17px; font-weight: 700; }
    QLabel#accuracyBadge { color: #5fd39a; font-size: 30px; font-weight: 750; }
    QLabel#coachText { color: #edf5ef; font-size: 14px; line-height: 155%; }
    QLabel#statusLabel { color: #edf5ef; font-size: 13px; font-weight: 600; }
    QLabel#engineResult { color: #e2c17f; font-size: 11px; }
    QLabel#emptyState { color: #a9b9ad; font-size: 13px; padding: 24px; qproperty-wordWrap: true; }
    QLabel#avatar { border: 2px solid #5fd39a; border-radius: 14px; background: #1a251f; color: #5fd39a; font-size: 22px; font-weight: 700; }
    QFrame#panel, QFrame#viewerHead { background: #121b16; border: 1px solid #26382d; border-radius: 14px; }
    QFrame#callout { background: #16271e; border: 1px solid #2f5b42; border-radius: 14px; }
    QFrame#statCard { background: #141d18; border: 1px solid #26382d; border-radius: 13px; }
    QFrame#sidebar { background: #0f1712; border-right: 1px solid #26382d; }
    QPushButton { background: #18231d; color: #edf5ef; border: 1px solid #2f4437; border-radius: 9px; padding: 9px 14px; font-weight: 600; }
    QPushButton:hover { background: #203027; border-color: #5fd39a; }
    QPushButton:disabled { color: #65766b; border-color: #233329; background: #131a16; }
    QPushButton[primary="true"] { background: #5fd39a; color: #06110a; border-color: #5fd39a; }
    QPushButton[primary="true"]:hover { background: #8ee6b6; }
    QPushButton#smallButton { padding: 6px 10px; font-size: 11px; }
    QPushButton#linkButton { background: transparent; border: 0; color: #8ee6b6; text-align: left; padding: 3px 0; font-weight: 600; }
    QPushButton#linkButton:hover { color: #c4f5da; text-decoration: underline; }
    QPushButton#navButton { background: transparent; border: 0; text-align: left; padding: 11px 14px; color: #a9b9ad; }
    QPushButton#navButton:hover { background: #18261e; color: #edf5ef; }
    QPushButton#navButton[active="true"] { background: #1a2b21; color: #5fd39a; border-left: 3px solid #5fd39a; }
    QLineEdit, QComboBox, QDateEdit, QPlainTextEdit, QSpinBox { background: #141e18; color: #edf5ef; border: 1px solid #2f4437; border-radius: 9px; padding: 8px 10px; selection-background-color: #2f9e68; }
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: #5fd39a; }
    QComboBox::drop-down { border: 0; }
    QTreeWidget, QListWidget { background: #121b16; color: #edf5ef; border: 1px solid #26382d; border-radius: 10px; gridline-color: #1d2a22; }
    QTreeWidget::item, QListWidget::item { padding: 8px; border-bottom: 1px solid #1d2a22; }
    QTreeWidget::item:hover, QListWidget::item:hover { background: #1b2a21; }
    QTreeWidget::item:selected, QListWidget::item:selected { background: #285642; }
    QHeaderView::section { background: #17221b; color: #8ba092; border: 0; border-bottom: 1px solid #26382d; padding: 9px; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
    QScrollBar:vertical, QScrollBar:horizontal { background: #0d1410; width: 10px; height: 10px; }
    QScrollBar::handle { background: #2f4437; border-radius: 5px; }
    QScrollBar::handle:hover { background: #5fd39a; }
    QProgressBar { background: #0d1410; border: 1px solid #26382d; border-radius: 8px; text-align: center; height: 20px; }
    QProgressBar::chunk { background: #5fd39a; border-radius: 8px; }
    QStatusBar { color: #8ba092; }
    QSlider::groove:horizontal { background: #1a251f; height: 6px; border-radius: 3px; }
    QSlider::handle:horizontal { background: #5fd39a; width: 14px; margin: -4px 0; border-radius: 7px; }
    QMenu { background: #18231d; border: 1px solid #2f4437; border-radius: 8px; padding: 4px; }
    QMenu::item { padding: 7px 20px; border-radius: 5px; }
    QMenu::item:selected { background: #285642; }
    QToolTip { background: #1d2b23; color: #edf5ef; border: 1px solid #3d5a48; border-radius: 6px; padding: 6px; }
    QMessageBox { background: #101712; }
    QDialogButtonBox QPushButton { min-width: 84px; }
    """


class EngineSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Insight8x8 · Engine settings")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        form.setContentsMargins(22, 20, 22, 20)
        form.setSpacing(12)
        intro = QLabel("Stockfish runs locally. Choose the executable and how deeply it should search.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        form.addRow(intro)
        path_row = QHBoxLayout()
        self.path_input = QLineEdit(settings.value("enginePath", "", type=str))
        self.path_input.setPlaceholderText("Leave empty to use Stockfish from PATH")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(browse)
        form.addRow("Stockfish executable", path_row)
        self.depth_input = QSpinBox()
        self.depth_input.setRange(4, 30)
        self.depth_input.setValue(int(settings.value("analysisDepth", 12, type=int)))
        form.addRow("Search depth", self.depth_input)
        self.movetime_input = QSpinBox()
        self.movetime_input.setRange(50, 2000)
        self.movetime_input.setSingleStep(50)
        self.movetime_input.setSuffix(" ms")
        self.movetime_input.setValue(int(settings.value("analysisMovetime", 150, type=int)))
        form.addRow("Time per position", self.movetime_input)
        note = QLabel("Higher values are more accurate but take longer on large games.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Locate Stockfish", self.path_input.text() or str(Path.home()))
        if path:
            self.path_input.setText(path)

    def values(self):
        return {
            "enginePath": self.path_input.text().strip(),
            "analysisDepth": self.depth_input.value(),
            "analysisMovetime": self.movetime_input.value(),
        }


class OnboardingDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Welcome to Insight8x8")
        self.setMinimumWidth(540)
        form = QFormLayout(self)
        form.setContentsMargins(24, 22, 24, 22)
        form.setSpacing(12)
        intro = QLabel("Analyse your Chess.com games locally, then turn repeated mistakes into training.")
        intro.setObjectName("viewerTitle")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.username_input = QLineEdit(settings.value("username", "", type=str))
        self.username_input.setPlaceholderText("Chess.com username")
        form.addRow("Username", self.username_input)
        engine_row = QHBoxLayout()
        self.engine_input = QLineEdit(settings.value("enginePath", "", type=str))
        self.engine_input.setPlaceholderText("Leave empty to use Stockfish from PATH")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        engine_row.addWidget(self.engine_input, 1)
        engine_row.addWidget(browse)
        form.addRow("Stockfish", engine_row)
        self.focus_input = QComboBox()
        self.focus_input.addItems(["Balanced", "Tactics", "Openings", "Endgame"])
        self.focus_input.setCurrentText(settings.value("coachingFocus", "Balanced", type=str))
        form.addRow("Training focus", self.focus_input)
        self.llm_check = QCheckBox("Enable optional LLM coaching (advanced)")
        url = settings.value("llmUrl", "", type=str)
        self.llm_check.setChecked(bool(url))
        form.addRow(self.llm_check)
        self.llm_url = QLineEdit(url)
        self.llm_url.setPlaceholderText("https://api.example.com/v1/chat/completions")
        self.llm_model = QLineEdit(settings.value("llmModel", "gpt-4o-mini", type=str))
        self.llm_key = QLineEdit(os.environ.get("INSIGHT8X8_LLM_KEY", ""))
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key.setPlaceholderText("Session only, or set INSIGHT8X8_LLM_KEY")
        form.addRow("LLM endpoint", self.llm_url)
        form.addRow("LLM model", self.llm_model)
        form.addRow("LLM API key", self.llm_key)
        note = QLabel("Local coaching works without any network access. The LLM key is never written to disk.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.llm_check.toggled.connect(self._toggle_llm)
        self._toggle_llm(self.llm_check.isChecked())

    def _toggle_llm(self, checked):
        for field in (self.llm_url, self.llm_model, self.llm_key):
            field.setEnabled(checked)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Locate Stockfish", self.engine_input.text() or str(Path.home()))
        if path:
            self.engine_input.setText(path)

    def values(self):
        return {
            "username": self.username_input.text().strip(),
            "enginePath": self.engine_input.text().strip(),
            "coachingFocus": self.focus_input.currentText(),
            "llmEnabled": self.llm_check.isChecked(),
            "llmUrl": self.llm_url.text().strip() if self.llm_check.isChecked() else "",
            "llmModel": self.llm_model.text().strip() or "gpt-4o-mini",
            "llmKey": self.llm_key.text().strip() if self.llm_check.isChecked() else "",
        }


class Insight8x8App(QMainWindow):
    def __init__(self):
        super().__init__()
        db_path = Path(os.environ.get("INSIGHT8X8_DB", str(BASE_DIR / "data" / "insight8x8.db")))
        self.store = Store(db_path)
        self.client = ChessComClient(self.store)
        self.settings = QSettings("Insight8x8", "Insight8x8")
        self.analyzer = StockfishAnalyzer(self.settings.value("enginePath", "", type=str))
        self.username = ""
        self.pending_username = ""
        self.connection = None
        self.games = []
        self.favorites = set()
        self.workers = []
        self.archive_worker = None
        self._closing = False
        self._store_closed = False
        self.viewers = []
        self.game_filters = {
            False: self._default_filters(),
            True: self._default_filters(),
        }
        self.training = {
            "lessons": [],
            "exercises": [],
            "weaknesses": [],
            "current": None,
            "index": 0,
            "correct": 0,
            "answered": 0,
        }
        self.coach = LLMCoach()
        self.setWindowTitle("Insight8x8 — AI-powered chess analysis")
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)
        self.setStyleSheet(stylesheet())
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.setStatusBar(self.status)
        self._build_shell()
        self._build_shortcuts()
        self._refresh_engine_status()
        self.status.showMessage("Ready · Enter a Chess.com username to begin")
        if not self.settings.value("onboardingComplete", False, type=bool):
            QTimer.singleShot(60, self._run_onboarding)
        saved = self.settings.value("username", "", type=str)
        if saved:
            QTimer.singleShot(120, lambda: self.connect_account(saved, silent=True))

    def _default_filters(self):
        return {
            "search": "",
            "result": "all",
            "timeClass": "all",
            "color": "all",
            "dateFrom": QDate(2000, 1, 1).toString("yyyy-MM-dd"),
            "dateTo": QDate.currentDate().addYears(2).toString("yyyy-MM-dd"),
            "sort": "recent",
            "pageSize": 50,
            "page": 1,
        }

    def _build_shortcuts(self):
        shortcuts = [
            ("Focus search", QKeySequence("Ctrl+F"), lambda: self._focus_search()),
            ("Refresh archives", QKeySequence("F5"), lambda: self.start_archive_load(refresh=True)),
            ("Engine settings", QKeySequence("Ctrl+,"), self._open_settings),
        ]
        for index, key in enumerate(
            ("overview", "games", "openings", "compare", "favorites", "training", "progress", "export"), 1
        ):
            shortcuts.append((f"Open {key}", QKeySequence(f"Ctrl+{index}"), lambda checked=False, value=key: self.show_tab(value)))
        for text, sequence, handler in shortcuts:
            action = QAction(text, self)
            action.setShortcut(sequence)
            action.triggered.connect(handler)
            self.addAction(action)

    def _focus_search(self):
        self.show_tab("games")
        self.game_filters[False]["widgets"]["search"].setFocus()

    def _open_settings(self):
        dialog = EngineSettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        for key, value in values.items():
            self.settings.setValue(key, value)
        self.analyzer.set_path(values["enginePath"])
        self._refresh_engine_status()
        state = "ready" if self.analyzer.available else "not configured"
        self.status.showMessage(
            f"Engine settings saved · Stockfish {state} · depth {values['analysisDepth']}"
        )

    def _run_onboarding(self):
        dialog = OnboardingDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        for key, value in values.items():
            if key not in ("llmKey", "llmEnabled"):
                self.settings.setValue(key, value)
        self.settings.setValue("onboardingComplete", True)
        if values["username"]:
            self.username_input.setText(values["username"])
        self.analyzer.set_path(values["enginePath"])
        self._apply_llm_settings(values)
        self._refresh_engine_status()
        self.status.showMessage("Welcome to Insight8x8 · connect your account to begin")

    def _apply_llm_settings(self, values=None):
        values = values or {}
        if values.get("llmKey"):
            os.environ["INSIGHT8X8_LLM_KEY"] = values["llmKey"]
        self.coach = LLMCoach(
            url=values.get("llmUrl", self.settings.value("llmUrl", "", type=str)),
            key=values.get("llmKey") or os.environ.get("INSIGHT8X8_LLM_KEY", ""),
            model=values.get("llmModel", self.settings.value("llmModel", "gpt-4o-mini", type=str)),
        )

    def _analysis_options(self):
        return {
            "depth": int(self.settings.value("analysisDepth", 12, type=int)),
            "movetime": int(self.settings.value("analysisMovetime", 150, type=int)),
        }

    def _refresh_engine_status(self):
        if not hasattr(self, "engine_status"):
            return
        if self.analyzer.available:
            text = f"Stockfish ready · depth {self.settings.value('analysisDepth', 12, type=int)}"
        else:
            text = "Stockfish not found · open Engine settings"
        self.engine_status.setText(text)

    def _build_shell(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.landing_page = self._build_landing()
        self.dashboard_page = self._build_dashboard()
        self.stack.addWidget(self.landing_page)
        self.stack.addWidget(self.dashboard_page)

    def _build_landing(self):
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(60, 48, 60, 48)
        root.setSpacing(48)
        left = QVBoxLayout()
        left.setSpacing(16)
        brand = QLabel("INSIGHT8X8")
        brand.setObjectName("heroAccent")
        badge = QLabel("YOUR PERSONAL AI-POWERED CHESS ANALYSIS COMPANION")
        badge.setObjectName("sectionLabel")
        title = QLabel("Don't just see\nyour blunders.")
        title.setObjectName("heroTitle")
        accent = QLabel("Understand them. Learn from them. Win more.")
        accent.setObjectName("heroAccent")
        lead = QLabel(
            "Explore your Chess.com games using just your username. Stockfish checks every "
            "move and Insight8x8 turns the evaluation into clear, human-friendly explanations "
            "of why each move matters — including the mistakes and missed opportunities."
        )
        lead.setObjectName("lead")
        lead.setWordWrap(True)
        lead.setMaximumWidth(620)
        left.addWidget(brand)
        left.addWidget(badge)
        left.addWidget(title)
        left.addWidget(accent)
        left.addWidget(lead)
        benefits = QHBoxLayout()
        benefits.setSpacing(8)
        for heading, body in (
            ("Explore", "Every public game, instantly"),
            ("Understand", "Plain-English move analysis"),
            ("Improve", "Patterns you can act on"),
        ):
            card = QFrame()
            card.setObjectName("panel")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(3)
            card_heading = QLabel(heading.upper())
            card_heading.setObjectName("sectionLabel")
            card_body = QLabel(body)
            card_body.setObjectName("muted")
            card_body.setWordWrap(True)
            card_layout.addWidget(card_heading)
            card_layout.addWidget(card_body)
            benefits.addWidget(card)
        left.addLayout(benefits)
        form_panel = QFrame()
        form_panel.setObjectName("panel")
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(20, 18, 20, 18)
        form_layout.setSpacing(10)
        form_label = QLabel("START WITH A CHESS.COM USERNAME")
        form_label.setObjectName("sectionLabel")
        form_row = QHBoxLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("e.g. magnuscarlsen")
        self.username_input.setMinimumHeight(44)
        self.username_input.setToolTip("Only public Chess.com usernames are accepted — no password needed.")
        self.username_input.returnPressed.connect(lambda: self.connect_account(self.username_input.text()))
        self.connect_button = QPushButton("Analyze my games →")
        self.connect_button.setProperty("primary", True)
        self.connect_button.setMinimumHeight(44)
        self.connect_button.setToolTip("Load public archives and prepare your games for analysis (Ctrl+F to search later).")
        self.connect_button.clicked.connect(lambda: self.connect_account(self.username_input.text()))
        form_row.addWidget(self.username_input, 1)
        form_row.addWidget(self.connect_button)
        note = QLabel("Public data only. No Chess.com password is ever required. Press Enter to continue.")
        note.setObjectName("muted")
        self.landing_error = QLabel()
        self.landing_error.setObjectName("engineResult")
        self.landing_error.setWordWrap(True)
        self.landing_error.hide()
        form_layout.addWidget(form_label)
        form_layout.addLayout(form_row)
        form_layout.addWidget(note)
        form_layout.addWidget(self.landing_error)
        left.addWidget(form_panel)
        features = QLabel("STOCKFISH ANALYSIS   •   INTERACTIVE REPLAY   •   RATING CHARTS   •   PGN & CSV EXPORT")
        features.setObjectName("muted")
        left.addWidget(features)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("panel")
        self.preview_frame.hide()
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(18, 16, 18, 16)
        preview_head = QHBoxLayout()
        self.preview_avatar = AvatarLabel(size=64)
        preview_text = QVBoxLayout()
        self.preview_name = QLabel()
        self.preview_name.setObjectName("viewerTitle")
        self.preview_meta = QLabel()
        self.preview_meta.setObjectName("muted")
        preview_text.addWidget(self.preview_name)
        preview_text.addWidget(self.preview_meta)
        preview_head.addWidget(self.preview_avatar)
        preview_head.addLayout(preview_text)
        preview_head.addStretch()
        open_button = QPushButton("Open dashboard →")
        open_button.setProperty("primary", True)
        open_button.clicked.connect(self.show_dashboard)
        preview_head.addWidget(open_button)
        self.preview_stats = QLabel()
        self.preview_stats.setObjectName("muted")
        self.preview_stats.setWordWrap(True)
        preview_layout.addLayout(preview_head)
        preview_layout.addWidget(self.preview_stats)
        left.addWidget(self.preview_frame)
        left.addStretch()
        root.addLayout(left, 3)
        board_panel = QFrame()
        board_panel.setObjectName("panel")
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(20, 20, 20, 20)
        board = ChessBoard()
        board.set_position(DEMO_FEN, {"from": "c4", "to": "c5"}, "white")
        board.setMaximumWidth(520)
        board_layout.addWidget(board, 0, Qt.AlignmentFlag.AlignCenter)
        caption = QLabel("Every position has a story · open a game to reveal it")
        caption.setObjectName("muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        board_layout.addWidget(caption)
        root.addWidget(board_panel, 2)
        return page

    def _build_dashboard(self):
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(252)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 20, 18, 20)
        side_layout.setSpacing(14)
        brand = QLabel("INSIGHT8X8")
        brand.setObjectName("heroAccent")
        side_layout.addWidget(brand)
        player_frame = QFrame()
        player_frame.setObjectName("panel")
        player_layout = QVBoxLayout(player_frame)
        player_layout.setContentsMargins(14, 14, 14, 14)
        player_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.side_avatar = AvatarLabel(size=72)
        self.side_name = QLabel()
        self.side_name.setObjectName("viewerTitle")
        self.side_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.side_country = QLabel()
        self.side_country.setObjectName("muted")
        self.side_country.setAlignment(Qt.AlignmentFlag.AlignCenter)
        player_layout.addWidget(self.side_avatar, 0, Qt.AlignmentFlag.AlignCenter)
        player_layout.addWidget(self.side_name)
        player_layout.addWidget(self.side_country)
        side_layout.addWidget(player_frame)
        self.load_progress = QProgressBar()
        self.load_progress.setVisible(False)
        side_layout.addWidget(self.load_progress)
        self.archive_hint = QLabel()
        self.archive_hint.setObjectName("muted")
        self.archive_hint.setWordWrap(True)
        self.archive_hint.hide()
        side_layout.addWidget(self.archive_hint)
        nav_frame = QFrame()
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(4)
        self.nav_buttons = {}
        nav_order = (
            ("overview", "Overview"),
            ("games", "Match history"),
            ("openings", "Openings"),
            ("compare", "Compare players"),
            ("favorites", "Favorite games"),
            ("training", "Training"),
            ("progress", "Progress"),
            ("export", "Export center"),
        )
        primary_nav = {"overview", "games", "training", "progress", "export"}
        for index, (key, text) in enumerate(nav_order, 1):
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setToolTip(f"{text} (Ctrl+{index})")
            button.clicked.connect(lambda checked=False, value=key: self.show_tab(value))
            self.nav_buttons[key] = button
            if key in primary_nav:
                nav_layout.addWidget(button)
            else:
                button.hide()
        more = QPushButton("More tools ▾")
        more.setObjectName("navButton")
        more_menu = QMenu(more)
        for key in ("openings", "compare", "favorites"):
            more_menu.addAction(
                self.nav_buttons[key].text(),
                lambda checked=False, value=key: self.show_tab(value),
            )
        more.setMenu(more_menu)
        nav_layout.addWidget(more)
        side_layout.addWidget(nav_frame)
        side_layout.addStretch()
        self.engine_status = QLabel()
        self.engine_status.setObjectName("muted")
        self.engine_status.setWordWrap(True)
        side_layout.addWidget(self.engine_status)
        settings_button = QPushButton("Engine settings")
        settings_button.setToolTip("Set the Stockfish executable, search depth and speed (Ctrl+,)")
        settings_button.clicked.connect(self._open_settings)
        refresh = QPushButton("Refresh archives")
        refresh.setToolTip("Reload monthly archives from Chess.com (F5)")
        refresh.clicked.connect(lambda: self.start_archive_load(refresh=True))
        change = QPushButton("Change account")
        change.clicked.connect(self.change_account)
        side_layout.addWidget(settings_button)
        side_layout.addWidget(refresh)
        side_layout.addWidget(change)
        root.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(26, 22, 26, 22)
        content.setSpacing(14)
        head = QHBoxLayout()
        self.page_title = QLabel("Overview")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("muted")
        head_text = QVBoxLayout()
        head_text.addWidget(self.page_title)
        head_text.addWidget(self.page_subtitle)
        head.addLayout(head_text)
        head.addStretch()
        analyze = QPushButton("Analyze a game →")
        analyze.setProperty("primary", True)
        analyze.setToolTip("Open the most recent game in the replay and analysis viewer")
        analyze.clicked.connect(self._open_first_game)
        head.addWidget(analyze)
        content.addLayout(head)
        self.tabs = QStackedWidget()
        self.overview_page = self._build_overview()
        self.games_page = self._build_games(False)
        self.favorites_page = self._build_games(True)
        self.openings_page = self._build_openings()
        self.compare_page = self._build_compare()
        self.training_page = self._build_training()
        self.progress_page = self._build_progress()
        self.export_page = self._build_export()
        for widget in (
            self.overview_page,
            self.games_page,
            self.openings_page,
            self.compare_page,
            self.favorites_page,
            self.training_page,
            self.progress_page,
            self.export_page,
        ):
            self.tabs.addWidget(widget)
        content.addWidget(self.tabs, 1)
        root.addLayout(content, 1)
        return page

    def _build_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.overview_inner_layout = QVBoxLayout(inner)
        self.overview_inner_layout.setContentsMargins(2, 2, 12, 2)
        self.overview_inner_layout.setSpacing(14)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    def _build_games(self, favorites_only):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        toolbar = QFrame()
        toolbar.setObjectName("panel")
        bar_layout = QGridLayout(toolbar)
        bar_layout.setContentsMargins(12, 10, 12, 10)
        bar_layout.setSpacing(8)
        bar_layout.setColumnStretch(0, 3)
        search = QLineEdit()
        search.setPlaceholderText("Search opponent, opening or ECO… (Ctrl+F)")
        search.setToolTip("Type an opponent, opening name or ECO code")
        sort = QComboBox()
        sort.addItems([
            "Newest first", "Oldest first", "Opponent A–Z", "Rating high → low",
            "Rating low → high", "Wins first", "Losses first",
        ])
        sort.setToolTip("Choose how games are ordered before pagination")
        page_size = QComboBox()
        page_size.addItems(["25 per page", "50 per page", "100 per page", "All games"])
        page_size.setCurrentIndex(1)
        result = QComboBox()
        result.addItems(["All results", "Wins", "Losses", "Draws"])
        time_control = QComboBox()
        time_control.addItems(["All time controls", "Bullet", "Blitz", "Rapid", "Daily"])
        color = QComboBox()
        color.addItems(["Both colors", "As White", "As Black"])
        date_from = QDateEdit()
        date_from.setCalendarPopup(True)
        date_from.setDisplayFormat("yyyy-MM-dd")
        date_from.setDate(QDate(2000, 1, 1))
        date_to = QDateEdit()
        date_to.setCalendarPopup(True)
        date_to.setDisplayFormat("yyyy-MM-dd")
        date_to.setDate(QDate.currentDate().addYears(2))
        clear = QPushButton("Clear filters")
        clear.setToolTip("Reset search, filters, sorting and pagination")
        for widget in (search, sort, page_size, result, time_control, color, date_from, date_to, clear):
            widget.setMinimumHeight(36)
        bar_layout.addWidget(search, 0, 0, 1, 3)
        bar_layout.addWidget(sort, 0, 3)
        bar_layout.addWidget(page_size, 0, 4)
        bar_layout.addWidget(clear, 0, 5)
        bar_layout.addWidget(result, 1, 0)
        bar_layout.addWidget(time_control, 1, 1)
        bar_layout.addWidget(color, 1, 2)
        bar_layout.addWidget(date_from, 1, 3)
        bar_layout.addWidget(date_to, 1, 4)
        layout.addWidget(toolbar)
        empty_label = QLabel()
        empty_label.setObjectName("emptyState")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.hide()
        layout.addWidget(empty_label)
        tree = QTreeWidget()
        tree.setColumnCount(9)
        tree.setHeaderLabels([
            "Result", "Opponent", "Opp. rating", "Δ", "Color",
            "Opening", "Time", "Date", "Fav",
        ])
        for column, tip in enumerate((
            "Win, loss or draw", "Opponent username", "Opponent's rating",
            "Your rating minus opponent rating", "Color you played",
            "Opening from the PGN", "Time class and control", "Game date",
            "Click to save or unsave this game",
        )):
            tree.headerItem().setToolTip(column, tip)
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        tree.setMinimumHeight(320)
        tree.setToolTip("Double-click or press Enter to replay and analyze · click ☆ to save")
        layout.addWidget(tree, 1)
        pager = QHBoxLayout()
        previous = QPushButton("← Previous")
        page_spin = QSpinBox()
        page_spin.setRange(1, 1)
        page_spin.setToolTip("Jump to a page")
        page_label = QLabel()
        page_label.setObjectName("muted")
        next_button = QPushButton("Next →")
        count_label = QLabel()
        count_label.setObjectName("muted")
        pager.addWidget(previous)
        pager.addWidget(page_label)
        pager.addWidget(page_spin)
        pager.addWidget(next_button)
        pager.addStretch()
        pager.addWidget(count_label)
        layout.addLayout(pager)
        self.game_filters[favorites_only]["widgets"] = {
            "search": search,
            "sort": sort,
            "pageSize": page_size,
            "result": result,
            "timeClass": time_control,
            "color": color,
            "dateFrom": date_from,
            "dateTo": date_to,
            "emptyLabel": empty_label,
            "tree": tree,
            "previous": previous,
            "next": next_button,
            "pageSpin": page_spin,
            "pageLabel": page_label,
            "countLabel": count_label,
        }
        search.textChanged.connect(lambda: self._on_filter_changed(favorites_only))
        for combo in (sort, page_size, result, time_control, color):
            combo.currentIndexChanged.connect(lambda: self._on_filter_changed(favorites_only))
        date_from.dateChanged.connect(lambda: self._on_filter_changed(favorites_only))
        date_to.dateChanged.connect(lambda: self._on_filter_changed(favorites_only))
        clear.clicked.connect(lambda: self._clear_filters(favorites_only))
        previous.clicked.connect(lambda: self._change_page(favorites_only, -1))
        next_button.clicked.connect(lambda: self._change_page(favorites_only, 1))
        page_spin.valueChanged.connect(lambda value: self._set_page(favorites_only, value))
        tree.itemActivated.connect(lambda item, column: self.open_game(item.data(0, Qt.ItemDataRole.UserRole)))
        tree.itemClicked.connect(lambda item, column: self._tree_item_clicked(favorites_only, item, column))
        tree.customContextMenuRequested.connect(lambda pos: self._tree_context_menu(favorites_only, tree, pos))
        return page

    def _build_openings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.openings_inner_layout = QVBoxLayout(inner)
        self.openings_inner_layout.setContentsMargins(2, 2, 12, 2)
        self.openings_inner_layout.setSpacing(14)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    def _build_compare(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QFrame()
        toolbar.setObjectName("panel")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(14, 12, 14, 12)
        self.compare_input = QLineEdit()
        self.compare_input.setPlaceholderText("Other Chess.com username")
        self.compare_input.returnPressed.connect(self.compare_players)
        button = QPushButton("Compare")
        button.setProperty("primary", True)
        button.clicked.connect(self.compare_players)
        bar.addWidget(self.compare_input, 1)
        bar.addWidget(button)
        layout.addWidget(toolbar)
        self.compare_scroll = QScrollArea()
        self.compare_scroll.setWidgetResizable(True)
        self.compare_result = QWidget()
        self.compare_result_layout = QVBoxLayout(self.compare_result)
        self.compare_result_layout.setContentsMargins(2, 2, 12, 2)
        self.compare_scroll.setWidget(self.compare_result)
        layout.addWidget(self.compare_scroll, 1)
        return page

    def _build_training(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.training_status = QLabel("Analyse games to unlock personalised coaching.")
        self.training_status.setObjectName("muted")
        self.training_status.setWordWrap(True)
        build = QPushButton("Build coaching")
        build.setProperty("primary", True)
        build.clicked.connect(self._build_training_data)
        reset = QPushButton("Reset session")
        reset.clicked.connect(self._reset_training_session)
        toolbar.addWidget(self.training_status, 1)
        toolbar.addWidget(reset)
        toolbar.addWidget(build)
        layout.addLayout(toolbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(2, 2, 12, 2)
        inner_layout.setSpacing(14)
        self.lessons_panel, self.lessons_layout = make_panel("Personal lessons")
        inner_layout.addWidget(self.lessons_panel)
        exercise_panel, exercise_layout = make_panel("Practice")
        self.training_board = ChessBoard()
        self.training_board.setMinimumSize(280, 280)
        self.training_board.setMaximumSize(380, 380)
        self.training_prompt = QLabel("Run Stockfish analysis on a game, then build your coaching plan.")
        self.training_prompt.setObjectName("viewerTitle")
        self.training_prompt.setWordWrap(True)
        self.training_options_layout = QVBoxLayout()
        self.training_feedback = QLabel()
        self.training_feedback.setObjectName("muted")
        self.training_feedback.setWordWrap(True)
        self.training_score = QLabel()
        self.training_score.setObjectName("muted")
        self.next_exercise_button = QPushButton("Next exercise")
        self.next_exercise_button.clicked.connect(self._next_training_exercise)
        board_row = QHBoxLayout()
        board_row.addWidget(self.training_board, 0, Qt.AlignmentFlag.AlignCenter)
        details = QVBoxLayout()
        details.addWidget(self.training_prompt)
        details.addLayout(self.training_options_layout)
        details.addWidget(self.training_feedback)
        details.addWidget(self.training_score)
        details.addWidget(self.next_exercise_button)
        details.addStretch()
        board_row.addLayout(details, 1)
        exercise_layout.addLayout(board_row)
        inner_layout.addWidget(exercise_panel)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    def _build_progress(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.progress_inner_layout = QVBoxLayout(inner)
        self.progress_inner_layout.setContentsMargins(2, 2, 12, 2)
        self.progress_inner_layout.setSpacing(14)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    def _save_game_analysis(self, game_id, result):
        if not self.username or not game_id:
            return
        game = next((item for item in self.games if item.get("id") == game_id), {})
        self.store.set_training(
            self.username,
            f"analysis:{game_id}",
            {
                "game_id": game_id,
                "date": game.get("date"),
                "color": game.get("color"),
                "opponent": game.get("opponent"),
                "timeClass": game.get("timeClass"),
                "playerRating": game.get("playerRating"),
                "analysis": result,
            },
        )
        self.status.showMessage(f"Analysis saved for coaching · {game_id}")

    def _collect_analyses(self):
        if not self.username:
            return []
        values = self.store.all_training(self.username)
        return [
            value
            for key, value in values.items()
            if key.startswith("analysis:") and isinstance(value, dict) and value.get("analysis")
        ]

    def _build_training_data(self):
        analyses = self._collect_analyses()
        if not analyses:
            QMessageBox.information(
                self,
                "Insight8x8",
                "Analyse at least one game first. Coaching is built from your saved Stockfish analyses.",
            )
            return
        self.training_status.setText("Building personalised lessons…")
        llm = self.coach if getattr(self.coach, "available", False) else None
        worker = self._track(FunctionWorker(build_coaching_data, analyses, self.store, self.username, llm))
        worker.succeeded.connect(self._training_data_ready)
        worker.failed.connect(
            lambda message: self.training_status.setText(f"Coaching failed: {message}")
        )
        worker.start()

    def _training_data_ready(self, data):
        self.training.update(data)
        self.training["current"] = None
        self.training["index"] = 0
        self.training["correct"] = 0
        self.training["answered"] = 0
        self.training_status.setText(
            f"Built {len(self.training['lessons'])} lessons and {len(self.training['exercises'])} exercises "
            f"from {len(self._collect_analyses())} analysed games."
        )
        self._refresh_training()
        self._refresh_progress()

    def _refresh_training(self):
        if not hasattr(self, "lessons_layout"):
            return
        clear_layout(self.lessons_layout)
        lessons = self.training.get("lessons") or []
        if not lessons:
            label = QLabel("No lessons yet. Analyse a game, then choose Build coaching.")
            label.setObjectName("muted")
            label.setWordWrap(True)
            self.lessons_layout.addWidget(label)
        for lesson in lessons[:6]:
            panel, panel_layout = make_panel()
            heading = QLabel(f"{lesson.get('title', 'Lesson')} · {lesson.get('severity', 'medium')}")
            heading.setObjectName("viewerTitle")
            summary = QLabel(lesson.get("summary", ""))
            summary.setObjectName("muted")
            summary.setWordWrap(True)
            panel_layout.addWidget(heading)
            panel_layout.addWidget(summary)
            for step in lesson.get("steps") or []:
                item = QLabel(f"• {step}")
                item.setWordWrap(True)
                panel_layout.addWidget(item)
            self.lessons_layout.addWidget(panel)
        exercises = self.training.get("exercises") or []
        if not exercises:
            self.training_prompt.setText("No exercises yet. Analyse games and build your coaching plan.")
            self.training_feedback.setText("")
            self.training_score.setText("")
            clear_layout(self.training_options_layout)
            self.training_board.set_position(DEMO_FEN)
            self.next_exercise_button.setEnabled(False)
            return
        if self.training.get("current") is None:
            self.training["index"] = 0
            self._show_training_exercise()
        else:
            self._show_training_exercise()

    def _show_training_exercise(self):
        exercises = self.training.get("exercises") or []
        index = int(self.training.get("index") or 0)
        if index >= len(exercises):
            self._finish_training_session()
            return
        exercise = exercises[index]
        self.training["current"] = exercise
        fen = exercise.get("fen", DEMO_FEN)
        side = "white" if str(fen).split(" ")[1:2] == ["w"] else "black"
        self.training_board.set_position(fen, orientation=side)
        self.training_prompt.setText(
            f"Exercise {index + 1}/{len(exercises)} · {exercise.get('prompt', '')}"
        )
        self.training_feedback.setText("")
        self.training_score.setText(
            f"Answered {self.training.get('answered', 0)} · Correct {self.training.get('correct', 0)}"
        )
        self.next_exercise_button.setEnabled(False)
        clear_layout(self.training_options_layout)
        for option in exercise.get("options") or []:
            button = QPushButton(str(option))
            button.clicked.connect(
                lambda checked=False, value=option: self._answer_training(value)
            )
            self.training_options_layout.addWidget(button)

    def _answer_training(self, option):
        exercise = self.training.get("current")
        if not exercise or self.next_exercise_button.isEnabled():
            return
        correct = str(option) == str(exercise.get("answer"))
        if correct:
            self.training["correct"] += 1
        self.training["answered"] += 1
        focus = self.settings.value("coachingFocus", "Balanced", type=str)
        record_progress(
            self.store,
            self.username,
            {"focus": focus, "correct": int(correct), "total": 1},
        )
        feedback = "Correct." if correct else f"Not quite — {exercise.get('answer')} is stronger."
        explanation = exercise.get("explanation") or exercise.get("whyBetter") or ""
        if explanation:
            feedback += f" {explanation}"
        self.training_feedback.setText(feedback)
        self.training_score.setText(
            f"Answered {self.training['answered']} · Correct {self.training['correct']}"
        )
        self.next_exercise_button.setEnabled(True)
        for index in range(self.training_options_layout.count()):
            item = self.training_options_layout.itemAt(index)
            if item and item.widget():
                item.widget().setEnabled(False)

    def _next_training_exercise(self):
        self.training["index"] = int(self.training.get("index") or 0) + 1
        self._show_training_exercise()

    def _finish_training_session(self):
        answered = int(self.training.get("answered") or 0)
        correct = int(self.training.get("correct") or 0)
        self.training["current"] = None
        self.training["index"] = 0
        self.next_exercise_button.setEnabled(False)
        QMessageBox.information(
            self,
            "Insight8x8",
            f"Session complete: {correct}/{answered} correct. Your results are on the Progress tab.",
        )
        self._refresh_progress()

    def _reset_training_session(self):
        self.training["current"] = None
        self.training["index"] = 0
        self.training["correct"] = 0
        self.training["answered"] = 0
        self._refresh_training()

    def _refresh_progress(self):
        if not hasattr(self, "progress_inner_layout"):
            return
        clear_layout(self.progress_inner_layout)
        summary = improvement_summary(progress_history(self.store, self.username))
        cards, card_layout = make_panel()
        grid = QGridLayout()
        stats = [
            ("Sessions", fmt_number(summary["sessions"]), "Completed training runs"),
            ("Accuracy", f"{summary['accuracy']}%", "Average across sessions"),
            ("Trend", f"{summary['trend']:+.1f}%", "Recent five sessions"),
            ("Best streak", fmt_number(summary["bestStreak"]), "Sessions at 80%+"),
        ]
        for index, (title, value, hint) in enumerate(stats):
            grid.addWidget(StatCard(title, value, hint), index // 2, index % 2)
        card_layout.addLayout(grid)
        self.progress_inner_layout.addWidget(cards)
        if summary["labels"]:
            chart_panel, chart_layout = make_panel("Accuracy over time")
            chart = LineChart()
            chart.set_data(
                summary["labels"],
                [{"name": "Accuracy", "color": "#5fd39a", "values": summary["accuracySeries"]}],
            )
            chart.setMinimumHeight(220)
            chart_layout.addWidget(chart)
            self.progress_inner_layout.addWidget(chart_panel)
        if summary["focus"]:
            focus_panel, focus_layout = make_panel("Training focus")
            chart = BarChart()
            chart.set_data(
                [item["label"] for item in summary["focus"]],
                [item["count"] for item in summary["focus"]],
            )
            chart.setMinimumHeight(200)
            focus_layout.addWidget(chart)
            self.progress_inner_layout.addWidget(focus_panel)
        history_panel, history_layout = make_panel("Recent sessions")
        records = progress_history(self.store, self.username)[-8:]
        if not records:
            label = QLabel("No training sessions yet. Answer exercises on the Training tab.")
            label.setObjectName("muted")
            history_layout.addWidget(label)
        for item in reversed(records):
            total = int(item.get("total") or 0)
            correct = int(item.get("correct") or 0)
            accuracy = round(100 * correct / total) if total else 0
            history_layout.addWidget(
                QLabel(
                    f"{item.get('date', '')} · {item.get('focus', 'mixed')} · "
                    f"{correct}/{total} · {accuracy}%"
                )
            )
        self.progress_inner_layout.addWidget(history_panel)
        report_panel, report_layout = make_panel("Training report")
        note = QLabel("Download a CSV summary of sessions, lessons and recommended exercises.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        report_button = QPushButton("Download training report")
        report_button.clicked.connect(lambda: self.export_file("training_report"))
        report_layout.addWidget(note)
        report_layout.addWidget(report_button)
        self.progress_inner_layout.addWidget(report_panel)

    def _build_export(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(2, 2, 12, 2)
        grid.setSpacing(14)
        self.export_summary = QLabel()
        self.export_summary.setObjectName("muted")
        grid.addWidget(self.export_summary, 0, 0, 1, 2)
        cards = [
            ("Complete PGN collection", "Export the current scope as one PGN file, ready for any chess database.", "Download PGN", lambda: self.export_file("pgn")),
            ("Match history CSV", "Opponents, ratings, results, openings and links in spreadsheet form.", "Download CSV", lambda: self.export_file("csv")),
            ("Favorites", "Saved games stay available in the local application database.", "View favorites", lambda: self.show_tab("favorites")),
            ("Engine analysis", "Open any game and run Stockfish to see why each move matters.", "Open a game", self._open_first_game),
            ("Training report", "Download lessons, exercises and progress history as a CSV report.", "Download report", lambda: self.export_file("training_report")),
        ]
        for index, (title, body, button_text, handler) in enumerate(cards):
            row, col = divmod(index + 2, 2)
            panel, panel_layout = make_panel()
            heading = QLabel(title)
            heading.setObjectName("viewerTitle")
            description = QLabel(body)
            description.setObjectName("muted")
            description.setWordWrap(True)
            button = QPushButton(button_text)
            button.clicked.connect(handler)
            panel_layout.addWidget(heading)
            panel_layout.addWidget(description)
            panel_layout.addStretch()
            panel_layout.addWidget(button)
            grid.addWidget(panel, row, col)
        scope_panel, scope_layout = make_panel("Export scope")
        scope_row = QHBoxLayout()
        self.export_scope = QComboBox()
        self.export_scope.addItems(["All games", "Favorite games"])
        self.export_scope.currentIndexChanged.connect(self._refresh_export)
        scope_note = QLabel("PGN and CSV downloads use the Match history filters, sorting and scope shown here.")
        scope_note.setObjectName("muted")
        scope_note.setWordWrap(True)
        scope_row.addWidget(self.export_scope)
        scope_row.addWidget(scope_note, 1)
        scope_layout.addLayout(scope_row)
        grid.addWidget(scope_panel, 5, 0, 1, 2)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    def _track(self, worker):
        self.workers.append(worker)
        worker.finished.connect(lambda: self._forget(worker))
        return worker

    def _forget(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        if self._closing and not self.workers and not self._store_closed:
            self._store_closed = True
            self.store.close()

    def connect_account(self, username, silent=False):
        name = str(username or "").strip()
        try:
            name = validate_username(name)
        except Exception as error:
            self.landing_error.setText(str(error))
            self.landing_error.show()
            return
        self.pending_username = name
        self.landing_error.hide()
        self.connect_button.setEnabled(False)
        self.connect_button.setText("Connecting…")
        self.status.showMessage(f"Connecting to Chess.com as {name}…")
        worker = self._track(FunctionWorker(self.client.connect, name))
        worker.succeeded.connect(lambda data, value=name: self._connected(data, value))
        worker.failed.connect(self._connect_failed)
        worker.start()

    def _connected(self, data, requested):
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Analyze my games →")
        if requested != self.pending_username:
            return
        self.username = data["username"]
        self.connection = data
        self.settings.setValue("username", self.username)
        self._fill_preview()
        self.landing_error.hide()
        self.status.showMessage(f"Connected as {self.username}. Loading archive…")
        self.start_archive_load(refresh=False, auto_show=True)

    def _connect_failed(self, message):
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Analyze my games →")
        self.landing_error.setText(message)
        self.landing_error.show()
        self.status.showMessage(message)

    def _fill_preview(self):
        profile = self.connection["profile"]
        stats = self.connection["stats"]
        self.username_input.setText(self.username)
        self.preview_avatar.set_url(profile.get("avatar"))
        if not profile.get("avatar"):
            self.preview_avatar.set_text_avatar(profile.get("username"))
        self.preview_name.setText(profile.get("username") or self.username)
        self.preview_meta.setText(
            f"{profile.get('country', '')} · {profile.get('status', '')} · Last online {profile.get('lastOnlineLabel', '')}"
        )
        self.preview_stats.setText(
            f"Rapid {fmt_number(stats['ratings'].get('rapid'))}    "
            f"Blitz {fmt_number(stats['ratings'].get('blitz'))}    "
            f"Bullet {fmt_number(stats['ratings'].get('bullet'))}    "
            f"Daily {fmt_number(stats['ratings'].get('daily'))}    "
            f"Archives {fmt_number(self.connection.get('archiveCount'))}"
        )
        self.preview_frame.show()

    def start_archive_load(self, refresh=False, auto_show=False):
        if not self.username or self._closing:
            return
        if self.archive_worker and self.archive_worker.username == self.username and self.archive_worker.isRunning():
            return
        self.load_progress.setVisible(True)
        self.load_progress.setValue(0)
        self.archive_hint.hide()
        worker = ArchiveWorker(self.client, self.username, refresh)
        self.archive_worker = worker
        self._track(worker)
        worker.progressed.connect(lambda loaded, total, w=worker: self._load_progress(loaded, total, w))
        worker.succeeded.connect(lambda result, w=worker: self._archive_done(result, w, auto_show))
        worker.failed.connect(lambda message, w=worker: self._archive_failed(message, w))
        worker.start()

    def _load_progress(self, loaded, total, worker):
        if worker is not self.archive_worker:
            return
        percent = int((loaded / total) * 100) if total else 0
        self.load_progress.setValue(percent)
        self.load_progress.setFormat(f"Loading archives  {loaded}/{total}")
        self.status.showMessage(f"Loading public archives {loaded}/{total}…")

    def _archive_done(self, result, worker, auto_show):
        if self.archive_worker is worker:
            self.archive_worker = None
        if worker.username != self.username:
            return
        self.games = self.client.cached_games(self.username)
        self.favorites = set(self.store.favorites(self.username))
        self.load_progress.setVisible(False)
        self._refresh_all_pages()
        if auto_show:
            self.show_dashboard()
        errors = result.get("errors") or []
        message = f"{fmt_number(len(self.games))} games ready."
        if errors:
            message += f" {len(errors)} archive(s) could not be loaded."
            self.archive_hint.setText(
                f"{len(errors)} archive(s) unavailable. Showing cached games; press F5 to retry."
            )
            self.archive_hint.show()
        else:
            self.archive_hint.hide()
        self.status.showMessage(message)

    def _archive_failed(self, message, worker):
        if self.archive_worker is worker:
            self.archive_worker = None
        if worker.username != self.username:
            return
        self.load_progress.setVisible(False)
        self.games = self.client.cached_games(self.username)
        self.favorites = set(self.store.favorites(self.username))
        self._refresh_all_pages()
        if self.games:
            self.archive_hint.setText(
                f"Offline mode · showing {fmt_number(len(self.games))} cached games. Press F5 to retry."
            )
            self.archive_hint.show()
            self.status.showMessage(f"{message} Showing {fmt_number(len(self.games))} cached games.")
        else:
            self.archive_hint.setText("Could not load games. Check your connection and press F5 to retry.")
            self.archive_hint.show()
            self.status.showMessage(message)

    def _refresh_all_pages(self):
        self._refresh_sidebar()
        self._refresh_overview()
        self._refresh_games(False)
        self._refresh_games(True)
        self._refresh_openings()
        self._refresh_training()
        self._refresh_progress()
        self._refresh_export()

    def _refresh_sidebar(self):
        if not self.connection:
            return
        profile = self.connection["profile"]
        self.side_avatar.set_url(profile.get("avatar"))
        if not profile.get("avatar"):
            self.side_avatar.set_text_avatar(profile.get("username"))
        self.side_name.setText(profile.get("username") or self.username)
        self.side_country.setText(profile.get("country") or "")

    def show_dashboard(self):
        if not self.connection:
            return
        self.stack.setCurrentWidget(self.dashboard_page)
        self.show_tab("overview")

    def show_tab(self, key):
        pages = {
            "overview": self.overview_page,
            "games": self.games_page,
            "openings": self.openings_page,
            "compare": self.compare_page,
            "favorites": self.favorites_page,
            "training": self.training_page,
            "progress": self.progress_page,
            "export": self.export_page,
        }
        page = pages.get(key)
        if not page:
            return
        self.tabs.setCurrentWidget(page)
        titles = {
            "overview": "Overview",
            "games": "Match history",
            "openings": "Opening repertoire",
            "compare": "Player comparison",
            "favorites": "Favorite games",
            "training": "Training",
            "progress": "Progress",
            "export": "Export center",
        }
        self.page_title.setText(titles[key])
        if self.connection:
            profile = self.connection["profile"]
            self.page_subtitle.setText(
                f"{fmt_number(len(self.games))} games from {fmt_number(self.connection.get('archiveCount'))} monthly archives · {profile.get('lastOnlineLabel', '')}"
            )
        for name, button in self.nav_buttons.items():
            button.setProperty("active", name == key)
            button.style().unpolish(button)
            button.style().polish(button)
        if key == "overview":
            self._refresh_overview()
        elif key == "games":
            self._refresh_games(False)
        elif key == "favorites":
            self._refresh_games(True)
        elif key == "openings":
            self._refresh_openings()
        elif key == "training":
            self._refresh_training()
        elif key == "progress":
            self._refresh_progress()
        elif key == "export":
            self._refresh_export()

    def change_account(self):
        self.pending_username = ""
        self.username = ""
        self.connection = None
        self.games = []
        self.favorites = set()
        self.archive_worker = None
        self.load_progress.setVisible(False)
        self.archive_hint.hide()
        self.settings.remove("username")
        self.training.update(
            {
                "lessons": [],
                "exercises": [],
                "weaknesses": [],
                "current": None,
                "index": 0,
                "correct": 0,
                "answered": 0,
            }
        )
        self._refresh_training()
        self._refresh_progress()
        clear_layout(self.compare_result_layout)
        self.compare_input.clear()
        self.page_subtitle.setText("")
        for favorites_only in (False, True):
            self.game_filters[favorites_only].update(self._default_filters())
            self._clear_filters(favorites_only)
        self.stack.setCurrentWidget(self.landing_page)
        self.preview_frame.hide()
        self.status.showMessage("Disconnected. Enter another username.")

    def _refresh_overview(self):
        clear_layout(self.overview_inner_layout)
        if not self.connection:
            return
        stats = self.connection["stats"]
        heading = QLabel(f"Welcome back, {self.connection['profile'].get('username', self.username)}")
        heading.setObjectName("pageTitle")
        self.overview_inner_layout.addWidget(heading)
        coach = QFrame()
        coach.setObjectName("callout")
        coach_layout = QHBoxLayout(coach)
        coach_layout.setContentsMargins(16, 12, 16, 12)
        coach_text = QLabel(
            "LEARN FROM EVERY MOVE\n"
            "Open a game, run Stockfish, then step through the moves. The explanation and "
            "threat arrows appear automatically — no raw engine table to read."
        )
        coach_text.setWordWrap(True)
        analyze_button = QPushButton("Analyze a game →")
        analyze_button.setProperty("primary", True)
        analyze_button.clicked.connect(self._open_first_game)
        coach_layout.addWidget(coach_text, 1)
        coach_layout.addWidget(analyze_button)
        self.overview_inner_layout.addWidget(coach)
        grid = QGridLayout()
        grid.setSpacing(10)
        cards = [
            ("Rapid", fmt_number(stats["ratings"].get("rapid"))),
            ("Blitz", fmt_number(stats["ratings"].get("blitz"))),
            ("Bullet", fmt_number(stats["ratings"].get("bullet"))),
            ("Daily", fmt_number(stats["ratings"].get("daily"))),
            ("Highest", fmt_number((stats.get("highest") or {}).get("rating"))),
            ("Games", fmt_number(stats["totals"].get("games"))),
            ("Wins", fmt_number(stats["totals"].get("wins"))),
            ("Losses", fmt_number(stats["totals"].get("losses"))),
            ("Draws", fmt_number(stats["totals"].get("draws"))),
            ("Win rate", f"{stats['totals'].get('winPercentage')}%" if stats["totals"].get("winPercentage") is not None else "—"),
        ]
        for index, (label, value) in enumerate(cards):
            row, col = divmod(index, 5)
            grid.addWidget(StatCard(label, value), row, col)
        self.overview_inner_layout.addLayout(grid)
        insights = compute_insights(self.games)
        chart_row = QHBoxLayout()
        line_panel, line_layout = make_panel("Rating progression")
        chart = LineChart()
        labels = sorted(insights["ratingHistory"].keys()) or []
        months = sorted({month for series in insights["ratingHistory"].values() for month in series})[-24:]
        line_data = []
        for key, values in insights["ratingHistory"].items():
            line_data.append({
                "name": key.title(),
                "color": {"bullet": "#f07777", "blitz": "#73a7f4", "rapid": "#5fd39a", "daily": "#d8b46a"}.get(key, "#9bad9f"),
                "values": [values.get(month) for month in months],
            })
        chart.set_data([month[5:] for month in months], line_data)
        chart.setToolTip("Your latest rating for each month, split by time control.")
        line_layout.addWidget(chart)
        donut_panel, donut_layout = make_panel("Results")
        donut = DonutChart()
        donut.set_data(
            ["Wins", "Losses", "Draws"],
            [insights["totals"]["win"], insights["totals"]["loss"], insights["totals"]["draw"]],
            ["#5fd39a", "#f07777", "#d8b46a"],
        )
        donut_layout.addWidget(donut)
        donut.setToolTip("Wins, losses and draws across the loaded games.")
        chart_row.addWidget(line_panel, 3)
        chart_row.addWidget(donut_panel, 1)
        self.overview_inner_layout.addLayout(chart_row)
        chart_row_two = QHBoxLayout()
        bar_panel, bar_layout = make_panel("Games per month")
        bar = BarChart()
        months = sorted(insights["byMonth"])[-12:]
        bar.set_data([month[5:] for month in months], [insights["byMonth"][month] for month in months])
        bar.setToolTip("How many loaded games you played each month.")
        bar_layout.addWidget(bar)
        time_panel, time_layout = make_panel("Time control mix")
        for key, record in sorted(insights["byClass"].items(), key=lambda item: item[1]["games"], reverse=True):
            row_layout = QHBoxLayout()
            label = QLabel(key.title())
            label.setMinimumWidth(70)
            progress = QProgressBar()
            progress.setRange(0, max(1, max(item["games"] for item in insights["byClass"].values())))
            progress.setValue(record["games"])
            progress.setFormat(f"{record['games']}")
            wins = record.get("win", 0)
            rate = round(wins / record["games"] * 100) if record["games"] else 0
            win_label = QLabel(f"{rate}% wins")
            win_label.setObjectName("muted")
            row_layout.addWidget(label)
            row_layout.addWidget(progress, 1)
            row_layout.addWidget(win_label)
            time_layout.addLayout(row_layout)
        if not insights["byClass"]:
            note = QLabel("No time-control data yet.")
            note.setObjectName("muted")
            time_layout.addWidget(note)
        chart_row_two.addWidget(bar_panel, 3)
        chart_row_two.addWidget(time_panel, 2)
        self.overview_inner_layout.addLayout(chart_row_two)
        discovery = QHBoxLayout()
        openings_panel, openings_layout = make_panel("Most played openings")
        for record in sorted(insights["byOpening"].values(), key=lambda item: item["games"], reverse=True)[:7]:
            decided = record["win"] + record["loss"] + record["draw"]
            rate = round(record["win"] / decided * 100) if decided else 0
            name = f"{record['eco'] + ' · ' if record['eco'] else ''}{record['name']}"
            link = QPushButton(name)
            link.setObjectName("linkButton")
            link.setToolTip("Filter match history to this opening")
            link.clicked.connect(lambda checked=False, value=record["name"]: self._filter_by_search(value))
            detail = QLabel(f"{rate}% wins · {record['games']} games")
            detail.setObjectName("muted")
            openings_layout.addWidget(link)
            openings_layout.addWidget(detail)
        discovery.addWidget(openings_panel)
        opponents_panel, opponents_layout = make_panel("Frequent opponents")
        for name, count in sorted(insights["byOpponent"].items(), key=lambda item: item[1], reverse=True)[:7]:
            link = QPushButton(str(name))
            link.setObjectName("linkButton")
            link.setToolTip("Filter match history to this opponent")
            link.clicked.connect(lambda checked=False, value=str(name): self._filter_by_search(value))
            detail = QLabel(f"{count} games")
            detail.setObjectName("muted")
            opponents_layout.addWidget(link)
            opponents_layout.addWidget(detail)
        discovery.addWidget(opponents_panel)
        self.overview_inner_layout.addLayout(discovery)
        self.overview_inner_layout.addStretch()

    def _filter_by_search(self, text):
        widgets = self.game_filters[False]["widgets"]
        widgets["search"].setText(str(text))
        self._on_filter_changed(False)
        self.show_tab("games")

    def _filtered_games(self, favorites_only):
        filters = self.game_filters[favorites_only]
        output = []
        for game in self.games:
            if favorites_only and game["id"] not in self.favorites:
                continue
            if game_matches_filters(game, filters):
                output.append(game)
        sort = filters.get("sort", "recent")
        if sort == "oldest":
            output.sort(key=lambda game: (str(game.get("date") or ""), game.get("timestamp") or 0))
        elif sort == "opponent":
            output.sort(key=lambda game: str(game.get("opponent") or "").lower())
        elif sort == "rating_desc":
            output.sort(key=lambda game: game.get("playerRating") or -1, reverse=True)
        elif sort == "rating_asc":
            output.sort(key=lambda game: game.get("playerRating") if game.get("playerRating") is not None else 10**9)
        elif sort == "wins":
            output.sort(key=lambda game: (game.get("result") == "win", game.get("timestamp") or 0), reverse=True)
        elif sort == "losses":
            output.sort(key=lambda game: (game.get("result") == "loss", game.get("timestamp") or 0), reverse=True)
        else:
            output.sort(
                key=lambda game: (str(game.get("date") or ""), game.get("timestamp") or 0),
                reverse=True,
            )
        return output

    def _refresh_games(self, favorites_only):
        filters = self.game_filters[favorites_only]
        widgets = filters.get("widgets")
        if not widgets:
            return
        games = self._filtered_games(favorites_only)
        page_size = int(filters.get("pageSize", 50) or 0)
        pages = 1 if not page_size or not games else max(1, (len(games) + page_size - 1) // page_size)
        filters["page"] = max(1, min(int(filters.get("page", 1)), pages))
        if page_size:
            start = (filters["page"] - 1) * page_size
            visible = games[start:start + page_size]
        else:
            visible = games
        tree = widgets["tree"]
        tree.setUpdatesEnabled(False)
        tree.clear()
        for game in visible:
            delta = None
            if game.get("playerRating") is not None and game.get("opponentRating") is not None:
                delta = game["playerRating"] - game["opponentRating"]
            delta_text = f"{delta:+d}" if delta is not None else "—"
            result = result_letter(game.get("result"))
            opponent = str(game.get("opponent") or "")
            opening = game.get("opening") or "Unknown"
            item = QTreeWidgetItem([
                result,
                opponent,
                str(game.get("opponentRating") or "—"),
                delta_text,
                (game.get("color") or "").title(),
                opening,
                f"{game.get('timeClass', '')} · {game.get('timeControl', '')}",
                fmt_date(game.get("date")),
                "★" if game["id"] in self.favorites else "☆",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, game["id"])
            item.setToolTip(
                0,
                f"{result} vs {opponent} · {opening}\n"
                f"Double-click or press Enter to replay and analyze.\n"
                f"Click the star to save or unsave this game."
            )
            tree.addTopLevelItem(item)
        tree.setUpdatesEnabled(True)
        empty = widgets["emptyLabel"]
        if not games:
            if favorites_only:
                empty.setText("No favorite games yet. Open Match history and click ☆ on a game to save it.")
            elif not self.games:
                empty.setText("No games loaded yet. Connect a Chess.com username to build your archive.")
            else:
                empty.setText("No games match these filters. Try Clear filters or a wider date range.")
            empty.show()
            tree.hide()
        else:
            empty.hide()
            tree.show()
        widgets["previous"].setEnabled(filters["page"] > 1)
        widgets["next"].setEnabled(filters["page"] < pages)
        with QSignalBlocker(widgets["pageSpin"]):
            widgets["pageSpin"].setRange(1, pages)
            widgets["pageSpin"].setValue(filters["page"])
        widgets["pageLabel"].setText(f"Page {filters['page']} of {pages}")
        widgets["countLabel"].setText(
            f"{fmt_number(len(games))} of {fmt_number(len(self.games))} games"
            + (" · Favorites" if favorites_only else "")
        )

    def _on_filter_changed(self, favorites_only):
        widgets = self.game_filters[favorites_only]["widgets"]
        filters = self.game_filters[favorites_only]
        result_values = ["all", "win", "loss", "draw"]
        time_values = ["all", "bullet", "blitz", "rapid", "daily"]
        color_values = ["all", "white", "black"]
        sort_values = ["recent", "oldest", "opponent", "rating_desc", "rating_asc", "wins", "losses"]
        page_values = [25, 50, 100, 0]
        filters["search"] = widgets["search"].text()
        filters["result"] = result_values[widgets["result"].currentIndex()]
        filters["timeClass"] = time_values[widgets["timeClass"].currentIndex()]
        filters["color"] = color_values[widgets["color"].currentIndex()]
        filters["dateFrom"] = widgets["dateFrom"].date().toString("yyyy-MM-dd")
        filters["dateTo"] = widgets["dateTo"].date().toString("yyyy-MM-dd")
        filters["sort"] = sort_values[widgets["sort"].currentIndex()]
        filters["pageSize"] = page_values[widgets["pageSize"].currentIndex()]
        filters["page"] = 1
        self._refresh_games(favorites_only)

    def _clear_filters(self, favorites_only):
        widgets = self.game_filters[favorites_only]["widgets"]
        controls = ("search", "sort", "pageSize", "result", "timeClass", "color", "dateFrom", "dateTo")
        blockers = [QSignalBlocker(widgets[key]) for key in controls]
        widgets["search"].clear()
        widgets["sort"].setCurrentIndex(0)
        widgets["pageSize"].setCurrentIndex(1)
        widgets["result"].setCurrentIndex(0)
        widgets["timeClass"].setCurrentIndex(0)
        widgets["color"].setCurrentIndex(0)
        widgets["dateFrom"].setDate(QDate(2000, 1, 1))
        widgets["dateTo"].setDate(QDate.currentDate().addYears(2))
        del blockers
        self.game_filters[favorites_only].update(self._default_filters())
        self._refresh_games(favorites_only)

    def _change_page(self, favorites_only, direction):
        self.game_filters[favorites_only]["page"] += direction
        self._refresh_games(favorites_only)

    def _set_page(self, favorites_only, page):
        self.game_filters[favorites_only]["page"] = max(1, int(page))
        self._refresh_games(favorites_only)

    def _tree_item_clicked(self, favorites_only, item, column):
        if column == 8:
            self.toggle_favorite(item.data(0, Qt.ItemDataRole.UserRole))

    def _tree_context_menu(self, favorites_only, tree, position):
        item = tree.itemAt(position)
        if item is None:
            return
        game_id = item.data(0, Qt.ItemDataRole.UserRole)
        favorite = game_id in self.favorites
        menu = QMenu(tree)
        open_action = menu.addAction("Open & analyze game")
        open_action.triggered.connect(lambda: self.open_game(game_id))
        favorite_action = menu.addAction("Remove from favorites" if favorite else "Save to favorites")
        favorite_action.triggered.connect(lambda: self.toggle_favorite(game_id))
        menu.addSeparator()
        copy_action = menu.addAction("Copy Chess.com link")
        copy_action.triggered.connect(lambda: self._copy_game_link(game_id))
        menu.exec(tree.mapToGlobal(position))

    def _copy_game_link(self, game_id):
        game = next((item for item in self.games if item.get("id") == game_id), None)
        if not game or not game.get("url"):
            self.status.showMessage("No Chess.com link is available for this game.")
            return
        QApplication.clipboard().setText(str(game["url"]))
        self.status.showMessage("Chess.com link copied to clipboard.")

    def _refresh_openings(self):
        clear_layout(self.openings_inner_layout)
        insights = compute_insights(self.games)
        openings = sorted(insights["byOpening"].values(), key=lambda item: item["games"], reverse=True)
        if not openings:
            label = QLabel("No openings loaded yet.")
            label.setObjectName("muted")
            self.openings_inner_layout.addWidget(label)
            return
        panel, layout = make_panel("Opening win rates")
        for record in openings[:40]:
            decided = record["win"] + record["loss"] + record["draw"]
            rate = round(record["win"] / decided * 100) if decided else 0
            row = QHBoxLayout()
            name = QPushButton(f"{record['eco'] + ' · ' if record['eco'] else ''}{record['name']}")
            name.setObjectName("linkButton")
            name.setMinimumWidth(320)
            name.setToolTip("Show all games with this opening")
            name.clicked.connect(lambda checked=False, value=record["name"]: self._filter_by_search(value))
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(rate)
            progress.setFormat(f"{rate}%")
            count = QLabel(f"{record['games']} games")
            count.setObjectName("muted")
            count.setMinimumWidth(80)
            row.addWidget(name)
            row.addWidget(progress, 1)
            row.addWidget(count)
            layout.addLayout(row)
        self.openings_inner_layout.addWidget(panel)
        color_panel, color_layout = make_panel("White versus Black")
        for color in ("white", "black"):
            record = insights["byColor"][color]
            games = record["win"] + record["loss"] + record["draw"]
            rate = round(record["win"] / games * 100) if games else 0
            label = QLabel(f"{color.title()}: {rate}% wins over {games} games")
            color_layout.addWidget(label)
        self.openings_inner_layout.addWidget(color_panel)
        self.openings_inner_layout.addStretch()

    def compare_players(self):
        other = self.compare_input.text().strip()
        try:
            other = validate_username(other)
        except Exception as error:
            self.status.showMessage(str(error))
            return
        if not self.username or other == self.username:
            self.status.showMessage("Choose a different Chess.com username.")
            return
        self.status.showMessage(f"Comparing {self.username} and {other}…")
        worker = self._track(FunctionWorker(self._compare_data, other))
        worker.succeeded.connect(self._show_compare)
        worker.failed.connect(lambda message: self.status.showMessage(message))
        worker.start()

    def _compare_data(self, other):
        first_profile = self.client.profile(self.username)
        second_profile = self.client.profile(other)
        first_stats = self.client.stats(self.username)
        second_stats = self.client.stats(other)
        head_to_head = [
            game for game in self.client.cached_games(self.username)
            if str(game.get("opponent") or "").lower() == other
        ]
        return {
            "players": [
                {"username": self.username, "profile": first_profile, "stats": first_stats},
                {"username": other, "profile": second_profile, "stats": second_stats},
            ],
            "headToHead": head_to_head,
        }

    def _show_compare(self, data):
        clear_layout(self.compare_result_layout)
        grid = QGridLayout()
        for index, player in enumerate(data["players"]):
            panel, layout = make_panel()
            head = QHBoxLayout()
            avatar = AvatarLabel(size=54)
            avatar.set_url(player["profile"].get("avatar"))
            if not player["profile"].get("avatar"):
                avatar.set_text_avatar(player["username"])
            name = QLabel(player["username"])
            name.setObjectName("viewerTitle")
            country = QLabel(player["profile"].get("country", ""))
            country.setObjectName("muted")
            head.addWidget(avatar)
            text = QVBoxLayout()
            text.addWidget(name)
            text.addWidget(country)
            head.addLayout(text)
            layout.addLayout(head)
            stats = player["stats"]
            values = [
                ("Rapid", fmt_number(stats["ratings"].get("rapid"))),
                ("Blitz", fmt_number(stats["ratings"].get("blitz"))),
                ("Bullet", fmt_number(stats["ratings"].get("bullet"))),
            ("Highest", fmt_number((stats.get("highest") or {}).get("rating"))),
                ("Games", fmt_number(stats["totals"].get("games"))),
                ("Win rate", f"{stats['totals'].get('winPercentage')}%" if stats["totals"].get("winPercentage") is not None else "—"),
            ]
            stat_grid = QGridLayout()
            for position, (label, value) in enumerate(values):
                row, col = divmod(position, 2)
                stat_grid.addWidget(StatCard(label, value), row, col)
            layout.addLayout(stat_grid)
            grid.addWidget(panel, 0, index)
        self.compare_result_layout.addLayout(grid)
        head = section_label(f"Head-to-head · {len(data['headToHead'])} games")
        self.compare_result_layout.addWidget(head)
        if data["headToHead"]:
            tree = QTreeWidget()
            tree.setColumnCount(6)
            tree.setHeaderLabels(["Result", "Color", "Opening", "Time", "Date", "Rating"])
            tree.setRootIsDecorated(False)
            for game in data["headToHead"]:
                item = QTreeWidgetItem([
                    result_text(game.get("result")),
                    (game.get("color") or "").title(),
                    game.get("opening") or "Unknown",
                    game.get("timeClass") or "",
                    fmt_date(game.get("date")),
                    str(game.get("playerRating") or "—"),
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, game["id"])
                tree.addTopLevelItem(item)
            tree.itemDoubleClicked.connect(lambda item, column: self.open_game(item.data(0, Qt.ItemDataRole.UserRole)))
            tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.compare_result_layout.addWidget(tree)
        else:
            label = QLabel("No direct encounters found in this archive.")
            label.setObjectName("muted")
            self.compare_result_layout.addWidget(label)
        self.compare_result_layout.addStretch()

    def open_game(self, game_id):
        if not game_id or not self.username:
            return
        existing = next(
            (viewer for viewer in self.viewers if getattr(viewer, "game", {}).get("id") == game_id),
            None,
        )
        if existing:
            existing.raise_()
            existing.activateWindow()
            return
        worker = self._track(FunctionWorker(self.client.cached_game_detail, self.username, game_id))
        worker.succeeded.connect(lambda detail: self._show_game(detail, game_id))
        worker.failed.connect(lambda message: self.status.showMessage(message))
        worker.start()

    def _open_first_game(self):
        if not self.games:
            QMessageBox.information(
                self,
                "Insight8x8",
                "No games are loaded yet. Connect a Chess.com username, then open a game from Match history.",
            )
            return
        visible = self._filtered_games(False)
        self.open_game(visible[0]["id"] if visible else self.games[0]["id"])

    def _show_game(self, detail, game_id):
        if not detail:
            self.status.showMessage("Game not found in cached archives.")
            return
        viewer = GameViewerDialog(
            detail,
            self.analyzer,
            game_id in self.favorites,
            self,
            coach=self.coach,
            **self._analysis_options(),
        )
        viewer.favorite_toggled.connect(self.toggle_favorite)
        viewer.analysis_saved.connect(self._save_game_analysis)
        viewer.show()
        self.viewers.append(viewer)
        viewer.destroyed.connect(lambda: self.viewers.remove(viewer) if viewer in self.viewers else None)

    def toggle_favorite(self, game_id):
        if game_id in self.favorites:
            self.store.remove_favorite(self.username, game_id)
            self.favorites.discard(game_id)
        else:
            self.store.add_favorite(self.username, game_id)
            self.favorites.add(game_id)
        for viewer in self.viewers:
            if getattr(viewer, "game", {}).get("id") == game_id:
                viewer.set_favorite(game_id in self.favorites)
        self._refresh_games(False)
        self._refresh_games(True)
        self._refresh_export()

    def _refresh_export(self):
        if hasattr(self, "export_summary"):
            favorites_only = hasattr(self, "export_scope") and self.export_scope.currentIndex() == 1
            count = len(self._filtered_games(favorites_only))
            label = "favorite game" if favorites_only else "game"
            self.export_summary.setText(
                f"{fmt_number(count)} {label}{'' if count == 1 else 's'} in the current export scope · "
                f"{fmt_number(len(self.games))} games loaded · {fmt_number(len(self.favorites))} favorites · "
                f"{fmt_number(len(progress_history(self.store, self.username)))} training sessions · "
                f"Local database: {self.store.path}"
            )

    def _export_text(self, kind, favorites_only=False):
        if kind == "training_report":
            analyses = self._collect_analyses()
            data = build_coaching_data(analyses, self.store, self.username)
            return training_report(
                self.store,
                self.username,
                data["lessons"],
                data["exercises"],
                data["history"],
            )
        selected_ids = {game["id"] for game in self._filtered_games(favorites_only)}
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
            "White Accuracy (%)", "Black Accuracy (%)", "Accuracy Source", "Termination", "URL",
        ])
        for game in details:
            row = [
                game.get("date"), game.get("result"), game.get("color"), game.get("opponent"),
                game.get("opponentRating"), game.get("playerRating"), game.get("timeClass"),
                game.get("timeControl"), game.get("opening"), game.get("eco"), game.get("moves"),
                game.get("accuracy"), game.get("whiteAccuracy"), game.get("blackAccuracy"),
                game.get("accuracySource"), game.get("termination"), game.get("url"),
            ]
            writer.writerow([csv_safe(value) for value in row])
        return output.getvalue()

    def export_file(self, kind):
        favorites_only = hasattr(self, "export_scope") and self.export_scope.currentIndex() == 1
        scope = "favorites" if favorites_only else "games"
        if kind == "training_report":
            if not progress_history(self.store, self.username) and not self._collect_analyses():
                QMessageBox.information(
                    self, "Insight8x8", "No training progress or analyses to export yet."
                )
                return
            suffix = "csv"
            caption = "Save training report"
            default_name = f"{self.username or 'insight8x8'}-training-report.csv"
        else:
            if not self._filtered_games(favorites_only):
                QMessageBox.information(self, "Insight8x8", f"There are no {scope} to export with the current filters.")
                return
            suffix = "pgn" if kind == "pgn" else "csv"
            caption = "Save PGN collection" if kind == "pgn" else "Save match history CSV"
            default_name = f"{self.username}-{scope}.{suffix}"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            caption,
            default_name,
            f"{suffix.upper()} files (*.{suffix});;All files (*)",
        )
        if not filename:
            return
        worker = self._track(FunctionWorker(self._export_text, kind, favorites_only))
        worker.succeeded.connect(lambda text, path=filename: self._write_export(path, text))
        worker.failed.connect(lambda message: self.status.showMessage(message))
        worker.start()

    def _write_export(self, path, text):
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.status.showMessage(f"Saved {path}")
        except OSError as error:
            self.status.showMessage(str(error))

    def closeEvent(self, event):
        self._closing = True
        for worker in list(self.workers):
            if worker.isRunning():
                worker.wait(1500)
        if not self.workers and not self._store_closed:
            self._store_closed = True
            self.store.close()
        event.accept()


def main():
    load_env_file()
    app = QApplication(sys.argv)
    app.setApplicationName("Insight8x8")
    app.setOrganizationName("Insight8x8")
    window = Insight8x8App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
