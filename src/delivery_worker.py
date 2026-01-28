class DeliveryWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, config, run_data, sct):
        super().__init__()
        self.config = config
        self.run_data = run_data
        self.sct = sct
        self.is_running = True
        
    def find_image(self, img_path, timeout=30, threshold=0.8):
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
                # 스크린샷 캡처
                monitor = self.sct.monitors[1]
                screenshot = self.sct.grab(monitor)
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
        self.progress_signal.emit(f"'{name}' 이미지 찾는 중...")
        pos = self.find_image(img_path, timeout)
        if pos:
            pyautogui.click(pos[0], pos[1])
            self.progress_signal.emit(f"'{name}' 클릭 완료")
            return True
        else:
            self.progress_signal.emit(f"'{name}' 못 찾음")
            return False

    def click_loc(self, pos, name):
        self.progress_signal.emit(f"'{name}' 클릭 ({pos['x']}, {pos['y']})")
        pyautogui.click(pos['x'], pos['y'])

    def run(self):
        try:
            # 1. 듀이(이미지) 클릭 후 i 키
            if not self.find_and_click(self.config['dewey_img'], "듀이", timeout=5):
                self.finished_signal.emit(False, "듀이를 찾지 못했습니다")
                return
            
            time.sleep(0.5)
            pyautogui.press('i')
            time.sleep(0.5)
            
            # 2. 배송(좌표) 클릭
            self.click_loc(self.config['delivery_pos'], "배송 버튼")
            time.sleep(0.5)
            
            # 3. 받는사람(좌표) 클릭 후 닉네임 입력
            self.click_loc(self.config['receiver_pos'], "받는사람 입력칸")
            time.sleep(0.2)
            # 한글 입력 문제 해결을 위해 클립보드 사용 권장하지만 여기서는 단순 text 입력 가정
            # 한글 닉네임일 경우 pyperclip 필요할 수 있음. 일단 pyautogui.typewrite 사용
            pyautogui.write(self.run_data['nickname'])
            time.sleep(0.5)
            
            # 4. 아이템 등록 반복
            qty = self.run_data['quantity']
            for i in range(qty):
                if not self.is_running: return
                self.progress_signal.emit(f"아이템 등록 ({i+1}/{qty})")
                
                if not self.find_and_click(self.config['cider_img'], "사이다", timeout=2):
                    self.finished_signal.emit(False, "사이다 이미지를 못 찾았습니다")
                    return
                time.sleep(0.3)
                
                if not self.find_and_click(self.config['empty_slot_img'], "빈칸", timeout=2):
                    self.finished_signal.emit(False, "빈칸 이미지를 못 찾았습니다")
                    return
                time.sleep(0.3)
            
            # 5. 청구1, 청구2 클릭
            self.click_loc(self.config['charge1_pos'], "청구금액 1")
            time.sleep(0.2)
            self.click_loc(self.config['charge2_pos'], "청구금액 2")
            time.sleep(0.2)
            
            # 6. 가격 입력 후 엔터
            pyautogui.write(self.run_data['price'])
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.5)
            
            # 7. 보내기(이미지) 클릭 -> 1초 대기 -> 엔터 -> 1초 대기
            if not self.find_and_click(self.config['send_img'], "보내기", timeout=3):
                self.finished_signal.emit(False, "보내기 버튼을 못 찾았습니다")
                return
            
            time.sleep(1.0)
            pyautogui.press('enter')
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
                pyautogui.press('esc')
                time.sleep(0.2)
                
            self.finished_signal.emit(True, "배송 완료!")
            
        except Exception as e:
            self.finished_signal.emit(False, f"에러 발생: {str(e)}")

    def stop(self):
        self.is_running = False
