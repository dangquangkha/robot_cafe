# -*- coding: utf-8 -*-
import sys
import json
import os
import requests
import qrcode
import smtplib
from email.mime.text import MIMEText
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSlider, QMessageBox, QSizePolicy, QSpacerItem, QScrollArea,QScroller,QScrollerProperties
)
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint, pyqtSignal, QObject # === THAY ĐỔI ===
from PyQt5.QtGui import QPixmap,QImage

import serial
from time import sleep, strftime, time
import io

# === THÊM MỚI: Các thư viện từ testv4.py ===
import pygame # Để phát âm thanh
from openai import OpenAI
from dotenv import load_dotenv
import speech_recognition as sr
import threading

import serial.tools.list_ports # <--- THÊM DÒNG NÀY
# ... (phần còn lại của file)
# ==========================================

class VendingMachine(QWidget):
    # Tín hiệu để cập nhật UI từ luồng nghe
    text_recognized = pyqtSignal(str)

    loop_step_required = pyqtSignal()
    loop_stopped_ui_update_required = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Máy Bán Hàng Tự Động (Robot Nhà Hàng)')
        self.showFullScreen()
        self.HEROKU_APP_URL = "https://khai-flask-todo-app-a81bf71c8cf2.herokuapp.com/" 
        self.current_order_id = None
        
        # === 1. KHỞI TẠO CÁC BIẾN TRẠNG THÁI (QUAN TRỌNG) ===
        self.is_listening = False
        self.is_in_conversation_loop = False 
        self.selected_product = None
        self.quantity = 1
        self.sugar_amount = 10
        self.order_id = str(int(time()))
        
        # === 2. KHỞI TẠO OPENAI & PYGAME TRƯỚC (ĐƯA LÊN ĐẦU) ===
        # Tải file .env
        load_dotenv()
        
        # Khởi tạo Client OpenAI
        try:
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY không tìm thấy trong file .env")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi API", f"Lỗi: {e}. Hãy chắc chắn bạn đã tạo file .env")
            sys.exit()

        # Khởi tạo Pygame Mixer
        try:
            pygame.mixer.init()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi Âm thanh", f"Lỗi khởi tạo Pygame: {e}")
            sys.exit()

        # Khởi tạo bộ nhận diện giọng nói
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.text_recognized.connect(self.process_voice_command)
        
        # Kết nối tín hiệu UI
        self.loop_step_required.connect(self.start_listening_loop_step)
        self.loop_stopped_ui_update_required.connect(self.safe_reset_status_label)

        # === 3. SAU ĐÓ MỚI KẾT NỐI PHẦN CỨNG (ARDUINO) ===
        # (Để nếu lỗi thì self.client đã có sẵn để robot nói báo lỗi)
        self.serial_port = None
        detected_port = self.get_arduino_port()
        
        if detected_port:
            try:
                self.serial_port = serial.Serial(detected_port, 9600, timeout=1)
                sleep(2)
                print(f"Kết nối thành công Arduino trên cổng {detected_port}")
                self.speak("Đã kết nối với hệ thống phần cứng.")
            except serial.SerialException as e:
                print(f"Lỗi kết nối cổng {detected_port}: {e}")
                self.speak("Lỗi kết nối phần cứng.")
        else:
            print("Không tìm thấy cổng COM nào!")
            # Bây giờ gọi speak ở đây sẽ KHÔNG bị lỗi nữa vì self.client đã có ở bước 2
            self.speak("Cảnh báo: Không tìm thấy mạch điều khiển.")

        # === 4. TẢI DỮ LIỆU SẢN PHẨM & MENU ===
        self.products_file = 'products.json'
        self.load_products() 
        
        menu_string = self.generate_menu_string()

        self.conversation_history = [{
            "role": "system",
            "content": (
                "Bạn là một robot phục vụ Cà Phê thông minh và thân thiện. "
                "Nhiệm vụ chính của bạn là nhận order và trả lời câu hỏi VỀ THỰC ĐƠN SAU ĐÂY. "
                "KHÔNG được bịa ra món ăn không có trong thực đơn. "
                f"--- THỰC ĐƠN HÔM NAY ---\n{menu_string}\n--- HẾT THỰC ĐƠN ---\n"
                "Luôn trả lời bằng tiếng Việt."
            )
        }]
        
        # Khởi tạo giao diện
        self.init_product_screen()

    def get_arduino_port(self):
        """
        Hàm quét các cổng COM và trả về cổng có khả năng là Arduino nhất.
        """
        ports = serial.tools.list_ports.comports()
        
        # Danh sách các từ khóa thường có trong tên Driver của Arduino
        # CH340 là chip thường dùng trong các mạch Arduino giá rẻ/clone
        arduino_identifiers = ['Arduino', 'CH340', 'USB Serial', 'USB-SERIAL']
        
        print("Đang quét các cổng COM hiện có:")
        for port in ports:
            description = port.description
            device = port.device
            print(f"- Tìm thấy: {device} ({description})")
            
            # Kiểm tra xem mô tả cổng có chứa từ khóa không
            for identifier in arduino_identifiers:
                if identifier in description:
                    print(f"-> Đã chọn cổng: {device} (Khớp từ khóa '{identifier}')")
                    return device
                    
        # Nếu không tìm thấy từ khóa, thử trả về cổng COM đầu tiên tìm thấy (nếu có)
        if len(ports) > 0:
            print(f"-> Không nhận ra tên Arduino, nhưng chọn đại cổng đầu tiên: {ports[0].device}")
            return ports[0].device
            
        return None

    # === THAY ĐỔI: Hàm speak mới dùng OpenAI + Pygame (từ testv4.py) ===
    def speak(self, text, interrupt_listen=True): # <-- THÊM interrupt_listen
        if not text:
            return

        # Dừng nghe nếu đang nghe (VÀ NẾU CÓ YÊU CẦU)
        if interrupt_listen: # <-- THÊM ĐIỀU KIỆN NÀY
            self.is_listening = False 

        print(f"Bot nói: {text}")

        # Cập nhật UI (phải chạy trên main thread)
        # Chúng ta dùng QTimer để đảm bảo nó chạy trên luồng chính
# Dòng 102 (SỬA LẠI)
        QTimer.singleShot(0, self.safe_update_status)
        
        # Chạy TTS và phát âm thanh trong luồng riêng
        threading.Thread(target=self._speak_thread, args=(text,), daemon=True).start()

    def safe_reset_status_label(self):
        """Hàm an toàn để reset status_label, dùng try/except."""
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText("Nhấn để nói")
        except RuntimeError:
            pass # Bỏ qua nếu widget đã bị xóa
            
    # Thêm hàm này vào trong Class của bạn
    def safe_update_status(self):
        """Hàm an toàn để cập nhật status_label, dùng try/except."""
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText("Robot đang nói...")
        except RuntimeError:
            pass # Bỏ qua nếu widget đã bị xóa

    def _speak_thread(self, text):
        """Hàm chạy ngầm để gọi API và phát âm thanh (AN TOÀN VỚI LUỒNG)"""
        
        # === THAY ĐỔI: Tên file duy nhất ===
        # Dùng ID của luồng và thời gian để đảm bảo file là duy nhất
        thread_id = threading.get_ident()
        filename = f"response_{thread_id}_{int(time() * 1000)}.mp3"
        # =================================
        
        try:
            # 1. Gọi API TTS của OpenAI
            with self.client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy", 
                input=text
            ) as response:
                response.stream_to_file(filename) # Ghi vào file duy nhất

            # 2. Phát âm thanh bằng Pygame
            pygame.mixer.music.load(filename) # Tải file duy nhất
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                sleep(0.1)
            
            pygame.mixer.music.unload() 
            
        except Exception as e:
            print(f"Lỗi khi phát âm thanh: {e}")
        finally:
            # 3. Xóa file duy nhất
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except PermissionError:
                    # Pygame (trên Windows) thỉnh thoảng vẫn giữ file
                    print(f"Cảnh báo: Không thể xóa {filename}, file có thể đang bị khóa.")

        # Gửi tín hiệu (giữ nguyên)
        if self.is_in_conversation_loop:
            self.loop_step_required.emit()
        else:
            self.loop_stopped_ui_update_required.emit()

    # === THÊM MỚI: Hàm chào mừng theo yêu cầu ===
    def initial_greeting(self):
        """Robot tự giới thiệu khi khởi động"""
        intro_text = "Chào bạn! Tôi là robot Cà Phê. Tôi có thể giúp bạn pha đồ uống. Bạn muốn dùng gì?"
        self.speak(intro_text)
        # Thêm vào lịch sử chat để robot biết đã chào
        self.conversation_history.append({"role": "assistant", "content": intro_text})


# === THAY THẾ HÀM start_listening BẰNG 3 HÀM DƯỚI ĐÂY ===

    def toggle_conversation_loop(self):
        """Bắt đầu hoặc dừng vòng lặp trò chuyện."""
        if not self.is_in_conversation_loop:
            # BẮT ĐẦU VÒNG LẶP
            self.is_in_conversation_loop = True
            if hasattr(self, 'listen_btn'):
                self.listen_btn.setText("Dừng...")
            
            # Bắt đầu bằng cách nói, hàm speak sẽ tự động gọi hàm nghe
            self.speak("Tôi đang nghe đây.")
        else:
            # DỪNG VÒNG LẶP
            self.stop_conversation_loop()
            self.speak("Đã dừng.") # Thông báo đã dừng

    def start_listening_loop_step(self):
        """Kích hoạt một lượt nghe trong vòng lặp."""
        # Kiểm tra lại phòng khi người dùng vừa nhấn Dừng
        if not self.is_in_conversation_loop:
            return 

        if self.is_listening:
            return # Đã có luồng nghe khác (tránh trùng lặp)
        
        self.is_listening = True
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText("Đang nghe...")
        except RuntimeError:
            pass # Widget đã bị xó
        
        # Chạy _listen_thread (luồng chặn) trong một Thread riêng
        threading.Thread(target=self._listen_thread, daemon=True).start()

    def stop_conversation_loop(self):
        """Hàm dọn dẹp để dừng vòng lặp một cách an toàn."""
        self.is_in_conversation_loop = False
        self.is_listening = False # Ngắt mọi luồng nghe
        
        # === SỬA LỖI RUNTIME ERROR ===
        try:
            if hasattr(self, 'listen_btn') and self.listen_btn:
                self.listen_btn.setText("Nhấn để nói")
        except RuntimeError:
            pass # Bỏ qua nếu widget đã bị xóa

        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText("Nhấn để nói")
        except RuntimeError:
            pass # Bỏ qua nếu widget đã bị xóa
        # === KẾT THÚC SỬA LỖI ===

    def _listen_thread(self):
        if not self.is_listening:
            return
            
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            print("Bot đang nghe...")
            try:
                audio = self.recognizer.listen(source, timeout=35, phrase_time_limit=45)
            except sr.WaitTimeoutError:
                print("Hết thời gian chờ.")
                self.is_listening = False
                self.text_recognized.emit("Lỗi: Hết giờ")
                return

        self.is_listening = False 

        try:
            text = self.recognizer.recognize_google(audio, language='vi-VN')
            print(f"Bạn nói: {text}")
            self.text_recognized.emit(text) 
        except sr.UnknownValueError:
            print("Không nhận diện được.")
            self.text_recognized.emit("Lỗi: Không rõ")
        except sr.RequestError as e:
            print(f"Lỗi dịch vụ Google; {e}")
            self.text_recognized.emit("Lỗi: Mạng")

    # === THAY ĐỔI: Hàm xử lý logic (Nâng cấp lên GPT) ===
    def process_voice_command(self, text):
        text = text.lower()
        # Dòng 252 (SỬA LẠI)
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText(f"Bạn: {text}")
        except RuntimeError:
            pass # Widget đã bị xóa

        # === THÊM ĐIỀU KIỆN DỪNG "TẠM BIỆT" ===
        if "tạm biệt" in text:
            self.stop_conversation_loop() # Dừng vòng lặp
            self.speak("Tạm biệt! Hẹn gặp lại.")
            return # Kết thúc xử lý
        # === KẾT THÚC THAY ĐỔI ===

        if "lỗi" in text:
            if "không rõ" in text:
                self.speak("Tôi không nghe rõ, bạn có thể nói lại không?")
            elif "hết giờ" in text:
                self.speak("Tôi không nghe thấy gì.")
            # Không cần return, hàm speak sẽ tự động gọi nghe lại
            return

        # === Logic xử lý menu (Ưu tiên 1) ===
        found_product = None
        for product in self.products:
            if product['name'].lower() in text:
                found_product = product
                break
        
        if found_product:

            # === THÊM ĐOẠN NÀY ĐỂ CHẶN GIỌNG NÓI ===
            if found_product['id'] not in [1, 7]:
                self.speak(f"Xin lỗi, hiện tại tôi chưa phục vụ món {found_product['name']}.")
                return
            # =============================
            if found_product['quantity'] > 0:
                # Thêm vào lịch sử để AI biết
                self.conversation_history.append({"role": "user", "content": text})
                response = f"Đã chọn {found_product['name']}. Mời bạn tới thanh toán."
                self.conversation_history.append({"role": "assistant", "content": response})
                
                # === THÊM ĐIỀU KIỆN DỪNG KHI CHỌN MÓN ===
                self.stop_conversation_loop() # Dừng vòng lặp để thanh toán
                self.speak(response)
                self.on_product_clicked(found_product) # Kích hoạt luồng thanh toán
            else:
                self.speak(f"Xin lỗi, {found_product['name']} đã hết hàng.")
                # Tự động nghe lại
            return

        # === Logic OpenAI (Ưu tiên 2) ===
        # (Không cần điều kiện "tạm biệt" ở đây nữa)
            
        # Nếu không phải lệnh menu, hỏi AI
        # Dòng 293 (SỬA LẠI)
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText("Robot đang suy nghĩ...")
        except RuntimeError:
            pass # Widget đã bị xóa
        
        self.conversation_history.append({"role": "user", "content": text})
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=self.conversation_history,
                temperature=0.7,
                max_tokens=150,
            )
            ai_response = response.choices[0].message.content.strip()
            self.conversation_history.append({"role": "assistant", "content": ai_response})
            self.speak(ai_response) # Nói xong sẽ tự động nghe lại
            
        except Exception as e:
            print(f"Lỗi khi gọi API OpenAI: {e}")
            self.speak("Tôi đang gặp một chút sự cố, bạn vui lòng thử lại sau nhé.")
            self.conversation_history.pop()

        # Không set self.is_listening = False ở đây nữa


    # === THAY ĐỔI: Thêm mock data nhà hàng ===
    def load_products(self):
        # Dữ liệu giả định nhà hàng theo yêu cầu
        default_products = [
            # --- 4 MÓN CÓ SẴN CỦA BẠN ---
            {'id': 1, 'name': 'Cà phê nâu', 'price': 18000, 'image': 'cafe_brown.jpg', 'quantity': 1000, 'type': 'milk'},
            {'id': 7, 'name': 'Cà Phê Đen', 'price': 15000, 'image': 'ca-phe-den-scaled.jpg', 'quantity': 1000, 'type': 'sugar'},
            #{'id': 2, 'name': 'Nước Cam Ép', 'price': 25000, 'image': 'orange_juice.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 3, 'name': 'Sinh Tố Bơ', 'price': 30000, 'image': 'avocado_smoothie.png', 'quantity': 100, 'type': 'milk'},
            {'id': 4, 'name': 'Nước Ion Kiềm', 'price': 3000, 'image': 'ion.png', 'quantity': 1000, 'type': 'sugar'},
            
            # --- 15 MÓN MỚI ĐƯỢC THÊM TỪ ẢNH ---
            #{'id': 5, 'name': 'Nước Ép Táo', 'price': 25000, 'image': 'apple_juice.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 6, 'name': 'Nước Ép Bơ', 'price': 30000, 'image': 'avocado_juice.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 8, 'name': 'Cappuccino', 'price': 35000, 'image': 'cappuccino.png', 'quantity': 100, 'type': 'milk'},
            #{'id': 9, 'name': 'Nước Ép Cà Rốt', 'price': 25000, 'image': 'carrot_juice.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 10, 'name': 'Nước Dừa', 'price': 20000, 'image': 'coconut_water.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 11, 'name': 'Trà Xanh', 'price': 20000, 'image': 'green_tea.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 12, 'name': 'Nước Chanh', 'price': 20000, 'image': 'lemon_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 13, 'name': 'Trà Lipton', 'price': 15000, 'image': 'lipton_tea.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 14, 'name': 'Sinh Tố Xoài', 'price': 30000, 'image': 'mango_smoothie.png', 'quantity': 100, 'type': 'milk'},
            #{'id': 15, 'name': 'Trà Sữa', 'price': 35000, 'image': 'milk_tea.png', 'quantity': 100, 'type': 'milk'},
            {'id': 16, 'name': 'Sữa Tươi', 'price': 15000, 'image': 'milk.png', 'quantity': 100, 'type': 'milk'},
            {'id': 17, 'name': 'Trà Đào', 'price': 25000, 'image': 'peach_tea.png', 'quantity': 100, 'type': 'sugar'},
            #{'id': 18, 'name': 'Trà Sữa Trân Châu', 'price': 40000, 'image': 'pearl_milk_tea.png', 'quantity': 100, 'type': 'milk'},
            #{'id': 19, 'name': 'Nước Ép Dứa', 'price': 25000, 'image': 'pineapple_juice.png', 'quantity': 100, 'type': 'sugar'},
            # --- 3 MÓN MỚI BỔ SUNG ---
            {'id': 20, 'name': 'Sữa Đậu Nành', 'price': 15000, 'image': 'soy_milk.png', 'quantity': 100, 'type': 'milk'},
            #{'id': 21, 'name': 'Sinh Tố Dâu', 'price': 30000, 'image': 'strawberry_smoothie.png', 'quantity': 100, 'type': 'milk'},
            {'id': 22, 'name': 'Sữa Chua Uống', 'price': 20000, 'image': 'yogurt_drink.png', 'quantity': 100, 'type': 'milk'},
        ]

        # Ghi đè file products.json
        with open(self.products_file, 'w', encoding='utf-8') as f:
            json.dump(default_products, f, indent=4, ensure_ascii=False)

        # Đọc sản phẩm từ file JSON
        with open(self.products_file, 'r', encoding='utf-8') as f:
            self.products = json.load(f)
        print(f"Số sản phẩm trong danh sách: {len(self.products)}")

    def generate_menu_string(self):
        """Tạo một chuỗi văn bản liệt kê menu để AI có thể hiểu."""
        if not hasattr(self, 'products') or not self.products:
            return "Hiện tại không có thông tin menu."
        
        menu_lines = []
        for item in self.products:
            # Lấy tên và giá, định dạng lại giá
            name = item.get('name', 'N/A')
            price = item.get('price', 0)
            formatted_price = f"{price:,}".replace(',', '.') # Ví dụ: 18.000
            
            # Kiểm tra tồn kho
            quantity = item.get('quantity', 0)
            if quantity > 0:
                menu_lines.append(f"- {name}: {formatted_price} VND")
            else:
                menu_lines.append(f"- {name}: (Đã hết hàng)")
        
        return "\n".join(menu_lines)

    def save_products(self):
        # (Giữ nguyên)
        with open(self.products_file, 'w', encoding='utf-8') as f:
            json.dump(self.products, f, indent=4, ensure_ascii=False)

    def clear_layout(self, layout):
        # (Giữ nguyên)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            else:
                sub_layout = item.layout()
                if sub_layout:
                    self.clear_layout(sub_layout)
                    sub_layout.deleteLater()

    # def event(self, event):
    #     # (Giữ nguyên)
    #     if event.type() == QEvent.TouchBegin or event.type() == QEvent.TouchUpdate or event.type() == QEvent.TouchEnd:
    #         touch_points = event.touchPoints()
    #         if touch_points:
    #             touch_point = touch_points[0]
    #             if event.type() == QEvent.TouchBegin:
    #                 self.last_pos = touch_point.pos()
    #             elif event.type() == QEvent.TouchUpdate:
    #                 delta = touch_point.pos() - self.last_pos
    #                 if hasattr(self, 'scroll_area'):
    #                      self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().value() - delta.y())
    #                      self.last_pos = touch_point.pos()
    #         return True
    #     return super().event(event)

    # === THAY ĐỔI: Sửa màn hình chính cho giống Robot nhà hàng ===
    def init_product_screen(self):
        if hasattr(self, 'main_layout'):
            self.clear_layout(self.main_layout)
        else:
            self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # 1. Header
        header_layout = QHBoxLayout()
        title_label = QLabel('CHÀO MỪNG, TÔI LÀ ROBOT PHỤC VỤ')
        title_label.setStyleSheet('font-size: 30px; font-weight: bold; color: #FF6200;')
        title_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(title_label)
        self.main_layout.addLayout(header_layout)

        # 2. Vùng cuộn sản phẩm (QScrollArea)
        self.scroll_area = QScrollArea() 
        self.scroll_area.setWidgetResizable(True)
        # CSS giúp thanh cuộn to, dễ chạm bằng tay
        self.scroll_area.setStyleSheet("""
            QScrollArea { border: none; background-color: transparent; }
            QScrollBar:vertical {
                border: none; background: #f0f0f0; width: 25px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #FF6200; min-height: 30px; border-radius: 12px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        
        # --- CẤU HÌNH VUỐT CẢM ỨNG (TOUCH SCROLL) ---
        # SỬA THÀNH: Truy cập thông qua ScrollBar
        self.scroll_area.verticalScrollBar().setSingleStep(10) # Tăng độ mượt
        self.scroll_area.horizontalScrollBar().setSingleStep(10)
        target_view = self.scroll_area.viewport()
        target_view.setAttribute(Qt.WA_AcceptTouchEvents) # Chấp nhận sự kiện cảm ứng
        
        # Kích hoạt QScroller để vuốt như điện thoại
        self.scroller = QScroller.scroller(target_view)
        self.scroller.grabGesture(target_view, QScroller.LeftMouseButtonGesture)
        
        # Tinh chỉnh độ nhạy
        props = self.scroller.scrollerProperties()
        props.setScrollMetric(QScrollerProperties.DragStartDistance, 0.001)
        props.setScrollMetric(QScrollerProperties.DragVelocitySmoothingFactor, 0.6)
        props.setScrollMetric(QScrollerProperties.FrameRate, QScrollerProperties.Fps60)
        self.scroller.setScrollerProperties(props)
        # --------------------------------------------

        # 3. Grid chứa sản phẩm
        scroll_widget = QWidget()
        self.grid_layout = QGridLayout(scroll_widget)
        self.grid_layout.setSpacing(20)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)

        self.refresh_grid() # Nạp danh sách món ăn

        self.scroll_area.setWidget(scroll_widget)
        self.main_layout.addWidget(self.scroll_area)

        # 4. Footer (Nút nói và trạng thái)
        self.footer_layout = QHBoxLayout()
        self.listen_btn = QPushButton("Nhấn để nói")
        self.listen_btn.setStyleSheet('background-color: #FF6200; color: white; font-size: 20px; border-radius: 15px; padding: 10px; height: 60px; min-width: 200px;')
        self.listen_btn.clicked.connect(self.toggle_conversation_loop)
        
        self.status_label = QLabel("Nhấn để nói")
        self.status_label.setStyleSheet('font-size: 20px; color: #002266; margin-left: 15px;')
        
        self.footer_layout.addWidget(self.listen_btn)
        self.footer_layout.addWidget(self.status_label)
        self.footer_layout.addStretch()

        self.main_layout.addLayout(self.footer_layout)

        # 5. Giao diện tổng thể
        self.setStyleSheet("""
            QWidget { background: qradialgradient(cx:0.5, cy:0.5, radius:1, fx:0.5, fy:0.5, stop:0 #00CCFF, stop:1 #FFFFFF); }
        """)
        
        # Lời chào của Robot
        QTimer.singleShot(1000, self.initial_greeting)

    # def refresh_grid(self):
    #     # (Giữ nguyên)
    #     self.clear_layout(self.grid_layout)
    #     col_count = 2 
    #     row, col = 0, 0

    #     valid_products = [p for p in self.products if p['quantity'] > 0]
    #     print(f"Số sản phẩm hợp lệ: {len(valid_products)}")

    #     for product in valid_products:
    #         prod_btn = QPushButton()
    #         prod_btn.setFixedSize(650, 700)
    #         prod_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    #         prod_btn.setStyleSheet('background-color: white; border: 2px solid #E0E0E0; border-radius: 15px;')

    #         layout = QVBoxLayout()
    #         img_label = QLabel()
    #         image_path = f'images/{product["image"]}'
    #         pixmap = QPixmap(image_path).scaled(400, 400, Qt.KeepAspectRatio)
    #         if pixmap.isNull() or not os.path.exists(image_path):
    #             img_label.setText(f'[Ảnh {product["image"]} không tồn tại]')
    #             img_label.setStyleSheet('font-size: 24px; color: red;')
    #         else:
    #             img_label.setPixmap(pixmap)
    #         img_label.setAlignment(Qt.AlignCenter)
    #         layout.addWidget(img_label)

    #         text_label = QLabel(f"{product['name']}\n{product['price']:,} VND".replace(',', '.'))
    #         text_label.setStyleSheet('font-size: 48px; color: #FF6200;')
    #         text_label.setAlignment(Qt.AlignCenter)
    #         layout.addWidget(text_label)

    #         prod_btn.setLayout(layout)
    #         prod_btn.clicked.connect(lambda _, p=product: self.on_product_clicked(p))
            
    #         self.grid_layout.addWidget(prod_btn, row, col)

    #         col += 1
    #         if col >= col_count:
    #             col = 0
    #             row += 1

    def refresh_grid(self):
        # Xóa layout cũ để vẽ lại
        self.clear_layout(self.grid_layout)
        col_count = 2 # Số cột hiển thị (2 cột mỗi hàng)
        row, col = 0, 0

        # Lọc ra các sản phẩm còn hàng
        valid_products = [p for p in self.products if p['quantity'] > 0]
        print(f"Số sản phẩm hợp lệ: {len(valid_products)}")

        for product in valid_products:
            prod_btn = QPushButton()
            prod_btn.setFixedSize(650, 700) # Kích thước nút bấm
            prod_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            # Bên trong vòng lặp for product in valid_products:
            prod_btn.setAttribute(Qt.WA_AcceptTouchEvents)
            layout = QVBoxLayout()
            
            # --- XỬ LÝ ẢNH ---
            img_label = QLabel()
            image_path = f'images/{product["image"]}'
            pixmap = QPixmap(image_path).scaled(400, 400, Qt.KeepAspectRatio)
            
            if pixmap.isNull() or not os.path.exists(image_path):
                img_label.setText(f'[Ảnh {product["image"]} không tồn tại]')
                img_label.setStyleSheet('font-size: 24px; color: red;')
            else:
                img_label.setPixmap(pixmap)
            
            img_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(img_label)
            # -----------------

            # --- HIỂN THỊ TÊN VÀ GIÁ ---
            text_label = QLabel(f"{product['name']}\n{product['price']:,} VND".replace(',', '.'))
            # Ép cứng màu sắc để kể cả khi nút bị disable (mờ), chữ vẫn rõ màu cam
            text_label.setStyleSheet('font-size: 48px; color: #FF6200; font-weight: bold;')
            text_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(text_label)

            prod_btn.setLayout(layout)
            
            # === LOGIC QUAN TRỌNG: CHỈ CHO PHÉP MUA ID 1 VÀ ID 7 ===
            if product['id'] in [1, 7]:
                # -> TRƯỜNG HỢP ĐƯỢC MUA (Active)
                prod_btn.setStyleSheet('background-color: white; border: 2px solid #E0E0E0; border-radius: 15px;')
                prod_btn.setCursor(Qt.PointingHandCursor) # Đổi con trỏ thành bàn tay
                prod_btn.clicked.connect(lambda _, p=product: self.on_product_clicked(p))
            else:
                # -> TRƯỜNG HỢP CHỈ HIỂN THỊ (Inactive)
                # Đặt nền hơi xám để người dùng biết là không chọn được
                prod_btn.setStyleSheet('background-color: #F2F2F2; border: 1px solid #CCCCCC; border-radius: 15px;')
                prod_btn.setEnabled(False) # Khóa nút này lại, không cho click
            # =======================================================

            # Thêm vào lưới (Grid Layout)
            self.grid_layout.addWidget(prod_btn, row, col)

            # Thuật toán chia cột/hàng
            col += 1
            if col >= col_count:
                col = 0
                row += 1

    def on_product_clicked(self, product):
        # (Giữ nguyên)
        self.selected_product = product
        self.sugar_type = 'Lượng sữa' if self.selected_product['type'] == 'milk' else 'Lượng đường'
        self.init_payment_screen()

    # === BỎ: Hàm update_footer cũ (vì đã thay bằng footer mới) ===
    def update_footer(self):
        pass 
    def select_product(self, product, button):
        pass
    # =========================================================

    def init_sugar_screen(self):
        # (Giữ nguyên - Màn hình này hiện đang bị bỏ qua do on_product_clicked
        # đi thẳng tới payment, nhưng giữ lại không sao)
        pass 

    def update_sugar_label(self, value):
        # (Giữ nguyên)
        self.sugar_amount = value
        self.sugar_label.setText(f'{value} gam {self.sugar_type.lower()}')

    # Dòng 680 (Sửa lại hàm này)
    # === SỬA LẠI: Màn hình thanh toán gọi API Server ===
    def init_payment_screen(self):
        self.clear_layout(self.main_layout)

        title = QLabel('THANH TOÁN QUÉT MÃ QR')
        title.setStyleSheet('font-size: 40px; font-weight: bold; color: #FF6200;')
        self.main_layout.addWidget(title, alignment=Qt.AlignCenter)

        # Label hiển thị trạng thái hoặc ảnh QR
        self.qr_label = QLabel("Đang kết nối máy chủ tạo mã...")
        self.qr_label.setStyleSheet('font-size: 24px; color: blue;')
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.qr_label)

        # Thông tin tiền
        total_money = self.selected_product['price'] * self.quantity
        payment_info = QLabel(
            f"Sản phẩm: {self.selected_product['name']}\n"
            f"Cần thanh toán: {total_money:,} VND".replace(',', '.')
        )
        payment_info.setStyleSheet('font-size: 35px; color: #002266;')
        payment_info.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(payment_info)

        # Nút hủy (để khách thoát ra nếu không muốn mua nữa)
        cancel_btn = QPushButton("Hủy bỏ")
        cancel_btn.setStyleSheet("background-color: red; color: white; font-size: 20px; padding: 10px;")
        cancel_btn.clicked.connect(self.reset_to_product_screen)
        self.main_layout.addWidget(cancel_btn, alignment=Qt.AlignCenter)

        # Gọi hàm tạo giao dịch
        # Dùng QTimer.singleShot để không làm đơ giao diện ngay lập tức
        QTimer.singleShot(500, self.create_payment_order)

    # === THÊM MỚI: Gọi API tạo thanh toán ===
    def create_payment_order(self):
        try:
            total_money = self.selected_product['price'] * self.quantity
            info = f"Ban{self.order_id}" # Nội dung chuyển khoản
            
            # Gọi API Create Payment
            url = f"{self.HEROKU_APP_URL}/create-payment?amount={total_money}&info={info}&table=1"
            print(f"Calling: {url}")
            
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if response.status_code == 200 and 'orderId' in data:
                self.current_order_id = data['orderId']
                qr_url = data['payUrl']
                
                print(f"Order ID: {self.current_order_id}, QR Link: {qr_url}")
                
                # Tải ảnh QR về hiển thị
                qr_img_resp = requests.get(qr_url, timeout=10)
                pixmap = QPixmap()
                pixmap.loadFromData(qr_img_resp.content)
                
                self.qr_label.setPixmap(pixmap.scaled(400, 400, Qt.KeepAspectRatio))
                self.speak("Mời bạn quét mã QR. Hệ thống sẽ tự động pha chế khi nhận được tiền.")
                
                # Bắt đầu vòng lặp kiểm tra trạng thái (Polling)
                self.payment_check_timer = QTimer()
                self.payment_check_timer.timeout.connect(self.check_payment_status)
                self.payment_check_timer.start(3000) # Kiểm tra mỗi 3 giây
                
            else:
                self.qr_label.setText("Lỗi tạo mã: " + data.get('error', 'Unknown'))
                
        except Exception as e:
            print(f"Lỗi tạo đơn: {e}")
            self.qr_label.setText("Lỗi kết nối Server!")

    # === THÊM MỚI: Kiểm tra trạng thái thanh toán (Polling) ===
    def check_payment_status(self):
        if not self.current_order_id:
            return

        try:
            url = f"{self.HEROKU_APP_URL}/check-status?orderId={self.current_order_id}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                status = response.json().get('status')
                print(f"Trạng thái đơn {self.current_order_id}: {status}")
                
                if status == 'paid':
                    # === THANH TOÁN THÀNH CÔNG ===
                    self.payment_check_timer.stop() # Dừng kiểm tra
                    self.qr_label.setText("THANH TOÁN THÀNH CÔNG!")
                    self.qr_label.setStyleSheet("color: green; font-size: 30px; font-weight: bold;")
                    
                    self.speak("Đã nhận được thanh toán via SePay. Đang tiến hành pha chế.")
                    
                    # Chuyển sang màn hình pha chế sau 2 giây
                    QTimer.singleShot(2000, self.start_brewing)
                    
        except Exception as e:
            print(f"Lỗi check status: {e}")
            # Không dừng timer, cứ thử lại lần sau


    def start_brewing(self):
        # 1. Cập nhật giao diện (Giữ nguyên)
        self.clear_layout(self.main_layout)

        title = QLabel('ĐANG PHA CHẾ...')
        title.setStyleSheet('font-size: 30px; font-weight: bold; color: #FF6200;')
        self.main_layout.addWidget(title, alignment=Qt.AlignCenter)

        self.brewing_label = QLabel('Đang gửi lệnh tới Robot...')
        self.brewing_label.setStyleSheet('font-size: 20px; color: #002266;')
        self.main_layout.addWidget(self.brewing_label, alignment=Qt.AlignCenter)

        # Thiết lập các bước hiển thị cho người dùng đỡ chán (Giữ nguyên)
        self.brewing_steps = [
            'Máy đang chạy...',
            'Vui lòng đợi giây lát...',
            'Sắp hoàn thành...',
        ]
        self.brewing_step = 0
        self.brewing_timer = QTimer()
        self.brewing_timer.timeout.connect(self.update_brewing)
        self.brewing_timer.start(2000) # Chạy thanh trạng thái chậm lại chút cho khớp với máy bơm

        # === PHẦN SỬA ĐỔI QUAN TRỌNG TẠI ĐÂY ===
        if self.serial_port:
            try:
                print("Thanh toán thành công -> Gửi lệnh kích hoạt (số 1)")
                # Gửi duy nhất ký tự '1' xuống Arduino
                self.serial_port.write(b'1') 
            except Exception as e:
                print(f"Lỗi khi gửi lệnh xuống Arduino: {e}")
        else:
            print("Chưa kết nối Arduino nên không gửi lệnh được.")
            
        self.update()

    def update_brewing(self):
        # (Giữ nguyên)
        if self.brewing_step < len(self.brewing_steps):
            step_text = self.brewing_steps[self.brewing_step]
            self.brewing_label.setText(step_text)
            self.speak(step_text) # <-- Thêm nói
            self.brewing_step += 1
        else:
            self.brewing_timer.stop()
            self.finish_brewing()

    def finish_brewing(self):
        # (Giữ nguyên)
        for product in self.products:
            if product['id'] == self.selected_product['id']:
                product['quantity'] -= self.quantity
                break
        self.save_products()
        self.send_email_notification()

        self.clear_layout(self.main_layout)
        msg = QLabel('Pha chế xong! Vui lòng nhận sản phẩm.\nCảm ơn quý khách!')
        msg.setStyleSheet('font-size: 30px; font-weight: bold; color: #FF6200;')
        msg.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(msg)
        
        self.speak("Pha chế xong! Mời quý khách nhận sản phẩm. Cảm ơn đã sử dụng dịch vụ!")

        QTimer.singleShot(3000, self.reset_to_product_screen)

    def send_email_notification(self):
        # (Giữ nguyên)
        pass 

    def reset_to_product_screen(self):
        # (Giữ nguyên)
        # === THÊM: Dừng timer kiểm tra thanh toán nếu đang chạy ===
        if hasattr(self, 'payment_check_timer') and self.payment_check_timer.isActive():
            self.payment_check_timer.stop()
        self.selected_product = None
        self.sugar_amount = 10
        self.quantity = 1
        self.order_id = str(int(time()))
        # Reset lại lịch sử chat nhưng giữ lại system prompt
        if hasattr(self, 'conversation_history') and self.conversation_history:
             system_prompt = self.conversation_history[0]
             self.conversation_history = [system_prompt]
        
        self.stop_conversation_loop()
        self.init_product_screen()

    def keyPressEvent(self, event):
        # (Giữ nguyên)
        if event.key() == Qt.Key_Escape:
            self.close()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VendingMachine()
    window.show()
    sys.exit(app.exec_())