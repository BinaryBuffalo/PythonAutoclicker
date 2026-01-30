# -*- coding: utf-8 -*-
import sys
import threading
import random
import time
import json
import os
import math

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QSlider,
    QPushButton, QHBoxLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QFrame, QGroupBox, QCheckBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from pynput import mouse, keyboard

# --- Helper functions ---
def key_to_string(key):
    if isinstance(key, keyboard.Key): return key.name
    elif isinstance(key, keyboard.KeyCode): return getattr(key, 'char', None)
    return None
def string_to_key(key_str):
    if key_str is None: return None
    try: return getattr(keyboard.Key, key_str)
    except AttributeError:
        try: return keyboard.KeyCode.from_char(key_str)
        except (TypeError, ValueError): print(f"Warning: Could not convert string '{key_str}' back to key."); return None
def button_to_string(button):
    if button: return button.name
    return None
def string_to_button(button_str):
    if button_str: return getattr(mouse.Button, button_str, None)
    return None

# --- Signal class ---
class WorkerSignals(QObject):
    update_status = pyqtSignal(str)
    set_toggle_display = pyqtSignal(str)
    update_last_interval = pyqtSignal(float)

class AutoClicker:
    def __init__(self, signals):
        self.signals = signals
        self.running = False
        self.armed = False  
        self.smart_mode = False 
        
        # --- Counters for Math Logic ---
        self.total_events = 0
        self.virtual_clicks = 0
        
        self.button = mouse.Button.left
        # --- Default Values ---
        self.min_cps = 8.00; self.max_cps = 12.00
        self.interval_algorithm = "Uniform CPS (Uniform Jitter)"
        self.min_jitter_ms = 5.0
        self.max_jitter_ms = 15.0
        self.gaussian_cps_std_dev_factor = 0.25
        self.click_duration_mean_ms = 30.0; self.click_duration_std_dev_ms = 5.0
        self.shake_amount_x = 1; self.shake_amount_y = 1
        self.drag_x_min = 0; self.drag_x_max = 0; self.drag_y_min = 0; self.drag_y_max = 0
        self.sensitivity = 1.0; self.drag_smoothness_factor = 0.3
        self.buffer = []; self.mouse_controller = mouse.Controller(); self.click_thread = None
        self._burst_target_cps = (self.min_cps + self.max_cps) / 2.0; self._burst_trend = 0

    def set_options(self, min_cps, max_cps, algorithm, min_jitter_ms, max_jitter_ms,
                    cps_std_dev_factor, duration_mean, duration_std_dev,
                    shake_x, shake_y, drag_x_min, drag_x_max, drag_y_min, drag_y_max,
                    sensitivity, drag_smoothness, click_type, smart_mode):
        
        # Force a clean reset when settings change
        self.cleanup_session()
        self.running = False
        self.armed = False
        self.signals.update_status.emit("Status: Settings Applied (Stopped/Disarmed)")

        self.min_cps = float(min_cps); self.max_cps = float(max(max_cps, min_cps + 0.01))
        self.interval_algorithm = algorithm
        self.min_jitter_ms = float(min_jitter_ms)
        self.max_jitter_ms = float(max(max_jitter_ms, min_jitter_ms))
        self.gaussian_cps_std_dev_factor = float(cps_std_dev_factor)
        self.click_duration_mean_ms = float(duration_mean); self.click_duration_std_dev_ms = float(max(0.1, duration_std_dev))
        self.shake_amount_x = shake_x; self.shake_amount_y = shake_y
        self.drag_x_min = drag_x_min; self.drag_x_max = max(drag_x_max, drag_x_min)
        self.drag_y_min = drag_y_min; self.drag_y_max = max(drag_y_max, drag_y_min)
        self.sensitivity = float(sensitivity); self.drag_smoothness_factor = float(drag_smoothness)
        self.button = mouse.Button.left if click_type == 'Left' else mouse.Button.right
        self.smart_mode = smart_mode 
        self._burst_target_cps = (self.min_cps + self.max_cps) / 2.0; self._burst_trend = 0
        self.prepare_click_buffer()

    def prepare_click_buffer(self):
        self.buffer = []; buffer_size = 500
        avg_cps=(self.min_cps+self.max_cps)/2.0; cps_range=self.max_cps-self.min_cps
        cps_std_dev=(cps_range*self.gaussian_cps_std_dev_factor) if cps_range>0 else 0.1
        safe_cps_std_dev=max(0.01,cps_std_dev); spike_chance=0.05; spike_fast_multiplier=0.5; spike_slow_multiplier=1.5
        burst_drift_chance=0.1; burst_drift_amount=(cps_range*0.05) if cps_range>0 else 0.05
        burst_max_trend_steps=10; burst_current_steps=0
        avg_jitter_sigma_ms = (self.min_jitter_ms + self.max_jitter_ms) / 2.0

        for i in range(buffer_size):
            base_interval=0.1; target_cps=avg_cps
            if "Uniform CPS" in self.interval_algorithm:
                 target_cps = random.uniform(self.min_cps, self.max_cps) if cps_range > 0 else self.min_cps
                 target_cps = max(1.0, target_cps)
                 base_interval = 1.0 / target_cps
            elif "Gaussian CPS" in self.interval_algorithm:
                 target_cps = random.gauss(avg_cps, safe_cps_std_dev)
                 target_cps = max(1.0, min(target_cps, self.max_cps * 1.2))
                 base_interval = 1.0 / target_cps
            elif "Spiky Random" in self.interval_algorithm:
                 normal_interval = 1.0 / max(1.0, avg_cps)
                 base_interval = normal_interval * (spike_fast_multiplier if random.random() < 0.5 else spike_slow_multiplier) if random.random() < spike_chance else normal_interval
            elif "Humanized Burst" in self.interval_algorithm:
                 if random.random()<burst_drift_chance or burst_current_steps>=burst_max_trend_steps: self._burst_trend=random.choice([-1,0,1]); burst_current_steps=0
                 else: burst_current_steps+=1
                 self._burst_target_cps+=self._burst_trend*burst_drift_amount; self._burst_target_cps=max(self.min_cps,min(self._burst_target_cps,self.max_cps))
                 target_cps=random.gauss(self._burst_target_cps,safe_cps_std_dev/2.0); target_cps=max(1.0,target_cps); base_interval=1.0/target_cps

            interval_with_jitter = base_interval
            if "Uniform Jitter" in self.interval_algorithm:
                if self.max_jitter_ms > self.min_jitter_ms:
                     jitter_amount_sec = random.uniform(self.min_jitter_ms, self.max_jitter_ms) / 1000.0
                     if random.random() < 0.5: interval_with_jitter += jitter_amount_sec
                     else: interval_with_jitter -= jitter_amount_sec
            elif "Gaussian Jitter" in self.interval_algorithm:
                 jitter_sigma_sec = max(0.0001, avg_jitter_sigma_ms / 1000.0)
                 jitter_amount_sec = random.gauss(0, jitter_sigma_sec)
                 interval_with_jitter += jitter_amount_sec

            duration_std_dev_sec=max(0.0001,self.click_duration_std_dev_ms/1000.0); click_duration_sec=random.gauss(self.click_duration_mean_ms/1000.0,duration_std_dev_sec); click_duration_sec=max(0.001,click_duration_sec)
            final_delay_sec=max(0.001,interval_with_jitter-click_duration_sec)
            dx=int(random.randint(-self.shake_amount_x,self.shake_amount_x)*self.sensitivity); dy=int(random.randint(-self.shake_amount_y,self.shake_amount_y)*self.sensitivity)
            drag_x=int(random.randint(self.drag_x_min,self.drag_x_max)*self.sensitivity); drag_y=int(random.randint(self.drag_y_min,self.drag_y_max)*self.sensitivity)
            self.buffer.append((final_delay_sec, click_duration_sec, dx, dy, drag_x, drag_y))

    def cleanup_session(self):
        """Resets counters to prevent stale data from breaking the next click session."""
        self.total_events = 0
        self.virtual_clicks = 0
        print("Session Cleaned: Counters reset to 0.")

    def toggle(self):
        if not self.smart_mode:
            # Standard Mode Toggle
            self.running = not self.running
            self.armed = False
            status = "Running" if self.running else "Stopped"
            self.signals.update_status.emit(f"Status: {status}")
            if self.running:
                if self.click_thread and self.click_thread.is_alive(): return
                self.click_thread = threading.Thread(target=self.run_clicker, daemon=True)
                self.click_thread.start()
        else:
            # Smart Mode Toggle (Arm/Disarm)
            self.armed = not self.armed
            self.cleanup_session() # CLEANUP ON TOGGLE
            self.running = False
            status = "ARMED (Hold Click)" if self.armed else "DISARMED (Smart Mode)"
            self.signals.update_status.emit(f"Status: {status}")

    def start_smart_click(self):
        """Called by the listener when Smart Mode is ARMED and Mouse is Pressed."""
        if not self.armed: return
        if self.running: return 
        
        # --- CRITICAL FIX: Reset counters right before starting ---
        # We set total_events to 1 because the physical 'Down' just happened to trigger this.
        self.total_events = 1 
        self.virtual_clicks = 0
        self.running = True
        
        self.click_thread = threading.Thread(target=self.run_clicker, daemon=True)
        self.click_thread.start()

    def run_clicker(self):
        idx = 0; self.prepare_click_buffer()
        print(f"Clicker Thread Started. Smart Mode: {self.smart_mode}")
        
        while self.running:
            if not self.buffer or idx >= len(self.buffer):
                if not self.running: break
                self.prepare_click_buffer()
                if not self.buffer: time.sleep(0.1); continue
                idx = 0
            
            delay_sec, click_duration_sec, dx, dy, drag_x, drag_y = self.buffer[idx]
            
            try:
                # --- Clicking Logic ---
                start_pos = self.mouse_controller.position
                
                if drag_x != 0 or drag_y != 0: pass 

                pos_before_click = self.mouse_controller.position
                shaken_pos = (pos_before_click[0]+dx, pos_before_click[1]+dy)
                self.mouse_controller.position = shaken_pos
                
                self.mouse_controller.press(self.button)
                time.sleep(max(0.001, click_duration_sec))
                self.mouse_controller.release(self.button)
                
                self.virtual_clicks += 1
                self.mouse_controller.position = pos_before_click
                self.signals.update_last_interval.emit(delay_sec + click_duration_sec)

                # --- SMART MODE MATH CHECK ---
                if self.smart_mode:
                    if not self.armed:
                        self.running = False; break

                    # Wait a tiny bit for the listener to process the 'Up' event we just sent
                    time.sleep(0.002) 
                    
                    check_val = self.total_events - (self.virtual_clicks * 2)
                    
                    # Debug log (Optional, remove if spammy)
                    # print(f"[Smart Logic] Virt: {self.virtual_clicks} | Total: {self.total_events} | Check: {check_val}")
                    
                    # LOGIC:
                    # Ideal State (Hold): Check = 1
                    # Released State: Check = 2
                    # Accidental Click during hold: Check = 3 (Wait, let's just stop to be safe)
                    
                    if check_val >= 2:
                        print(f">> Release Detected (Check: {check_val}). Stopping.")
                        self.running = False
                        self.signals.update_status.emit("Status: ARMED (Waiting)")
                        # NO BREAK HERE yet, we break after the finally block or loop logic handles it
                        break

            except Exception as e: 
                print(f"Error: {e}")
                self.running = False
                break
            
            time.sleep(max(0.001, delay_sec)); idx += 1
        
        # --- LOOP EXIT / CLEANUP ---
        # This ensures that when the loop dies, we are ready for the next click immediately
        self.cleanup_session()
        self.running = False 


class ClickerGUI(QWidget):
    def __init__(self):
        super().__init__(); self.signals=WorkerSignals(); self.signals.update_status.connect(self.update_status_label)
        self.signals.set_toggle_display.connect(self.update_toggle_display); self.signals.update_last_interval.connect(self.update_last_interval_label)
        self.setWindowTitle("Smart Auto Clicker V7.5 (Cleanup Fix)"); self.clicker=AutoClicker(self.signals)
        self.toggle_key=None; self.toggle_button=None; self.listening_for_toggle=False
        self.default_config_path=os.path.join(os.path.expanduser("~"),"autoclicker_config_v7.json")
        self.kb_listener=None; self.mouse_listener=None
        self.init_ui(); self.apply_settings(); self.load_config(self.default_config_path,silent=True); self.start_listeners()

    def update_status_label(self, text): self.status_label.setText(text)
    def update_toggle_display(self, text): self.toggle_display_label.setText(text)
    def update_last_interval_label(self, interval_sec): self.last_interval_label.setText(f"Last Interval: {interval_sec * 1000:.2f} ms")

    def init_ui(self):
        main_layout = QVBoxLayout(); top_bar_layout = QHBoxLayout(); status_group = QGroupBox("Status"); status_layout = QVBoxLayout()
        self.status_label = QLabel("Status: Stopped."); self.last_interval_label = QLabel("Last Interval: N/A")
        status_layout.addWidget(self.status_label); status_layout.addWidget(self.last_interval_label); status_group.setLayout(status_layout); top_bar_layout.addWidget(status_group, 1)
        toggle_group = QGroupBox("Activation"); toggle_layout = QVBoxLayout(); self.set_toggle_button = QPushButton("Set Toggle Key/Button"); self.set_toggle_button.clicked.connect(self.set_toggle_prompt)
        self.toggle_display_label = QLabel("Current Toggle: None"); toggle_layout.addWidget(self.set_toggle_button); toggle_layout.addWidget(self.toggle_display_label); toggle_group.setLayout(toggle_layout); top_bar_layout.addWidget(toggle_group, 1); main_layout.addLayout(top_bar_layout)
        
        settings_layout = QVBoxLayout(); timing_group = QGroupBox("Click Timing & Rate"); timing_layout = QVBoxLayout(); cps_box = QHBoxLayout()
        self.min_cps_spinbox = self.create_double_spinbox(1.00, 100.00, 8.00, 0.10, 2); self.max_cps_spinbox = self.create_double_spinbox(1.00, 100.00, 12.00, 0.10, 2)
        cps_box.addWidget(QLabel("Min CPS:")); cps_box.addWidget(self.min_cps_spinbox); cps_box.addWidget(QLabel("Max CPS:")); cps_box.addWidget(self.max_cps_spinbox); timing_layout.addLayout(cps_box)
        duration_box = QHBoxLayout(); self.duration_mean_spinbox = self.create_double_spinbox(1.0, 500.0, 30.0, 1.0, 1); self.duration_stddev_spinbox = self.create_double_spinbox(0.1, 100.0, 5.0, 0.5, 1)
        duration_box.addWidget(QLabel("Click Duration (ms): Mean")); duration_box.addWidget(self.duration_mean_spinbox); duration_box.addWidget(QLabel("StdDev")); duration_box.addWidget(self.duration_stddev_spinbox); timing_layout.addLayout(duration_box)

        algo_box = QHBoxLayout(); self.algorithm_combo = QComboBox(); self.algorithm_combo.addItems(["Uniform CPS (Uniform Jitter)", "Gaussian CPS (Gaussian Jitter)", "Spiky Random (Uniform Jitter)", "Humanized Burst (Gaussian Jitter)"])
        algo_box.addWidget(QLabel("Algorithm:")); algo_box.addWidget(self.algorithm_combo, 1)
        self.min_jitter_spinbox = self.create_double_spinbox(0.00, 200.00, 5.00, 0.50, 2); self.max_jitter_spinbox = self.create_double_spinbox(0.00, 200.00, 15.00, 0.50, 2)
        algo_box.addWidget(QLabel("Jitter (ms): Min")); algo_box.addWidget(self.min_jitter_spinbox); algo_box.addWidget(QLabel("Max")); algo_box.addWidget(self.max_jitter_spinbox)
        self.cps_stddev_factor_spinbox = self.create_double_spinbox(0.01, 1.00, 0.25, 0.01, 2); algo_box.addWidget(QLabel("Gauss σ:")); algo_box.addWidget(self.cps_stddev_factor_spinbox); timing_layout.addLayout(algo_box); timing_group.setLayout(timing_layout); settings_layout.addWidget(timing_group)

        movement_group = QGroupBox("Movement"); movement_layout = QVBoxLayout(); shake_box = QHBoxLayout()
        self.shake_x_layout, self.shake_x_slider, self.shake_x_label = self.create_slider(0, 25, 1, "Shake X"); shake_box.addLayout(self.shake_x_layout)
        self.shake_y_layout, self.shake_y_slider, self.shake_y_label = self.create_slider(0, 25, 1, "Shake Y"); shake_box.addLayout(self.shake_y_layout); movement_layout.addLayout(shake_box)
        drag_range_box = QHBoxLayout(); self.drag_x_min_spin, self.drag_x_max_spin = self.create_spinbox_range(-75, 75, 0, 0); drag_range_box.addWidget(QLabel("Drag X:")); drag_range_box.addWidget(self.drag_x_min_spin); drag_range_box.addWidget(self.drag_x_max_spin)
        self.drag_y_min_spin, self.drag_y_max_spin = self.create_spinbox_range(-75, 75, 0, 0); drag_range_box.addWidget(QLabel("Drag Y:")); drag_range_box.addWidget(self.drag_y_min_spin); drag_range_box.addWidget(self.drag_y_max_spin); movement_layout.addLayout(drag_range_box)
        drag_smooth_box = QHBoxLayout(); self.drag_smooth_spinbox = self.create_double_spinbox(0.00, 1.00, 0.30, 0.05, 2); drag_smooth_box.addWidget(QLabel("Smoothness:")); drag_smooth_box.addWidget(self.drag_smooth_spinbox)
        self.sensitivity_spinbox = self.create_double_spinbox(0.10, 5.00, 1.00, 0.05, 2); drag_smooth_box.addWidget(QLabel("Sensitivity:")); drag_smooth_box.addWidget(self.sensitivity_spinbox); movement_layout.addLayout(drag_smooth_box); movement_group.setLayout(movement_layout); settings_layout.addWidget(movement_group)
        
        # --- General Group ---
        general_group = QGroupBox("General"); general_layout = QHBoxLayout(); self.click_type_box = QComboBox(); self.click_type_box.addItems(["Left", "Right"])
        general_layout.addWidget(QLabel("Click Type:")); general_layout.addWidget(self.click_type_box)
        
        self.smart_mode_check = QCheckBox("Enable Smart Hold-to-Click (Math Detection)"); self.smart_mode_check.setChecked(False)
        self.smart_mode_check.setToolTip("Toggle key ARMS the clicker. Holding Mouse triggers it. \nTotal vs Virtual clicks logic detects release.")
        self.smart_mode_check.stateChanged.connect(self.apply_settings)
        general_layout.addWidget(self.smart_mode_check)
        
        general_group.setLayout(general_layout); settings_layout.addWidget(general_group)
        main_layout.addLayout(settings_layout); button_box = QHBoxLayout(); self.apply_button = QPushButton("Apply Settings"); self.apply_button.clicked.connect(self.apply_settings); button_box.addWidget(self.apply_button)
        self.save_button = QPushButton("Save Config"); self.save_button.clicked.connect(self.save_config); button_box.addWidget(self.save_button); self.load_button = QPushButton("Load Config"); self.load_button.clicked.connect(lambda: self.load_config()); button_box.addWidget(self.load_button); main_layout.addLayout(button_box)
        self.setLayout(main_layout)

    def create_double_spinbox(self, min_val, max_val, start_val, step, decimals): spinbox = QDoubleSpinBox(); spinbox.setRange(min_val, max_val); spinbox.setValue(start_val); spinbox.setSingleStep(step); spinbox.setDecimals(decimals); return spinbox
    def create_spinbox_range(self, min_val, max_val, start_min, start_max): min_spin = QSpinBox(); min_spin.setRange(min_val, max_val); min_spin.setValue(start_min); max_spin = QSpinBox(); max_spin.setRange(min_val, max_val); max_spin.setValue(start_max); min_spin.valueChanged.connect(lambda val: max_spin.setMinimum(val)); max_spin.valueChanged.connect(lambda val: min_spin.setMaximum(val)); return min_spin, max_spin
    def create_slider(self, min_val, max_val, start, label_text): layout = QHBoxLayout(); label_widget = QLabel(f"{label_text}: {start}"); slider = QSlider(Qt.Horizontal); slider.setRange(min_val, max_val); slider.setValue(start); slider.valueChanged.connect(lambda val, l=label_widget, t=label_text: l.setText(f"{t}: {val}")); layout.addWidget(label_widget, 1); layout.addWidget(slider, 2); return layout, slider, label_widget

    def apply_settings(self):
        min_cps=self.min_cps_spinbox.value(); max_cps=self.max_cps_spinbox.value()
        algorithm=self.algorithm_combo.currentText()
        min_jitter_ms=self.min_jitter_spinbox.value(); max_jitter_ms=self.max_jitter_spinbox.value()
        cps_std_dev_factor=self.cps_stddev_factor_spinbox.value()
        duration_mean=self.duration_mean_spinbox.value(); duration_std_dev=self.duration_stddev_spinbox.value()
        shake_x=self.shake_x_slider.value(); shake_y=self.shake_y_slider.value()
        drag_x_min=self.drag_x_min_spin.value(); drag_x_max=self.drag_x_max_spin.value()
        drag_y_min=self.drag_y_min_spin.value(); drag_y_max=self.drag_y_max_spin.value()
        sensitivity=self.sensitivity_spinbox.value(); drag_smoothness=self.drag_smooth_spinbox.value()
        click_type=self.click_type_box.currentText()
        smart_mode = self.smart_mode_check.isChecked()

        if max_cps <= min_cps: self.max_cps_spinbox.setValue(min_cps + 0.01); max_cps = min_cps + 0.01
        if max_jitter_ms < min_jitter_ms: self.max_jitter_spinbox.setValue(min_jitter_ms); max_jitter_ms = min_jitter_ms

        self.clicker.set_options(min_cps, max_cps, algorithm, min_jitter_ms, max_jitter_ms,
                                 cps_std_dev_factor, duration_mean, duration_std_dev,
                                 shake_x, shake_y, drag_x_min, drag_x_max, drag_y_min, drag_y_max,
                                 sensitivity, drag_smoothness, click_type, smart_mode) 
        print(f"Settings applied. Smart Mode: {smart_mode}")

    def set_toggle_prompt(self): self.status_label.setText("Press any key/mouse button..."); self.listening_for_toggle = True;
    def set_toggle_input(self, input_obj):
        current_toggle_str = "None"; self.toggle_key = None; self.toggle_button = None
        if isinstance(input_obj, keyboard.Key) or isinstance(input_obj, keyboard.KeyCode):
            self.toggle_key = input_obj; key_str = key_to_string(input_obj)
            if key_str: current_toggle_str = f"Key: {key_str}"
        elif isinstance(input_obj, mouse.Button):
            self.toggle_button = input_obj; button_str = button_to_string(input_obj); current_toggle_str = f"Mouse: {button_str}"
        self.listening_for_toggle = False; self.signals.update_status.emit("Status: Stopped"); self.signals.set_toggle_display.emit(f"Current Toggle: {current_toggle_str}")

    def start_listeners(self):
        try:
            if self.kb_listener is None or not self.kb_listener.is_alive():self.kb_listener=keyboard.Listener(on_press=self.on_press);self.kb_listener.start()
            if self.mouse_listener is None or not self.mouse_listener.is_alive():self.mouse_listener=mouse.Listener(on_click=self.on_click);self.mouse_listener.start()
        except Exception as e: QMessageBox.critical(self,"Listener Error",f"Could not start: {e}")
    
    def on_press(self, key):
        if self.listening_for_toggle: self.set_toggle_input(key)
        elif key == self.toggle_key: self.toggle_clicker()
            
    def on_click(self, x, y, button, pressed):
        if self.listening_for_toggle and pressed: 
            self.set_toggle_input(button)
            return
        if button == self.toggle_button and pressed: 
            self.toggle_clicker()
            return

        # Track stats for Smart Mode
        if button == self.clicker.button:
            if pressed: self.clicker.total_events += 1
            else: self.clicker.total_events += 1

        if self.clicker.smart_mode and self.clicker.armed and button == self.clicker.button and pressed:
            self.clicker.start_smart_click()

    def toggle_clicker(self): self.clicker.toggle()

    def save_config(self):
        cfg_path,_=QFileDialog.getSaveFileName(self,"Save Config",self.default_config_path,"JSON (*.json)");
        if not cfg_path:return; 
        if not cfg_path.lower().endswith('.json'): cfg_path += '.json'
        settings = {
            'min_cps': self.min_cps_spinbox.value(), 'max_cps': self.max_cps_spinbox.value(),
            'algorithm': self.algorithm_combo.currentText(),
            'min_jitter_ms': self.min_jitter_spinbox.value(), 'max_jitter_ms': self.max_jitter_spinbox.value(),
            'cps_std_dev_factor': self.cps_stddev_factor_spinbox.value(),
            'duration_mean_ms': self.duration_mean_spinbox.value(), 'duration_std_dev_ms': self.duration_stddev_spinbox.value(),
            'shake_x': self.shake_x_slider.value(), 'shake_y': self.shake_y_slider.value(),
            'drag_x_min': self.drag_x_min_spin.value(), 'drag_x_max': self.drag_x_max_spin.value(),
            'drag_y_min': self.drag_y_min_spin.value(), 'drag_y_max': self.drag_y_max_spin.value(),
            'sensitivity': self.sensitivity_spinbox.value(), 'drag_smoothness': self.drag_smooth_spinbox.value(),
            'click_type': self.click_type_box.currentText(),
            'smart_mode': self.smart_mode_check.isChecked(),
            'toggle_key': key_to_string(self.toggle_key), 'toggle_button': button_to_string(self.toggle_button)
        }
        try:
            with open(cfg_path,'w') as f: json.dump(settings,f,indent=4)
            self.default_config_path=cfg_path; QMessageBox.information(self,"Success",f"Saved to {cfg_path}")
        except Exception as e: QMessageBox.warning(self,"Error",f"Failed save: {e}")

    def load_config(self, cfg_path=None, silent=False):
         if cfg_path is None: cfg_path,_=QFileDialog.getOpenFileName(self,"Load Config",self.default_config_path,"JSON (*.json)")
         if not cfg_path or not os.path.exists(cfg_path): return
         try:
              with open(cfg_path,'r') as f: settings = json.load(f)
              self.min_cps_spinbox.setValue(settings.setdefault('min_cps', 8.0)); self.max_cps_spinbox.setValue(settings.setdefault('max_cps', 12.0))
              self.algorithm_combo.setCurrentText(settings.setdefault('algorithm', "Uniform CPS (Uniform Jitter)"))
              self.min_jitter_spinbox.setValue(settings.setdefault('min_jitter_ms', 5.0)); self.max_jitter_spinbox.setValue(settings.setdefault('max_jitter_ms', 15.0))
              self.cps_stddev_factor_spinbox.setValue(settings.setdefault('cps_std_dev_factor', 0.25))
              self.duration_mean_spinbox.setValue(settings.setdefault('duration_mean_ms', 30.0)); self.duration_stddev_spinbox.setValue(settings.setdefault('duration_std_dev_ms', 5.0))
              self.shake_x_slider.setValue(settings.setdefault('shake_x', 1)); self.shake_y_slider.setValue(settings.setdefault('shake_y', 1))
              self.drag_x_min_spin.setValue(settings.setdefault('drag_x_min', 0)); self.drag_x_max_spin.setValue(settings.setdefault('drag_x_max', 0))
              self.drag_y_min_spin.setValue(settings.setdefault('drag_y_min', 0)); self.drag_y_max_spin.setValue(settings.setdefault('drag_y_max', 0))
              self.sensitivity_spinbox.setValue(settings.setdefault('sensitivity', 1.0)); self.drag_smooth_spinbox.setValue(settings.setdefault('drag_smoothness', 0.3))
              self.click_type_box.setCurrentText(settings.setdefault('click_type', 'Left'))
              self.smart_mode_check.setChecked(settings.setdefault('smart_mode', False))
              tk=settings.get('toggle_key'); tb=settings.get('toggle_button');
              if tk: self.set_toggle_input(string_to_key(tk))
              elif tb: self.set_toggle_input(string_to_button(tb))
              self.apply_settings(); self.default_config_path = cfg_path
         except Exception as e:
              if not silent: QMessageBox.warning(self,"Error",f"Failed load: {e}")

    def closeEvent(self, event):
        if self.clicker.running: self.clicker.toggle(); time.sleep(0.1)
        if self.kb_listener: self.kb_listener.stop() 
        if self.mouse_listener: self.mouse_listener.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = ClickerGUI()
    gui.show()
    sys.exit(app.exec_())