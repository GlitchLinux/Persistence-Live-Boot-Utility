#!/usr/bin/env python3
"""
gLiTcH Linux - Live Boot Persistence Utility
Run as root: sudo python3 glitch_persistence.py
"""

import sys
import os
import re
import subprocess
import json

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QRadioButton, QButtonGroup, QLineEdit, QCheckBox,
    QTextEdit, QMessageBox, QGroupBox, QHeaderView, QFrame,
    QGridLayout, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPalette, QColor


# ── Theme ──
DARK_THEME = """
QMainWindow, QWidget { background-color: #393939; color: #fff;
    font-family: 'Ubuntu','DejaVu Sans',sans-serif; font-size: 14px; }
QLabel#title { font-size: 20px; font-weight: bold; padding: 4px 0; }
QLabel#step { font-size: 12px; color: #888; }
QPushButton { background: #636363; color: #fff; border: 1px solid #7a7a7a;
    border-radius: 5px; padding: 8px 20px; font-weight: bold; min-width: 80px; }
QPushButton:hover { background: #7a7a7a; }
QPushButton:disabled { background: #4a4a4a; color: #666; }
QPushButton#danger { color: #ff4444; border-color: #ff4444; }
QPushButton#success { color: #00ff88; border-color: #00ff88; }
QLineEdit { background: #2e2e2e; color: #fff; border: 1px solid #636363;
    border-radius: 4px; padding: 6px 10px; }
QRadioButton { spacing: 6px; padding: 3px; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px;
    border: 2px solid #636363; background: #2e2e2e; }
QRadioButton::indicator:checked { background: #fff; }
QCheckBox { spacing: 6px; padding: 3px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;
    border: 2px solid #636363; background: #2e2e2e; }
QCheckBox::indicator:checked { background: #fff; }
QTextEdit { background: #1d1d1d; color: #fff; border: 1px solid #4a4a4a;
    border-radius: 4px; padding: 6px; font-family: monospace; }
QTableWidget { background: #2e2e2e; color: #fff; border: 1px solid #4a4a4a;
    border-radius: 4px; }
QHeaderView::section { background: #4a4a4a; color: #fff; padding: 6px;
    border: none; border-bottom: 2px solid #fff; font-weight: bold; }
QGroupBox { border: 1px solid #4a4a4a; border-radius: 5px; margin-top: 10px;
    padding-top: 14px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QFrame#sep { background: #4a4a4a; max-height: 1px; }
QFrame#card { background: #2e2e2e; border: 1px solid #4a4a4a;
    border-radius: 6px; padding: 12px; }
"""


# ── Helpers ──
def run_cmd(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timed out"
    except Exception as e:
        return -1, "", str(e)


def human_size(b):
    if not b or b <= 0:
        return "0 B"
    for u in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(b) < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def detect_live_devices():
    live = set()
    mounts = ["/run/live/medium", "/cdrom", "/lib/live/mount/medium",
              "/run/live/rootfs", "/lib/live/mount/rootfs"]
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    for lm in mounts:
                        if parts[1].startswith(lm):
                            live.add(parts[0])
                            parent = re.sub(r'p?\d+$', '', parts[0])
                            if parent != parts[0]:
                                live.add(parent)
    except Exception:
        pass
    
    rc, out, _ = run_cmd("losetup -a 2>/dev/null")
    if rc == 0 and out:
        for line in out.splitlines():
            for lm in mounts:
                if lm in line:
                    m = re.match(r'(/dev/loop\d+)', line)
                    if m:
                        live.add(m.group(1))
    return live


def get_partitions():
    live = detect_live_devices()
    parts = []
    rc, out, _ = run_cmd("lsblk -b -J -o NAME,PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT,TYPE -a")
    if rc != 0:
        return parts
    try:
        data = json.loads(out)
    except:
        return parts

    def walk(devs):
        for d in devs:
            children = d.get("children", [])
            if children:
                walk(children)
            path = d.get("path", f"/dev/{d.get('name','')}")
            dtype = d.get("type", "")
            size = d.get("size") or 0
            if dtype in ("loop", "rom"):
                continue
            if dtype == "disk" and children:
                continue
            if size < 50 * 1024 * 1024:
                continue
            mp = d.get("mountpoint") or ""
            is_live = path in live
            is_mt = bool(mp.strip())
            status = "⚠ LIVE BOOT" if is_live else ("Mounted" if is_mt else "Available")
            parts.append({
                'name': path,
                'size': size,
                'size_human': human_size(size),
                'fstype': d.get("fstype") or "",
                'label': d.get("label") or "",
                'mountpoint': mp,
                'mounted': is_mt,
                'is_live': is_live,
                'type': dtype,
                'status': status
            })

    walk(data.get("blockdevices", []))
    parts.sort(key=lambda p: (p['is_live'], p['mounted'], p['name']))
    return parts


# ── Worker Thread ──
class Worker(QThread):
    log = pyqtSignal(str, str)
    done = pyqtSignal(bool, str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        try:
            p = self.cfg['partition']
            if self.cfg['encrypted']:
                self._encrypted(p, self.cfg['passphrase'])
            else:
                self._unencrypted(p)
        except Exception as e:
            self.log.emit(f"Fatal: {e}", "ERROR")
            self.done.emit(False, str(e))

    def _step(self, cmd, desc, timeout=120):
        self.log.emit(desc, "STEP")
        rc, out, err = run_cmd(cmd, timeout)
        if rc != 0:
            self.log.emit(f"  Failed: {err}", "ERROR")
        return rc, out, err

    def _encrypted(self, part, pw):
        mapper, mnt = "encData", "/mnt/persistence"

        self._step(f"umount {part} 2>/dev/null || true", f"Unmounting {part}...")
        
        # Check if cryptsetup is available
        rc, _, err = run_cmd("which cryptsetup")
        if rc != 0:
            self.done.emit(False, "cryptsetup not installed. Install with: apt install cryptsetup")
            return
        
        rc, _, err = self._step(
            f"echo -n '{pw}' | cryptsetup luksFormat --batch-mode {part} --key-file=-",
            "Applying LUKS encryption...")
        if rc != 0:
            self.done.emit(False, f"LUKS format failed: {err}")
            return

        run_cmd(f"cryptsetup luksClose {mapper} 2>/dev/null || true")
        rc, _, err = self._step(
            f"echo -n '{pw}' | cryptsetup luksOpen {part} {mapper} --key-file=-",
            "Opening LUKS volume...")
        if rc != 0:
            self.done.emit(False, f"LUKS open failed: {err}")
            return

        rc, _, err = self._step(f"mkfs.ext4 -F /dev/mapper/{mapper}", "Creating ext4 filesystem...")
        if rc != 0:
            run_cmd(f"cryptsetup luksClose {mapper} 2>/dev/null || true")
            self.done.emit(False, f"mkfs.ext4 failed: {err}")
            return

        self._step(f"e2label /dev/mapper/{mapper} persistence", "Labeling 'persistence'...")
        run_cmd(f"mkdir -p {mnt}")
        rc, _, err = run_cmd(f"mount /dev/mapper/{mapper} {mnt}")
        if rc != 0:
            run_cmd(f"cryptsetup luksClose {mapper} 2>/dev/null || true")
            self.done.emit(False, f"Mount failed: {err}")
            return

        self._step(f"echo '/ union' > {mnt}/persistence.conf", "Writing persistence.conf...")
        self._step(f"umount {mnt}", "Unmounting...")
        self._step(f"cryptsetup luksClose {mapper}", "Closing LUKS volume...")

        self.log.emit("Done!", "SUCCESS")
        self.done.emit(True,
            "Encrypted persistence partition ready.\n"
            "Boot with: GLITCH-QUANTUX-KDE - Encrypted Persistence")

    def _unencrypted(self, part):
        mnt = "/mnt/persistence"

        self._step(f"umount {part} 2>/dev/null || true", f"Unmounting {part}...")
        rc, _, err = self._step(f"mkfs.ext4 -F {part}", f"Formatting {part} as ext4...")
        if rc != 0:
            self.done.emit(False, f"mkfs.ext4 failed: {err}")
            return

        self._step(f"e2label {part} persistence", "Labeling 'persistence'...")
        run_cmd(f"mkdir -p {mnt}")
        rc, _, err = run_cmd(f"mount {part} {mnt}")
        if rc != 0:
            self.done.emit(False, f"Mount failed: {err}")
            return

        self._step(f"echo '/ union' > {mnt}/persistence.conf", "Writing persistence.conf...")
        self._step(f"umount {mnt}", "Unmounting...")

        self.log.emit("Done!", "SUCCESS")
        self.done.emit(True,
            "Unencrypted persistence partition ready.\n"
            "Boot with: GLITCH-QUANTUX-KDE - Unencrypted Persistence")


# ── Main Window ──
class GlitchPersistence(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("gLiTcH Linux — Persistence Setup")
        self.setFixedSize(750, 550)
        
        self.mode = None
        self.partition = None
        self.worker = None
        self._parts = []

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setStyleSheet("background:#2e2e2e; padding:12px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 6, 16, 6)
        
        title_layout = QVBoxLayout()
        title = QLabel("gLiTcH Linux — Persistence Setup")
        title.setObjectName("title")
        title_layout.addWidget(title)
        self.step_label = QLabel("Step 1/4")
        self.step_label.setObjectName("step")
        title_layout.addWidget(self.step_label)
        header_layout.addLayout(title_layout, 1)
        layout.addWidget(header)

        # Separator
        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        # Stacked widget for pages
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        
        # Create pages
        self.stack.addWidget(self.create_welcome_page())   # 0
        self.stack.addWidget(self.create_mode_page())      # 1
        self.stack.addWidget(self.create_partition_page()) # 2
        self.stack.addWidget(self.create_run_page())       # 3

        # Footer
        footer = QFrame()
        footer.setStyleSheet("background:#2e2e2e;")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 8, 16, 8)
        
        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self.go_back)
        footer_layout.addWidget(self.back_btn)
        
        footer_layout.addStretch()
        
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.close)
        footer_layout.addWidget(self.quit_btn)
        
        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self.go_next)
        footer_layout.addWidget(self.next_btn)
        
        layout.addWidget(footer)
        
        self.update_navigation()
        self.show()

    def create_welcome_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(15)
        
        title = QLabel("Live Boot Persistence Setup")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        info = QLabel(
            "A live boot session is <b>amnesiac</b> by default — all changes are lost on reboot.\n\n"
            "This utility creates a <b>persistence partition</b> that stores your changes between sessions, "
            "turning your live USB into a portable, stateful system."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        
        # GRUB entries box
        grub_box = QGroupBox("Corresponding GRUB Entries")
        grub_layout = QVBoxLayout(grub_box)
        grub_text = QTextEdit()
        grub_text.setReadOnly(True)
        grub_text.setMaximumHeight(120)
        grub_text.setPlainText(
            'menuentry "GLITCH-QUANTUX-KDE - Encrypted Persistence" {\n'
            '    linux /live/vmlinuz boot=live components quiet splash \\\n'
            '          persistent=cryptsetup persistence-encryption=luks persistence\n'
            '    initrd /live/initrd.gz\n}\n\n'
            'menuentry "GLITCH-QUANTUX-KDE - Unencrypted Persistence" {\n'
            '    linux /live/vmlinuz boot=live components quiet splash persistence\n'
            '    initrd /live/initrd.gz\n}'
        )
        grub_layout.addWidget(grub_text)
        layout.addWidget(grub_box)
        
        layout.addStretch()
        
        self.ack_checkbox = QCheckBox("I understand this will FORMAT a partition, destroying all data on it.")
        self.ack_checkbox.setStyleSheet("color:#ff8888;")
        self.ack_checkbox.toggled.connect(self.update_navigation)
        layout.addWidget(self.ack_checkbox)
        
        return page

    def create_mode_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(15)
        
        title = QLabel("Choose Persistence Mode")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        self.mode_group = QButtonGroup()
        
        # Encrypted option
        enc_card = QFrame()
        enc_card.setObjectName("card")
        enc_layout = QVBoxLayout(enc_card)
        self.enc_radio = QRadioButton("🔒 LUKS Encrypted Persistence")
        self.enc_radio.setStyleSheet("font-weight:bold; color:#00ff88;")
        self.mode_group.addButton(self.enc_radio)
        enc_layout.addWidget(self.enc_radio)
        enc_layout.addWidget(QLabel("Encrypted with passphrase. Recommended for portable drives."))
        layout.addWidget(enc_card)
        
        # Unencrypted option
        unc_card = QFrame()
        unc_card.setObjectName("card")
        unc_layout = QVBoxLayout(unc_card)
        self.unc_radio = QRadioButton("📁 Unencrypted Persistence")
        self.unc_radio.setStyleSheet("font-weight:bold;")
        self.mode_group.addButton(self.unc_radio)
        unc_layout.addWidget(self.unc_radio)
        unc_layout.addWidget(QLabel("Plain ext4. Simpler, but no encryption."))
        layout.addWidget(unc_card)
        
        self.mode_group.buttonToggled.connect(self.on_mode_changed)
        
        # Passphrase box (initially hidden)
        self.pw_box = QGroupBox("LUKS Passphrase")
        self.pw_box.setVisible(False)
        pw_layout = QGridLayout(self.pw_box)
        
        pw_layout.addWidget(QLabel("Passphrase:"), 0, 0)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw1.setPlaceholderText("Enter passphrase")
        self.pw1.textChanged.connect(self.update_navigation)
        pw_layout.addWidget(self.pw1, 0, 1)
        
        pw_layout.addWidget(QLabel("Confirm:"), 1, 0)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.pw2.setPlaceholderText("Confirm passphrase")
        self.pw2.textChanged.connect(self.update_navigation)
        pw_layout.addWidget(self.pw2, 1, 1)
        
        self.pw_match_label = QLabel("")
        self.pw_match_label.setStyleSheet("font-size:12px;")
        pw_layout.addWidget(self.pw_match_label, 2, 1)
        
        layout.addWidget(self.pw_box)
        layout.addStretch()
        
        return page

    def on_mode_changed(self):
        encrypted = self.enc_radio.isChecked()
        self.pw_box.setVisible(encrypted)
        self.mode = 'encrypted' if encrypted else ('unencrypted' if self.unc_radio.isChecked() else None)
        self.update_navigation()

    def create_partition_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 16, 28, 16)
        layout.setSpacing(10)
        
        title = QLabel("Select Target Partition")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        warning = QLabel("⚠ Red = live boot media (do NOT select). Any device GRUB can see at boot is valid.")
        warning.setStyleSheet("font-size:12px; color:#ff8888;")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        
        self.partition_table = QTableWidget()
        self.partition_table.setColumnCount(6)
        self.partition_table.setHorizontalHeaderLabels(["Device", "Size", "Type", "FS", "Label", "Status"])
        self.partition_table.horizontalHeader().setStretchLastSection(True)
        self.partition_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.partition_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.partition_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.partition_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.partition_table.verticalHeader().setVisible(False)
        self.partition_table.itemSelectionChanged.connect(self.on_partition_selected)
        layout.addWidget(self.partition_table, 1)
        
        refresh_btn = QPushButton("⟳ Refresh")
        refresh_btn.clicked.connect(self.scan_partitions)
        layout.addWidget(refresh_btn, 0, Qt.AlignRight)
        
        return page

    def scan_partitions(self):
        self.partition_table.setRowCount(0)
        self._parts = get_partitions()
        self.partition_table.setRowCount(len(self._parts))
        
        for i, part in enumerate(self._parts):
            color = QColor("#ff4444") if part['is_live'] else (QColor("#ff8888") if part['mounted'] else None)
            
            for col, value in enumerate([part['name'], part['size_human'], part['type'], 
                                          part['fstype'], part['label']]):
                item = QTableWidgetItem(value)
                if color:
                    item.setForeground(color)
                self.partition_table.setItem(i, col, item)
            
            status_item = QTableWidgetItem(part['status'])
            if part['is_live']:
                status_item.setForeground(QColor("#ff4444"))
            elif part['mounted']:
                status_item.setForeground(QColor("#ff8888"))
            else:
                status_item.setForeground(QColor("#00ff88"))
            self.partition_table.setItem(i, 5, status_item)
        
        self.partition = None
        self.update_navigation()

    def on_partition_selected(self):
        rows = self.partition_table.selectionModel().selectedRows()
        if rows:
            part = self._parts[rows[0].row()]
            if part['is_live']:
                QMessageBox.warning(self, "Live Boot Media", 
                                   f"{part['name']} is your LIVE BOOT media!\nSelect a different partition.")
                self.partition_table.clearSelection()
                self.partition = None
            else:
                self.partition = part['name']
        else:
            self.partition = None
        self.update_navigation()

    def create_run_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 16, 28, 16)
        layout.setSpacing(10)
        
        self.run_title = QLabel("Setting up persistence...")
        self.run_title.setObjectName("title")
        self.run_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.run_title)
        
        self.run_log = QTextEdit()
        self.run_log.setReadOnly(True)
        layout.addWidget(self.run_log, 1)
        
        self.run_result = QLabel("")
        self.run_result.setWordWrap(True)
        self.run_result.setVisible(False)
        layout.addWidget(self.run_result)
        
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.setObjectName("success")
        self.exit_btn.clicked.connect(self.close)
        self.exit_btn.setVisible(False)
        layout.addWidget(self.exit_btn, 0, Qt.AlignCenter)
        
        return page

    def update_navigation(self):
        current_index = self.stack.currentIndex()
        labels = ["Step 1/4 — Welcome", "Step 2/4 — Mode", "Step 3/4 — Partition", "Step 4/4 — Setup"]
        self.step_label.setText(labels[current_index])
        
        self.back_btn.setVisible(0 < current_index < 3)
        self.next_btn.setVisible(current_index < 3)
        self.quit_btn.setVisible(current_index < 3)
        
        if current_index == 0:
            self.next_btn.setEnabled(self.ack_checkbox.isChecked())
        elif current_index == 1:
            ok = self.mode is not None
            if self.mode == 'encrypted':
                match = self.pw1.text() == self.pw2.text() and len(self.pw1.text()) >= 1
                ok = ok and match
                if self.pw1.text() and self.pw2.text():
                    if match:
                        self.pw_match_label.setText("✓ Match")
                        self.pw_match_label.setStyleSheet("color:#00ff88;")
                    else:
                        self.pw_match_label.setText("✗ No match")
                        self.pw_match_label.setStyleSheet("color:#ff4444;")
                else:
                    self.pw_match_label.setText("")
            self.next_btn.setEnabled(ok)
        elif current_index == 2:
            self.next_btn.setEnabled(self.partition is not None)
            self.next_btn.setText("Begin Setup ⚡")

    def go_next(self):
        current_index = self.stack.currentIndex()
        
        if current_index == 2:  # Partition page, about to start setup
            reply = QMessageBox.warning(self, "Confirm",
                f"ERASE ALL DATA on {self.partition}?\n\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self.stack.setCurrentIndex(3)
            self.update_navigation()
            self.start_setup()
        elif current_index < 3:
            self.stack.setCurrentIndex(current_index + 1)
            if current_index + 1 == 2:  # Just switched to partition page
                self.scan_partitions()
            self.update_navigation()
            if current_index + 1 != 2:
                self.next_btn.setText("Next →")

    def go_back(self):
        current_index = self.stack.currentIndex()
        if current_index > 0:
            self.stack.setCurrentIndex(current_index - 1)
            self.next_btn.setText("Next →")
            self.update_navigation()

    def start_setup(self):
        self.run_log.clear()
        self.run_result.setVisible(False)
        self.exit_btn.setVisible(False)
        self.run_title.setText("Setting up persistence...")
        
        config = {
            'partition': self.partition,
            'encrypted': self.mode == 'encrypted'
        }
        if config['encrypted']:
            config['passphrase'] = self.pw1.text()
        
        self.worker = Worker(config)
        self.worker.log.connect(self.on_log)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def on_log(self, message, level):
        colors = {"INFO": "#ccc", "SUCCESS": "#00ff88", "ERROR": "#ff4444", 
                  "STEP": "#88aaff", "WARNING": "#ffaa44"}
        prefixes = {"INFO": "  ", "SUCCESS": "✓", "ERROR": "✗", "STEP": "→", "WARNING": "⚠"}
        
        color = colors.get(level, "#ccc")
        prefix = prefixes.get(level, "  ")
        
        self.run_log.append(f'<span style="color:{color};">{prefix} {message}</span>')
        self.run_log.verticalScrollBar().setValue(self.run_log.verticalScrollBar().maximum())

    def on_done(self, success, message):
        if success:
            self.run_title.setText("✓ Persistence Setup Complete")
            self.run_title.setStyleSheet("color:#00ff88;")
            self.run_result.setStyleSheet("color:#00ff88;")
        else:
            self.run_title.setText("✗ Setup Failed")
            self.run_title.setStyleSheet("color:#ff4444;")
            self.run_result.setStyleSheet("color:#ff4444;")
        
        self.run_result.setText(message)
        self.run_result.setVisible(True)
        self.exit_btn.setVisible(True)
        self.pw1.clear()
        self.pw2.clear()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(self, "Abort?", 
                "Setup still running. Quit anyway?", 
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.wait(3000)
        event.accept()


# ── Main Entry Point ──
def main():
    if os.geteuid() != 0:
        print("\033[91m[ERROR]\033[0m Run as root: sudo python3 glitch_persistence.py")
        sys.exit(1)
    
    # Suppress XDG_RUNTIME_DIR warning
    if not os.environ.get('XDG_RUNTIME_DIR'):
        os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-root'
    
    app = QApplication(sys.argv)
    app.setApplicationName("gLiTcH Persistence")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_THEME)
    
    # Set dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#393939"))
    palette.setColor(QPalette.WindowText, QColor("#ffffff"))
    palette.setColor(QPalette.Base, QColor("#2e2e2e"))
    palette.setColor(QPalette.Text, QColor("#ffffff"))
    palette.setColor(QPalette.Button, QColor("#636363"))
    palette.setColor(QPalette.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#939393"))
    app.setPalette(palette)
    
    window = GlitchPersistence()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()