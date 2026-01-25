import cv2
import numpy as np
import mss
import time
from PyQt5.QtCore import QThread, pyqtSignal
from src.utils import calculate_relative_coordinates

class TrackerWorker(QThread):
    position_update = pyqtSignal(int, int) # x, y
    status_update = pyqtSignal(str)
    map_dimensions = pyqtSignal(int, int) # w, h
    map_region_update = pyqtSignal(object) # list of (x,y) tuples
    
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.map_ready = False
        self.template = None # Character template
        self.sct = None
        
        # Feature detector call
        self.orb = cv2.ORB_create(nfeatures=5000) # Maximize features
        # KNN requires crossCheck=False, standard BFMatcher is fine
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        
        # Map data
        self.map_img = None
        self.map_kp = None
        self.map_des = None
        self.map_w = 0
        self.map_h = 0
        
        # Char matching threshold
        self.char_threshold = 0.8
        # Map matching params
        self.min_match_count = 4 # Relaxed threshold
        
        self.search_region = None

    def set_search_region(self, x, y, w, h):
        self.search_region = {'top': int(y), 'left': int(x), 'width': int(w), 'height': int(h)}

    def set_map_source(self, image_path):
        # Use numpy fromfile to handle unicode paths (e.g. Korean) correctly
        try:
            img_array = np.fromfile(image_path, np.uint8)
            img_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception:
            img_bgr = None

        if img_bgr is None:
            self.status_update.emit("Failed to load map image")
            return False
            
        # Convert to gray for features
        img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        # Compute features
        kp, des = self.orb.detectAndCompute(img_gray, None)
        
        if des is None or len(kp) < self.min_match_count:
            self.status_update.emit(f"Not enough features in map ({len(kp) if kp else 0})")
            return False
            
        self.map_img = img_gray
        self.map_kp = kp
        self.map_des = des
        self.map_h, self.map_w = img_gray.shape[:2]
        self.map_ready = True
        self.status_update.emit(f"Map loaded: {len(kp)} features")
        self.map_dimensions.emit(self.map_w, self.map_h)
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
        self.status_update.emit("Character loaded")
        return True

    def run(self):
        self.running = True
        self.sct = mss.mss()
        self.status_update.emit("Tracking started")
        
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
                    # If using custom region, it might be invalid. Reset?
                    # self.search_region = None 
                    time.sleep(1)
                    continue

                img_screen_bgra = np.array(screenshot)
                img_screen_bgr = cv2.cvtColor(img_screen_bgra, cv2.COLOR_BGRA2BGR)
                img_screen_gray = cv2.cvtColor(img_screen_bgr, cv2.COLOR_BGR2GRAY)
                
                # 1. Find Map in Screen
                kp_s, des_s = self.orb.detectAndCompute(img_screen_gray, None)
                
                if des_s is None:
                     self.status_update.emit("No features detected on screen (Step 1)")
                     self.map_region_update.emit([])
                     time.sleep(0.1)
                     continue
                
                # KNN Match with Ratio Test
                try:
                    matches = self.bf.knnMatch(self.map_des, des_s, k=2)
                except Exception:
                    # Can happen if not enough descriptors
                    self.status_update.emit("Matching error (feature count mismatch) (Step 1)")
                    time.sleep(0.1)
                    continue
                
                good_matches = []
                for m, n in matches:
                    if m.distance < 0.85 * n.distance: # Relaxed ratio
                        good_matches.append(m)
                
                if len(good_matches) < self.min_match_count:
                    self.status_update.emit(f"Matches: {len(good_matches)}/{self.min_match_count} (Screen Feats: {len(kp_s)}) (Step 1)")
                    self.map_region_update.emit([])
                    time.sleep(0.1)
                    continue
                
                src_pts = np.float32([self.map_kp[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp_s[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
                
                # Find Homography -> Switch to Affine Partial (Rotation + Scale + Translation only)
                # This enforces a rigid shape (rectangle) effectively, preventing skew.
                M, inliers = cv2.estimateAffinePartial2D(src_pts, dst_pts)
                
                if M is None:
                    self.status_update.emit(f"Affine failed (Matches: {len(good_matches)}) (Step 1)")
                    self.map_region_update.emit([])
                    time.sleep(0.1)
                    continue
                
                # Calculate detected map region on screen for overlay
                h, w = self.map_h, self.map_w
                # Points: TL, BL, BR, TR
                pts = np.float32([[0, 0], [0, h-1], [w-1, h-1], [w-1, 0]]).reshape(-1, 1, 2)
                
                try:
                    # Affine Transform
                    dst = cv2.transform(pts, M)
                    
                    # User requested "Rectangle" shape. 
                    # The affine transform might include rotation.
                    # If we want a strict axis-aligned rectangle (Bounding Box):
                    bx, by, bw, bh = cv2.boundingRect(dst)
                    
                    # Create points for the bounding rect
                    rect_points = [
                        (bx, by),
                        (bx, by + bh),
                        (bx + bw, by + bh),
                        (bx + bw, by)
                    ]

                    # If using region, offset points to global coords
                    offset_x = rect['left']
                    offset_y = rect['top']
                    
                    final_points = []
                    for pt in rect_points:
                        final_points.append((int(pt[0] + offset_x), int(pt[1] + offset_y)))
                    self.map_region_update.emit(final_points)
                    
                    # 2. Find Character in Screen
                    res = cv2.matchTemplate(img_screen_bgr, self.template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    
                    if max_val >= self.char_threshold:
                        # Character found on screen
                        th, tw = self.template.shape[:2]
                        screen_center_x = max_loc[0] + tw // 2
                        screen_center_y = max_loc[1] + th / 2
                        
                        # 3. Transform Screen Coords -> Map Coords
                        # Invert Affine 2D
                        M_inv = cv2.invertAffineTransform(M)
                        
                        # Project point
                        screen_pt = np.array([[[screen_center_x, screen_center_y]]], dtype=np.float32)
                        map_pt = cv2.transform(screen_pt, M_inv)
                        
                        target_x = map_pt[0][0][0]
                        target_y = map_pt[0][0][1]
                        
                        # Calculate relative coordinates (from Map Image Center)
                        rel_x, rel_y = calculate_relative_coordinates(self.map_w, self.map_h, target_x, target_y)
                        self.position_update.emit(rel_x, rel_y)
                        self.status_update.emit(f"Map({int(target_x)},{int(target_y)}) Matches:{len(good_matches)}")
                    else:
                        self.status_update.emit(f"Char not found. Matches:{len(good_matches)}")
                        
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

    def stop(self):
        self.running = False
        self.wait()
