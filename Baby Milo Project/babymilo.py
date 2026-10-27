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
motor_dir = tk.StringVar(value="--")
last_command = tk.StringVar(value="None")
connection_status = tk.StringVar(value="Disconnected")

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
                            val = float(line.split("=")[1])
                            current_angle.set(f"{val:.2f}°")
                        except:
                            pass
                    
                    elif line.startswith("vbat"):
                        append_terminal(line)

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