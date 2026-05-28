#!/usr/bin/env python3
"""Simple serial monitor for ESP32 debugging"""

import serial
import sys

try:
    ser = serial.Serial('COM3', 115200, timeout=1)
    print("Connected to COM3 at 115200 baud\n")
    print("=" * 60)
    print("ESP32 Serial Output:")
    print("=" * 60 + "\n")
    
    while True:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').rstrip()
            if line:
                print(line)
        else:
            # Keep the connection alive
            pass
            
except KeyboardInterrupt:
    print("\n\nMonitoring stopped")
    if 'ser' in locals():
        ser.close()
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
