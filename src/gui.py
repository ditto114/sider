import json
import os
import time
import pyperclip
import pyautogui
import keyboard
import cv2
import numpy as np
import mss
from PyQt5.QtWidgets import (QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
                             QFileDialog, QApplication, QRubberBand, QMainWindow, 
                             QInputDialog, QCheckBox, QComboBox, QLineEdit, QSpinBox,
                             QGroupBox, QTabWidget, QGridLayout, QScrollArea, QTextEdit,
                             QTreeWidget, QTreeWidgetItem)
from PyQt5.QtGui import QFont
import re
try:
    from scapy.all import AsyncSniffer, Raw
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
from PyQt5.QtCore import Qt, QRect, QSize, pyqtSignal, QPoint, QTimer, QThread, QObject, QDateTime
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QPolygon

# NPC 목록 정의
NPC_LIST = ['도란', '듀이']

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

class DeliveryWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, config, run_data, sct):
        super().__init__()
        self.config = config
        self.run_data = run_data
        self.sct = sct
        self.is_running = True
        
    def find_image(self, img_path, timeout=30, threshold=0.7):
        """이미지 찾기 (타임아웃 지원)"""
        start_time = time.time()
        
        # 템플릿 로드
        try:
            img_array = np.fromfile(img_path, np.uint8)
            template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if template is None:
                return None
        except:
            return None
            
        h, w = template.shape[:2]
        
        while time.time() - start_time < timeout:
            if not self.is_running: return None
            
            try:
                # 스크린샷 캡처 (스레드 안전을 위해 매번 생성)
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    screenshot = sct.grab(monitor)
                    img = np.array(screenshot)
                
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                # 매칭
                res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                
                if max_val >= threshold:
                    center_x = max_loc[0] + w // 2
                    center_y = max_loc[1] + h // 2
                    return (center_x, center_y)
            except:
                pass
            
            time.sleep(0.5)
            
        return None

    def find_and_click(self, img_path, name, timeout=5):
        self.progress_signal.emit(f"'{name}' 찾는 중 (전체화면)...")
        pos = self.find_image(img_path, timeout)
        if pos:
            pyautogui.click(pos[0], pos[1])
            self.progress_signal.emit(f"'{name}' 클릭 완료")
            return True
        else:
            self.progress_signal.emit(f"'{name}' 못 찾음")
            # 디버깅용 이미지 저장
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    screenshot = sct.grab(monitor)
                    img = np.array(screenshot)
                
                # mss raw is BGRA. cvtColor BGRA2BGR
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                # Windows cv2.imwrite 한글 경로 문제 회피
                filename = f"debug_fail_{int(time.time())}.png"
                
                # imencode 사용하여 한글 경로 지원 (혹은 그냥 영문 파일명 사용)
                result, encoded_img = cv2.imencode('.png', img)
                if result:
                    with open(filename, "wb") as f:
                        encoded_img.tofile(f)
                        
                self.progress_signal.emit(f"캡처 저장: {filename}")
            except Exception as e:
                print(f"디버그 저장 실패: {e}")
                self.progress_signal.emit(f"저장 실패: {e}")
            return False

    def click_loc(self, pos, name):
        self.progress_signal.emit(f"'{name}' 클릭 ({pos['x']}, {pos['y']})")
        pyautogui.click(pos['x'], pos['y'])

    def press_key(self, key, duration=0.1):
        """키 입력 (pyautogui 사용)"""
        pyautogui.keyDown(key)
        time.sleep(duration)
        pyautogui.keyUp(key)
        
    def paste_text(self, text):
        """텍스트 붙여넣기 (클립보드 사용)"""
        pyperclip.copy(text)
        time.sleep(0.3)
        keyboard.press('ctrl')
        time.sleep(0.05)
        keyboard.press('v')
        time.sleep(0.1)
        keyboard.release('v')
        time.sleep(0.05)
        keyboard.release('ctrl')

    def run(self):
        try:
            # 1. i 키 입력 (듀이 이동 완료 후 바로 시작)
            self.progress_signal.emit("i 키 입력...")
            self.press_key('i')
            time.sleep(0.5)
            
            # 2. 배송(좌표) 클릭
            self.click_loc(self.config['delivery_pos'], "배송 버튼")
            time.sleep(0.5)
            
            # 3. 받는사람(좌표) 클릭 후 닉네임 입력
            self.click_loc(self.config['receiver_pos'], "받는사람 입력칸")
            time.sleep(0.3)
            self.paste_text(self.run_data['nickname'])
            time.sleep(0.5)
            
            # 4. 아이템 등록 반복
            qty = self.run_data['quantity']
            for i in range(qty):
                if not self.is_running: return
                self.progress_signal.emit(f"아이템 등록 ({i+1}/{qty})")
                
                # 마우스 치우기 (이미지 가림 방지)
                pyautogui.moveTo(200, 200)
                time.sleep(0.2)
                
                # 사이다 클릭
                if not self.find_and_click(self.config['cider_img'], "사이다", timeout=5):
                    self.finished_signal.emit(False, "사이다 이미지를 못 찾았습니다")
                    return
                time.sleep(0.3)
                
                # 빈칸 클릭
                if not self.find_and_click(self.config['empty_slot_img'], "빈칸", timeout=5):
                    self.finished_signal.emit(False, "빈칸 이미지를 못 찾았습니다")
                    return
                time.sleep(0.3)
            
            # 5. 청구1, 청구2 클릭
            self.click_loc(self.config['charge1_pos'], "청구금액 1")
            time.sleep(0.2)
            self.click_loc(self.config['charge2_pos'], "청구금액 2")
            time.sleep(0.2)
            
            # 6. 가격 입력 후 엔터
            self.paste_text(str(self.run_data['price']))
            time.sleep(0.2)
            self.press_key('enter')
            time.sleep(0.5)
            
            # 7. 보내기(이미지) 클릭 -> 1초 대기 -> 엔터 -> 1초 대기
            if not self.find_and_click(self.config['send_img'], "보내기", timeout=3):
                self.finished_signal.emit(False, "보내기 버튼을 못 찾았습니다")
                return
            
            time.sleep(1.0)
            self.press_key('enter')
            time.sleep(1.0)
            
            # 8. 확인(이미지) 감지 (30초) -> 클릭 -> 1초 대기
            self.progress_signal.emit("첫 번째 확인 버튼 대기 (30s)...")
            if not self.find_and_click(self.config['confirm_img'], "확인(1)", timeout=30):
                self.finished_signal.emit(False, "첫 번째 확인 버튼 타임아웃")
                return
            
            time.sleep(1.0)
            
            # 9. 확인(이미지) 감지 (30초) -> 클릭
            self.progress_signal.emit("두 번째 확인 버튼 대기 (30s)...")
            if not self.find_and_click(self.config['confirm_img'], "확인(2)", timeout=30):
                self.finished_signal.emit(False, "두 번째 확인 버튼 타임아웃")
                return
                
            # 10. ESC 3회
            time.sleep(0.5)
            for _ in range(3):
                self.press_key('esc')
                time.sleep(0.3)
                
            self.finished_signal.emit(True, "배송 완료!")
            
        except Exception as e:
            self.finished_signal.emit(False, f"에러 발생: {str(e)}")

    def stop(self):
        self.is_running = False

class DeliveryWidget(QWidget):
    task_finished = pyqtSignal(bool, str)
    def __init__(self):
        super().__init__()
        self.worker = None
        self.config = {
            'cider_img': '', 'empty_slot_img': '', 'send_img': '', 'confirm_img': '',
            'delivery_pos': {'x': 0, 'y': 0}, 'receiver_pos': {'x': 0, 'y': 0},
            'charge1_pos': {'x': 0, 'y': 0}, 'charge2_pos': {'x': 0, 'y': 0}
        }
        self.sct = None
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        
        # 설정 그룹 (스크롤 가능하게)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        setting_widget = QWidget()
        setting_layout = QGridLayout()
        
        # 이미지 설정
        self.img_labels = {}
        self.img_status_labels = {}
        images = [
            ('cider_img', '사이다 이미지'),
            ('empty_slot_img', '빈칸 이미지'),
            ('send_img', '보내기 이미지'),
            ('confirm_img', '확인 이미지')
        ]
        
        for i, (key, label) in enumerate(images):
            setting_layout.addWidget(QLabel(label + ":"), i, 0)
            lbl_path = QLabel("없음")
            lbl_path.setStyleSheet("color: #888; font-size: 10px;")
            self.img_labels[key] = lbl_path
            setting_layout.addWidget(lbl_path, i, 1)
            btn = QPushButton("선택")
            btn.clicked.connect(lambda ch, k=key: self.select_image(k))
            setting_layout.addWidget(btn, i, 2)
            
            # 상태 라벨
            lbl_status = QLabel("-")
            lbl_status.setFixedWidth(60)
            lbl_status.setAlignment(Qt.AlignCenter)
            self.img_status_labels[key] = lbl_status
            setting_layout.addWidget(lbl_status, i, 3)

        # 이미지 테스트 버튼
        btn_test = QPushButton("이미지 인식 테스트")
        btn_test.clicked.connect(self.test_images)
        btn_test.setStyleSheet("background-color: #2196F3; color: white;")
        setting_layout.addWidget(btn_test, len(images), 0, 1, 4)
            
        # 좌표 설정
        self.coord_labels = {}
        coords = [
            ('delivery_pos', '배송 버튼'),
            ('receiver_pos', '받는사람 칸'),
            ('charge1_pos', '청구1'),
            ('charge2_pos', '청구2')
        ]
        
        base_row = len(images) + 1
        for i, (key, label) in enumerate(coords):
            setting_layout.addWidget(QLabel(label + ":"), base_row + i, 0)
            lbl_coord = QLabel("(0, 0)")
            self.coord_labels[key] = lbl_coord
            setting_layout.addWidget(lbl_coord, base_row + i, 1)
            btn = QPushButton("설정 (3초)")
            btn.clicked.connect(lambda ch, k=key: self.set_coordinate(k))
            setting_layout.addWidget(btn, base_row + i, 2)
            
        setting_widget.setLayout(setting_layout)
        scroll.setWidget(setting_widget)
        layout.addWidget(scroll)
        
        # 실행 그룹
        run_group = QGroupBox("실행")
        run_layout = QGridLayout()
        
        run_layout.addWidget(QLabel("닉네임:"), 0, 0)
        self.txt_nickname = QLineEdit()
        run_layout.addWidget(self.txt_nickname, 0, 1)
        
        run_layout.addWidget(QLabel("수량:"), 1, 0)
        self.spin_qty = QSpinBox()
        self.spin_qty.setRange(1, 100)
        run_layout.addWidget(self.spin_qty, 1, 1)
        
        run_layout.addWidget(QLabel("가격:"), 2, 0)
        self.txt_price = QLineEdit()
        run_layout.addWidget(self.txt_price, 2, 1)
        
        run_group.setLayout(run_layout)
        layout.addWidget(run_group)
        
        # 버튼 및 로그
        self.btn_start = QPushButton("배송 시작")
        self.btn_start.clicked.connect(self.start_delivery)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        layout.addWidget(self.btn_start)
        
        self.lbl_status = QLabel("대기 중")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        self.setLayout(layout)
        
    def select_image(self, key):
        path, _ = QFileDialog.getOpenFileName(self, "이미지 선택", "", "Images (*.png *.jpg *.bmp)")
        if path:
            self.config[key] = path
            self.img_labels[key].setText(os.path.basename(path))
            
    def set_coordinate(self, key):
        self.lbl_status.setText("3초 뒤 마우스 위치가 저장됩니다...")
        QTimer.singleShot(3000, lambda: self._save_coordinate(key))
        
    def _save_coordinate(self, key):
        pos = pyautogui.position()
        self.config[key] = {'x': pos.x, 'y': pos.y}
        self.coord_labels[key].setText(f"({pos.x}, {pos.y})")
        self.lbl_status.setText(f"좌표 저장 완료: {pos.x}, {pos.y}")
        
    def load_config(self, config_data):
        if not config_data: return
        self.config.update(config_data)
        
        # UI 업데이트
        for key, lbl in self.img_labels.items():
            if self.config.get(key):
                lbl.setText(os.path.basename(self.config[key]))
        
        for key, lbl in self.coord_labels.items():
            pos = self.config.get(key, {'x': 0, 'y': 0})
            lbl.setText(f"({pos['x']}, {pos['y']})")
            
    def get_config(self):
        return self.config
        
    def start_delivery(self):
        # Worker execution
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            self.btn_start.setText("배송 시작")
            self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
            self.lbl_status.setText("중지됨")
            return
            
        # 유효성 검사
        nickname = self.txt_nickname.text()
        price = self.txt_price.text()
        if not nickname or not price:
            self.lbl_status.setText("닉네임과 가격을 입력하세요")
            return
            
        if self.sct is None:
            self.sct = mss.mss()
            
        run_data = {
            'nickname': nickname,
            'quantity': self.spin_qty.value(),
            'price': price
        }
        
        # MainWindow를 통해 NavigationOverlay 접근
        main_window = self.window()
        if not hasattr(main_window, 'nav_overlay'):
             self.lbl_status.setText("오류: NavigationOverlay를 찾을 수 없음")
             return

        # 듀이 이동 시작
        self.lbl_status.setText("듀이에게 이동 중...")
        main_window.nav_overlay.start_npc_movement('듀이')
        
        # 도착 시 배송 시작하도록 연결
        self.delivery_run_data = run_data
        try: main_window.nav_overlay.movement_finished.disconnect(self._on_dewy_arrived)
        except: pass
        main_window.nav_overlay.movement_finished.connect(self._on_dewy_arrived)
        
        self.btn_start.setText("이동 및 배송 중지")
        self.btn_start.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 10px;")
        
    def _on_dewy_arrived(self):
        try: self.window().nav_overlay.movement_finished.disconnect(self._on_dewy_arrived)
        except: pass
        self._start_delivery_worker(self.delivery_run_data)

    def _start_delivery_worker(self, run_data):
        # 이동 완료, 배송 시작
        self.lbl_status.setText("이동 완료! 배송 작업을 시작합니다...")
        
        # (연결 해제는 _on_dewy_arrived에서 처리됨)
        
        self.worker = DeliveryWorker(self.config, run_data, self.sct)
        self.worker.progress_signal.connect(self.update_status)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        
    def update_status(self, msg):
        self.lbl_status.setText(msg)
        
    def on_finished(self, success, msg):
        self.btn_start.setText("배송 시작")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        if success:
            self.lbl_status.setText(f"완료: {msg}")
        else:
            self.lbl_status.setText(f"실패: {msg}")
        self.task_finished.emit(success, msg)

    def test_images(self):
        """현재 화면에서 이미지 찾기 테스트"""
        
        try:
            self.lbl_status.setText("이미지 인식 테스트 중...")
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                img = np.array(screenshot)
            
            img_color = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            for key, status_lbl in self.img_status_labels.items():
                path = self.config.get(key)
                if not path or not os.path.exists(path):
                    status_lbl.setText("파일 없음")
                    status_lbl.setStyleSheet("color: gray")
                    continue
                    
                try:
                    # 템플릿 로드
                    img_array = np.fromfile(path, np.uint8)
                    template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    if template is None:
                        status_lbl.setText("로드 실패")
                        continue
                        
                    # 컬러 매칭
                    res = cv2.matchTemplate(img_color, template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(res)
                    
                    if max_val >= 0.7:
                        status_lbl.setText(f"✅ {int(max_val*100)}%")
                        status_lbl.setStyleSheet("color: green; font-weight: bold;")
                    else:
                        status_lbl.setText(f"❌ {int(max_val*100)}%")
                        status_lbl.setStyleSheet("color: red;")
                        
                except Exception as e:
                    status_lbl.setText("오류")
                    print(e)
            
            self.lbl_status.setText("테스트 완료")
            
        except Exception as e:
            self.lbl_status.setText(f"테스트 오류: {e}")

    def start_delivery_auto(self, nick, qty, price):
        self.txt_nickname.setText(nick)
        self.spin_qty.setValue(qty)
        self.txt_price.setText(price)
        self.start_delivery()

class PurchaseWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, config, qty, sct):
        super().__init__()
        self.config = config
        self.qty = qty
        self.sct = sct
        self.is_running = True
        
    def press_key(self, key, duration=0.1):
        pyautogui.keyDown(key)
        time.sleep(duration)
        pyautogui.keyUp(key)
        
    def find_image(self, img_path, timeout=5, threshold=0.7):
        start_time = time.time()
        try:
            img_array = np.fromfile(img_path, np.uint8)
            template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if template is None: return None
        except: return None
        
        while time.time() - start_time < timeout:
            if not self.is_running: return None
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    img = np.array(sct.grab(monitor))
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                
                if max_val >= threshold:
                    h, w = template.shape[:2]
                    return (max_loc[0] + w // 2, max_loc[1] + h // 2)
            except: pass
            time.sleep(0.3)
        return None

    def find_and_click(self, img_path, name, timeout=5):
        self.progress_signal.emit(f"'{name}' 찾는 중...")
        pos = self.find_image(img_path, timeout)
        if pos:
            pyautogui.click(pos[0], pos[1])
            return True
        return False

    def run(self):
        try:
            # 상점 창 열릴 때까지 대기
            self.progress_signal.emit("상점 열리는 중...")
            time.sleep(0.2)
            
            pos = self.config['purchase_pos']
            
            for i in range(self.qty):
                if not self.is_running: return
                self.progress_signal.emit(f"구매 진행 중 ({i+1}/{self.qty})")
                
                # 1. 구매 좌표 더블클릭
                pyautogui.doubleClick(pos['x'], pos['y'], interval=0.1)
                time.sleep(0.2)
                
                # 2. 확인 이미지 클릭
                if not self.find_and_click(self.config['confirm_img'], "확인", timeout=3):
                    self.finished_signal.emit(False, "확인 버튼을 못 찾았습니다")
                    return
                time.sleep(0.2)
                
            # 종료 시 ESC (3회)
            time.sleep(0.5)
            for _ in range(3):
                self.press_key('esc')
                time.sleep(0.3)
            self.finished_signal.emit(True, "구매 완료!")
            
        except Exception as e:
            self.finished_signal.emit(False, f"에러: {e}")
            
    def stop(self):
        self.is_running = False

class PurchaseWidget(QWidget):
    task_finished = pyqtSignal(bool, str)
    def __init__(self):
        super().__init__()
        self.worker = None
        self.config = {
            'purchase_pos': {'x': 0, 'y': 0},
            'confirm_img': ''
        }
        self.sct = None
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        setting_layout = QGridLayout()
        
        # 이미지 설정 (확인 버튼)
        self.lbl_img = QLabel("없음")
        self.lbl_img.setStyleSheet("color: #888; font-size: 10px;")
        btn_img = QPushButton("확인 이미지 선택")
        btn_img.clicked.connect(self.select_image)
        setting_layout.addWidget(QLabel("확인 이미지:"), 0, 0)
        setting_layout.addWidget(self.lbl_img, 0, 1)
        setting_layout.addWidget(btn_img, 0, 2)
        
        # 좌표 설정 (구매 버튼)
        self.lbl_coord = QLabel("(0, 0)")
        btn_coord = QPushButton("구매 좌표 설정 (3초)")
        btn_coord.clicked.connect(self.set_coordinate)
        setting_layout.addWidget(QLabel("구매 버튼:"), 1, 0)
        setting_layout.addWidget(self.lbl_coord, 1, 1)
        setting_layout.addWidget(btn_coord, 1, 2)
        
        layout.addLayout(setting_layout)
        
        # 수량 설정
        run_layout = QHBoxLayout()
        run_layout.addWidget(QLabel("구매 수량:"))
        self.spin_qty = QSpinBox()
        self.spin_qty.setRange(1, 1000)
        run_layout.addWidget(self.spin_qty)
        
        # 최소 유지 수량
        run_layout.addWidget(QLabel("최소 유지:"))
        self.spin_min_qty = QSpinBox()
        self.spin_min_qty.setRange(0, 1000)
        run_layout.addWidget(self.spin_min_qty)

        layout.addLayout(run_layout)
        
        # 버튼
        self.btn_start = QPushButton("구매 시작")
        self.btn_start.clicked.connect(self.start_purchase)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        layout.addWidget(self.btn_start)
        
        self.lbl_status = QLabel("대기 중")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        layout.addStretch()
        self.setLayout(layout)
        
    def select_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "이미지 선택", "", "Images (*.png *.jpg *.bmp)")
        if path:
            self.config['confirm_img'] = path
            self.lbl_img.setText(os.path.basename(path))
            
    def set_coordinate(self):
        self.lbl_status.setText("3초 뒤 좌표 저장...")
        QTimer.singleShot(3000, self._save_coordinate)
        
    def _save_coordinate(self):
        pos = pyautogui.position()
        self.config['purchase_pos'] = {'x': pos.x, 'y': pos.y}
        self.lbl_coord.setText(f"({pos.x}, {pos.y})")
        self.lbl_status.setText("좌표 저장 완료")
        
    def start_purchase(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            self.btn_start.setText("구매 시작")
            self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
            return

        if not self.config['confirm_img'] or self.config['purchase_pos']['x'] == 0:
            self.lbl_status.setText("이미지와 좌표를 설정해주세요")
            return
            
        main_window = self.window()
        if hasattr(main_window, 'nav_overlay'):
            self.lbl_status.setText("도란에게 이동 중...")
            
            try: main_window.nav_overlay.movement_finished.disconnect(self._on_doran_arrived)
            except: pass
            main_window.nav_overlay.movement_finished.connect(self._on_doran_arrived)
            
            main_window.nav_overlay.start_npc_movement('도란')
            
            self.btn_start.setText("이동/구매 중지")
            self.btn_start.setStyleSheet("background-color: #f44336; color: white; padding: 10px;")
            
    def _on_doran_arrived(self):
         try: self.window().nav_overlay.movement_finished.disconnect(self._on_doran_arrived)
         except: pass
         self._run_worker()

    def _run_worker(self):
        # (기존 disconnect 제거됨 - _on_doran_arrived에서 처리)
        
        if self.sct is None: self.sct = mss.mss()
        self.worker = PurchaseWorker(self.config, self.spin_qty.value(), self.sct)
        self.worker.progress_signal.connect(lambda m: self.lbl_status.setText(m))
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        
    def on_finished(self, success, msg):
        self.btn_start.setText("구매 시작")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        col = "green" if success else "red"
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet(f"color: {col}")
        self.task_finished.emit(success, msg)

    def start_purchase_auto(self):
        self.start_purchase()

    def load_config(self, data):
        if not data: return
        self.config.update(data)
        if self.config['confirm_img']: self.lbl_img.setText(os.path.basename(self.config['confirm_img']))
        pos = self.config['purchase_pos']
        self.lbl_coord.setText(f"({pos['x']}, {pos['y']})")
        self.spin_qty.setValue(self.config.get('qty', 100))
        self.spin_min_qty.setValue(self.config.get('min_qty', 0))
        
    def get_config(self):
        self.config['qty'] = self.spin_qty.value()
        self.config['min_qty'] = self.spin_min_qty.value()
        return self.config

class PacketSignal(QObject):
    received = pyqtSignal(str)

class PacketWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.sniffer = None
        self.packet_signal = PacketSignal()
        self.packet_signal.received.connect(self.append_log)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("패킷 캡쳐 시작")
        self.btn_start.clicked.connect(self.start_capture)
        self.btn_stop = QPushButton("패킷 캡쳐 종료")
        self.btn_stop.clicked.connect(self.stop_capture)
        self.btn_stop.setEnabled(False)
        self.btn_clear = QPushButton("기록 초기화")
        self.btn_clear.clicked.connect(self.clear_log)
        
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_clear)
        layout.addLayout(btn_layout)
        
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        # 폰트 설정
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.text_log.setFont(font)
        layout.addWidget(self.text_log)
        
        self.setLayout(layout)
        
    def start_capture(self):
        if not SCAPY_AVAILABLE:
            self.append_log("Error: scapy 모듈이 설치되지 않았습니다.")
            return

        self.append_log(">>> 캡쳐 시작 (Port 32800)...")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        
        try:
            self.sniffer = AsyncSniffer(
                filter="port 32800",
                prn=self.process_packet,
                store=0
            )
            self.sniffer.start()
        except Exception as e:
            self.append_log(f"Error: {e} (Npcap 설치 필요)")
            self.stop_capture()
            
    def stop_capture(self):
        if self.sniffer:
            self.sniffer.stop()
            self.sniffer = None
        self.append_log(">>> 캡쳐 종료.")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        
    def clear_log(self):
        self.text_log.clear()
        
    def process_packet(self, packet):
        if packet.haslayer(Raw):
            load = bytes(packet[Raw].load)
            try:
                text = load.decode('utf-8', errors='replace')
            except:
                text = str(load)
            
            # "사이다주문" 텍스트가 포함된 패킷만 처리
            if "사이다주문" not in text:
                return

            # 한글, 영어, 숫자만 표시, 나머지는 '-'
            filtered = re.sub(r'[^가-힣a-zA-Z0-9\s]', '-', text)
            
            # 연속된 하이픈 압축: ---- -> (-4)
            filtered = re.sub(r'-{2,}', lambda m: f"(-{len(m.group(0))})", filtered)
            
            msg = f"[{len(load)}B] {filtered}"
            self.packet_signal.received.emit(msg)

    def append_log(self, text):
        self.text_log.append(text)
        sb = self.text_log.verticalScrollBar()
        sb.setValue(sb.maximum())

class NavigationOverlay(QWidget):
    """목적지 맵 선택, 좌표 입력, 자동 이동 기능이 있는 오버레이"""
    
    # 핫키에서 Qt 스레드로 안전하게 시그널 전달
    toggle_signal = pyqtSignal()
    movement_finished = pyqtSignal() # 이동 완료 시그널
    delivery_requested = pyqtSignal(str, int, str) # 배송 요청 (닉네임, 수량, 가격)
    
    def __init__(self):
        super().__init__()
        # 마우스 입력을 받을 수 있도록 WindowTransparentForInput 제거
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(0, 0, 300, 320)  # NPC 버튼 추가로 높이 증가
        
        # 현재 상태
        self.current_map = None
        self.current_x = 0
        self.current_y = 0
        self.available_maps = []  # [{'name': str, 'path': str, 'portals': {target_map: x_coord}}]
        
        # NPC 데이터: {npc_name: {'image_path': str, 'map': str, 'x': int}}
        self.npc_data = {}
        
        # 자동 이동 상태
        self.is_moving = False
        self.target_x = 0
        self.target_map = None
        self.tolerance = 10  # ±10 허용 오차
        
        # 다른 맵 이동 상태
        self.move_phase = 'idle'  # 'idle', 'to_portal', 'entering_portal', 'waiting_map_change', 'to_destination', 'npc_moving'
        self.portal_x = None
        self.portal_enter_timer = None
        self.map_change_timeout = 0
        
        # NPC 이동 상태
        self.target_npc = None  # 현재 이동 중인 NPC 이름
        self.npc_template = None  # NPC 이미지 템플릿 (cv2 형식)
        self.npc_detect_threshold = 0.7  # NPC 감지 임계값
        self.sct = None  # mss 스크린샷 객체
        
        # 타이머 (이동 상태 체크용)
        self.move_timer = QTimer()
        self.move_timer.timeout.connect(self.check_movement)
        
        # 키 상태 추적
        self.key_pressed = None  # 'left' or 'right' or None
        
        # F4 핫키 시그널 연결
        self.toggle_signal.connect(self.toggle_movement)
        self.hotkey_registered = False
        
        # UI 구성
        self.init_ui()
        
        # 드래그 이동용
        self.drag_position = None
        
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(8)
        
        # 현재 좌표 표시
        self.lbl_info = QLabel("Map: None\nX: 0, Y: 0")
        self.lbl_info.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        self.lbl_info.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.lbl_info)
        
        # 구분선
        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: rgba(255,255,255,0.3);")
        main_layout.addWidget(separator)
        
        # 목적지 맵 선택
        map_layout = QHBoxLayout()
        lbl_dest_map = QLabel("목적지:")
        lbl_dest_map.setStyleSheet("color: white; font-size: 12px;")
        lbl_dest_map.setFixedWidth(50)
        self.combo_map = QComboBox()
        self.combo_map.setStyleSheet("""
            QComboBox {
                background-color: rgba(60, 60, 60, 200);
                color: white;
                border: 1px solid rgba(255,255,255,0.3);
                border-radius: 4px;
                padding: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: rgba(60, 60, 60, 230);
                color: white;
                selection-background-color: rgba(100, 100, 255, 200);
            }
        """)
        map_layout.addWidget(lbl_dest_map)
        map_layout.addWidget(self.combo_map)
        main_layout.addLayout(map_layout)
        
        # 목적지 X 좌표 입력
        coord_layout = QHBoxLayout()
        lbl_x = QLabel("X 좌표:")
        lbl_x.setStyleSheet("color: white; font-size: 12px;")
        lbl_x.setFixedWidth(50)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(-10000, 10000)
        self.spin_x.setValue(0)
        self.spin_x.setStyleSheet("""
            QSpinBox {
                background-color: rgba(60, 60, 60, 200);
                color: white;
                border: 1px solid rgba(255,255,255,0.3);
                border-radius: 4px;
                padding: 4px;
            }
        """)
        coord_layout.addWidget(lbl_x)
        coord_layout.addWidget(self.spin_x)
        main_layout.addLayout(coord_layout)
        
        # 이동 버튼
        self.btn_move = QPushButton("이동")
        self.btn_move.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 150, 50, 200);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(70, 180, 70, 200);
            }
            QPushButton:pressed {
                background-color: rgba(40, 120, 40, 200);
            }
            QPushButton:disabled {
                background-color: rgba(100, 100, 100, 200);
            }
        """)
        self.btn_move.clicked.connect(self.toggle_movement)
        main_layout.addWidget(self.btn_move)
        
        # 상태 표시
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #aaa; font-size: 11px;")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        main_layout.addWidget(self.lbl_status)
        
        # 구분선 2
        separator2 = QLabel()
        separator2.setFixedHeight(1)
        separator2.setStyleSheet("background-color: rgba(255,255,255,0.3);")
        main_layout.addWidget(separator2)
        
        # NPC 이동 섹션
        npc_label = QLabel("NPC 이동")
        npc_label.setStyleSheet("color: white; font-size: 12px; font-weight: bold;")
        npc_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(npc_label)
        
        # NPC 버튼들
        npc_btn_layout = QHBoxLayout()
        npc_btn_style = """
            QPushButton {
                background-color: rgba(100, 100, 200, 200);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(120, 120, 220, 200);
            }
            QPushButton:pressed {
                background-color: rgba(80, 80, 180, 200);
            }
            QPushButton:disabled {
                background-color: rgba(100, 100, 100, 200);
            }
        """
        
        self.npc_buttons = {}
        for npc_name in NPC_LIST:
            btn = QPushButton(npc_name)
            btn.setStyleSheet(npc_btn_style)
            btn.clicked.connect(lambda checked, name=npc_name: self.start_npc_movement(name))
            npc_btn_layout.addWidget(btn)
            self.npc_buttons[npc_name] = btn
        
        main_layout.addLayout(npc_btn_layout)
        
        # ── 배송 시스템 ──
        delivery_section = QLabel("── 배송 시스템 ──")
        delivery_section.setStyleSheet("color: white; font-weight: bold; margin-top: 10px;")
        delivery_section.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(delivery_section)
        
        # 사이다 수량 (현재 재고)
        stock_layout = QHBoxLayout()
        stock_label = QLabel("사이다 재고:")
        stock_label.setStyleSheet("color: white;")
        self.spin_cider_stock = QSpinBox()
        self.spin_cider_stock.setRange(0, 10000)
        self.spin_cider_stock.setStyleSheet("background-color: #444; color: white; padding: 4px; border-radius: 4px;") 
        stock_layout.addWidget(stock_label)
        stock_layout.addWidget(self.spin_cider_stock)
        main_layout.addLayout(stock_layout)
        
        # 입력 폼
        form_layout = QGridLayout()
        self.edit_nickname = QLineEdit()
        self.edit_nickname.setPlaceholderText("닉네임")
        self.spin_delivery_qty = QSpinBox()
        self.spin_delivery_qty.setRange(1, 1000)
        self.spin_delivery_qty.setPrefix("수량: ")
        self.edit_price = QLineEdit()
        self.edit_price.setPlaceholderText("가격")
        
        # 스타일
        input_style = "background-color: #444; color: white; padding: 4px; border: 1px solid #666; border-radius: 4px;"
        self.edit_nickname.setStyleSheet(input_style)
        self.spin_delivery_qty.setStyleSheet(input_style)
        self.edit_price.setStyleSheet(input_style)
        
        form_layout.addWidget(self.edit_nickname, 0, 0, 1, 2)
        form_layout.addWidget(self.spin_delivery_qty, 1, 0)
        form_layout.addWidget(self.edit_price, 1, 1)
        main_layout.addLayout(form_layout)
        
        # 배송 요청 버튼
        self.btn_request_delivery = QPushButton("배송 요청")
        self.btn_request_delivery.setStyleSheet("background-color: #2196F3; color: white; padding: 8px; font-weight: bold; border-radius: 4px;")
        self.btn_request_delivery.clicked.connect(self.on_request_delivery_click)
        main_layout.addWidget(self.btn_request_delivery)
        
        self.setLayout(main_layout)
        self.resize(300, 600)
    
    def on_request_delivery_click(self):
        nick = self.edit_nickname.text()
        qty = self.spin_delivery_qty.value()
        price = self.edit_price.text()
        if not nick:
             self.lbl_status.setText("닉네임을 입력하세요")
             return
        self.lbl_status.setText("1초 후 배송 요청...")
        self.btn_request_delivery.setEnabled(False)
        QTimer.singleShot(1000, lambda: self._emit_delivery_request(nick, qty, price))
        
    def _emit_delivery_request(self, nick, qty, price):
        self.btn_request_delivery.setEnabled(True)
        self.delivery_requested.emit(nick, qty, price)

    def on_request_delivery_click(self):
        nick = self.edit_nickname.text()
        qty = self.spin_delivery_qty.value()
        price = self.edit_price.text()
        if not nick:
             self.lbl_status.setText('닉네임을 입력하세요')
             return
        self.lbl_status.setText('1초 후 배송 요청...')
        self.btn_request_delivery.setEnabled(False)
        QTimer.singleShot(1000, lambda: self._emit_delivery_request(nick, qty, price))
        
    def _emit_delivery_request(self, nick, qty, price):
        self.btn_request_delivery.setEnabled(True)
        self.delivery_requested.emit(nick, qty, price)

    def set_available_maps(self, maps):
        """사용 가능한 맵 목록 설정"""
        self.available_maps = maps
        self.combo_map.clear()
        
        self.hidden_portals = {}
        for m in maps:
            self.combo_map.addItem(m['name'])
            if 'hidden_portal' in m and m['hidden_portal']:
                self.hidden_portals[m['name']] = m['hidden_portal']
    
    def update_coords(self, name, x, y):
        """현재 좌표 업데이트"""
        prev_map = self.current_map
        self.current_map = name
        self.current_x = x
        self.current_y = y
        self.lbl_info.setText(f"Map: {name}\nX: {x}, Y: {y}")
        self.adjustSize()
        
        # 맵 변경 감지 (포탈 이동 중일 때)
        if self.is_moving and self.move_phase == 'waiting_map_change':
            if prev_map != name and name == self.target_map:
                # 목적지 맵에 도착함
                if self.target_npc:
                    # NPC 이동 중이면 npc_moving 단계로
                    self.move_phase = 'npc_moving'
                    self.lbl_status.setText(f"맵 변경 완료! {self.target_npc}을(를) 찾아 이동 중...")
                else:
                    self.move_phase = 'to_destination'
                    self.lbl_status.setText(f"맵 변경 완료! 목적지로 이동 중...")
    
    def toggle_movement(self):
        """이동 시작/중지 토글"""
        if self.is_moving:
            self.stop_movement()
        else:
            # 1초 딜레이 후 이동 시작
            self.btn_move.setEnabled(False)
            self.lbl_status.setText("1초 후 이동...")
            QTimer.singleShot(1000, self._delayed_start_movement)
    
    def _delayed_start_movement(self):
        """딜레이 후 이동 시작"""
        self.btn_move.setEnabled(True)
        self.start_movement()
    
    def get_portal_coord(self, from_map, to_map):
        """현재 맵에서 목적지 맵으로 가는 포탈 좌표 반환"""
        for m in self.available_maps:
            if m['name'] == from_map:
                portals = m.get('portals', {})
                if to_map in portals:
                    return portals[to_map]
        return None
    
    def set_npc_data(self, npc_data):
        """NPC 데이터 설정"""
        self.npc_data = npc_data
        # NPC 버튼 활성화/비활성화
        for npc_name, btn in self.npc_buttons.items():
            if npc_name in npc_data and npc_data[npc_name].get('image_path'):
                btn.setEnabled(True)
            else:
                btn.setEnabled(False)
    
    def start_npc_movement(self, npc_name, click=True):
        """NPC로의 이동 시작"""
        self.auto_click_npc = click
        if self.is_moving:
            self.stop_movement()
            return
        
        # 1초 딜레이 후 이동 시작
        self.lbl_status.setText(f"1초 후 {npc_name}으로 이동...")
        for btn in self.npc_buttons.values():
            btn.setEnabled(False)
        self.btn_move.setEnabled(False)
        QTimer.singleShot(1000, lambda: self._delayed_start_npc_movement(npc_name))
    
    def _delayed_start_npc_movement(self, npc_name):
        """딜레이 후 NPC 이동 시작"""
        # 버튼 복원
        self.btn_move.setEnabled(True)
        for name, btn in self.npc_buttons.items():
            if name in self.npc_data and self.npc_data[name].get('image_path'):
                btn.setEnabled(True)
        
        if npc_name not in self.npc_data:
            self.lbl_status.setText(f"{npc_name} NPC가 설정되지 않았습니다")
            return
        
        npc_info = self.npc_data[npc_name]
        if not npc_info.get('image_path') or not os.path.exists(npc_info['image_path']):
            self.lbl_status.setText(f"{npc_name} 이미지가 없습니다")
            return
        
        if not npc_info.get('map'):
            self.lbl_status.setText(f"{npc_name} 위치가 설정되지 않았습니다")
            return
        
        # NPC 템플릿 이미지 로드
        try:
            img_array = np.fromfile(npc_info['image_path'], np.uint8)
            self.npc_template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if self.npc_template is None:
                self.lbl_status.setText(f"{npc_name} 이미지 로드 실패")
                return
        except Exception as e:
            self.lbl_status.setText(f"이미지 로드 오류: {str(e)}")
            return
        
        # mss 초기화
        if self.sct is None:
            self.sct = mss.mss()
        
        self.target_npc = npc_name
        self.target_map = npc_info['map']
        self.target_x = npc_info['x']
        
        # 현재 맵과 NPC 맵이 같은지 확인
        if self.current_map == self.target_map:
            self.move_phase = 'npc_moving'
            self.portal_x = None
        else:
            # 다른 맵으로 이동 - 포탈 좌표 확인
            portal_x = self.get_portal_coord(self.current_map, self.target_map)
            if portal_x is None:
                self.lbl_status.setText(f"'{self.current_map}'에서 '{self.target_map}'으로\\n포탈이 설정되지 않았습니다")
                return
            self.portal_x = portal_x
            self.move_phase = 'to_portal'
            self.map_change_timeout = 0
        
        self.is_moving = True
        self.btn_move.setEnabled(False)
        
        # NPC 버튼 스타일 변경
        for name, btn in self.npc_buttons.items():
            if name == npc_name:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(200, 50, 50, 200);
                        color: white;
                        border: none;
                        border-radius: 4px;
                        padding: 8px;
                        font-weight: bold;
                    }
                """)
            else:
                btn.setEnabled(False)
        
        if self.move_phase == 'to_portal':
            self.lbl_status.setText(f"{npc_name}을(를) 찾아 포탈로 이동 중...")
        else:
            self.lbl_status.setText(f"{npc_name}을(를) 찾아 이동 중...")
        
        # 이동 체크 타이머 시작
        self.move_timer.start(100)
        self.check_movement()
    
    def detect_npc(self):
        """화면에서 NPC 이미지 감지, 감지되면 위치 반환"""
        if self.npc_template is None or self.sct is None:
            return None
        
        try:
            # 전체 화면 캡처
            monitor = self.sct.monitors[1]
            screenshot = self.sct.grab(monitor)
            img_screen = np.array(screenshot)
            img_screen_bgr = cv2.cvtColor(img_screen, cv2.COLOR_BGRA2BGR)
            
            # 템플릿 매칭
            result = cv2.matchTemplate(img_screen_bgr, self.npc_template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= self.npc_detect_threshold:
                # NPC 발견! 중심 좌표 계산
                h, w = self.npc_template.shape[:2]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                return (center_x, center_y, max_val)
        except Exception as e:
            print(f"NPC 감지 오류: {e}")
        
        return None
    
    def click_npc(self, x, y):
        """NPC 클릭"""
        try:
            pyautogui.click(x, y)
            self.lbl_status.setText(f"{self.target_npc} 클릭 완료!")
        except Exception as e:
            self.lbl_status.setText(f"클릭 오류: {str(e)}")
    
    def start_movement(self):
        """자동 이동 시작"""
        if self.combo_map.currentText() == "":
            self.lbl_status.setText("목적지 맵을 선택하세요")
            return
        
        self.target_map = self.combo_map.currentText()
        self.target_x = self.spin_x.value()
        
        # 현재 맵과 목적지 맵이 같은지 확인
        if self.current_map == self.target_map:
            # 같은 맵 내 이동
            self.move_phase = 'to_destination'
            self.portal_x = None
        else:
            # 다른 맵으로 이동 - 포탈 좌표 확인
            portal_x = self.get_portal_coord(self.current_map, self.target_map)
            if portal_x is None:
                self.lbl_status.setText(f"'{self.current_map}'에서 '{self.target_map}'으로 가는\n포탈이 설정되지 않았습니다")
                return
            self.portal_x = portal_x
            self.move_phase = 'to_portal'
            self.map_change_timeout = 0
        
        self.is_moving = True
        self.btn_move.setText("정지")
        self.btn_move.setStyleSheet("""
            QPushButton {
                background-color: rgba(200, 50, 50, 200);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(220, 70, 70, 200);
            }
        """)
        
        if self.move_phase == 'to_portal':
            self.lbl_status.setText(f"포탈로 이동 중... (X={self.portal_x})")
        else:
            self.lbl_status.setText(f"이동 중... 목표: X={self.target_x}")
        
        # 이동 체크 타이머 시작 (100ms 간격)
        self.move_timer.start(100)
        
        # 초기 방향 결정 및 키 누르기
        self.check_movement()
    
    def stop_movement(self):
        """자동 이동 중지"""
        self.is_moving = False
        self.move_phase = 'idle'
        self.move_timer.stop()
        self.portal_x = None
        self.map_change_timeout = 0
        
        # NPC 관련 상태 초기화
        self.target_npc = None
        self.npc_template = None
        self.waiting_for_click = False
        
        # 눌린 키 해제
        self.release_key()
        
        self.btn_move.setText("이동")
        self.btn_move.setEnabled(True)
        self.btn_move.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 150, 50, 200);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(70, 180, 70, 200);
            }
        """)
        
        # NPC 버튼 복원
        npc_btn_style = """
            QPushButton {
                background-color: rgba(100, 100, 200, 200);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(120, 120, 220, 200);
            }
            QPushButton:pressed {
                background-color: rgba(80, 80, 180, 200);
            }
            QPushButton:disabled {
                background-color: rgba(100, 100, 100, 200);
            }
        """
        for npc_name, btn in self.npc_buttons.items():
            btn.setStyleSheet(npc_btn_style)
            # 활성화 상태는 NPC 데이터에 따라 결정
            if npc_name in self.npc_data and self.npc_data[npc_name].get('image_path'):
                btn.setEnabled(True)
            else:
                btn.setEnabled(False)
        
        self.lbl_status.setText("이동 중지됨")
    
    def check_movement(self):
        """이동 상태 체크 및 방향키 제어"""
        if not self.is_moving:
            return
        

        
        if self.move_phase == 'to_portal':
            # 포탈로 이동 중
            
            # 히든 포탈 로직
            if hasattr(self, 'hidden_portals') and self.current_map in self.hidden_portals:
                 hp_target = self._check_hidden_portal_path(self.current_x, self.portal_x)
                 if hp_target is not None:
                     self.move_phase = 'to_hidden_portal'
                     self.target_hp_x = hp_target
                     self.lbl_status.setText(f"히든 포탈로 이동 중 (목표:{hp_target})")
                     return

            self._move_to_x(self.portal_x, on_arrive=self._enter_portal)
        
        elif self.move_phase == 'entering_portal':
            # 포탈 진입 중 (윗방향키 입력 후 대기)
            pass  # 타이머로 처리됨
        
        elif self.move_phase == 'waiting_map_change':
            # 맵 변경 대기 중
            self.map_change_timeout += 100
            if self.map_change_timeout > 5000:  # 5초 타임아웃
                self.lbl_status.setText("맵 변경 타임아웃")
                self.stop_movement()
                return
            self.lbl_status.setText(f"맵 변경 대기 중... ({self.map_change_timeout//1000}s)")
        
        elif self.move_phase == 'to_destination':
            # 목적지로 이동 중
            if self.current_map != self.target_map:
                # 아직 맵이 안 바뀜 (같은 맵 이동의 경우 이 조건은 false)
                if self.portal_x is not None:
                    self.lbl_status.setText("목적지 맵으로 이동 중 오류")
                    self.stop_movement()
                    return
            
            # 히든 포탈 로직
            if hasattr(self, 'hidden_portals') and self.current_map in self.hidden_portals:
                 hp_target = self._check_hidden_portal_path(self.current_x, self.target_x)
                 if hp_target is not None:
                     self.move_phase = 'to_hidden_portal'
                     self.target_hp_x = hp_target
                     self.lbl_status.setText(f"히든 포탈로 이동 중 (목표:{hp_target})")
                     return

            self._move_to_x(self.target_x, on_arrive=self._arrive_destination)
        
        elif self.move_phase == 'npc_moving':
            # NPC로 이동 중 (같은 맵 내)
            if hasattr(self, 'hidden_portals') and self.current_map in self.hidden_portals:
                 hp_target = self._check_hidden_portal_path(self.current_x, self.target_x)
                 if hp_target is not None:
                     self.move_phase = 'to_hidden_portal'
                     self.target_hp_x = hp_target
                     self.lbl_status.setText(f"히든 포탈로 이동 중 (목표:{hp_target})")
                     return
            self._move_to_x(self.target_x, on_arrive=self._arrive_destination)
            
        elif self.move_phase == 'to_hidden_portal':
             self._move_to_x(self.target_hp_x, on_arrive=self._enter_hidden_portal)
             
        elif self.move_phase == 'using_hidden_portal':
             pass

    
    def _click_and_stop(self, x, y):
        """NPC 클릭 후 이동 중지"""
        self.waiting_for_click = False  # 플래그 초기화
        self.click_npc(x, y)
        self.stop_movement()
        self.movement_finished.emit()
    
    def _move_to_x(self, target_x, on_arrive=None):
        """특정 X 좌표로 이동"""
        # 현재 좌표가 없으면(미니맵 인식 실패 등) 대기
        if self.current_x == 0:
            return
            
        diff = target_x - self.current_x
        
        # 목적지에 도달했는지 확인 (±tolerance)
        if abs(diff) <= self.tolerance:
            self.release_key()
            if on_arrive:
                on_arrive()
            else:
                self.stop_movement()
            return
        
        # 방향 결정
        # 방향 결정
        debug_info = getattr(self, 'path_debug_info', '')
        if diff > 0:
            # 오른쪽으로 이동해야 함
            if self.key_pressed != 'right':
                self.release_key()
                pyautogui.keyDown('right')
                self.key_pressed = 'right'
            self.lbl_status.setText(f"→ 이동 중 (목표: {target_x}, 현재: {self.current_x})\n{debug_info}")
        else:
            # 왼쪽으로 이동해야 함
            if self.key_pressed != 'left':
                self.release_key()
                pyautogui.keyDown('left')
                self.key_pressed = 'left'
            self.lbl_status.setText(f"← 이동 중 (목표: {target_x}, 현재: {self.current_x})\n{debug_info}")
    
    def _enter_portal(self):
        """포탈 진입 (윗방향키 입력)"""
        self.move_phase = 'entering_portal'
        self.lbl_status.setText("포탈 진입 중...")
        
        # 윗방향키 입력
        pyautogui.press('up')
        
        # 잠시 후 맵 변경 대기 상태로 전환
        QTimer.singleShot(500, self._wait_for_map_change)
    
    def _enter_hidden_portal(self):
        """히든 포탈 진입"""
        self.move_phase = 'using_hidden_portal'
        self.lbl_status.setText("히든 포탈 이용 중...")
        self.release_key() # 멈춤
        pyautogui.press('up')
        # 이동 후 좌표 갱신 대기
        QTimer.singleShot(200, self._after_hidden_portal)
        
    def _after_hidden_portal(self):
        # 히든 포탈 이용 완료 후 다시 목적지로
        if not self.is_moving: return
        
        # 맵이 아직 다르면 포탈로 가야 함
        if self.current_map != self.target_map:
             self.move_phase = 'to_portal'
        elif self.target_npc:
             self.move_phase = 'npc_moving'
        else:
             self.move_phase = 'to_destination'
             
    def _check_hidden_portal_path(self, current_x, target_x):
        hp = self.hidden_portals[self.current_map]
        h1, h2 = hp['x1'], hp['x2']
        
        # 직접 이동 거리
        direct_dist = abs(target_x - current_x)
        
        # h1 이용 거리: cur -> h1 ... (teleport to h2) ... h2 -> target
        via_h1 = abs(h1 - current_x) + abs(target_x - h2)
        
        # h2 이용 거리: cur -> h2 ... (teleport to h1) ... h1 -> target
        via_h2 = abs(h2 - current_x) + abs(target_x - h1)
        
        # 디버그 정보
        self.path_debug_info = f"[거리] 직접:{direct_dist} / H1경유:{via_h1} / H2경유:{via_h2}"

        # 최소 거리 찾기
        if via_h1 < direct_dist and via_h1 < via_h2:
             return h1
        elif via_h2 < direct_dist:
             return h2
             
        return None

    def _wait_for_map_change(self):
        """맵 변경 대기 시작"""
        if not self.is_moving:
            return
        self.move_phase = 'waiting_map_change'
        self.map_change_timeout = 0
        self.lbl_status.setText("맵 변경 대기 중...")
    
    def _arrive_destination(self):
        """목적지 도착"""
        self.lbl_status.setText(f"도착! (X={self.current_x})")
        
        # NPC 이동 중이었다면 도착해서도 1초 후 한번 더 이미지 찾고 클릭 시도
        if self.target_npc:
            if getattr(self, 'auto_click_npc', True):
                self.lbl_status.setText(f"도착! 1초 후 {self.target_npc} 찾기...")
                self.release_key()
                QTimer.singleShot(1000, self._try_click_npc_after_arrive)
            else:
                self.lbl_status.setText(f"{self.target_npc} 앞 대기")
                self.stop_movement()
        else:
            self.stop_movement()

    def _try_click_npc_after_arrive(self):
        """도착 후 NPC 찾아서 클릭 시도"""
        if not self.is_moving: return
        
        npc_result = self.detect_npc()
        if npc_result:
            x, y, conf = npc_result
            self._click_and_stop(x, y)
        else:
            self.lbl_status.setText(f"{self.target_npc} 못 찾음 (도착 완료)")
            self.stop_movement()
    
    def release_key(self):
        """눌린 키 해제"""
        if self.key_pressed == 'left':
            pyautogui.keyUp('left')
        elif self.key_pressed == 'right':
            pyautogui.keyUp('right')
        self.key_pressed = None
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw rounded rect with semi-transparent black background
        painter.setBrush(QColor(0, 0, 0, 180))  # 더 진한 배경
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        painter.drawRoundedRect(self.rect(), 10, 10)
    
    # 드래그로 오버레이 이동
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_position:
            self.move(event.globalPos() - self.drag_position)
            event.accept()
    
    def mouseReleaseEvent(self, event):
        self.drag_position = None
    
    def showEvent(self, event):
        """오버레이가 표시될 때 F4 핫키 등록"""
        super().showEvent(event)
        if not self.hotkey_registered:
            keyboard.add_hotkey('F4', self._on_hotkey)
            self.hotkey_registered = True
    
    def hideEvent(self, event):
        """오버레이가 숨겨질 때 F4 핫키 해제"""
        super().hideEvent(event)
        self._unregister_hotkey()
    
    def _on_hotkey(self):
        """F4 핫키가 눌렸을 때 (다른 스레드에서 호출됨)"""
        self.toggle_signal.emit()
    
    def _unregister_hotkey(self):
        """핫키 해제"""
        if self.hotkey_registered:
            try:
                keyboard.remove_hotkey('F4')
            except:
                pass
            self.hotkey_registered = False
    
    def closeEvent(self, event):
        # 창 닫힐 때 키 해제 및 핫키 해제
        self.release_key()
        self.move_timer.stop()
        self._unregister_hotkey()
        super().closeEvent(event)

class OrderWidget(QWidget):
    order_added = pyqtSignal(object, str) # QTreeWidgetItem, nickname

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 트리 위젯 생성
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["시간", "채널", "닉네임", "상태", "메시지"])
        # 컬럼 너비 조정
        self.tree.setColumnWidth(0, 150) # 시간
        self.tree.setColumnWidth(1, 60)  # 채널
        self.tree.setColumnWidth(2, 100) # 닉네임
        self.tree.setColumnWidth(3, 60)  # 상태
        
        layout.addWidget(self.tree)
        self.setLayout(layout)

    def process_packet_text(self, msg):
        """
        패킷 텍스트 처리: 
        메시지 포맷: [size] filtered_text
        filtered_text 포맷: <닉네임>(-n)<채널>(-n)<메시지>(-n)
        단, filtered_text에는 "사이다주문"이 포함되어 있음.
        """
        # [..B] 포맷 제거하고 텍스트만 추출
        match = re.search(r'\[\d+B\] (.+)', msg)
        if not match:
            return
            
        text = match.group(1)
        
        # "사이다주문" 체크 (이미 PacketWidget에서 했지만 안전하게 한번 더)
        if "사이다주문" not in text:
            return

        # 구분자 (-숫자)로 분리 (정규식 특수문자 이스케이프 주의)
        # gui.py의 압축 로직: lambda m: f"(-{len(m.group(0))})" -> '(-4)' 형태.
        # 따라서 regex: \(\-\d+\)
        
        parts = re.split(r'\(\-\d+\)', text)
        
        # 비어있는 문자열 제거 (공백만 있는 경우 제거하지 않음, 데이터가 중요하므로.)
        # 앞뒤 공백 제거 후 빈 문자열이 아닌 것만 남김
        parts = [p.strip() for p in parts if p.strip()]
        
        # "사이다주문" 이 포함된 파트를 찾아서 그 앞의 파트들을 닉네임, 채널로 인식
        target_idx = -1
        for i, part in enumerate(parts):
            if "사이다주문" in part:
                target_idx = i
                break
                
        if target_idx >= 2:
            nickname = parts[target_idx-2]
            channel = parts[target_idx-1]
            message = parts[target_idx]
            
            self.add_order(channel, nickname, message)

    def add_order(self, channel, nickname, message):
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        # 상태: 대기 (기본값)
        item = QTreeWidgetItem([timestamp, channel, nickname, "대기", message])
        self.tree.addTopLevelItem(item)
        # 스크롤 최하단으로 이동
        self.tree.scrollToItem(item)
        
        # 메인 윈도우에 알림
        self.order_added.emit(item, nickname)

class MainWindow(QMainWindow):
    def __init__(self, tracker):
        super().__init__()
        self.tracker = tracker
        self.nav_overlay = NavigationOverlay()  # InfoOverlay → NavigationOverlay
        self.detection_overlay = DetectionOverlay()
        
        # State for persistence
        self.current_maps = [] # List of {'name': str, 'path': str, 'portals': {target_map: x_coord}}
        self.current_char_path = None
        self.current_search_region = None
        self.current_search_region = None
        self.show_detection_overlay = True  # 미니맵 감지 오버레이 표시 여부
        self.delivery_queue = [] # 대기열 목록
        self.pending_delivery = None # 현재 진행 중인 배송 작업
        
        # NPC 데이터: {npc_name: {'image_path': str, 'map': str, 'x': int}}
        self.npc_data = {npc: {'image_path': '', 'map': '', 'x': 0} for npc in NPC_LIST}
        
        self.delivery_widget = DeliveryWidget()
        self.purchase_widget = PurchaseWidget()
        self.packet_widget = PacketWidget()
        self.order_widget = OrderWidget()
        
        self.initUI()
        self.connect_signals()
        self.load_config()
        
    def initUI(self):
        self.setWindowTitle("Minimap Tracker")
        self.setGeometry(100, 100, 350, 850)
        
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 탭 위젯 생성
        self.tabs = QTabWidget()
        
        # === 홈 탭 (기존 기능) ===
        home_scroll = QScrollArea()
        home_scroll.setWidgetResizable(True)
        home_widget = QWidget()
        layout = QVBoxLayout()
        
        # Region Selection
        self.btn_select_region = QPushButton("Select Search Area")
        self.lbl_region = QLabel("Search Area: Full Screen")
        
        # Map Loading
        self.btn_load_map = QPushButton("Add Minimap (Max 2)")
        self.btn_clear_maps = QPushButton("Clear Maps")
        self.lbl_map = QLabel("Maps: None")
        
        # Map Visualization (Removed)
        # self.map_viz = MapVizWidget()
        
        # Image Loading
        self.btn_load_image = QPushButton("Load Character Image")
        self.lbl_image_status = QLabel("Image: None")
        
        # Main window coords (optional, since we have overlay)
        self.lbl_coords = QLabel("Relative Coordinates: (0, 0)")
        
        # Portal Settings Section
        portal_section = QLabel("── 포탈 설정 ──")
        portal_section.setStyleSheet("font-weight: bold; color: #666;")
        portal_section.setAlignment(Qt.AlignCenter)
        
        # Source map selection
        portal_src_layout = QHBoxLayout()
        lbl_src = QLabel("출발 맵:")
        lbl_src.setFixedWidth(60)
        self.combo_portal_src = QComboBox()
        self.combo_portal_src.currentIndexChanged.connect(self.on_portal_src_changed)
        portal_src_layout.addWidget(lbl_src)
        portal_src_layout.addWidget(self.combo_portal_src)
        
        # Destination map selection
        portal_dst_layout = QHBoxLayout()
        lbl_dst = QLabel("도착 맵:")
        lbl_dst.setFixedWidth(60)
        self.combo_portal_dst = QComboBox()
        portal_dst_layout.addWidget(lbl_dst)
        portal_dst_layout.addWidget(self.combo_portal_dst)
        
        # Portal X coordinate
        portal_x_layout = QHBoxLayout()
        lbl_portal_x = QLabel("포탈 X:")
        lbl_portal_x.setFixedWidth(60)
        self.spin_portal_x = QSpinBox()
        self.spin_portal_x.setRange(-10000, 10000)
        self.spin_portal_x.setValue(0)
        portal_x_layout.addWidget(lbl_portal_x)
        portal_x_layout.addWidget(self.spin_portal_x)
        
        # Portal save button
        self.btn_save_portal = QPushButton("포탈 저장")
        self.btn_save_portal.clicked.connect(self.save_portal)
        
        # Current portal info
        self.lbl_portal_info = QLabel("설정된 포탈: 없음")
        self.lbl_portal_info.setWordWrap(True)
        self.lbl_portal_info.setStyleSheet("color: #888; font-size: 10px;")
        
        # NPC Settings Section
        npc_section = QLabel("── NPC 설정 ──")
        npc_section.setStyleSheet("font-weight: bold; color: #666;")
        npc_section.setAlignment(Qt.AlignCenter)
        
        # NPC 선택
        npc_select_layout = QHBoxLayout()
        lbl_npc = QLabel("NPC:")
        lbl_npc.setFixedWidth(60)
        self.combo_npc = QComboBox()
        for npc_name in NPC_LIST:
            self.combo_npc.addItem(npc_name)
        self.combo_npc.currentIndexChanged.connect(self.on_npc_selected)
        npc_select_layout.addWidget(lbl_npc)
        npc_select_layout.addWidget(self.combo_npc)
        
        # NPC 이미지 선택
        npc_img_layout = QHBoxLayout()
        self.btn_npc_image = QPushButton("이미지 선택")
        self.btn_npc_image.clicked.connect(self.select_npc_image)
        self.lbl_npc_image = QLabel("없음")
        self.lbl_npc_image.setStyleSheet("color: #888;")
        npc_img_layout.addWidget(self.btn_npc_image)
        npc_img_layout.addWidget(self.lbl_npc_image)
        
        # NPC 맵 선택
        npc_map_layout = QHBoxLayout()
        lbl_npc_map = QLabel("맵:")
        lbl_npc_map.setFixedWidth(60)
        self.combo_npc_map = QComboBox()
        npc_map_layout.addWidget(lbl_npc_map)
        npc_map_layout.addWidget(self.combo_npc_map)
        
        # NPC X 좌표
        npc_x_layout = QHBoxLayout()
        lbl_npc_x = QLabel("X 좌표:")
        lbl_npc_x.setFixedWidth(60)
        self.spin_npc_x = QSpinBox()
        self.spin_npc_x.setRange(-10000, 10000)
        self.spin_npc_x.setValue(0)
        npc_x_layout.addWidget(lbl_npc_x)
        npc_x_layout.addWidget(self.spin_npc_x)
        
        # NPC 저장 버튼
        self.btn_save_npc = QPushButton("NPC 저장")
        self.btn_save_npc.clicked.connect(self.save_npc)
        
        # NPC 정보 표시
        self.lbl_npc_info = QLabel("설정된 NPC: 없음")
        self.lbl_npc_info.setWordWrap(True)
        self.lbl_npc_info.setStyleSheet("color: #888; font-size: 10px;")
        
        # Detection overlay toggle
        self.chk_detection_overlay = QCheckBox("미니맵 감지 영역 표시")
        self.chk_detection_overlay.setChecked(True)
        
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
        layout.addWidget(self.btn_clear_maps)
        layout.addWidget(self.lbl_map)
        # Hidden Portal Settings
        hp_section = QLabel("── 히든 포탈 설정 ──")
        hp_section.setStyleSheet("font-weight: bold; color: #666;")
        hp_section.setAlignment(Qt.AlignCenter)
        
        # 맵 선택 콤보박스
        hp_map_layout = QHBoxLayout()
        hp_map_layout.addWidget(QLabel("맵:"))
        self.combo_hp_map = QComboBox()
        self.combo_hp_map.currentIndexChanged.connect(self.on_hp_map_selected)
        hp_map_layout.addWidget(self.combo_hp_map)
        
        # 좌표 입력
        hp_coord_layout = QHBoxLayout()
        self.spin_hp_x1 = QSpinBox()
        self.spin_hp_x1.setRange(-10000, 10000)
        self.spin_hp_x1.setPrefix("X1: ")
        self.spin_hp_x2 = QSpinBox()
        self.spin_hp_x2.setRange(-10000, 10000)
        self.spin_hp_x2.setPrefix("X2: ")
        hp_coord_layout.addWidget(self.spin_hp_x1)
        hp_coord_layout.addWidget(self.spin_hp_x2)
        
        # 저장 버튼
        self.btn_save_hp = QPushButton("히든 포탈 저장")
        self.btn_save_hp.clicked.connect(self.save_hidden_portal)
        
        # 정보 라벨
        self.lbl_hp_info = QLabel("설정된 히든 포탈: 없음")
        self.lbl_hp_info.setStyleSheet("color: #888; font-size: 10px;")

        layout.addWidget(hp_section)
        layout.addLayout(hp_map_layout)
        layout.addLayout(hp_coord_layout)
        layout.addWidget(self.btn_save_hp)
        layout.addWidget(self.lbl_hp_info)
        layout.addSpacing(10)
        layout.addWidget(self.btn_load_image)
        layout.addWidget(self.lbl_image_status)
        layout.addSpacing(10)
        layout.addWidget(self.lbl_coords)
        layout.addSpacing(10)
        layout.addWidget(portal_section)
        layout.addLayout(portal_src_layout)
        layout.addLayout(portal_dst_layout)
        layout.addLayout(portal_x_layout)
        layout.addWidget(self.btn_save_portal)
        layout.addWidget(self.lbl_portal_info)
        layout.addSpacing(10)
        layout.addWidget(npc_section)
        layout.addLayout(npc_select_layout)
        layout.addLayout(npc_img_layout)
        layout.addLayout(npc_map_layout)
        layout.addLayout(npc_x_layout)
        layout.addWidget(self.btn_save_npc)
        layout.addWidget(self.lbl_npc_info)
        layout.addSpacing(10)
        layout.addWidget(self.chk_detection_overlay)
        layout.addStretch()
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        
        home_widget.setLayout(layout)
        home_scroll.setWidget(home_widget)
        
        self.tabs.addTab(home_scroll, "홈")
        self.tabs.addTab(self.delivery_widget, "배송")
        self.tabs.addTab(self.purchase_widget, "구매")
        self.tabs.addTab(self.packet_widget, "패킷")
        self.tabs.addTab(self.order_widget, "주문")
        
        main_layout.addWidget(self.tabs)
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def connect_signals(self):
        self.btn_select_region.clicked.connect(self.open_selection_overlay)
        self.btn_load_map.clicked.connect(self.load_map)
        self.btn_clear_maps.clicked.connect(self.clear_maps)
        self.btn_load_image.clicked.connect(self.load_image)
        self.btn_start.clicked.connect(self.start_tracking)
        self.btn_stop.clicked.connect(self.stop_tracking)
        self.chk_detection_overlay.stateChanged.connect(self.toggle_detection_overlay)
        
        self.tracker.position_update.connect(self.update_coordinates)
        self.tracker.status_update.connect(self.update_status)
        # self.tracker.map_dimensions.connect(self.map_viz.set_map_dimensions)
        self.tracker.map_region_update.connect(self.detection_overlay.update_region)
        
        self.nav_overlay.movement_finished.connect(self.on_movement_finished)
        self.nav_overlay.delivery_requested.connect(self.process_delivery_request)
        self.delivery_widget.task_finished.connect(self.on_delivery_task_finished)
        self.purchase_widget.task_finished.connect(self.on_purchase_task_finished)
        self.packet_widget.packet_signal.received.connect(self.order_widget.process_packet_text)
        self.order_widget.order_added.connect(self.handle_new_order)

    def open_selection_overlay(self):
        self.selector = SelectionOverlay()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.show()

    def on_region_selected(self, x, y, w, h):
        self.tracker.set_search_region(x, y, w, h)
        self.current_search_region = {'x': x, 'y': y, 'w': w, 'h': h}
        self.lbl_region.setText(f"Area: {x}, {y} ({w}x{h})")

    def load_map(self):
        if len(self.current_maps) >= 2:
            self.statusBar().showMessage("Max 2 maps allowed.")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Select Minimap Image", "", "Images (*.png *.jpg *.bmp)")
        if path:
            name, ok = QInputDialog.getText(self, "Map Name", "Enter name for this map:")
            if ok and name:
                slot = len(self.current_maps)
                if self.tracker.set_map_source(slot, name, path):
                    self.current_maps.append({'name': name, 'path': path, 'portals': {}})
                    self.update_map_label()
                    self.update_portal_combos()
                    self.update_npc_map_combo()
                    self.update_hp_map_combo()
                    self.nav_overlay.set_available_maps(self.current_maps)
                    self.check_ready()

    def clear_maps(self):
        self.tracker.clear_maps()
        self.current_maps = []
        self.update_map_label()
        self.update_portal_combos()
        self.update_npc_map_combo()
        self.update_hp_map_combo()
        self.nav_overlay.set_available_maps([])
        self.check_ready()

    def update_map_label(self):
        names = [m['name'] for m in self.current_maps]
        self.lbl_map.setText("Maps:\n" + "\n".join(names) if names else "Maps: None")
    
    def update_portal_combos(self):
        """포탈 설정 콤보박스 업데이트"""
        self.combo_portal_src.clear()
        self.combo_portal_dst.clear()
        for m in self.current_maps:
            self.combo_portal_src.addItem(m['name'])
            self.combo_portal_dst.addItem(m['name'])
        self.update_portal_info()
    
    def on_portal_src_changed(self, index):
        """출발 맵 선택 변경 시"""
        self.update_portal_info()
    
    def update_portal_info(self):
        """현재 선택된 맵의 포탈 정보 표시"""
        src_name = self.combo_portal_src.currentText()
        if not src_name:
            self.lbl_portal_info.setText("설정된 포탈: 없음")
            return
        
        for m in self.current_maps:
            if m['name'] == src_name:
                portals = m.get('portals', {})
                if portals:
                    info_lines = [f"  → {dst}: X={x}" for dst, x in portals.items()]
                    self.lbl_portal_info.setText(f"{src_name}의 포탈:\n" + "\n".join(info_lines))
                else:
                    self.lbl_portal_info.setText(f"{src_name}의 포탈: 없음")
                return
        self.lbl_portal_info.setText("설정된 포탈: 없음")
    
    def save_portal(self):
        """포탈 설정 저장"""
        src_name = self.combo_portal_src.currentText()
        dst_name = self.combo_portal_dst.currentText()
        portal_x = self.spin_portal_x.value()
        
        if not src_name or not dst_name:
            self.statusBar().showMessage("맵을 선택해주세요")
            return
        
        if src_name == dst_name:
            self.statusBar().showMessage("출발 맵과 도착 맵이 같습니다")
            return
        
        # 포탈 정보 저장
        for m in self.current_maps:
            if m['name'] == src_name:
                if 'portals' not in m:
                    m['portals'] = {}
                m['portals'][dst_name] = portal_x
                break
        
        self.update_portal_info()
        self.nav_overlay.set_available_maps(self.current_maps)
        self.save_config()
        self.statusBar().showMessage(f"포탈 저장: {src_name} → {dst_name} (X={portal_x})")
    
    def update_npc_map_combo(self):
        """NPC 맵 콤보박스 업데이트"""
        self.combo_npc_map.clear()
        for m in self.current_maps:
            self.combo_npc_map.addItem(m['name'])
    
    def on_npc_selected(self, index):
        """NPC 선택 변경 시 UI 업데이트"""
        npc_name = self.combo_npc.currentText()
        if not npc_name or npc_name not in self.npc_data:
            return
        
        npc_info = self.npc_data[npc_name]
        
        # 이미지 경로 표시
        if npc_info.get('image_path'):
            filename = os.path.basename(npc_info['image_path'])
            self.lbl_npc_image.setText(filename)
        else:
            self.lbl_npc_image.setText("없음")
        
        # 맵 선택
        if npc_info.get('map'):
            idx = self.combo_npc_map.findText(npc_info['map'])
            if idx >= 0:
                self.combo_npc_map.setCurrentIndex(idx)
        
        # X 좌표
        self.spin_npc_x.setValue(npc_info.get('x', 0))
        
        self.update_npc_info()
    
    def select_npc_image(self):
        """NPC 이미지 선택"""
        npc_name = self.combo_npc.currentText()
        if not npc_name:
            return
        
        path, _ = QFileDialog.getOpenFileName(self, f"{npc_name} 이미지 선택", "", "Images (*.png *.jpg *.bmp)")
        if path:
            self.npc_data[npc_name]['image_path'] = path
            self.lbl_npc_image.setText(os.path.basename(path))
            self.update_npc_info()
    
    def save_npc(self):
        """NPC 설정 저장"""
        npc_name = self.combo_npc.currentText()
        if not npc_name:
            self.statusBar().showMessage("NPC를 선택해주세요")
            return
        
        npc_map = self.combo_npc_map.currentText()
        npc_x = self.spin_npc_x.value()
        
        if not npc_map:
            self.statusBar().showMessage("맵을 선택해주세요")
            return
        
        self.npc_data[npc_name]['map'] = npc_map
        self.npc_data[npc_name]['x'] = npc_x
        
        self.update_npc_info()
        self.nav_overlay.set_npc_data(self.npc_data)
        self.save_config()
        self.statusBar().showMessage(f"NPC 저장: {npc_name} - {npc_map} (X={npc_x})")
    
    def update_npc_info(self):
        """NPC 정보 표시 업데이트"""
        info_lines = []
        for npc_name, npc_info in self.npc_data.items():
            if npc_info.get('image_path') and npc_info.get('map'):
                info_lines.append(f"  {npc_name}: {npc_info['map']} X={npc_info.get('x', 0)}")
        
        if info_lines:
            self.lbl_npc_info.setText("설정된 NPC:\\n" + "\\n".join(info_lines))
        else:
            self.lbl_npc_info.setText("설정된 NPC: 없음")

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
        else:
            self.btn_start.setEnabled(False)

    def start_tracking(self):
        self.save_config() # Save on start just in case
        self.tracker.start()
        self.nav_overlay.set_available_maps(self.current_maps)
        self.nav_overlay.set_npc_data(self.npc_data)
        self.nav_overlay.show()
        if self.show_detection_overlay:
            self.detection_overlay.show()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_load_map.setEnabled(False)
        self.btn_clear_maps.setEnabled(False)
        self.btn_load_image.setEnabled(False)
        self.btn_select_region.setEnabled(False)

    def stop_tracking(self):
        self.tracker.stop()
        self.nav_overlay.stop_movement()  # 이동 중이면 중지
        self.nav_overlay.hide()
        self.detection_overlay.hide()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_load_map.setEnabled(True)
        self.btn_clear_maps.setEnabled(True)
        self.btn_load_image.setEnabled(True)
        self.btn_select_region.setEnabled(True)

    def toggle_detection_overlay(self, state):
        self.show_detection_overlay = state == Qt.Checked
        if self.tracker.running:
            if self.show_detection_overlay:
                self.detection_overlay.show()
            else:
                self.detection_overlay.hide()

    def update_coordinates(self, name, x, y):
        self.lbl_coords.setText(f"Map: {name} | Coords: ({x}, {y})")
        self.nav_overlay.update_coords(name, x, y)
        # self.map_viz.update_pos(x, y)

    def update_status(self, msg):
        self.statusBar().showMessage(msg)

    def update_hp_map_combo(self):
        self.combo_hp_map.clear()
        for m in self.current_maps:
             self.combo_hp_map.addItem(m['name'])
             
    def on_hp_map_selected(self, index):
        if index < 0 or index >= len(self.current_maps): return
        m = self.current_maps[index]
        if 'hidden_portal' in m and m['hidden_portal']:
             hp = m['hidden_portal']
             self.spin_hp_x1.setValue(hp['x1'])
             self.spin_hp_x2.setValue(hp['x2'])
             self.lbl_hp_info.setText(f"설정됨: X1={hp['x1']}, X2={hp['x2']}")
        else:
             self.spin_hp_x1.setValue(0)
             self.spin_hp_x2.setValue(0)
             self.lbl_hp_info.setText("설정된 히든 포탈: 없음")

    def save_hidden_portal(self):
        idx = self.combo_hp_map.currentIndex()
        if idx < 0: return
        
        x1 = self.spin_hp_x1.value()
        x2 = self.spin_hp_x2.value()
        
        self.current_maps[idx]['hidden_portal'] = {'x1': x1, 'x2': x2}
        self.lbl_hp_info.setText(f"저장됨: X1={x1}, X2={x2}")
        self.nav_overlay.set_available_maps(self.current_maps)

    def load_config(self):
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding='utf-8') as f:
                    config = json.load(f)
                    
                if 'maps' in config and isinstance(config['maps'], list):
                    for m in config['maps']:
                         if len(self.current_maps) >= 2: break
                         if os.path.exists(m['path']):
                             slot = len(self.current_maps)
                             if self.tracker.set_map_source(slot, m['name'], m['path']):
                                 # 포탈 정보도 함께 로드
                                 map_data = {'name': m['name'], 'path': m['path'], 'portals': m.get('portals', {}), 'hidden_portal': m.get('hidden_portal', {})}
                                 self.current_maps.append(map_data)
                    self.update_map_label()
                    self.update_portal_combos()
                    self.update_npc_map_combo()
                    self.update_hp_map_combo()
                    self.nav_overlay.set_available_maps(self.current_maps)
                
                # Backwards compatibility for old config
                elif 'map_path' in config and config['map_path'] and os.path.exists(config['map_path']):
                     path = config['map_path']
                     if self.tracker.set_map_source(0, "Default Map", path):
                         self.current_maps.append({'name': "Default Map", 'path': path, 'portals': {}})
                         self.update_map_label()
                         self.update_portal_combos()
                         self.update_npc_map_combo()
                         self.nav_overlay.set_available_maps(self.current_maps)
                
                if 'char_path' in config and config['char_path'] and os.path.exists(config['char_path']):
                    path = config['char_path']
                    if self.tracker.set_template(path):
                        self.current_char_path = path
                        self.lbl_image_status.setText(f"Loaded: {path.split('/')[-1]}")
                        
                if 'search_region' in config and config['search_region']:
                    r = config['search_region']
                    self.on_region_selected(r['x'], r['y'], r['w'], r['h'])
                
                if 'show_detection_overlay' in config:
                    self.show_detection_overlay = config['show_detection_overlay']
                    self.chk_detection_overlay.setChecked(self.show_detection_overlay)
                
                # NPC 데이터 로드
                if 'npc_data' in config and isinstance(config['npc_data'], dict):
                    for npc_name, npc_info in config['npc_data'].items():
                        if npc_name in self.npc_data:
                            self.npc_data[npc_name] = npc_info
                    self.update_npc_info()
                    self.nav_overlay.set_npc_data(self.npc_data)
                    # 첫 번째 NPC 선택 시 UI 업데이트
                    self.on_npc_selected(0)
                
                # 배송 설정 로드
                if 'delivery_config' in config:
                    self.delivery_widget.load_config(config['delivery_config'])
                    
                # 구매 설정 로드
                if 'purchase_config' in config:
                    self.purchase_widget.load_config(config['purchase_config'])
                    
                self.check_ready()
            except Exception as e:
                print(f"Failed to load config: {e}")

    def save_config(self):
        config = {
            'maps': self.current_maps,
            'char_path': self.current_char_path,
            'search_region': self.current_search_region,
            'show_detection_overlay': self.show_detection_overlay,
            'npc_data': self.npc_data,

            'delivery_config': self.delivery_widget.get_config(),
            'purchase_config': self.purchase_widget.get_config()
        }
        try:
            with open("config.json", "w", encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)
        
    def on_movement_finished(self):
        if hasattr(self, 'waiting_for_doran') and self.waiting_for_doran:
             self.waiting_for_doran = False
             self.log("도란 도착 -> 1초 후 구매 시작")
             QTimer.singleShot(1000, self.purchase_widget.start_purchase_auto)


    def process_delivery_request(self, nick, qty, price, item_widget=None):
        """배송 요청 처리 (item_widget이 있으면 큐 관리 시스템을 따름)"""
        task = {
            'nick': nick,
            'qty': int(qty),
            'price': price,
            'phase': 'INIT',
            'item': item_widget
        }
        
        self.process_task(task)

    def process_task(self, task):
        self.pending_delivery = task
        nick = task['nick']
        qty = task['qty']
        
        if task['item']:
            task['item'].setText(3, "진행") # 상태 열 업데이트
            
        self.log(f"[배송진행] {nick}, {qty}개")
        
        current_stock = self.nav_overlay.spin_cider_stock.value()
        req_qty = int(qty)
        
        if current_stock < req_qty:
            self.log(f"재고 부족 ({current_stock} < {req_qty}) -> 구매 진행")
            self.pending_delivery['phase'] = 'PRE_DELIVERY_PURCHASE'
            self.start_purchase_sequence()
        else:
            self.start_delivery_sequence()

    def start_purchase_sequence(self):
        self.log("구매 시퀀스 시작 (도란 이동 및 구매)")
        self.purchase_widget.start_purchase_auto()

    def on_purchase_task_finished(self, success, msg):
        if not success:
             self.log(f"구매 실패: {msg}")
             self._finalize_current_task(False)
             return
             
        purchased_qty = self.purchase_widget.config.get('qty', 100)
        current_stock = self.nav_overlay.spin_cider_stock.value()
        self.nav_overlay.spin_cider_stock.setValue(current_stock + purchased_qty)
        self.log(f"구매 완료. 현재 재고: {current_stock + purchased_qty}")
        
        if not self.pending_delivery: return

        phase = self.pending_delivery.get('phase')
        if phase == 'PRE_DELIVERY_PURCHASE':
            req_qty = self.pending_delivery['qty'] # pending_date['qty'] -> pending_delivery['qty']
            if self.nav_overlay.spin_cider_stock.value() < req_qty:
                self.log("재고 여전히 부족 -> 재구매 진행")
                self.purchase_widget.start_purchase_auto()
            else:
                self.start_delivery_sequence()
        elif phase == 'MIN_STOCK_CHECK':
            if self.nav_overlay.spin_cider_stock.value() < self.purchase_widget.config.get('min_qty', 0):
                 self.log("최소 수량 미달 -> 추가 구매")
                 self.purchase_widget.start_purchase_auto()
            else:
                 self.log("재고 보충 완료. 듀이에게 복귀합니다.")
                 self.nav_overlay.start_npc_movement('듀이', click=False)
                 self._finalize_current_task(True) # 재고보충은 별도 큐 처리가 아니긴 함 (현재 로직상)
            
    def start_delivery_sequence(self):
        if not self.pending_delivery: return
        nick = self.pending_delivery['nick']
        qty = self.pending_delivery['qty']
        price = self.pending_delivery.get('price', '')
        self.log(f"배송 시작: {nick} ({qty}개)")
        self.delivery_widget.start_delivery_auto(nick, qty, price)
        
    def on_delivery_task_finished(self, success, msg):
        if not success:
            self.log(f"배송 실패: {msg}")
            self._finalize_current_task(False)
            return
            
        qty = self.pending_delivery['qty']
        current_stock = self.nav_overlay.spin_cider_stock.value()
        new_stock = max(0, current_stock - qty)
        self.nav_overlay.spin_cider_stock.setValue(new_stock)
        self.log(f"배송 완료. 남은 재고: {new_stock}")
        
        min_qty = self.purchase_widget.config.get('min_qty', 0)
        if new_stock < min_qty:
            self.log(f"최소 재고 미달 ({new_stock} < {min_qty}) -> 보충 구매 시작")
            self.pending_delivery['phase'] = 'MIN_STOCK_CHECK'
            self.start_purchase_sequence()
        else:
            self.log("작업 완료.")
            self._finalize_current_task(True)

    def _finalize_current_task(self, success):
        """현재 작업 마무리 및 다음 큐 실행"""
        if self.pending_delivery and self.pending_delivery.get('item'):
            status = "완료" if success else "실패"
            self.pending_delivery['item'].setText(3, status)
            
        self.pending_delivery = None
        self.process_next_in_queue()

    def handle_new_order(self, item, nickname):
        """주문 탭에서 새로운 주문이 들어왔을 때"""
        # 배송 정보 가져오기 (오버레이가 아니라 배송 위젯에서 가져와야 함, 혹은 고정값?)
        # 유저 요청: "배송 탭의 수량, 가격 정보를 기반으로"
        qty = self.delivery_widget.spin_qty.value()
        price = self.delivery_widget.txt_price.text()
        
        task = {
            'nick': nickname,
            'qty': int(qty),
            'price': price,
            'phase': 'INIT',
            'item': item
        }
        
        if self.pending_delivery:
            # 이미 작업 중이면 대기열에 추가
            self.delivery_queue.append(task)
            item.setText(3, "대기")
            self.log(f"대기열 추가: {nickname} (현재 대기: {len(self.delivery_queue)})")
        else:
            # 바로 시작
            self.process_task(task)

    def process_next_in_queue(self):
        if self.delivery_queue:
            next_task = self.delivery_queue.pop(0)
            self.process_task(next_task)

            
    def log(self, msg):
        self.statusBar().showMessage(msg)
        self.nav_overlay.lbl_status.setText(msg)
