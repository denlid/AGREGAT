from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QWidget


class EdgeHandle(QWidget):
    def __init__(self, edges, parent):
        super().__init__(parent)
        self.edges = edges
        # Transparent background but captures mouse events
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        # Set cursor based on edges
        if edges == (Qt.Edge.TopEdge | Qt.Edge.LeftEdge) or edges == (
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge
        ):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges == (Qt.Edge.TopEdge | Qt.Edge.RightEdge) or edges == (
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
        ):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Native OS resize functionality
            wh = self.window().windowHandle()
            if wh:
                wh.startSystemResize(self.edges)
            event.accept()


class AdvancedResizer(QObject):
    """
    Adds 8 transparent overlay widgets to the window edges to enable
    advanced native OS resizing on frameless windows.
    """

    def __init__(self, window, margin=6):
        super().__init__(window)
        self.window = window
        self.margin = margin
        self.handles = {}

        edges = [
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        ]

        for e in edges:
            h = EdgeHandle(e, window)
            self.handles[e] = h

        self.window.installEventFilter(self)
        self.update_geometry()

    def eventFilter(self, obj, event):
        if obj == self.window and event.type() == QEvent.Type.Resize:
            self.update_geometry()
        return False

    def update_geometry(self):
        w = self.window.width()
        h = self.window.height()
        m = self.margin

        self.handles[Qt.Edge.TopEdge].setGeometry(m, 0, w - 2 * m, m)
        self.handles[Qt.Edge.BottomEdge].setGeometry(m, h - m, w - 2 * m, m)
        self.handles[Qt.Edge.LeftEdge].setGeometry(0, m, m, h - 2 * m)
        self.handles[Qt.Edge.RightEdge].setGeometry(w - m, m, m, h - 2 * m)

        self.handles[Qt.Edge.TopEdge | Qt.Edge.LeftEdge].setGeometry(0, 0, m, m)
        self.handles[Qt.Edge.TopEdge | Qt.Edge.RightEdge].setGeometry(w - m, 0, m, m)
        self.handles[Qt.Edge.BottomEdge | Qt.Edge.LeftEdge].setGeometry(0, h - m, m, m)
        self.handles[Qt.Edge.BottomEdge | Qt.Edge.RightEdge].setGeometry(w - m, h - m, m, m)

        for handle in self.handles.values():
            handle.raise_()
