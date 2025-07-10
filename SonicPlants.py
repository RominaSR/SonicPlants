import socket
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets
import numpy as np
import time
import csv
from datetime import datetime
import rtmidi

UDP_IP = "0.0.0.0"
UDP_PORT = 5005
FS = 200
WINDOW_SECONDS = 10
BUFFER_SIZE = FS * WINDOW_SECONDS
MAX_HISTORY = FS * 3600 * 3

def moving_average(x, w):
    if len(x) < w:
        return x
    return np.convolve(x, np.ones(w)/w, mode='same')

class SmoothBuffer:
    def __init__(self, window_size=3):
        self.window_size = window_size
        self.buffer = []
    def process(self, v):
        self.buffer.append(v)
        if len(self.buffer) > self.window_size:
            self.buffer.pop(0)
        return np.mean(self.buffer)

midiout = rtmidi.MidiOut()
midi_ports = midiout.get_ports()
for i, port in enumerate(midi_ports):
    if "IAC" in port or "Bus" in port or "Loop" in port:
        midiout.open_port(i)
        break
else:
    if midi_ports:
        midiout.open_port(0)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SonicPlants - Realtime Plant Biosignal Visualization")
        self.resize(1200, 800)
        self.graph_widget = pg.GraphicsLayoutWidget()
        self.setCentralWidget(self.graph_widget)
        self.plot_global = self.graph_widget.addPlot(title="GLOBAL signal (history)")
        self.plot_global.setLabel('bottom', 'Samples (all)')
        self.plot_global.setLabel('left', 'uV')
        self.curve_global = self.plot_global.plot(pen=pg.mkPen('#0066ff', width=1))
        self.graph_widget.nextRow()
        self.plot_window = self.graph_widget.addPlot(title=f"Moving window ({WINDOW_SECONDS}s)")
        self.plot_window.setLabel('bottom', 'Samples (last seconds)')
        self.plot_window.setLabel('left', 'uV')
        self.curve_window = self.plot_window.plot(pen=pg.mkPen('#FF6600', width=1))
        self.all_values = []
        self.data_buffer = np.zeros(BUFFER_SIZE)
        self.panel = QtWidgets.QWidget()
        self.layout = QtWidgets.QHBoxLayout(self.panel)
        self.checkbox_midi = QtWidgets.QCheckBox("MIDI Output")
        self.layout.addWidget(self.checkbox_midi)
        self.threshold_label = QtWidgets.QLabel("Threshold (uV):")
        self.layout.addWidget(self.threshold_label)
        self.threshold_spin = QtWidgets.QSpinBox()
        self.threshold_spin.setRange(1, 2000)
        self.threshold_spin.setValue(50)
        self.threshold_spin.setReadOnly(True)
        self.layout.addWidget(self.threshold_spin)
        self.btn_rec = QtWidgets.QPushButton("Rec")
        self.btn_rec.clicked.connect(self.start_recording)
        self.layout.addWidget(self.btn_rec)
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_recording)
        self.btn_stop.setEnabled(False)
        self.layout.addWidget(self.btn_stop)
        self.layout.addStretch()
        self.panel.setLayout(self.layout)
        dock = QtWidgets.QDockWidget("Options")
        dock.setWidget(self.panel)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
        self.is_recording = False
        self.csv_file = None
        self.csv_writer = None
        self.recording_start_time = None
        self.prev_val = None
        self.last_note_time = 0
        self.note_duration = 180
        self.min_interval = 200
        self.last_threshold = 50
        self.smoother = SmoothBuffer(window_size=3)
        self.note_history = []
        self.active_notes = {}
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(20)
        self.cleanup_timer = QtCore.QTimer()
        self.cleanup_timer.timeout.connect(self.cleanup_stuck_notes)
        self.cleanup_timer.start(500)

    def start_recording(self):
        nowstr = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save CSV Data", f"biosignal_{nowstr}.csv", "CSV files (*.csv)"
        )
        if path:
            self.csv_file = open(path, "w", newline='')
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(['timestamp', 'uV'])
            self.is_recording = True
            self.recording_start_time = time.time()
            self.btn_rec.setEnabled(False)
            self.btn_stop.setEnabled(True)

    def stop_recording(self):
        if self.is_recording and self.csv_file:
            self.is_recording = False
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
            self.recording_start_time = None
            self.btn_rec.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def send_midi(self, note, duration_ms=None):
        MAX_DURATION = 4000
        if duration_ms is None:
            duration_ms = self.note_duration
        duration_ms = min(duration_ms, MAX_DURATION)
        midiout.send_message([0x90, note, 100])
        self.active_notes[note] = time.time()*1000
        QtCore.QTimer.singleShot(int(duration_ms), lambda: self.send_note_off(note))

    def send_note_off(self, note):
        midiout.send_message([0x80, note, 0])
        self.active_notes.pop(note, None)

    def note_can_be_played(self, note):
        MAX_REPEAT = 3
        if len(self.note_history) < MAX_REPEAT:
            return True
        last_notes = self.note_history[-MAX_REPEAT:]
        if all(n == note for n in last_notes):
            return False
        return True

    def register_note(self, note):
        self.note_history.append(note)
        if len(self.note_history) > 10:
            self.note_history.pop(0)

    def cleanup_stuck_notes(self):
        MAX_DURATION = 4000
        now = time.time()*1000
        for note, t_on in list(self.active_notes.items()):
            if now - t_on > MAX_DURATION:
                self.send_note_off(note)

    def update_plot(self):
        midi_enabled = self.checkbox_midi.isChecked()
        threshold = self.last_threshold
        try:
            while True:
                msg, _ = sock.recvfrom(1024)
                s = msg.decode()
                v = None; th = None; midi_note = None; midi_dur = None
                for part in s.split(';'):
                    if part.startswith("uV:"):
                        v = float(part[3:])
                    elif part.startswith("THR:"):
                        th = float(part[4:])
                    elif part.startswith("MIDI:"):
                        midi_note = int(part[5:])
                    elif part.startswith("DUR:"):
                        midi_dur = float(part[4:])
                if v is not None:
                    self.all_values.append(v)
                    if len(self.all_values) > MAX_HISTORY:
                        self.all_values = self.all_values[-MAX_HISTORY:]
                    self.data_buffer = np.roll(self.data_buffer, -1)
                    self.data_buffer[-1] = v
                    if self.is_recording and self.csv_writer:
                        t_rel = time.time() - self.recording_start_time
                        self.csv_writer.writerow([t_rel, v])
                    if th is not None:
                        self.last_threshold = th
                        self.threshold_spin.setValue(int(round(th)))
                    v_smooth = self.smoother.process(v)
                    if self.prev_val is not None:
                        diff = abs(v_smooth - self.prev_val)
                        now = time.time()*1000
                        if diff >= threshold and (now - self.last_note_time) > self.min_interval:
                            if midi_enabled and midi_note is not None:
                                if self.note_can_be_played(midi_note):
                                    if midi_dur is not None:
                                        duration_ms = int(midi_dur * 1000)
                                    else:
                                        duration_ms = self.note_duration
                                    duration_ms = min(duration_ms, 4000)
                                    self.send_midi(midi_note, duration_ms)
                                    self.last_note_time = now
                                    self.register_note(midi_note)
                    self.prev_val = v_smooth
        except BlockingIOError:
            pass
        if len(self.all_values) > 1:
            global_y = moving_average(np.array(self.all_values), 5)
            self.curve_global.setData(global_y)
        if len(self.data_buffer) > 1:
            window_y = moving_average(self.data_buffer, 7)
            self.curve_window.setData(window_y)

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = MainWindow()
    window.show()
    app.exec_()
    midiout.close_port()
