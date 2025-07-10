# 🌱 SonicPlants

**SonicPlants** is a real-time biosignal visualization and sonification tool for plant signals, compatible with ESP32-based plant sensors.

---

## ✨ Features

- Real-time UDP signal reception, sonification, and visualization
- Dual window: global history + 10-second moving window
- MIDI note sonification with selectable threshold
- CSV recording for further analysis

---

## ✨ Quickstart

1. **Connect your SonicPlants device and configure Wi-Fi**
2. **Install Python 3** (recommended 3.7+)
3. **Install dependencies**  
   Open a terminal in this folder and run:
   ```bash
   pip install -r requirements.txt
4. Click **Rec** to start recording, **Stop** to end.
5. Adjust **threshold** as needed with potentiometer.
6. Enable or disable **MIDI** output.