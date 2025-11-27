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
    QGridLayout, QSlider, QMessageBox, QSizePolicy, QSpacerItem, QScrollArea
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

        # Khởi tạo serial (giữ nguyên)
        try:
            self.serial_port = serial.Serial('COM6', 9600, timeout=1)
            sleep(2)
        except serial.SerialException:
            print('Không thể kết nối với Arduino. Chạy ở chế độ không có serial.')
            self.serial_port = None

        # === THAY ĐỔI: Khởi tạo stack OpenAI & Pygame ===
        # 1. Tải file .env
        load_dotenv()
        
        # 2. Khởi tạo Client OpenAI
        try:
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY không tìm thấy trong file .env")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi API", f"Lỗi: {e}. Hãy chắc chắn bạn đã tạo file .env và đặt OPENAI_API_KEY vào đó.")
            sys.exit()

        # 3. Khởi tạo Pygame Mixer để phát âm thanh
        try:
            pygame.mixer.init()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi Âm thanh", f"Lỗi khởi tạo Pygame Mixer: {e}. Bạn có đang thiếu driver âm thanh không?")
            sys.exit()

        # 4. Khởi tạo bộ nhận diện giọng nói (giữ nguyên)
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.is_listening = False
        self.is_in_conversation_loop = False
        self.text_recognized.connect(self.process_voice_command) # Kết nối tín hiệu
        
        # === BƯỚC SỬA ĐỔI QUAN TRỌNG ===
        
        # 1. Tải sản phẩm LÊN TRƯỚC
        self.products_file = 'products.json'
        self.load_products() 
        
        # 2. Tạo chuỗi menu cho AI
        menu_string = self.generate_menu_string()

        # 3. "Nhồi" menu vào system prompt
        self.conversation_history = [{
            "role": "system",
            "content": (
                "Bạn là một robot phục vụ nhà hàng thông minh và thân thiện. "
                "Nhiệm vụ chính của bạn là nhận order và trả lời câu hỏi VỀ THỰC ĐƠN SAU ĐÂY. "
                "KHÔNG được bịa ra món ăn không có trong thực đơn. "
                f"--- THỰC ĐƠN HÔM NAY ---\n{menu_string}\n--- HẾT THỰC ĐƠN ---\n"
                "Nếu khách hỏi món không có trong thực đơn, hãy lịch sự từ chối và gợi ý các món có trong thực đơn. "
                "Nếu khách HỎI (ví dụ: 'cà phê giá bao nhiêu?', 'bạn có bán gì?', 'có trà sữa không?'), hãy trả lời dựa trên thực đơn. "
                "Luôn trả lời bằng tiếng Việt."
            )
        }]
        # === KẾT THÚC SỬA ĐỔI ===
        
        # Khởi tạo sản phẩm từ file JSON (phần này đã được dời lên trên)
        self.selected_product = None
        self.quantity = 1
        self.sugar_amount = 10
        self.order_id = str(int(time()))
        
        # Kết nối tín hiệu (giữ nguyên)
        self.loop_step_required.connect(self.start_listening_loop_step)
        self.loop_stopped_ui_update_required.connect(self.safe_reset_status_label)
        
        self.init_product_screen()

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
        intro_text = "Chào bạn! Tôi là robot phục vụ. Tôi có thể giúp bạn đặt nước, vận chuyển và phục vụ tại bàn. Bạn muốn dùng gì?"
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
            {'id': 1, 'name': 'Cà phê', 'price': 18000, 'image': 'coffee.png', 'quantity': 1000, 'type': 'milk'},
            {'id': 2, 'name': 'Nước Cam Ép', 'price': 25000, 'image': 'orange_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 3, 'name': 'Sinh Tố Bơ', 'price': 30000, 'image': 'avocado_smoothie.png', 'quantity': 100, 'type': 'milk'},
            {'id': 4, 'name': 'Nước Ion Kiềm', 'price': 3000, 'image': 'ion.png', 'quantity': 1000, 'type': 'sugar'},
            
            # --- 15 MÓN MỚI ĐƯỢC THÊM TỪ ẢNH ---
            {'id': 5, 'name': 'Nước Ép Táo', 'price': 25000, 'image': 'apple_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 6, 'name': 'Nước Ép Bơ', 'price': 30000, 'image': 'avocado_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 7, 'name': 'Cà Phê Đen', 'price': 15000, 'image': 'black_coffee.png', 'quantity': 1000, 'type': 'sugar'},
            {'id': 8, 'name': 'Cappuccino', 'price': 35000, 'image': 'cappuccino.png', 'quantity': 100, 'type': 'milk'},
            {'id': 9, 'name': 'Nước Ép Cà Rốt', 'price': 25000, 'image': 'carrot_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 10, 'name': 'Nước Dừa', 'price': 20000, 'image': 'coconut_water.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 11, 'name': 'Trà Xanh', 'price': 20000, 'image': 'green_tea.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 12, 'name': 'Nước Chanh', 'price': 20000, 'image': 'lemon_juice.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 13, 'name': 'Trà Lipton', 'price': 15000, 'image': 'lipton_tea.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 14, 'name': 'Sinh Tố Xoài', 'price': 30000, 'image': 'mango_smoothie.png', 'quantity': 100, 'type': 'milk'},
            {'id': 15, 'name': 'Trà Sữa', 'price': 35000, 'image': 'milk_tea.png', 'quantity': 100, 'type': 'milk'},
            {'id': 16, 'name': 'Sữa Tươi', 'price': 15000, 'image': 'milk.png', 'quantity': 100, 'type': 'milk'},
            {'id': 17, 'name': 'Trà Đào', 'price': 25000, 'image': 'peach_tea.png', 'quantity': 100, 'type': 'sugar'},
            {'id': 18, 'name': 'Trà Sữa Trân Châu', 'price': 40000, 'image': 'pearl_milk_tea.png', 'quantity': 100, 'type': 'milk'},
            {'id': 19, 'name': 'Nước Ép Dứa', 'price': 25000, 'image': 'pineapple_juice.png', 'quantity': 100, 'type': 'sugar'},
            # --- 3 MÓN MỚI BỔ SUNG ---
            {'id': 20, 'name': 'Sữa Đậu Nành', 'price': 15000, 'image': 'soy_milk.png', 'quantity': 100, 'type': 'milk'},
            {'id': 21, 'name': 'Sinh Tố Dâu', 'price': 30000, 'image': 'strawberry_smoothie.png', 'quantity': 100, 'type': 'milk'},
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

    def event(self, event):
        # (Giữ nguyên)
        if event.type() == QEvent.TouchBegin or event.type() == QEvent.TouchUpdate or event.type() == QEvent.TouchEnd:
            touch_points = event.touchPoints()
            if touch_points:
                touch_point = touch_points[0]
                if event.type() == QEvent.TouchBegin:
                    self.last_pos = touch_point.pos()
                elif event.type() == QEvent.TouchUpdate:
                    delta = touch_point.pos() - self.last_pos
                    if hasattr(self, 'scroll_area'):
                         self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().value() - delta.y())
                         self.last_pos = touch_point.pos()
            return True
        return super().event(event)

    # === THAY ĐỔI: Sửa màn hình chính cho giống Robot nhà hàng ===
    def init_product_screen(self):
        if hasattr(self, 'main_layout'):
            self.clear_layout(self.main_layout)
        else:
            self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel('CHÀO MỪNG, TÔI LÀ ROBOT PHỤC VỤ') # <-- Sửa tiêu đề
        title_label.setStyleSheet('font-size: 30px; font-weight: bold; color: #FF6200;')
        title_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(title_label)
        
        self.main_layout.addLayout(header_layout)

        # Grid sản phẩm (giữ nguyên QScrollArea)
        self.scroll_area = QScrollArea() 
        self.scroll_area.setWidgetResizable(True)
        # (CSS cho scrollbar giữ nguyên)
        self.scroll_area.setStyleSheet("""
            QScrollBar:vertical { ... } 
        """)
        self.scroll_area.viewport().setAttribute(Qt.WA_AcceptTouchEvents)

        scroll_widget = QWidget()
        self.grid_layout = QGridLayout(scroll_widget)
        self.grid_layout.setSpacing(20)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)

        self.refresh_grid() # Tải menu nhà hàng

        self.scroll_area.setWidget(scroll_widget)
        self.main_layout.addWidget(self.scroll_area)

        # === THÊM MỚI: Footer chứa nút Voice và Status ===
        self.footer_layout = QHBoxLayout()
        
        # Nút nghe lệnh
        self.listen_btn = QPushButton("Nhấn để nói")
        self.listen_btn.setStyleSheet('background-color: #FF6200; color: white; font-size: 20px; border-radius: 15px; padding: 10px; height: 60px; min-width: 200px;')
        self.listen_btn.clicked.connect(self.toggle_conversation_loop) # <-- Đổi tên hàm
        self.footer_layout.addWidget(self.listen_btn) # <-- Sửa tên biến

        # Label trạng thái
        self.status_label = QLabel("Nhấn để nói")
        self.status_label.setStyleSheet('font-size: 20px; color: #002266; margin-left: 15px;')
        self.footer_layout.addWidget(self.status_label)
        self.footer_layout.addStretch() # Đẩy về bên trái

        self.main_layout.addLayout(self.footer_layout)
        # ===============================================

        self.setStyleSheet("""
            QWidget {
                background: qradialgradient(cx:0.5, cy:0.5, radius:1, fx:0.5, fy:0.5, stop:0 #00CCFF, stop:1 #FFFFFF);
            }
        """)
        self.update()

        # === THÊM MỚI: Robot tự nói lời chào khi vào màn hình ===
        QTimer.singleShot(1000, self.initial_greeting) # Chờ 1s để app ổn định

    def refresh_grid(self):
        # (Giữ nguyên)
        self.clear_layout(self.grid_layout)
        col_count = 2 
        row, col = 0, 0

        valid_products = [p for p in self.products if p['quantity'] > 0]
        print(f"Số sản phẩm hợp lệ: {len(valid_products)}")

        for product in valid_products:
            prod_btn = QPushButton()
            prod_btn.setFixedSize(650, 700)
            prod_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            prod_btn.setStyleSheet('background-color: white; border: 2px solid #E0E0E0; border-radius: 15px;')

            layout = QVBoxLayout()
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

            text_label = QLabel(f"{product['name']}\n{product['price']:,} VND".replace(',', '.'))
            text_label.setStyleSheet('font-size: 48px; color: #FF6200;')
            text_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(text_label)

            prod_btn.setLayout(layout)
            prod_btn.clicked.connect(lambda _, p=product: self.on_product_clicked(p))
            
            self.grid_layout.addWidget(prod_btn, row, col)

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
    def init_payment_screen(self):
        # (Phần dọn dẹp và tiêu đề giữ nguyên)
        self.clear_layout(self.main_layout)

        title = QLabel('THANH TOÁN QUA mã QR')
        title.setStyleSheet('font-size: 48px; font-weight: bold; color: #FF6200;')
        self.main_layout.addWidget(title, alignment=Qt.AlignCenter)

        # === THAY ĐỔI LỚN TẠI ĐÂY: GỌI HÀM TẠO QR ĐỘNG ===
        qr_image_path = self.generate_vnpay_qr_url() # <-- GỌI HÀM MỚI TẠO
        
        qr_label = QLabel()
        
        if qr_image_path and os.path.exists(qr_image_path):
            pixmap = QPixmap(qr_image_path)
        else:
            pixmap = QPixmap() # Tạo một QPixmap rỗng
            
        if pixmap.isNull():
            qr_label.setText(f'Không thể tạo ảnh QR! Vui lòng kiểm tra log.')
            qr_label.setStyleSheet('font-size: 24px; color: red;')
        else:
            scaled_pixmap = pixmap.scaled(500, 500, Qt.KeepAspectRatio)
            qr_label.setPixmap(scaled_pixmap)
        # === KẾT THÚC THAY ĐỔI LỚN ===

        qr_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(qr_label)

        payment_info = QLabel(
            f"Sản phẩm: {self.selected_product['name']}\n"
            f"Số tiền: {self.selected_product['price'] * self.quantity:,} VND".replace(',', '.')
        )
        payment_info.setStyleSheet('font-size: 48px; color: #002266;')
        payment_info.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(payment_info)

        # (Phần Timer và self.speak giữ nguyên)
        self.timer_label = QLabel('Thời gian còn lại: 40 giây')
        self.timer_label.setStyleSheet('font-size: 48px; color: #002266;')
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.timer_label)

        self.remaining_time = 40
        self.payment_timer = QTimer()
        self.payment_timer.timeout.connect(self.update_payment_timer)
        self.payment_timer.start(1000)

        # Robot nói hướng dẫn thanh toán
        self.speak(f"Bạn vui lòng quét mã QR để thanh toán {self.selected_product['price'] * self.quantity:,} đồng.")

        self.update()

    # Dòng 743 (Sửa lại hàm này)
    def generate_vnpay_qr_url(self):
        """
        Tạo mã QR VietQR động và lưu vào một file để hiển thị.
        """
        try:
            # 1. CẤU HÌNH TÀI KHOẢN (THAY THẾ BẰNG THÔNG TIN CỦA BẠN)
            # THAY CÁC GIÁ TRỊ NÀY VÀO ĐỂ TẠO QR CỦA BẠN
            MY_BANK_ID = "MB"         # Mã ngân hàng (VD: MB, VCB, TECH, ACB,...)
            MY_ACCOUNT_NO = "0379262302"  # Số tài khoản của bạn
            
            # Lấy thông tin đơn hàng
            tong_tien = self.selected_product['price'] * self.quantity
            noi_dung = f"Thanh toan don hang {self.order_id}"
            
            # 2. TẠO URL GỌI API VIETQR.IO
            TEMPLATE = "compact"
            qr_url = (
                f"https://img.vietqr.io/image/{MY_BANK_ID}-{MY_ACCOUNT_NO}-{TEMPLATE}.png?"
                f"amount={tong_tien}&"
                f"addInfo={noi_dung.replace(' ', '%20')}&"
                f"accountName=CongtyVTG" # Tên gợi ý (Tùy chọn)
            )
            
            # 3. GỌI API VÀ LƯU ẢNH VÀO FILE
            response = requests.get(qr_url, timeout=10)
            
            if response.status_code == 200:
                qr_image_path = r'images/vietqr.png' # SỬ DỤNG TÊN FILE MỚI
                
                # Tạo thư mục images nếu chưa có
                os.makedirs(os.path.dirname(qr_image_path), exist_ok=True)
                
                with open(qr_image_path, 'wb') as f:
                    f.write(response.content)
                
                print(f"Đã tạo QR thành công: {qr_image_path}")
                return qr_image_path
            else:
                print(f"Lỗi khi gọi API VietQR: HTTP {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"Lỗi kết nối khi tạo QR: {e}")
            return None

    def update_payment_timer(self):
        # (Giữ nguyên)
        self.remaining_time -= 1
        self.timer_label.setText(f'Thời gian còn lại: {self.remaining_time} giây')

        if self.remaining_time <= 0:
            # Logic HẾT THỜI GIAN thanh toán (Fail)
            self.payment_timer.stop()
            QMessageBox.warning(self, 'Thông báo', 'Thanh toán thất bại, vui lòng thử lại.')
            self.speak("Đã hết thời gian thanh toán. Mời bạn thực hiện lại.")
            self.reset_to_product_screen()
        # elif self.remaining_time == 1:  # Giả lập thanh toán <--- DÒNG NÀY ĐÃ ĐƯỢC XÓA HOẶC COMMENT
        #     self.payment_timer.stop()
        #     self.speak("Thanh toán thành công. Mời bạn chờ pha chế.")
        #     self.start_brewing()

    def start_brewing(self):
        # (Giữ nguyên)
        self.clear_layout(self.main_layout)

        title = QLabel('ĐANG PHA CHẾ...')
        title.setStyleSheet('font-size: 30px; font-weight: bold; color: #FF6200;')
        self.main_layout.addWidget(title, alignment=Qt.AlignCenter)

        self.brewing_label = QLabel('Chuẩn bị nguyên liệu...')
        self.brewing_label.setStyleSheet('font-size: 20px; color: #002266;')
        self.main_layout.addWidget(self.brewing_label, alignment=Qt.AlignCenter)

        self.brewing_steps = [
            'Chuẩn bị nguyên liệu...',
            'Pha chế đồ uống...',
            'Hoàn thiện sản phẩm...',
        ]
        self.brewing_step = 0
        self.brewing_timer = QTimer()
        self.brewing_timer.timeout.connect(self.update_brewing)
        self.brewing_timer.start(1000)

        if self.serial_port:
            product_id = self.selected_product['id']
            self.serial_port.write(f"P{product_id}\n".encode())
            sleep(1)
            self.serial_port.write(f"S{self.sugar_amount}\n".encode())

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
        self.selected_product = None
        self.sugar_amount = 10
        self.quantity = 1
        self.order_id = str(int(time()))
        # Reset lại lịch sử chat nhưng giữ lại system prompt
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