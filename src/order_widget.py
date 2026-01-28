
class OrderWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 트리 위젯 생성
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["시간", "채널", "닉네임", "메시지"])
        # 컬럼 너비 조정
        self.tree.setColumnWidth(0, 150) # 시간
        self.tree.setColumnWidth(1, 60)  # 채널
        self.tree.setColumnWidth(2, 100) # 닉네임
        
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

        # 구분자 (-숫자)로 분리
        # text 예: 닉네임(-4)채널(-2)메시지(-10) -> ['닉네임', '채널', '메시지', '']
        parts = re.split(r'\(- \d+\)', text)
        
        # 압축된 (-N)이 re.sub에서 "(-N)" 형태로 바뀌었음. 
        # gui.py의 압축 로직: lambda m: f"(-{len(m.group(0))})" -> '(-4)' 형태.
        # 따라서 regex: \(- \d+ \)  (괄호 안에 -숫자)
        
        parts = re.split(r'\(- \d+\)', text)
        
        # 비어있는 문자열 제거
        parts = [p for p in parts if p.strip()]
        
        if len(parts) >= 3:
            # 순서대로 닉네임, 채널, 메시지라고 가정
            nickname = parts[0]
            channel = parts[1]
            message = "".join(parts[2:]) # 나머지는 메시지로
            
            self.add_order(channel, nickname, message)

    def add_order(self, channel, nickname, message):
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        item = QTreeWidgetItem([timestamp, channel, nickname, message])
        self.tree.addTopLevelItem(item)
        # 스크롤 최하단으로 이동
        self.tree.scrollToItem(item)
