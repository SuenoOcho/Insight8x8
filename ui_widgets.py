import math

from PyQt6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QUrl
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

FILES = "abcdefgh"
PIECE_GLYPHS = {"p": "♟", "r": "♜", "n": "♞", "b": "♝", "q": "♛", "k": "♚"}


def rounded_pixmap(pixmap, size):
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    scaled = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return result


class AvatarLabel(QLabel):
    def __init__(self, parent=None, size=64):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("avatar")
        self.set_text_avatar("?")
        self._pending_url = QUrl()
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self._on_loaded)

    def set_text_avatar(self, value):
        text = str(value or "?").strip()
        self.setPixmap(QPixmap())
        self.setText(text[:1].upper() if text else "?")

    def set_url(self, url):
        self.set_text_avatar("?")
        self._pending_url = QUrl(str(url)) if url else QUrl("about:blank")
        if not url:
            return
        request = QNetworkRequest(self._pending_url)
        request.setRawHeader(b"User-Agent", b"Insight8x8Desktop/1.0")
        self._manager.get(request)

    def _on_loaded(self, reply):
        if self._pending_url.isValid() and reply.url().toString() != self._pending_url.toString():
            reply.deleteLater()
            return
        image = QImage.fromData(reply.readAll().data())
        reply.deleteLater()
        if image.isNull():
            return
        self.setPixmap(rounded_pixmap(QPixmap.fromImage(image), self._size))


class StatCard(QFrame):
    def __init__(self, title, value, hint="", color=None, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("statTitle")
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("statValue")
        if color:
            self.value_label.setStyleSheet(f"color: {color};")
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("statHint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        if hint:
            layout.addWidget(self.hint_label)

    def set_value(self, value, hint=None):
        self.value_label.setText(str(value))
        if hint is not None:
            self.hint_label.setText(hint)


class DonutChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.labels = []
        self.colors = []
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels, values, colors):
        self.labels = labels
        self.values = values
        self.colors = colors
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 40
        side = max(60, side)
        rect = QRect(
            (self.width() - side) // 2,
            (self.height() - side) // 2,
            side,
            side,
        )
        pen = QPen(QColor("#1a251f"), 18)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        total = sum(self.values)
        if total:
            start = -90 * 16
            for label, value, color in zip(self.labels, self.values, self.colors):
                if not value:
                    continue
                span = int((value / total) * 360 * 16)
                pen = QPen(QColor(color), 18)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(pen)
                painter.drawArc(rect, start, span)
                start += span
        painter.setPen(QColor("#edf5ef"))
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{total:,}")
        painter.setPen(QColor("#9bad9f"))
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        legend_y = rect.bottom() + 16
        x = 14
        for label, value, color in zip(self.labels, self.values, self.colors):
            painter.fillRect(QRect(x, legend_y, 9, 9), QColor(color))
            painter.setPen(QColor("#9bad9f"))
            painter.drawText(QRect(x + 14, legend_y - 3, 90, 16), Qt.AlignmentFlag.AlignLeft, f"{label} {value}")
            x += min(115, self.width() // max(1, len(self.labels)))


class LineChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels = []
        self.series = []
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels, series):
        self.labels = labels
        self.series = series
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = 52, 18, 22, 46
        chart = QRect(left, top, self.width() - left - right, self.height() - top - bottom)
        if chart.width() <= 10 or chart.height() <= 10:
            return
        painter.setPen(QColor("#223329"))
        painter.drawRect(chart)
        all_values = [value for series in self.series for value in series["values"] if value is not None]
        if not all_values:
            painter.setPen(QColor("#9bad9f"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No rating history yet")
            return
        min_value = min(all_values)
        max_value = max(all_values)
        if min_value == max_value:
            min_value -= 50
            max_value += 50
        else:
            padding = (max_value - min_value) * 0.12
            min_value -= padding
            max_value += padding
        for step in range(5):
            y = chart.bottom() - int((chart.height() * step) / 4)
            painter.setPen(QColor("#1d2a22"))
            painter.drawLine(left, y, chart.right(), y)
            painter.setPen(QColor("#6f8476"))
            value = min_value + ((max_value - min_value) * step) / 4
            painter.drawText(QRect(0, y - 9, left - 8, 18), Qt.AlignmentFlag.AlignRight, str(round(value)))
        count = max(1, len(self.labels) - 1)
        for index, label in enumerate(self.labels):
            if len(self.labels) > 8 and index % max(1, len(self.labels) // 7) and index != len(self.labels) - 1:
                continue
            x = chart.left() + int((chart.width() * index) / count)
            painter.setPen(QColor("#6f8476"))
            painter.drawText(QRect(x - 28, chart.bottom() + 8, 56, 18), Qt.AlignmentFlag.AlignCenter, label)
        for series in self.series:
            pen = QPen(QColor(series["color"]), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            points = []
            for index, value in enumerate(series["values"]):
                if value is None:
                    continue
                x = chart.left() + int((chart.width() * index) / count)
                y = chart.bottom() - int(((value - min_value) / (max_value - min_value)) * chart.height())
                points.append(QPoint(x, y))
            for start, end in zip(points, points[1:]):
                painter.drawLine(start, end)
            if points:
                painter.setBrush(QBrush(QColor(series["color"])))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(points[-1], 4, 4)
        x = left
        for series in self.series:
            painter.fillRect(QRect(x, 4, 9, 9), QColor(series["color"]))
            painter.setPen(QColor("#9bad9f"))
            painter.drawText(QRect(x + 13, 0, 90, 16), Qt.AlignmentFlag.AlignLeft, series["name"])
            x += 100


class BarChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels = []
        self.values = []
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels, values):
        self.labels = labels
        self.values = values
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = 22, 12, 16, 38
        chart = QRect(left, top, self.width() - left - right, self.height() - top - bottom)
        if not self.values or chart.width() <= 10:
            painter.setPen(QColor("#9bad9f"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No monthly activity yet")
            return
        maximum = max(self.values)
        slot = chart.width() / len(self.values)
        bar_width = max(6, int(slot * 0.62))
        for index, (label, value) in enumerate(zip(self.labels, self.values)):
            height = int((value / max(1, maximum)) * chart.height())
            x = chart.left() + int(index * slot + (slot - bar_width) / 2)
            y = chart.bottom() - height
            gradient = QColor("#5fd39a")
            painter.fillRect(QRect(x, y, bar_width, max(2, height)), gradient)
            painter.setPen(QColor("#9bad9f"))
            painter.drawText(QRect(x - 12, chart.bottom() + 8, bar_width + 24, 18), Qt.AlignmentFlag.AlignCenter, label)
            painter.drawText(QRect(x - 12, y - 18, bar_width + 24, 16), Qt.AlignmentFlag.AlignCenter, str(value))


def _fen_rows(fen):
    rows = []
    for row in str(fen).split(" ")[0].split("/"):
        expanded = ""
        for token in row:
            expanded += " " * int(token) if token.isdigit() else token
        rows.append(expanded)
    return rows


class EvalBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cp = None
        self.mate = None
        self.orientation = "white"
        self.setMinimumWidth(32)
        self.setMaximumWidth(48)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip("Evaluation: white at the top when you play White")

    def sizeHint(self):
        return QSize(42, 360)

    def set_evaluation(self, score=None, orientation=None):
        score = score or {}
        self.cp = score.get("cp")
        self.mate = score.get("mate")
        if orientation:
            self.orientation = orientation
        self.update()

    def clear(self):
        self.cp = None
        self.mate = None
        self.update()

    def _white_share(self):
        if self.mate is not None:
            return 0.97 if int(self.mate) > 0 else 0.03
        try:
            cp = float(self.cp or 0)
        except (TypeError, ValueError):
            cp = 0
        return 0.5 + 0.5 * math.tanh(cp / 600)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        if width < 4 or height < 8:
            return
        share = self._white_share()
        white_height = max(2, int(height * share))
        black_height = height - white_height
        if self.orientation == "white":
            white_rect = QRect(0, black_height, width, white_height)
            black_rect = QRect(0, 0, width, black_height)
        else:
            white_rect = QRect(0, 0, width, white_height)
            black_rect = QRect(0, white_height, width, black_height)
        painter.fillRect(self.rect(), QColor("#0e1511"))
        painter.fillRect(white_rect, QColor("#f2f0e6"))
        painter.fillRect(black_rect, QColor("#252a26"))
        painter.setPen(QColor("#5fd39a"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        font = QFont("DejaVu Sans")
        font.setPointSize(max(7, min(11, width - 8)))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        if self.mate is not None:
            mate_text = f"M{abs(int(self.mate))}"
            white_text, black_text = (mate_text, "") if int(self.mate) > 0 else ("", mate_text)
        else:
            try:
                value = abs(float(self.cp or 0)) / 100
            except (TypeError, ValueError):
                value = 0
            text = "0" if value == 0 else f"{value:.1f}"
            white_text, black_text = text, text
        if white_rect.height() > 24 and white_text:
            painter.setPen(QColor("#101712"))
            painter.drawText(white_rect, Qt.AlignmentFlag.AlignCenter, white_text)
        if black_rect.height() > 24 and black_text:
            painter.setPen(QColor("#f2f0e6"))
            painter.drawText(black_rect, Qt.AlignmentFlag.AlignCenter, black_text)


class ChessBoard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        self.last_move = None
        self.orientation = "white"
        self.guide = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(520, 520)

    def set_position(self, fen, last_move=None, orientation=None, guide=None):
        self.fen = fen
        self.last_move = last_move
        if orientation:
            self.orientation = orientation
        self.guide = guide or {}
        self.update()

    def _display_rect(self, square_name, ox, oy, square_size):
        if len(square_name) < 2 or square_name[0] not in FILES:
            return None
        source_col = FILES.index(square_name[0])
        try:
            rank = int(square_name[1])
        except ValueError:
            return None
        source_row = 8 - rank
        if source_row < 0 or source_row > 7:
            return None
        display_col = source_col if self.orientation == "white" else 7 - source_col
        display_row = source_row if self.orientation == "white" else 7 - source_row
        return QRect(
            int(ox + display_col * square_size),
            int(oy + display_row * square_size),
            int(square_size + 0.6),
            int(square_size + 0.6),
        )

    def _draw_arrows(self, painter, ox, oy, square_size):
        for arrow in self.guide.get("arrows") or []:
            start = self._display_rect(str(arrow.get("from") or ""), ox, oy, square_size)
            end = self._display_rect(str(arrow.get("to") or ""), ox, oy, square_size)
            if start is None or end is None:
                continue
            start_point = QPointF(start.center().x(), start.center().y())
            end_point = QPointF(end.center().x(), end.center().y())
            threat = arrow.get("kind") == "threat"
            color = QColor("#e2c17f" if threat else "#5fd39a")
            color.setAlpha(210)
            pen = QPen(color, max(3, int(square_size * (0.045 if threat else 0.06))))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(start_point, end_point)
            dx = end_point.x() - start_point.x()
            dy = end_point.y() - start_point.y()
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            wing = max(6, square_size * 0.22)
            back = max(4, square_size * 0.12)
            tip = QPointF(end_point.x() - ux * back, end_point.y() - uy * back)
            left = QPointF(tip.x() - uy * wing, tip.y() + ux * wing)
            right = QPointF(tip.x() + uy * wing, tip.y() - ux * wing)
            painter.drawLine(end_point, left)
            painter.drawLine(end_point, right)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        if side < 8:
            return
        square = side / 8
        ox = (self.width() - side) / 2
        oy = (self.height() - side) / 2
        rows = _fen_rows(self.fen)
        focus_squares = set(self.guide.get("focus") or [])
        font = QFont("DejaVu Sans")
        font.setPointSize(max(12, int(square * 0.62)))
        coord_font = QFont()
        coord_font.setPointSize(max(7, int(square * 0.16)))
        for display_row in range(8):
            source_row = display_row if self.orientation == "white" else 7 - display_row
            rank = 8 - source_row
            for display_col in range(8):
                source_col = display_col if self.orientation == "white" else 7 - display_col
                piece = rows[source_row][source_col] if source_col < len(rows[source_row]) else " "
                square_name = FILES[source_col] + str(rank)
                rect = QRect(
                    int(ox + display_col * square),
                    int(oy + display_row * square),
                    int(square + 0.6),
                    int(square + 0.6),
                )
                light = (rank + source_col) % 2 == 0
                painter.fillRect(rect, QColor("#cfd8c9") if light else QColor("#4d765d"))
                if square_name in focus_squares:
                    overlay = QColor("#5fd39a")
                    overlay.setAlpha(90)
                    painter.fillRect(rect, overlay)
                if self.last_move and square_name in (self.last_move.get("from"), self.last_move.get("to")):
                    pen = QPen(QColor("#d8b46a"), 3)
                    painter.setPen(pen)
                    painter.drawRect(rect.adjusted(2, 2, -2, -2))
                if piece != " ":
                    painter.setFont(font)
                    painter.setPen(QColor("#fffaf0") if piece.isupper() else QColor("#0c120e"))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, PIECE_GLYPHS[piece.lower()])
                painter.setFont(coord_font)
                painter.setPen(QColor("#1d2a22") if light else QColor("#edf5ef"))
                if display_col == 0:
                    painter.drawText(rect.adjusted(4, 0, 0, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, str(rank))
                if display_row == 7:
                    painter.drawText(rect.adjusted(0, 2, -4, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, FILES[source_col])
        self._draw_arrows(painter, ox, oy, square)


def captured_pieces(moves, ply=None):
    upto = len(moves) if ply is None else max(0, min(int(ply), len(moves)))
    white_captured = []
    black_captured = []
    for move in moves[:upto]:
        glyph = PIECE_GLYPHS.get(move.get("captured"))
        if not glyph:
            continue
        if move.get("color") == "w":
            white_captured.append(glyph)
        else:
            black_captured.append(glyph)
    return "".join(white_captured), "".join(black_captured)
