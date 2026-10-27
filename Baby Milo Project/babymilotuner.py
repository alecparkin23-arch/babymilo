import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
import serial
import serial.tools.list_ports
import random
import csv
import os
import traceback
import winsound
import re
import math

# =============================
# Configuration and Globals
# =============================
SERIAL_PORT = None
BAUD_RATE = 9600
ser = None

default_values = {
    "P": 22.0,
    "D": 0.10,
    "SP": 9.0,
    "SI": 0.30,
    "TP": 56.67,
    "TD": 0.0
}

sliders = {}
entry_boxes = {}
telemetry_buffer = {"angles": []}
telemetry_lock = threading.Lock()
tuning_history = []
auto_tuning_enabled = False
auto_tune_last_message = ""
auto_tune_last_run = 0.0
auto_tune_after_id = None


def clamp(value, lo, hi):
    return max(lo, min(hi, value))

# =============================
# Utility Functions
# =============================
def append_terminal(text):
    """Append text to terminal output."""
    terminal_output.insert(tk.END, text + "\n")
    terminal_output.see(tk.END)

def send_command(cmd):
    """Send a command string with checksum."""
    def calculate_checksum(payload):
        checksum = sum(ord(c) for c in payload) % 256
        return f"{checksum}"

    if ':' in cmd:
        payload = cmd
        checksum = calculate_checksum(payload)
        frame = f"<{payload}|{checksum}>\n"
        if ser and ser.is_open:
            try:
                ser.write(frame.encode())
                ser.flush()
            except Exception as e:
                append_terminal(f"Error sending command: {e}")
                return
        append_terminal(f"<Sent: {payload}|{checksum}>")
    else:
        plain = f"{cmd}\n"
        if ser and ser.is_open:
            try:
                ser.write(plain.encode())
                ser.flush()
            except Exception as e:
                append_terminal(f"Error sending command: {e}")
                return
        append_terminal(f"<Sent: {cmd}>")

    last_command.set(f"Sent: {cmd}")

# =============================
# Serial Connection Handlers
# =============================
def connect_serial(auto=False):
    global ser, SERIAL_PORT

    try:
        if auto:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            for p in ports:
                try:
                    ser = serial.Serial(p, BAUD_RATE, timeout=1)
                    SERIAL_PORT = p
                    port_combobox.set(p)
                    connection_status.set(f"Auto-connected: {p}")
                    append_terminal(f"Auto-connected to {p}")
                    return
                except serial.SerialException:
                    continue
            connection_status.set("Auto-connect failed")
        else:
            sel = port_combobox.get().strip()
            if not sel:
                connection_status.set("No port selected")
                return

            ser = serial.Serial(sel, BAUD_RATE, timeout=1)
            SERIAL_PORT = sel
            connection_status.set(f"Connected: {sel}")
            append_terminal(f"Connected to {sel}")

    except Exception as e:
        connection_status.set("Connection failed")
        append_terminal(f"Connection error: {e}")

def disconnect_serial():
    """Disconnect from serial."""
    global ser
    try:
        if ser and ser.is_open:
            ser.close()
            connection_status.set("Disconnected")
            append_terminal("Serial disconnected.")
    except Exception as e:
        append_terminal(f"Disconnection error: {e}")


# =============================
# Self-Tuning Functions
# =============================
def evaluate_performance(angle_data, target=0.0):
    """Score recent angle behavior from 0.0 to 1.0, where higher is better."""
    if len(angle_data) < 5:
        return 0.0

    mean_angle = sum(angle_data) / len(angle_data)
    variance = sum((angle - mean_angle) ** 2 for angle in angle_data) / len(angle_data)
    stability = 1.0 / (1.0 + variance)

    error_values = [abs(target - angle) for angle in angle_data]
    mean_error = sum(error_values) / len(error_values)
    tracking_error = 1.0 / (1.0 + mean_error)

    oscillation = sum(
        abs(angle_data[index] - angle_data[index - 1])
        for index in range(1, len(angle_data))
    ) / (len(angle_data) - 1)
    oscillation_penalty = 1.0 / (1.0 + oscillation / 4.0)

    overshoot_ratio = sum(
        abs(angle) > abs(target) + 1.0 for angle in angle_data
    ) / len(angle_data)
    overshoot_score = 1.0 - overshoot_ratio

    settle_threshold = max(0.5, abs(target) * 0.15 + 0.5)
    settle_ratio = sum(error < settle_threshold for error in error_values) / len(error_values)

    score = (
        stability * 0.30
        + tracking_error * 0.30
        + oscillation_penalty * 0.20
        + overshoot_score * 0.10
        + settle_ratio * 0.10
    )
    return clamp(score, 0.0, 1.0)


def local_tune_pid(current_params, performance_score, recent_angles, tuning_mode="Balanced"):
    """Adjust PID values in small mode-dependent steps from recent telemetry."""
    tuned = dict(current_params)
    if not recent_angles:
        return tuned

    target = float(current_params.get("SP", 0.0))
    mean_angle = sum(recent_angles) / len(recent_angles)
    recent_delta = [
        recent_angles[index] - recent_angles[index - 1]
        for index in range(1, len(recent_angles))
    ]
    oscillation = sum(abs(delta) for delta in recent_delta) / max(len(recent_delta), 1)
    mean_error = sum(abs(target - angle) for angle in recent_angles) / len(recent_angles)
    overshoot = sum(
        abs(angle) > abs(target) + 1.0 for angle in recent_angles
    ) / len(recent_angles)
    settle_threshold = max(0.5, abs(target) * 0.15 + 0.5)
    settle_ratio = sum(
        abs(target - angle) < settle_threshold for angle in recent_angles
    ) / len(recent_angles)

    mode_strength = {"Conservative": 0.03, "Balanced": 0.06, "Aggressive": 0.10}.get(
        tuning_mode, 0.06
    )

    if performance_score > 0.82:
        log_auto_tune("Performance stable, no adjustment needed.")
        return tuned

    reason = "No adjustment needed."
    if oscillation > 4.0 or overshoot > 0.28:
        tuned["P"] *= 1.0 - mode_strength * 1.4
        tuned["D"] *= 1.0 + mode_strength * 2.0
        tuned["SI"] *= 1.0 - mode_strength * 0.5
        reason = "Reduced overshoot and damping oscillation."
    elif mean_error > max(1.0, abs(target) * 0.2):
        tuned["P"] *= 1.0 + mode_strength * 1.2
        tuned["SI"] *= 1.0 + mode_strength * 1.6
        reason = "Improved tracking and reduced steady-state error."
    elif settle_ratio < 0.65:
        tuned["P"] *= 1.0 + mode_strength * 0.8
        tuned["D"] *= 1.0 + mode_strength * 1.1
        tuned["SI"] *= 1.0 + mode_strength * 1.3
        reason = "Encouraging faster settling."

    if abs(mean_angle) > 3.0:
        tuned["SI"] *= 1.05
        reason = "Countered drift and mean-angle bias."

    tuned["P"] = clamp(tuned["P"], 5.0, 100.0)
    tuned["D"] = clamp(tuned["D"], 0.0, 5.0)
    tuned["SP"] = clamp(tuned["SP"], 1.0, 50.0)
    tuned["SI"] = clamp(tuned["SI"], 0.0, 1.0)
    tuned["TP"] = clamp(tuned["TP"], 10.0, 100.0)
    tuned["TD"] = clamp(tuned["TD"], 0.0, 5.0)

    log_auto_tune(reason)
    return tuned


def apply_tuning(new_params):
    for key, value in new_params.items():
        if key not in sliders:
            continue
        sliders[key].set(value)
        entry_boxes[key].delete(0, tk.END)
        entry_boxes[key].insert(0, f"{value:.2f}")
        send_command(f"{key}={value:.2f}")
    append_terminal("Auto-tune: Applied new PID values.")


def log_auto_tune(message):
    global auto_tune_last_message
    if message != auto_tune_last_message:
        append_terminal(f"Auto-tune: {message}")
        auto_tune_last_message = message


def auto_tune_tick():
    global auto_tune_after_id, auto_tune_last_run
    auto_tune_after_id = None
    if not auto_tuning_enabled:
        return

    with telemetry_lock:
        recent_angles = telemetry_buffer["angles"][-50:]

    if len(recent_angles) < 20:
        log_auto_tune("Waiting for more telemetry samples...")
    elif time.monotonic() - auto_tune_last_run >= 10:
        try:
            current = {key: float(entry_boxes[key].get()) for key in default_values}
            if not all(math.isfinite(value) for value in current.values()):
                raise ValueError("PID values must be finite")
        except ValueError:
            log_auto_tune("Enter valid PID values before tuning.")
        else:
            score = evaluate_performance(recent_angles, target=current["SP"])
            new_params = local_tune_pid(
                current, score, recent_angles, tuning_mode_var.get()
            )
            if new_params != current:
                apply_tuning(new_params)
                tuning_history.append({
                    "time": time.time(),
                    "score": score,
                    "params": new_params,
                })
                auto_tune_last_run = time.monotonic()
            else:
                log_auto_tune("No tuning adjustment needed.")

    if auto_tuning_enabled:
        auto_tune_after_id = root.after(3000, auto_tune_tick)


def toggle_auto_tuning():
    global auto_tuning_enabled, auto_tune_after_id, auto_tune_last_run
    auto_tuning_enabled = not auto_tuning_enabled
    if auto_tuning_enabled:
        auto_tune_status.set("Auto-tune ON")
        auto_tune_last_run = time.monotonic()
        append_terminal("Auto-tune enabled.")
        auto_tune_after_id = root.after(0, auto_tune_tick)
    else:
        auto_tune_status.set("Auto-tune OFF")
        if auto_tune_after_id is not None:
            root.after_cancel(auto_tune_after_id)
            auto_tune_after_id = None
        append_terminal("Auto-tune disabled.")

# =============================
# Create Root Window
# =============================
root = tk.Tk()
root.title("Robot Telemetry and Control - Simplified")
root.geometry("900x600")
root.grid_rowconfigure(0, weight=1)
root.grid_columnconfigure(0, weight=1)

main_frame = ttk.Frame(root, padding=4)
main_frame.grid(row=0, column=0, sticky="nsew")

# Open terminal window early
def open_terminal_window():
    global terminal_output
    terminal_win = tk.Toplevel(root)
    terminal_win.title("Serial Terminal Output")
    terminal_win.geometry("600x300")
    terminal_output = tk.Text(terminal_win, height=20)
    terminal_output.pack(fill="both", expand=True, padx=5, pady=5)

open_terminal_window()

# =============================
# Tkinter State Variables
# =============================
current_angle = tk.StringVar(value="--°")
battery_voltage = tk.StringVar(value="-- V")
motor_dir = tk.StringVar(value="--")
last_command = tk.StringVar(value="None")
connection_status = tk.StringVar(value="Disconnected")
auto_tune_status = tk.StringVar(value="Auto-tune OFF")
tuning_mode_var = tk.StringVar(value="Balanced")

# =============================
# GUI Layout - Connection Frame (Row 0)
# =============================
connect_frame = ttk.LabelFrame(main_frame, text="Connection")
connect_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=3)

ttk.Label(connect_frame, text="Port:").pack(side="left", padx=5)
port_combobox = ttk.Combobox(
    connect_frame,
    values=[p.device for p in serial.tools.list_ports.comports()],
    width=15
)
port_combobox.pack(side="left", padx=5)

ttk.Button(
    connect_frame,
    text="Refresh",
    command=lambda: port_combobox.config(
        values=[p.device for p in serial.tools.list_ports.comports()]
    )
).pack(side="left", padx=5)

ttk.Button(connect_frame, text="Connect", command=connect_serial).pack(side="left", padx=5)
ttk.Button(connect_frame, text="Disconnect", command=disconnect_serial).pack(side="left", padx=5)
ttk.Label(connect_frame, textvariable=connection_status, foreground="blue").pack(side="left", padx=10)

# =============================
# Speed Control (Row 1)
# =============================
speed_var = tk.IntVar(value=50)
turn_speed_var = tk.IntVar(value=40)

def update_speed_from_slider(val):
    try:
        v_int = int(float(val))
    except ValueError:
        v_int = 0
    speed_var.set(v_int)
    send_command(f"SPEED={v_int}")
    speed_label.config(text=f"Speed: {v_int}")

def update_turn_speed_from_slider(val):
    try:
        v_int = int(float(val))
    except ValueError:
        v_int = 0
    turn_speed_var.set(v_int)
    send_command(f"TURN={v_int}")
    turn_speed_label.config(text=f"Turn Speed: {v_int}")

speed_frame = ttk.LabelFrame(main_frame, text="Speed Control")
speed_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

ttk.Label(speed_frame, text="Speed:").pack(anchor="w", padx=5)
speed_slider = tk.Scale(
    speed_frame, from_=0, to=100, orient="horizontal", resolution=10,
    variable=speed_var, command=update_speed_from_slider, length=250
)
speed_slider.pack(anchor="w", padx=5)
speed_label = ttk.Label(speed_frame, text=f"Speed: {speed_var.get()}")
speed_label.pack(anchor="w", padx=5, pady=(0, 8))

ttk.Label(speed_frame, text="Turn Speed:").pack(anchor="w", padx=5)
turn_speed_slider = tk.Scale(
    speed_frame, from_=0, to=100, orient="horizontal", resolution=10,
    variable=turn_speed_var, command=update_turn_speed_from_slider, length=250
)
turn_speed_slider.pack(anchor="w", padx=5)
turn_speed_label = ttk.Label(speed_frame, text=f"Turn Speed: {turn_speed_var.get()}")
turn_speed_label.pack(anchor="w", padx=5)

# =============================
# PID Parameters (Row 1, Column 1)
# =============================
pid_frame = ttk.LabelFrame(main_frame, text="PID Parameters")
pid_frame.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

pid_keys = list(default_values.keys())

for i, key in enumerate(pid_keys):
    row = i % 3
    col = (i // 3) * 3

    ttk.Label(pid_frame, text=key).grid(row=row, column=col, sticky="e", padx=2)
    sliders[key] = tk.DoubleVar(value=default_values[key])

    def update_from_slider(v, name=key):
        value = float(v)
        entry_boxes[name].delete(0, tk.END)
        entry_boxes[name].insert(0, f"{value:.2f}")
        send_command(f"{name}={value:.2f}")

    slider = ttk.Scale(
        pid_frame, from_=0, to=100 if key != "SI" else 5,
        variable=sliders[key], command=update_from_slider,
        orient="horizontal", length=100
    )
    slider.grid(row=row, column=col + 1, sticky="w")

    entry = ttk.Entry(pid_frame, width=6)
    entry.insert(0, f"{default_values[key]:.2f}")
    entry.grid(row=row, column=col + 2, padx=(2, 6))
    entry_boxes[key] = entry

    def update_from_entry(event, name=key):
        try:
            value = float(entry_boxes[name].get())
            sliders[name].set(value)
            send_command(f"{name}={value:.2f}")
        except ValueError:
            append_terminal(f"Invalid input for {name}")

    entry.bind("<Return>", lambda e, name=key: update_from_entry(e, name))

ttk.Button(
    pid_frame,
    text="Reset Defaults",
    command=lambda: [
        sliders[k].set(v) or entry_boxes[k].delete(0, tk.END)
        or entry_boxes[k].insert(0, f"{v:.2f}")
        or send_command(f"{k}={v:.2f}")
        for k, v in default_values.items()
    ]
).grid(row=3, column=0, columnspan=6, pady=(5, 0))

# =============================
# Control Buttons (Row 2)
# =============================
button_frame = ttk.LabelFrame(main_frame, text="Drive Commands")
button_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

def send_drive_and_servo(drive_cmd, servo_cmd):
    payload = f"{drive_cmd}:{servo_cmd}"
    send_command(payload)

# D-pad buttons
pad = ttk.Frame(button_frame)
pad.pack(padx=10, pady=10)

ttk.Button(pad, text="↑ Forward", command=lambda: send_drive_and_servo("F", "ST"), width=12).grid(row=0, column=1, padx=5, pady=5)
ttk.Button(pad, text="← Left", command=lambda: send_drive_and_servo("L", "ST"), width=12).grid(row=1, column=0, padx=5, pady=5)
ttk.Button(pad, text="Stop", command=lambda: send_drive_and_servo("ST", "ST"), width=12).grid(row=1, column=1, padx=5, pady=5)
ttk.Button(pad, text="Right →", command=lambda: send_drive_and_servo("R", "ST"), width=12).grid(row=1, column=2, padx=5, pady=5)
ttk.Button(pad, text="↓ Back", command=lambda: send_drive_and_servo("B", "ST"), width=12).grid(row=2, column=1, padx=5, pady=5)

# Auto-tuning controls
auto_tune_button = ttk.Button(
    button_frame,
    text="Auto-Tune",
    command=toggle_auto_tuning
)
auto_tune_button.pack(side="left", padx=5, pady=5)

ttk.Label(button_frame, text="Mode:").pack(side="left", padx=(10, 2), pady=5)
tuning_mode_selector = ttk.Combobox(
    button_frame,
    textvariable=tuning_mode_var,
    values=["Conservative", "Balanced", "Aggressive"],
    width=14,
    state="readonly"
)
tuning_mode_selector.pack(side="left", padx=5, pady=5)
ttk.Label(button_frame, textvariable=auto_tune_status).pack(side="left", padx=5, pady=5)

# =============================
# Live Telemetry (Row 3)
# =============================
telemetry_frame = ttk.LabelFrame(main_frame, text="Live Telemetry")
telemetry_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

angle_gauge_frame = ttk.Frame(telemetry_frame)
angle_gauge_frame.pack(side="left", padx=8, pady=5)
ttk.Label(angle_gauge_frame, text="Angle").pack()

gauge_width = 250
gauge_height = 155
gauge_center_x = gauge_width / 2
gauge_center_y = 130
gauge_radius = 88
angle_gauge = tk.Canvas(
    angle_gauge_frame,
    width=gauge_width,
    height=gauge_height,
    highlightthickness=0
)
angle_gauge.pack()
angle_gauge.create_arc(
    gauge_center_x - gauge_radius,
    gauge_center_y - gauge_radius,
    gauge_center_x + gauge_radius,
    gauge_center_y + gauge_radius,
    start=0,
    extent=180,
    style="arc",
    outline="#777777",
    width=4
)

for tick_angle in range(-90, 91, 30):
    tick_radians = math.radians(tick_angle)
    inner_radius = gauge_radius - 10
    outer_radius = gauge_radius
    inner_x = gauge_center_x + inner_radius * math.sin(tick_radians)
    inner_y = gauge_center_y - inner_radius * math.cos(tick_radians)
    outer_x = gauge_center_x + outer_radius * math.sin(tick_radians)
    outer_y = gauge_center_y - outer_radius * math.cos(tick_radians)
    angle_gauge.create_line(inner_x, inner_y, outer_x, outer_y, width=2)
    label_radius = gauge_radius + 15
    angle_gauge.create_text(
        gauge_center_x + label_radius * math.sin(tick_radians),
        gauge_center_y - label_radius * math.cos(tick_radians),
        text=str(tick_angle),
        font=("TkDefaultFont", 8)
    )

angle_needle = angle_gauge.create_line(
    gauge_center_x,
    gauge_center_y,
    gauge_center_x,
    gauge_center_y - gauge_radius + 14,
    width=3,
    fill="red"
)
angle_gauge.create_oval(
    gauge_center_x - 5,
    gauge_center_y - 5,
    gauge_center_x + 5,
    gauge_center_y + 5,
    fill="#333333",
    outline=""
)
ttk.Label(
    angle_gauge_frame,
    textvariable=current_angle,
    font=("TkDefaultFont", 12, "bold")
).pack()

def update_angle_gauge(angle):
    bounded_angle = max(-90, min(90, angle))
    radians = math.radians(bounded_angle)
    needle_length = gauge_radius - 14
    needle_x = gauge_center_x + needle_length * math.sin(radians)
    needle_y = gauge_center_y - needle_length * math.cos(radians)
    current_angle.set(f"{angle:.2f}°")
    angle_gauge.coords(
        angle_needle,
        gauge_center_x,
        gauge_center_y,
        needle_x,
        needle_y
    )
    angle_gauge.itemconfig(
        angle_needle,
        fill="orange" if abs(angle) > 30 else "red"
    )

battery_frame = ttk.Frame(telemetry_frame)
battery_frame.pack(side="left", padx=12, anchor="n", pady=8)
ttk.Label(battery_frame, text="Battery:").pack(anchor="w")
ttk.Label(
    battery_frame,
    textvariable=battery_voltage,
    font=("TkDefaultFont", 12, "bold")
).pack(anchor="w")

# =============================
# Settings Save/Load (Row 4)
# =============================
def save_settings():
    data = {
        "pid": {k: float(entry_boxes[k].get()) for k in entry_boxes},
        "speed": int(speed_var.get()),
        "turn_speed": int(turn_speed_var.get()),
    }
    fname = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
    if fname:
        with open(fname, "w") as f:
            json.dump(data, f, indent=2)
        append_terminal(f"Settings saved to: {fname}")

def load_settings():
    fname = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
    if not fname:
        return
    try:
        with open(fname, "r") as f:
            data = json.load(f)
        
        for k, v in data.get("pid", {}).items():
            if k in sliders:
                sliders[k].set(float(v))
        
        speed_var.set(int(data.get("speed", 50)))
        turn_speed_var.set(int(data.get("turn_speed", 40)))
        
        append_terminal(f"Settings loaded from: {fname}")
    except Exception as e:
        append_terminal(f"Error loading settings: {e}")

settings_frame = ttk.Frame(main_frame)
settings_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

ttk.Button(settings_frame, text="Save Settings", command=save_settings).pack(side="left", padx=5)
ttk.Button(settings_frame, text="Load Settings", command=load_settings).pack(side="left", padx=5)

# =============================
# Serial Read Thread
# =============================
def read_serial():
    read_buffer = ""
    battery_readings = {}
    while True:
        try:
            if ser and hasattr(ser, 'in_waiting') and ser.in_waiting:
                bytes_in = ser.read(ser.in_waiting).decode(errors='ignore')
                read_buffer += bytes_in

                while '<' in read_buffer and '>' in read_buffer:
                    start = read_buffer.find('<')
                    end = read_buffer.find('>', start)
                    if end == -1:
                        break

                    frame = read_buffer[start:end + 1]
                    read_buffer = read_buffer[end + 1:]
                    line = frame[1:-1].strip()

                    if line.startswith("angle="):
                        try:
                            val = float(line.partition("=")[2])
                            if not math.isfinite(val):
                                raise ValueError("Angle must be finite")
                            with telemetry_lock:
                                telemetry_buffer["angles"].append(val)
                                if len(telemetry_buffer["angles"]) > 100:
                                    del telemetry_buffer["angles"][:-100]
                            root.after(0, update_angle_gauge, val)
                        except ValueError:
                            append_terminal(f"Invalid angle telemetry: {line}")
                    
                    elif line.startswith(("vbat=", "vbat1=", "vbat2=")):
                        try:
                            name, _, raw_voltage = line.partition("=")
                            voltage = float(raw_voltage)
                            if name == "vbat":
                                battery_voltage.set(f"{voltage:.2f} V")
                            else:
                                battery_readings[name] = voltage
                                battery_labels = {"vbat1": "Main", "vbat2": "Servo"}
                                battery_voltage.set(" | ".join(
                                    f"{battery_labels[key]}: {value:.2f} V"
                                    for key, value in battery_readings.items()
                                ))
                        except ValueError:
                            append_terminal(f"Invalid battery telemetry: {line}")

            else:
                time.sleep(0.01)

        except Exception as e:
            print(f"Read error: {e}")
            time.sleep(0.1)

threading.Thread(target=read_serial, daemon=True).start()

# =============================
# Keyboard Bindings - Arrow Keys with Diagonal Support
# =============================
keys_pressed = set()

def get_movement_command():
    """Determine movement based on currently pressed keys."""
    has_up = 'Up' in keys_pressed
    has_down = 'Down' in keys_pressed
    has_left = 'Left' in keys_pressed
    has_right = 'Right' in keys_pressed
    
    # Diagonal movements
    if has_up and has_left:
        return "FL"
    elif has_up and has_right:
        return "FR"
    elif has_down and has_left:
        return "BL"
    elif has_down and has_right:
        return "BR"
    # Single directions
    elif has_up:
        return "F"
    elif has_down:
        return "B"
    elif has_left:
        return "L"
    elif has_right:
        return "R"
    else:
        return "ST"

def update_movement():
    """Send movement command based on pressed keys."""
    cmd = get_movement_command()
    send_drive_and_servo(cmd, "ST")

def on_key_press(event):
    if event.keysym in ['Up', 'Down', 'Left', 'Right']:
        keys_pressed.add(event.keysym)
        update_movement()
    elif event.char == ' ':
        keys_pressed.clear()
        send_drive_and_servo("ST", "ST")
    elif event.char.lower() == 'z':
        send_command("Z")

def on_key_release(event):
    if event.keysym in ['Up', 'Down', 'Left', 'Right']:
        keys_pressed.discard(event.keysym)
        update_movement()

root.bind_all('<KeyPress>', on_key_press)
root.bind_all('<KeyRelease>', on_key_release)

# =============================
# Start GUI Loop
# =============================
root.mainloop()