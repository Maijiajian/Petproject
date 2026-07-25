# -*- coding: utf-8 -*-
"""
desktop_pet.py
桌面宠物核心逻辑：
- 无边框、透明背景、始终置顶的小人贴图
- 待机呼吸/晃动动画
- 随机在屏幕底部左右走动，走动时会自动翻转朝向
- 双击 / 右键菜单 触发"跳一跳"动作
- 单击弹出说话气泡（随机台词）
- 鼠标自由拖拽
- 系统托盘图标：显示/隐藏、退出
"""

import math
import os
import random
import sys
import time

from PyQt5.QtCore import Qt, QPoint, QTimer, QRect
from PyQt5.QtGui import QPixmap, QTransform, QPainter, QColor, QFont, QIcon, QPainterPath
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QMenu, QAction, QSystemTrayIcon
)

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
IMAGE_PATH = os.path.join(ASSET_DIR, "character.png")

PET_HEIGHT = 220          # 小人显示高度（像素），可自行调整大小
BOB_MARGIN = 26           # 顶部预留的呼吸/跳跃动画空间
WALK_SPEED = 2            # 走动速度（像素/帧）
FRAME_MS = 30             # 动画刷新间隔

PHRASES = [
    "主人好呀～",
    "今天也要元气满满哦！",
    "工作辛苦啦，摸摸头～",
    "记得多喝水，多休息呀～",
    "这件旗袍好看吗？",
    "再点我一下嘛～",
    "嘿嘿，被你抓到啦！",
    "要不要陪我走一走？",
    "拖着我到处逛逛吧～",
    "眼镜是不是很配我呀？",
]


class SpeechBubble(QWidget):
    """点击小人时弹出的说话气泡"""

    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.text = text
        self.font = QFont("Microsoft YaHei", 11)
        self._compute_size()

    def _compute_size(self):
        from PyQt5.QtGui import QFontMetrics
        fm = QFontMetrics(self.font)
        text_w = fm.horizontalAdvance(self.text)
        text_h = fm.height()
        self.resize(text_w + 40, text_h + 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRect(0, 0, self.width(), self.height() - 10)
        path = QPainterPath()
        path.addRoundedRect(1, 1, rect.width() - 2, rect.height() - 2, 12, 12)

        # 小尾巴（指向小人头顶）
        tail_x = self.width() / 2
        path.moveTo(tail_x - 8, rect.height())
        path.lineTo(tail_x, rect.height() + 10)
        path.lineTo(tail_x + 8, rect.height())
        path.closeSubpath()

        painter.setBrush(QColor(255, 255, 255, 235))
        painter.setPen(QColor(120, 120, 120, 180))
        painter.drawPath(path)

        painter.setPen(QColor(60, 60, 60))
        painter.setFont(self.font)
        painter.drawText(rect, Qt.AlignCenter, self.text)


class DesktopPet(QWidget):
    def __init__(self):
        super().__init__()

        # ---------- 窗口基础设置：无边框、透明、置顶、不出现在任务栏 ----------
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # ---------- 加载并缩放贴图 ----------
        self.pixmap_right = QPixmap(IMAGE_PATH)
        if self.pixmap_right.isNull():
            raise FileNotFoundError(f"找不到角色图片: {IMAGE_PATH}")

        scale_w = int(PET_HEIGHT * self.pixmap_right.width() / self.pixmap_right.height())
        self.pixmap_right = self.pixmap_right.scaled(
            scale_w, PET_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.pixmap_left = self.pixmap_right.transformed(QTransform().scale(-1, 1))
        self.pet_w = self.pixmap_right.width()
        self.pet_h = self.pixmap_right.height()

        self.resize(self.pet_w, self.pet_h + BOB_MARGIN)

        self.label = QLabel(self)
        self.label.setGeometry(0, BOB_MARGIN, self.pet_w, self.pet_h)
        self.label.setScaledContents(True)

        self.facing_right = True
        self._apply_pixmap()

        # ---------- 初始位置：屏幕右下角附近的"地面"上 ----------
        screen = QApplication.primaryScreen().availableGeometry()
        self.ground_y = screen.height() - self.pet_h - BOB_MARGIN - 40
        self.move(screen.width() - self.pet_w - 120, self.ground_y)
        self.screen_rect = screen

        # ---------- 状态机 ----------
        # idle / walk / jump / drag
        self.state = "idle"
        self.state_elapsed = 0.0
        self.jump_duration = 0.6
        self.jump_height = 60

        self.bob_phase = random.uniform(0, math.pi * 2)

        self.dragging = False
        self.drag_offset = QPoint()
        self.press_pos = None
        self.press_time = 0

        self.bubble = None

        # ---------- 定时器 ----------
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_tick)
        self.anim_timer.start(FRAME_MS)

        self.behavior_timer = QTimer(self)
        self.behavior_timer.timeout.connect(self._random_behavior)
        self.behavior_timer.start(random.randint(4000, 8000))

        self.show()
        self._create_tray_icon()

    # ------------------------------------------------------------------
    # 贴图 / 朝向
    # ------------------------------------------------------------------
    def _apply_pixmap(self):
        self.label.setPixmap(self.pixmap_right if self.facing_right else self.pixmap_left)

    # ------------------------------------------------------------------
    # 动画主循环
    # ------------------------------------------------------------------
    def _on_tick(self):
        dt = FRAME_MS / 1000.0
        self.state_elapsed += dt

        if self.state == "drag":
            return  # 拖拽时位置完全由鼠标控制

        if self.state == "idle":
            self._tick_idle()
        elif self.state == "walk":
            self._tick_walk()
        elif self.state == "jump":
            self._tick_jump()

    def _tick_idle(self):
        self.bob_phase += 0.10
        offset = int(math.sin(self.bob_phase) * 3)
        self.label.move(0, BOB_MARGIN + offset)

    def _tick_walk(self):
        self.bob_phase += 0.35
        offset = int(abs(math.sin(self.bob_phase)) * 6)
        self.label.move(0, BOB_MARGIN - offset)

        dx = WALK_SPEED if self.facing_right else -WALK_SPEED
        new_x = self.x() + dx
        min_x = self.screen_rect.x()
        max_x = self.screen_rect.x() + self.screen_rect.width() - self.pet_w

        if new_x <= min_x:
            new_x = min_x
            self.facing_right = True
            self._apply_pixmap()
        elif new_x >= max_x:
            new_x = max_x
            self.facing_right = False
            self._apply_pixmap()

        self.move(new_x, self.y())

        if self.state_elapsed > self.walk_duration:
            self._enter_idle()

    def _tick_jump(self):
        t = min(self.state_elapsed / self.jump_duration, 1.0)
        # 抛物线：t=0 和 t=1 时为 0，t=0.5 时最高
        arc = 4 * t * (1 - t)
        y_offset = int(-self.jump_height * arc)
        # 轻微的挤压拉伸效果，通过左右微移+高度模拟不太可行（单张图），这里只做位移
        self.move(self.x(), self.ground_y + y_offset)
        self.label.move(0, BOB_MARGIN)

        if t >= 1.0:
            self.move(self.x(), self.ground_y)
            self._enter_idle()

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------
    def _enter_idle(self):
        self.state = "idle"
        self.state_elapsed = 0.0

    def _enter_walk(self):
        self.state = "walk"
        self.state_elapsed = 0.0
        self.walk_duration = random.uniform(2.0, 5.0)
        self.facing_right = random.choice([True, False])
        self._apply_pixmap()

    def _enter_jump(self):
        self.state = "jump"
        self.state_elapsed = 0.0
        self.ground_y = self.y()

    def _random_behavior(self):
        self.behavior_timer.setInterval(random.randint(4000, 9000))
        if self.state in ("drag", "jump"):
            return

        choice = random.random()
        if choice < 0.4:
            self._enter_walk()
        elif choice < 0.55:
            self._enter_jump()
        elif choice < 0.75:
            self._say(random.choice(PHRASES))
        else:
            self._enter_idle()

    # ------------------------------------------------------------------
    # 说话气泡
    # ------------------------------------------------------------------
    def _say(self, text):
        if self.bubble is not None:
            self.bubble.close()
            self.bubble = None

        self.bubble = SpeechBubble(text)
        bubble_x = self.x() + self.pet_w // 2 - self.bubble.width() // 2
        bubble_y = self.y() - self.bubble.height() - 4
        self.bubble.move(bubble_x, bubble_y)
        self.bubble.show()

        QTimer.singleShot(2200, self._close_bubble)

    def _close_bubble(self):
        if self.bubble is not None:
            self.bubble.close()
            self.bubble = None

    def _sync_bubble_position(self):
        if self.bubble is not None:
            bubble_x = self.x() + self.pet_w // 2 - self.bubble.width() // 2
            bubble_y = self.y() - self.bubble.height() - 4
            self.bubble.move(bubble_x, bubble_y)

    # ------------------------------------------------------------------
    # 鼠标交互：拖拽 / 单击 / 双击 / 右键菜单
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.state = "drag"
            self.drag_offset = event.globalPos() - self.pos()
            self.press_pos = event.globalPos()
            self.press_time = time.time()

    def mouseMoveEvent(self, event):
        if self.dragging:
            new_pos = event.globalPos() - self.drag_offset
            self.move(new_pos)
            self._sync_bubble_position()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            moved_dist = 0
            if self.press_pos is not None:
                moved_dist = (event.globalPos() - self.press_pos).manhattanLength()

            self.ground_y = self.y()

            if moved_dist < 6:
                # 视为一次单击，说句话
                self._say(random.choice(PHRASES))

            self._enter_idle()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._enter_jump()
            self._say("跳一跳～")

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: white; border: 1px solid #ccc; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; }"
            "QMenu::item:selected { background-color: #f0d9e6; }"
        )

        act_say = QAction("💬 说句话", self)
        act_say.triggered.connect(lambda: self._say(random.choice(PHRASES)))

        act_walk = QAction("🚶 走一走", self)
        act_walk.triggered.connect(self._enter_walk)

        act_jump = QAction("🦘 跳一跳", self)
        act_jump.triggered.connect(self._enter_jump)

        act_hide = QAction("🙈 隐藏", self)
        act_hide.triggered.connect(self._hide_pet)

        act_quit = QAction("❌ 退出", self)
        act_quit.triggered.connect(QApplication.quit)

        menu.addAction(act_say)
        menu.addAction(act_walk)
        menu.addAction(act_jump)
        menu.addSeparator()
        menu.addAction(act_hide)
        menu.addAction(act_quit)

        menu.exec_(event.globalPos())

    # ------------------------------------------------------------------
    # 系统托盘
    # ------------------------------------------------------------------
    def _create_tray_icon(self):
        self.tray = QSystemTrayIcon(QIcon(self.pixmap_right), self)
        self.tray.setToolTip("桌面宠物")

        tray_menu = QMenu()
        act_show = QAction("显示", self)
        act_show.triggered.connect(self._show_pet)
        act_hide = QAction("隐藏", self)
        act_hide.triggered.connect(self._hide_pet)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(QApplication.quit)

        tray_menu.addAction(act_show)
        tray_menu.addAction(act_hide)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self._hide_pet()
            else:
                self._show_pet()

    def _show_pet(self):
        self.show()

    def _hide_pet(self):
        self._close_bubble()
        self.hide()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    pet = DesktopPet()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
