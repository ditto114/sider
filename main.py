import sys
from PyQt5.QtWidgets import QApplication
from src.gui import MainWindow
from src.tracker import TrackerWorker

def main():
    app = QApplication(sys.argv)
    
    tracker = TrackerWorker()
    window = MainWindow(tracker)
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
