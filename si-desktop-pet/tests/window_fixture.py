"""A separate, disposable application used only for native ledge checks."""
import json,sys
from pathlib import Path
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QWidget
app=QApplication([]);w=QWidget();w.setWindowTitle('Si QA · window platform fixture');w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint);w.resize(480,220);w.move(350,240);w.show()
Path(sys.argv[1]).write_text(json.dumps({'hwnd':int(w.winId())}),encoding='utf-8')
QTimer.singleShot(1400,lambda:w.move(500,320))
QTimer.singleShot(2800,w.showMinimized)
QTimer.singleShot(4200,app.quit)
app.exec()
