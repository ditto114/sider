import cv2
import numpy as np
import mss
import time
from PyQt5.QtCore import QThread, pyqtSignal
from src.utils import calculate_relative_coordinates

class TrackerWorker(QThread):
    position_update = pyqtSignal(str, int, int) # name, x, y
    status_update = pyqtSignal(str)
    map_dimensions = pyqtSignal(int, int) # w, h
    map_region_update = pyqtSignal(object) # list of (x,y) tuples
    
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.template = None # Character template
        self.template_scales = []  # 다중 스케일 템플릿
        self.sct = None
        
        # SIFT 특징점 검출기 사용 (ORB보다 정확함)
        self.sift = cv2.SIFT_create(nfeatures=3000)
        
        # FLANN 기반 매칭 (더 빠르고 정확)
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)
        
        # Maps storage: { slot_index: {'name': str, 'img': gray, 'kp': kp, 'des': des, 'w': w, 'h': h} }
        self.maps = {}
        
        # Char matching threshold
        self.char_threshold = 0.65  # 낮춘 임계값 (다중 스케일로 보완)
        # Map matching params
        self.min_match_count = 10  # SIFT는 더 정확하므로 임계값 상향
        
        self.search_region = None
        self.current_map_slot = None
        
        # 위치 안정화를 위한 변수
        self.last_position = None
        self.position_smoothing = 0.7  # 0~1, 높을수록 이전 위치에 가중치

    @property
    def map_ready(self):
        return len(self.maps) > 0

    def set_search_region(self, x, y, w, h):
        self.search_region = {'top': int(y), 'left': int(x), 'width': int(w), 'height': int(h)}

    def set_map_source(self, slot, name, image_path):
        # Use numpy fromfile to handle unicode paths (e.g. Korean) correctly
        try:
            img_array = np.fromfile(image_path, np.uint8)
            img_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception:
            img_bgr = None

        if img_bgr is None:
            self.status_update.emit(f"Failed to load map {name}")
            return False
            
        # Convert to gray for features
        img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        # SIFT 특징점 계산
        kp, des = self.sift.detectAndCompute(img_gray, None)
        
        if des is None or len(kp) < self.min_match_count:
            self.status_update.emit(f"Not enough features in map {name} ({len(kp) if kp else 0})")
            return False
            
        h, w = img_gray.shape[:2]
        self.maps[slot] = {
            'name': name,
            'img': img_gray,
            'kp': kp,
            'des': des.astype(np.float32),  # FLANN은 float32 필요
            'w': w,
            'h': h
        }
        
        self.status_update.emit(f"Map loaded: {name} ({len(kp)} features)")
        # Emit dim if this is the first map
        if len(self.maps) == 1:
             self.map_dimensions.emit(w, h)
        return True

    def set_template(self, image_path):
        # Use numpy fromfile to handle unicode paths correctly
        try:
            img_array = np.fromfile(image_path, np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception:
            img = None

        if img is None:
            self.status_update.emit("Failed to load character image")
            return False
        
        self.template = img
        
        # 다중 스케일 템플릿 생성 (0.8 ~ 1.2 배율)
        self.template_scales = []
        for scale in [0.8, 0.9, 1.0, 1.1, 1.2]:
            h, w = img.shape[:2]
            new_w = int(w * scale)
            new_h = int(h * scale)
            if new_w > 0 and new_h > 0:
                scaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                self.template_scales.append((scale, scaled))
        
        self.status_update.emit(f"Character loaded ({len(self.template_scales)} scales)")
        return True

    def clear_maps(self):
        self.maps.clear()
        self.current_map_slot = None
        self.last_position = None
        self.status_update.emit("All maps cleared")

    def run(self):
        self.running = True
        self.sct = mss.mss()
        self.status_update.emit("Tracking started")
        self.last_position = None
        
        monitor = self.sct.monitors[1] # Primary monitor
        
        while self.running:
            if not self.map_ready or self.template is None:
                time.sleep(0.1)
                continue
                
            try:
                # Capture screen (Full or Region)
                rect = self.search_region if self.search_region else monitor
                
                try:
                    screenshot = self.sct.grab(rect)
                except Exception as e:
                    self.status_update.emit(f"Grab failed: {e}")
                    time.sleep(1)
                    continue

                img_screen_bgra = np.array(screenshot)
                img_screen_bgr = cv2.cvtColor(img_screen_bgra, cv2.COLOR_BGRA2BGR)
                img_screen_gray = cv2.cvtColor(img_screen_bgr, cv2.COLOR_BGR2GRAY)
                
                # 1. SIFT 특징점 검출 (화면)
                kp_s, des_s = self.sift.detectAndCompute(img_screen_gray, None)
                
                if des_s is None or len(kp_s) < 4:
                     self.status_update.emit("No features detected on screen")
                     self.map_region_update.emit([])
                     time.sleep(0.1)
                     continue
                
                des_s = des_s.astype(np.float32)
                
                # 2. 모든 맵과 매칭하여 최적 맵 찾기
                best_map = None
                best_matches = []
                max_good_matches = 0
                new_slot = None
                
                for slot, map_data in self.maps.items():
                    try:
                        # FLANN 매칭
                        matches = self.flann.knnMatch(map_data['des'], des_s, k=2)
                        
                        # Lowe's ratio test (더 엄격한 0.7 사용)
                        good_matches = []
                        for match_pair in matches:
                            if len(match_pair) == 2:
                                m, n = match_pair
                                if m.distance < 0.7 * n.distance:
                                    good_matches.append(m)
                                
                        if len(good_matches) > max_good_matches:
                            max_good_matches = len(good_matches)
                            best_map = map_data
                            best_matches = good_matches
                            new_slot = slot
                    except Exception:
                        continue

                # 최소 매칭 수 확인
                if best_map is None or max_good_matches < self.min_match_count:
                    self.status_update.emit(f"Scanning... Best: {max_good_matches} matches (Screen: {len(kp_s)})")
                    self.map_region_update.emit([])
                    time.sleep(0.1)
                    continue
                
                # 맵 변경 감지
                if self.current_map_slot != new_slot:
                    self.current_map_slot = new_slot
                    self.last_position = None  # 맵 변경 시 위치 초기화
                    self.map_dimensions.emit(best_map['w'], best_map['h'])
                    self.status_update.emit(f"Detected: {best_map['name']}")

                # 3. Homography 추정 (Affine보다 더 정확)
                src_pts = np.float32([best_map['kp'][m.queryIdx].pt for m in best_matches]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp_s[m.trainIdx].pt for m in best_matches]).reshape(-1, 1, 2)
                
                # RANSAC으로 outlier 제거
                H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                
                if H is None:
                    self.status_update.emit(f"Homography failed ({best_map['name']})")
                    self.map_region_update.emit([])
                    time.sleep(0.1)
                    continue
                
                # 4. 맵 영역 시각화 (Homography로 변환된 4개 코너)
                h, w = best_map['h'], best_map['w']
                pts = np.float32([[0, 0], [0, h-1], [w-1, h-1], [w-1, 0]]).reshape(-1, 1, 2)
                
                try:
                    dst = cv2.perspectiveTransform(pts, H)
                    
                    offset_x = rect['left']
                    offset_y = rect['top']
                    
                    # 변환된 영역의 바운딩 박스 계산
                    dst_squeezed = dst.reshape(-1, 2)
                    x_coords = dst_squeezed[:, 0]
                    y_coords = dst_squeezed[:, 1]
                    
                    min_x, max_x = int(np.min(x_coords)), int(np.max(x_coords))
                    min_y, max_y = int(np.min(y_coords)), int(np.max(y_coords))
                    
                    # 오버레이용 폴리곤 포인트
                    final_points = []
                    for pt in dst_squeezed:
                        final_points.append((int(pt[0] + offset_x), int(pt[1] + offset_y)))
                    self.map_region_update.emit(final_points)
                    
                    # 5. 전체 감지 영역에서 캐릭터 검색 (미니맵 밖에서도 찾을 수 있도록)
                    search_area = img_screen_bgr
                    
                    # 6. 다중 스케일 템플릿 매칭
                    best_val = 0
                    best_loc = None
                    best_scale_template = None
                    
                    for scale, scaled_template in self.template_scales:
                        th, tw = scaled_template.shape[:2]
                        
                        # 템플릿이 검색 영역보다 크면 스킵
                        if th > search_area.shape[0] or tw > search_area.shape[1]:
                            continue
                        
                        res = cv2.matchTemplate(search_area, scaled_template, cv2.TM_CCOEFF_NORMED)
                        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                        
                        if max_val > best_val:
                            best_val = max_val
                            best_loc = max_loc
                            best_scale_template = scaled_template
                    
                    if best_val >= self.char_threshold and best_loc is not None:
                        th, tw = best_scale_template.shape[:2]
                        
                        # 화면 좌표 (전체 감지 영역 내 좌표)
                        screen_center_x = best_loc[0] + tw / 2
                        screen_center_y = best_loc[1] + th / 2
                        
                        # Homography 역변환으로 맵 좌표 계산
                        H_inv = np.linalg.inv(H)
                        screen_pt = np.array([[[screen_center_x, screen_center_y]]], dtype=np.float32)
                        map_pt = cv2.perspectiveTransform(screen_pt, H_inv)
                        
                        target_x = map_pt[0][0][0]
                        target_y = map_pt[0][0][1]
                        
                        # 7. 위치 스무딩 (急激한 변화 방지)
                        if self.last_position is not None:
                            last_x, last_y = self.last_position
                            # 급격한 이동 감지 (맵 크기의 10% 이상 이동)
                            jump_threshold = max(w, h) * 0.1
                            distance = np.sqrt((target_x - last_x)**2 + (target_y - last_y)**2)
                            
                            if distance < jump_threshold:
                                # 스무딩 적용
                                target_x = self.position_smoothing * last_x + (1 - self.position_smoothing) * target_x
                                target_y = self.position_smoothing * last_y + (1 - self.position_smoothing) * target_y
                            # 급격한 이동은 새 위치 그대로 사용 (순간이동 등)
                        
                        self.last_position = (target_x, target_y)
                        
                        rel_x, rel_y = calculate_relative_coordinates(w, h, target_x, target_y)
                        self.position_update.emit(best_map['name'], rel_x, rel_y)
                        
                        # Debug info
                        self.status_update.emit(f"{best_map['name']} | Matches: {max_good_matches} | Conf: {best_val:.2f}")
                    else:
                        self.status_update.emit(f"{best_map['name']} found, Char missing (Conf: {best_val:.2f})")
                        
                except Exception as e:
                     self.status_update.emit(f"Calc Error: {str(e)}")
                     self.map_region_update.emit([])

                time.sleep(0.05)
                
            except Exception as e:
                self.status_update.emit(f"Error: {str(e)}")
                self.map_region_update.emit([])
                time.sleep(1)
        
        self.status_update.emit("Tracking stopped")
        self.map_region_update.emit([])
        self.last_position = None

    def stop(self):
        self.running = False
        self.wait()
