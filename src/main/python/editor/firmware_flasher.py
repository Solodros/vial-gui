# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import hashlib
import struct
import time
import threading
import sys

from PyQt5.QtCore import pyqtSignal, QCoreApplication
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QHBoxLayout, QLineEdit, QToolButton, QPlainTextEdit, QProgressBar, QFileDialog, QDialog, \
    QCheckBox

from editor.basic_editor import BasicEditor
from unlocker import Unlocker
from util import tr, chunks, find_vial_devices, pad_for_vibl
from vial_device import VialBootloader, VialKeyboard


def send_retries(dev, data, retries=200):
    """ Sends usb packet up to 'retries' times, returns True if success, False if failed """

    if len(data) != 64:
        raise RuntimeError("sending invalid data length")

    for x in range(retries):
        ret = dev.send(data)
        if ret == len(data) + 1:
            return True
        elif ret < 0:
            time.sleep(0.01)
        else:
            return False
    return False


CHUNK = 64


def cmd_flash(device, firmware, enable_insecure, log_cb, progress_cb, complete_cb, error_cb):
    if firmware[0:8] not in [b"VIALFW00", b"VIALFW01"]:
        return error_cb("错误: 无效的签名")

    fw_uid = firmware[8:16]
    fw_ts = struct.unpack("<Q", firmware[16:24])[0]
    log_cb("* 固件生成时间: {} (UTC)".format(datetime.datetime.utcfromtimestamp(fw_ts)))

    fw_hash = firmware[32:64]
    fw_payload = firmware[64:]

    if hashlib.sha256(fw_payload).digest() != fw_hash:
        return error_cb("错误: 固件完整性检查失败\n\texpected={}\n\tgot={}".format(
            fw_hash.hex(),
            hashlib.sha256(fw_payload).hexdigest()
        ))

    # Check bootloader is correct version
    send_retries(device, pad_for_vibl(b"VC\x00"))
    ver = device.recv(8)[0]
    log_cb("* 引导加载程序版本: {}".format(ver))
    if ver not in [0, 1]:
        return error_cb("错误：不受支持的引导加载程序版本")

    send_retries(device, pad_for_vibl(b"VC\x01"))
    uid = device.recv(8)
    log_cb("* Vial ID: {}".format(uid.hex()))

    if uid == b"\xFF" * 8:
        log_cb("\n\n\n!!! 警告 !!!\n引导加载程序UID未设置, 请在产品固件发布之前"
               "在引导加载程序中配置UID\n!!! 警告 !!!\n\n")

    if uid != fw_uid:
        return error_cb("错误: 固件与当前设备不匹配\n\texpected={}\n\tgot={}".format(
            fw_uid.hex(),
            uid.hex()
        ))

    # OK all checks complete, we can flash now
    while len(fw_payload) % CHUNK != 0:
        fw_payload += b"\x00"

    # Flash
    log_cb("正在下载...")
    send_retries(device, pad_for_vibl(b"VC\x02" + struct.pack("<H", len(fw_payload) // CHUNK)))
    total = 0
    for part in chunks(fw_payload, CHUNK):
        if not send_retries(device, part):
            return error_cb("数据传输时发生错误, 固件损坏")
        total += len(part)
        progress_cb(total / len(fw_payload))

    # Reboot
    log_cb("重新启动...")
    # enable insecure mode on first boot in order to restore keymap/macros
    if enable_insecure:
        send_retries(device, pad_for_vibl(b"VC\x04"))
    send_retries(device, pad_for_vibl(b"VC\x03"))

    complete_cb("完成!")


class FirmwareFlasher(BasicEditor):
    log_signal = pyqtSignal(object)
    progress_signal = pyqtSignal(object)
    complete_signal = pyqtSignal(object)
    error_signal = pyqtSignal(object)

    def __init__(self, main, parent=None):
        super().__init__(parent)

        self.main = main

        self.log_signal.connect(self._on_log)
        self.progress_signal.connect(self._on_progress)
        self.complete_signal.connect(self._on_complete)
        self.error_signal.connect(self._on_error)

        self.selected_firmware_path = ""

        file_selector = QHBoxLayout()
        self.txt_file_selector = QLineEdit()
        self.txt_file_selector.setReadOnly(True)
        file_selector.addWidget(self.txt_file_selector)
        self.btn_select_file = QToolButton()
        self.btn_select_file.setText(tr("Flasher", "选择文件"))
        self.btn_select_file.clicked.connect(self.on_click_select_file)
        file_selector.addWidget(self.btn_select_file)
        self.addLayout(file_selector)
        self.txt_logger = QPlainTextEdit()
        self.txt_logger.setReadOnly(True)
        self.txt_logger.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.addWidget(self.txt_logger)
        progress_flash = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_flash.addWidget(self.progress_bar)
        self.btn_flash = QToolButton()
        self.btn_flash.setText(tr("Flasher", "下载"))
        self.btn_flash.clicked.connect(self.on_click_flash)
        progress_flash.addWidget(self.btn_flash)
        self.chk_restore_keymap = QCheckBox(main)
        self.chk_restore_keymap.hide()
        self.chk_restore_keymap.setText(tr("Flasher", "下载完成后恢复当前键盘布局"))
        self.chk_restore_keymap.setChecked(True)
        self.addWidget(self.chk_restore_keymap)
        self.addLayout(progress_flash)

        self.device = None

        self.layout_restore = self.uid_restore = None

    def rebuild(self, device):
        super().rebuild(device)
        self.txt_logger.clear()

        if not self.valid():
            return

        if isinstance(self.device, VialBootloader):
            self.log("有效的Vial引导程序设备:{}".format(self.device.desc["path"].decode("utf-8")))
            self.chk_restore_keymap.hide()
        elif isinstance(self.device, VialKeyboard):
            self.log("Vial 键盘检测")
            self.chk_restore_keymap.show()

    def valid(self):
        return (isinstance(self.device, VialBootloader) or \
               (isinstance(self.device, VialKeyboard) and self.device.keyboard.vibl)) \
               and sys.platform != "emscripten"

    def find_device_with_uid(self, cls, uid):
        devices = find_vial_devices({"definitions": {}})
        for dev in devices:
            if isinstance(dev, cls) and dev.get_uid() == uid:
                return dev
        return None

    def on_click_select_file(self):
        dialog = QFileDialog()
        dialog.setDefaultSuffix("vfw")
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setNameFilters(["Vial Firmware (*.vfw)"])
        if dialog.exec_() == QDialog.Accepted:
            self.selected_firmware_path = dialog.selectedFiles()[0]
            self.txt_file_selector.setText(self.selected_firmware_path)
            self.log("固件更新包: {}".format(self.selected_firmware_path))

    def on_click_flash(self):
        if not self.selected_firmware_path:
            self.log("错误: 请选择一个固件更新包")
            return

        with open(self.selected_firmware_path, "rb") as inf:
            firmware = inf.read()

        if len(firmware) > 10 * 1024 * 1024:
            self.log("错误: 固件过大. 请检查是否选择了正确的文件")
            return

        self.log("正在准备下载....")
        self.lock_ui()

        self.layout_restore = self.uid_restore = None

        if isinstance(self.device, VialKeyboard):
            # back up current layout
            if self.chk_restore_keymap.isChecked():
                self.log("正在备份当前布局...")
                self.layout_restore = self.device.keyboard.save_layout()

            # keep track of which keyboard we should restore saved layout to
            self.uid_restore = self.device.keyboard.get_uid()
            firmware_uid = firmware[8:16]
            if self.uid_restore != firmware_uid:
                self.log("错误: 固件UID与键盘UID不匹配. 请检查固件文件是否正确")
                self.unlock_ui(False)
                return

            Unlocker.unlock(self.device.keyboard)

            self.log("以引导加载模式重新启动...")
            self.device.keyboard.reset()

            # watch for bootloaders to appear and ask them for their UID, return one that matches the keyboard
            found = None
            while found is None:
                self.log("正在寻找设备...")
                QCoreApplication.processEvents()
                time.sleep(1)
                found = self.find_device_with_uid(VialBootloader, self.uid_restore)

            self.log("找到Vial引导程序设备:{}".format(found.desc["path"].decode("utf-8")))
            found.open()
            self.device = found

        threading.Thread(target=lambda: cmd_flash(
            self.device, firmware, self.layout_restore is not None,
            self.on_log, self.on_progress, self.on_complete, self.on_error)).start()

    def on_log(self, line):
        self.log_signal.emit(line)

    def on_progress(self, progress):
        self.progress_signal.emit(progress)

    def on_complete(self, msg):
        self.complete_signal.emit(msg)

    def on_error(self, msg):
        self.error_signal.emit(msg)

    def _on_log(self, msg):
        self.log(msg)

    def _on_progress(self, progress):
        self.progress_bar.setValue(int(progress * 100))

    def _on_complete(self, msg):
        self.log(msg)
        self.progress_bar.setValue(100)

        # if we were asked to restore a layout, find keyboard with matching UID and restore the layout to it
        if self.layout_restore:
            found = None
            while found is None:
                self.log("正在寻找设备...")
                QCoreApplication.processEvents()
                time.sleep(1)
                found = self.find_device_with_uid(VialKeyboard, self.uid_restore)

            self.log("找到Vial键盘设备:{}".format(found.desc["path"].decode("utf-8")))
            found.open()
            self.device = found
            self.log("正在恢复保存的布局...")
            QCoreApplication.processEvents()
            found.keyboard.restore_layout(self.layout_restore)
            found.keyboard.lock()
            found.close()
            self.log("完成!")

        self.unlock_ui()

    def _on_error(self, msg):
        self.log(msg)
        self.unlock_ui(False)

    def log(self, line):
        self.txt_logger.appendPlainText("[{}] {}".format(datetime.datetime.now(), line))

    def lock_ui(self):
        self.btn_select_file.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.main.lock_ui()

    def unlock_ui(self, force_refresh=True):
        self.btn_select_file.setEnabled(True)
        self.btn_flash.setEnabled(True)
        self.main.unlock_ui()
        if force_refresh:
            self.main.on_click_refresh()
