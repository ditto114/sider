import json
import os
from PyQt5.QtWidgets import (QWidget, QPushButton, QLabel, QVBoxLayout, 
                             QFileDialog, QApplication, QRubberBand, QMainWindow)
from PyQt5.QtCore import Qt, QRect, QSize, pyqtSignal, QPoint
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QPolygon

class SelectionOverlay(QWidget):
    region_selected = pyqtSignal(int, int, int, int) # x, y, w, h

    def __init__(self):
        super().__init__()
        # Use Tool flag or plain widget
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        
        self.start_point = None
        self.end_point = None
        self.is_selecting = False
        
        # Determine virtual geometry to cover all screens
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        total_rect = QRect()
        for i in range(screen_count):
            total_rect = total_rect.united(desktop.screenGeometry(i))
        self.setGeometry(total_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.pos()
            self.end_point = self.start_point
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.close()
            
            # Use points to determine rect
            if self.start_point and self.end_point:
                # Convert to global coordinates (though we are full screen, mapToGlobal refers to screen)
                # Since this widget covers the screen, pos() is effectively global relative to the widget's top-left
                # But widget top-left might be (0,0) of secondary monitor?
                # Using mapToGlobal is safer.
                global_start = self.mapToGlobal(self.start_point)
                global_end = self.mapToGlobal(self.end_point)
                
                rect = QRect(global_start, global_end).normalized()
                
                if rect.width() > 10 and rect.height() > 10:
                    self.region_selected.emit(rect.x(), rect.y(), rect.width(), rect.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Dim the screen (draw semi-transparent black over everything)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())
        
        if self.start_point and self.end_point:
            rect = QRect(self.start_point, self.end_point).normalized()
            
            # Clear the dimming for the selected area 
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.setBrush(Qt.SolidPattern) 
            painter.drawRect(rect)
            
            # Border
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

class MapVizWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(200, 200)
        self.setStyleSheet("border: 1px solid gray; background-color: #333;")
        self.map_w = 100
        self.map_h = 100
        self.char_pos = None # (x_norm, y_norm)

    def set_map_dimensions(self, w, h):
        self.map_w = w
        self.map_h = h
        self.char_pos = None
        self.update()

    def update_pos(self, rel_x, rel_y):
        # rel_x, rel_y are deviations from center
        # Center is (0,0)
        
        # Normalize to 0..1
        # X: -w/2 -> 0, +w/2 -> 1
        x_norm = (rel_x + self.map_w / 2) / self.map_w
        
        # Y: +h/2 (Top) -> 0, -h/2 (Bottom) -> 1
        # Since rel_y is positive Up (Cartesian) and local Y is positive Down.
        # Top of map: rel_y = h/2. We want 0.
        # Bottom of map: rel_y = -h/2. We want 1.
        y_norm = 0.5 - (rel_x / self.map_h) if self.map_h != 0 else 0.5 # Wait, typo in prev thought. rel_y
        y_norm = 0.5 - (rel_y / self.map_h)
        
        self.char_pos = (x_norm, y_norm)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw background (Map area representation)
        painter.fillRect(self.rect(), QColor("#222"))
        
        # Draw Character Dot
        if self.char_pos:
            cx = int(self.char_pos[0] * self.width())
            cy = int(self.char_pos[1] * self.height())
            
            painter.setBrush(QColor(0, 255, 0)) # Green dot
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(cx, cy), 5, 5)

class DetectionOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Cover all screens
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        total_rect = QRect()
        for i in range(screen_count):
            total_rect = total_rect.united(desktop.screenGeometry(i))
        self.setGeometry(total_rect)
        
        self.region_points = [] # list of QPoint

    def update_region(self, points):
        # points: list of (x,y) tuples
        self.region_points = [QPoint(x, y) for x, y in points]
        self.update()

    def paintEvent(self, event):
        if not self.region_points or len(self.region_points) < 3:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw Polygon
        poly = QPolygon(self.region_points)
        
        painter.setPen(QPen(QColor(0, 255, 255), 3)) # Cyan
        painter.setBrush(QColor(0, 255, 255, 50))
        painter.drawPolygon(poly)

class InfoOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, 200, 100) # Default size
        
        # UI
        self.layout = QVBoxLayout()
        self.lbl_info = QLabel("Coords: (0, 0)")
        self.lbl_info.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self.lbl_info.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.lbl_info)
        self.setLayout(self.layout)

    def update_coords(self, x, y):
        self.lbl_info.setText(f"X: {x}, Y: {y}")
        self.adjustSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw rounded rect with semi-transparent black background
        painter.setBrush(QColor(0, 0, 0, 128)) # 50% opacity
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 10, 10)

class MainWindow(QMainWindow):
    def __init__(self, tracker):
        super().__init__()
        self.tracker = tracker
        self.info_overlay = InfoOverlay()
        self.detection_overlay = DetectionOverlay()
        
        # State for persistence
        self.current_map_path = None
        self.current_char_path = None
        self.current_search_region = None
        
        self.initUI()
        self.connect_signals()
        self.load_config()
        
    def initUI(self):
        self.setWindowTitle("Minimap Tracker")
        self.setGeometry(100, 100, 300, 550)
        
        central_widget = QWidget()
        layout = QVBoxLayout()
        
        # Region Selection
        self.btn_select_region = QPushButton("Select Search Area")
        self.lbl_region = QLabel("Search Area: Full Screen")
        
        # Map Loading
        self.btn_load_map = QPushButton("Load Minimap Image")
        self.lbl_map = QLabel("Map: Not Loaded")
        
        # Map Visualization
        self.map_viz = MapVizWidget()
        
        # Image Loading
        self.btn_load_image = QPushButton("Load Character Image")
        self.lbl_image_status = QLabel("Image: None")
        
        # Main window coords (optional, since we have overlay)
        self.lbl_coords = QLabel("Relative Coordinates: (0, 0)")
        
        # Controls
        self.btn_start = QPushButton("Start Tracking")
        self.btn_stop = QPushButton("Stop Tracking")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        
        # Add to layout
        layout.addWidget(self.btn_select_region)
        layout.addWidget(self.lbl_region)
        layout.addSpacing(10)
        layout.addWidget(self.btn_load_map)
        layout.addWidget(self.lbl_map)
        layout.addWidget(self.map_viz, 0, Qt.AlignCenter)
        layout.addSpacing(10)
        layout.addWidget(self.btn_load_image)
        layout.addWidget(self.lbl_image_status)
        layout.addSpacing(10)
        layout.addWidget(self.lbl_coords)
        layout.addStretch()
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def connect_signals(self):
        self.btn_select_region.clicked.connect(self.open_selection_overlay)
        self.btn_load_map.clicked.connect(self.load_map)
        self.btn_load_image.clicked.connect(self.load_image)
        self.btn_start.clicked.connect(self.start_tracking)
        self.btn_stop.clicked.connect(self.stop_tracking)
        
        self.tracker.position_update.connect(self.update_coordinates)
        self.tracker.status_update.connect(self.update_status)
        self.tracker.map_dimensions.connect(self.map_viz.set_map_dimensions)
        self.tracker.map_region_update.connect(self.detection_overlay.update_region)

    def open_selection_overlay(self):
        self.selector = SelectionOverlay()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.show()

    def on_region_selected(self, x, y, w, h):
        self.tracker.set_search_region(x, y, w, h)
        self.current_search_region = {'x': x, 'y': y, 'w': w, 'h': h}
        self.lbl_region.setText(f"Area: {x}, {y} ({w}x{h})")

    def load_map(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Minimap Image", "", "Images (*.png *.jpg *.bmp)")
        if path:
            if self.tracker.set_map_source(path):
                self.current_map_path = path
                self.lbl_map.setText(f"Map Loaded: {path.split('/')[-1]}")
                self.check_ready()

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Character Image", "", "Images (*.png *.jpg *.bmp)")
        if path:
            if self.tracker.set_template(path):
                self.current_char_path = path
                self.lbl_image_status.setText(f"Loaded: {path.split('/')[-1]}")
                self.check_ready()

    def check_ready(self):
        if self.tracker.map_ready and self.tracker.template is not None:
            self.btn_start.setEnabled(True)

    def start_tracking(self):
        self.save_config() # Save on start just in case
        self.tracker.start()
        self.info_overlay.show()
        self.detection_overlay.show()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_load_map.setEnabled(False)
        self.btn_load_image.setEnabled(False)
        self.btn_select_region.setEnabled(False)

    def stop_tracking(self):
        self.tracker.stop()
        self.info_overlay.hide()
        self.detection_overlay.hide()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_load_map.setEnabled(True)
        self.btn_load_image.setEnabled(True)
        self.btn_select_region.setEnabled(True)

    def update_coordinates(self, x, y):
        self.lbl_coords.setText(f"Relative Coordinates: ({x}, {y})")
        self.info_overlay.update_coords(x, y)
        self.map_viz.update_pos(x, y)

    def update_status(self, msg):
        self.statusBar().showMessage(msg)

    def load_config(self):
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding='utf-8') as f:
                    config = json.load(f)
                    
                if 'map_path' in config and config['map_path'] and os.path.exists(config['map_path']):
                    path = config['map_path']
                    if self.tracker.set_map_source(path):
                        self.current_map_path = path
                        self.lbl_map.setText(f"Map Loaded: {path.split('/')[-1]}")
                
                if 'char_path' in config and config['char_path'] and os.path.exists(config['char_path']):
                    path = config['char_path']
                    if self.tracker.set_template(path):
                        self.current_char_path = path
                        self.lbl_image_status.setText(f"Loaded: {path.split('/')[-1]}")
                        
                if 'search_region' in config and config['search_region']:
                    r = config['search_region']
                    self.on_region_selected(r['x'], r['y'], r['w'], r['h'])
                    
                self.check_ready()
            except Exception as e:
                print(f"Failed to load config: {e}")

    def save_config(self):
        config = {
            'map_path': self.current_map_path,
            'char_path': self.current_char_path,
            'search_region': self.current_search_region
        }
        try:
            with open("config.json", "w", encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)
