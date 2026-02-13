# -*- coding: utf-8 -*-
# Wallace bubble planner — persistent drag layout, carry prefers top, view-mode locked
# Single-click = view (no drag), Double-click = edit (with confirm trim), per-stage undo/redo
# Drag: vertical-only, snap to row/mid lines, not below r0; drag is undoable and persisted
#

from __future__ import annotations
import sys, json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Union

from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import QBrush, QPen, QColor, QPainter, QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsEllipseItem, QGraphicsSimpleTextItem,
    QGraphicsLineItem, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFileDialog, QMessageBox, QComboBox, QCheckBox, QSpinBox, QPlainTextEdit, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem, QScrollArea
)

# ---------- layout constants ----------
ROW_GAP = 22.0
COL_GAP = 26.0
EPS = ROW_GAP * 0.30
RADIUS = 9.5

# ---------- colors ----------
COL_PP    = QColor("#4C8BF5")
COL_SEL   = QColor("#FFD166")
COL_WIRE  = QColor(0, 0, 0, 70)
BG_COLOR  = QColor("#f5f7fb")

# 阶段配色盘
STAGE_PALETTE = [
    "#D81B60", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA",
    "#00ACC1", "#F4511E", "#7CB342", "#3949AB", "#00897B",
    "#5E35B1", "#C0CA33", "#039BE5", "#FDD835", "#6D4C41",
]

# ---------- model dataclasses ----------
@dataclass
class PlacedAdder:
    type: str
    col: int
    inputs: List[str]
    stage: str

@dataclass
class StageLog:
    name: str
    ha_fmt: str
    fa_fmt: str
    sum_bus: str
    carry_bus: str
    color_hex: str = "#1E88E5"
    adders: List[PlacedAdder] = field(default_factory=list)
    s_seq: int = 0
    c_seq: int = 0
    baseline: Dict[int, List[float]] = field(default_factory=dict)
    positions: Dict[str, float] = field(default_factory=dict)   # label -> y (persist drag)
    undo_actions: List['UndoAction'] = field(default_factory=list)
    redo_actions: List['UndoAction'] = field(default_factory=list)

@dataclass
class BubbleSnapshot:
    label: str
    kind: str
    col: int
    row: int
    y: float
    stage_name: Optional[str] = None
    anchor_y: Optional[float] = None
    snap_between: bool = False
    stage_color: Optional[str] = None

@dataclass
class WireRef:
    item: QGraphicsLineItem
    from_label: str
    to_bubble: 'BitBubble'
    last_from_pos: Optional[QPointF] = None

@dataclass
class AdderAction:
    stage_name: str
    kind: str
    col: int
    removed: List[BubbleSnapshot]
    s_label: str
    c_label: str
    anchor_y: float
    wires: List[WireRef] = field(default_factory=list)

@dataclass
class MoveAction:
    stage_name: Optional[str]  # None/"" 表示 PP
    label: str
    col: int
    old_y: float
    new_y: float

UndoAction = Union[AdderAction, MoveAction]

# ---------- history structs (structured history) ----------
@dataclass
class NodeState:
    label: str
    kind: str
    col: int
    row: int
    y: float
    stage_name: Optional[str] = None
    color_hex: Optional[str] = None

@dataclass
class WireState:
    x1: float
    y1: float
    x2: float
    y2: float
    to_label: str
    to_stage: Optional[str] = None

@dataclass
class StageHistory:
    stage_name: str                 # 本阶段名
    compare_to: str                 # "PP" 或上一阶段名
    curr_nodes: List[NodeState]     # 本阶段完成后的整盘节点
    prev_nodes: List[NodeState]     # 前一盘（PP 或上一阶段）整盘节点
    wires: List[WireState]          # 指向本阶段 S/C 的连线几何
    new_labels: List[str] = field(default_factory=list)

# ---------- graphics bubble ----------
class BitBubble(QGraphicsEllipseItem):
    def __init__(self, label: str, kind: str, col: int, row: int, radius=RADIUS):
        super().__init__(-radius, -radius, 2*radius, 2*radius)
        self.label = label
        self.kind  = kind  # "PP"/"S"/"C"
        self.col   = col
        self.row   = row
        self.stage_name: Optional[str] = None
        self.anchor_y: Optional[float] = None
        self.manual_y: Optional[float] = None
        self.snap_between: bool = False
        self.stage_color_hex: Optional[str] = None
        self._owner = None
        self._dragging = False
        self._drag_start_y: Optional[float] = None
        self.setZValue(1)
        flags = QGraphicsEllipseItem.ItemIsSelectable | QGraphicsEllipseItem.ItemSendsScenePositionChanges
        self.setFlags(flags)
        self.setAcceptHoverEvents(True)
        self.textItem = QGraphicsSimpleTextItem(self)
        self.textItem.setText("+")
        self.textItem.setPos(-4.5, -8)
        self.setToolTip(f"{self.label}\nkind={self.kind}, col={self.col}, row={self.row}")
        self.updateColors()

    def _color_from_hex(self, hx: Optional[str], darker=False):
        if not hx: return QColor("#1E88E5")
        q = QColor(hx)
        return q.darker(125) if darker else q

    def updateColors(self):
        if self.isSelected():
            pen = QPen(COL_SEL, 2); brush = QBrush(COL_SEL.lighter(150))
        else:
            if self.kind == "PP":
                pen = QPen(COL_PP.darker(130), 1.2); brush = QBrush(COL_PP.lighter(150))
            elif self.kind == "S":
                base = self._color_from_hex(self.stage_color_hex, darker=False)
                pen = QPen(base.darker(130), 1.2); brush = QBrush(base.lighter(140))
            else:  # C
                base = self._color_from_hex(self.stage_color_hex, darker=True)
                pen = QPen(base.darker(130), 1.2); brush = QBrush(base.lighter(140))
        self.setPen(pen); self.setBrush(brush)

    def itemChange(self, change, value):
        if change == QGraphicsEllipseItem.ItemPositionChange:
            if self._owner is not None and self._dragging:
                if not (self._owner.curr and (self.flags() & QGraphicsEllipseItem.ItemIsMovable)):
                    return self.pos()
                new_pos: QPointF = value
                x_fixed = self.col * COL_GAP
                y_snapped = self._owner.snap_y_for(self, new_pos.y())
                if y_snapped > 0.0: y_snapped = 0.0
                return QPointF(x_fixed, y_snapped)
        elif change in (QGraphicsEllipseItem.ItemPositionHasChanged,
                        QGraphicsEllipseItem.ItemSelectedChange,
                        QGraphicsEllipseItem.ItemSelectedHasChanged):
            self.setZValue(1.0 + 0.001 * self.pos().y())
            self.updateColors()
            if self._owner is not None:
                self._owner._update_wires()
        return super().itemChange(change, value)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if self._owner and self._owner.curr and (self.flags() & QGraphicsEllipseItem.ItemIsMovable):
                selected_items = self.scene().selectedItems()
                movable_selected = [it for it in selected_items if isinstance(it, BitBubble)
                                    and (it.flags() & QGraphicsEllipseItem.ItemIsMovable)]
                if len(movable_selected) > 1:
                    for it in movable_selected:
                        it._dragging = True
                        it._drag_start_y = it.pos().y()
                else:
                    self._dragging = True
                    self._drag_start_y = self.pos().y()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if self._dragging:
                self._dragging = False
                end_y = self.pos().y()
                self.manual_y = end_y
                if self._owner and self._owner.curr:
                    if self.kind == "PP":
                        self._owner.pp_positions[self.label] = end_y
                    else:
                        stage_log = self._owner.stage_by_name.get(self.stage_name) if self.stage_name else None
                        if stage_log is not None:
                            stage_log.positions[self.label] = end_y
                    if self._drag_start_y is not None and abs(end_y - self._drag_start_y) > 1e-6:
                        self._owner._record_drag_move(self, self._drag_start_y, end_y)
                self._drag_start_y = None
                selected_items = self.scene().selectedItems()
                if len(selected_items) > 1:
                    for item in selected_items:
                        if isinstance(item, BitBubble) and item is not self:
                            if item._dragging:
                                item._dragging = False
                            new_y = item.pos().y()
                            item.manual_y = new_y
                            if self._owner and self._owner.curr:
                                if item.kind == "PP":
                                    self._owner.pp_positions[item.label] = new_y
                                else:
                                    st_log = self._owner.stage_by_name.get(item.stage_name) if item.stage_name else None
                                    if st_log is not None:
                                        st_log.positions[item.label] = new_y
                                if hasattr(item, "_drag_start_y") and item._drag_start_y is not None:
                                    if abs(new_y - item._drag_start_y) > 1e-6:
                                        self._owner._record_drag_move(item, item._drag_start_y, new_y)
                                item._drag_start_y = None
        super().mouseReleaseEvent(ev)

# ---------- view ----------
class BubbleView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(BG_COLOR)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        self.zoom_min = 0.25
        self.zoom_max = 4.00

    def reset_zoom(self):
        """Ctrl+0：恢复到 100% 缩放，并尽量保持当前视野中心不变。"""
        center_scene = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()         # 回到 1.0x
        self.centerOn(center_scene)   # 保持中心不乱跳

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15

        current = self.transform().m11()
        if current <= 1e-12:
            current = 1e-12

        target = current * factor

        if target < self.zoom_min:
            factor = self.zoom_min / current
        elif target > self.zoom_max:
            factor = self.zoom_max / current

        if abs(factor - 1.0) > 1e-12:
            self.scale(factor, factor)
        else:
            event.ignore()
# ---------- main window ----------
class MainWin(QMainWindow):
    def __init__(self, N=32):
        super().__init__()
        self.setWindowTitle("Wallace Bubble Planner")
        self.resize(1440, 920)

        self.N = N
        self.scene = QGraphicsScene(self)
        self.view  = BubbleView(self.scene)

        # left
        left = QWidget(); llay = QVBoxLayout(left)
        llay.addWidget(QLabel("阶段列表（单击查看 / 双击编辑）"))
        self.listStages = QListWidget(); llay.addWidget(self.listStages)
        self.btnDeleteStages = QPushButton("删除该阶段及其后续"); llay.addWidget(self.btnDeleteStages)
        llay.addStretch()

        # right
        right = QWidget(); rlay = QVBoxLayout(right)
        wNbox = QHBoxLayout()
        self.spN = QSpinBox(); self.spN.setRange(2, 256); self.spN.setValue(self.N)
        self.btnRebuild = QPushButton("重建位宽")
        wNbox.addWidget(QLabel("位宽 N×N：")); wNbox.addWidget(self.spN); wNbox.addWidget(self.btnRebuild)

        self.edStage  = QLineEdit("stg1")
        self.edSumBus = QLineEdit("{stage}_S")
        self.edCarBus = QLineEdit("{stage}_C")
        self.edFAfmt  = QLineEdit("{stage}fa{idx}")
        self.edHAfmt  = QLineEdit("{stage}ha{idx}")

        self.btnNewStage     = QPushButton("新建阶段")
        self.btnFinishStage  = QPushButton("结束阶段")
        self.btnResetStage   = QPushButton("重置当前阶段")

        self.cmbBatch = QComboBox(); self.cmbBatch.addItems(["无批量", "批量放置 HA", "批量放置 FA", "智能放置 HA/FA"])
        self.chkPad0  = QCheckBox("缺输入自动补 0")
        self.btnBatchApply = QPushButton("对所选批量放置")

        self.chkWires = QCheckBox("显示连线"); self.chkWires.setChecked(True)

        self.btnUndo = QPushButton("撤销  (Ctrl+Z)")
        self.btnRedo = QPushButton("重做  (Ctrl+Y)")
        self.btnHistory = QPushButton("查看阶段历史")  # 历史查看（结构化渲染 & 导出PNG）

        self.lblHint = QLabel("查看禁拖；编辑可垂直拖动（吸附行/中线，最低 r0；拖动持久化并可撤销/重做）")
        self.lblHint.setStyleSheet("color:#666")
        self.btnPreviewV   = QPushButton("预览 Verilog")
        self.btnExportJSON = QPushButton("保存方案(JSON)")
        self.btnLoadJSON   = QPushButton("加载方案(JSON)")

        rlay.addLayout(wNbox)
        for text, w in [
            ("阶段名（自动命名）", self.edStage), ("Sum 总线模板", self.edSumBus), ("Carry 总线模板", self.edCarBus),
            ("FA 命名模板", self.edFAfmt), ("HA 命名模板", self.edHAfmt),
        ]:
            rlay.addWidget(QLabel(text)); rlay.addWidget(w)
        rlay.addWidget(self.btnNewStage); rlay.addWidget(self.btnFinishStage); rlay.addWidget(self.btnResetStage)
        rlay.addSpacing(6); rlay.addWidget(self.lblHint); rlay.addSpacing(6)
        rlay.addWidget(QLabel("批量模式：")); rlay.addWidget(self.cmbBatch); rlay.addWidget(self.chkPad0); rlay.addWidget(self.btnBatchApply)
        rlay.addWidget(self.chkWires)
        rlay.addWidget(self.btnUndo); rlay.addWidget(self.btnRedo)
        rlay.addWidget(self.btnHistory)
        rlay.addStretch()
        rlay.addWidget(self.btnExportJSON); rlay.addWidget(self.btnLoadJSON)
        rlay.addWidget(self.btnPreviewV)

        center_layout = QHBoxLayout()
        center_layout.addWidget(left, 1)
        center_layout.addWidget(self.view, 4)
        center_layout.addWidget(right, 2)
        cw = QWidget(); cw.setLayout(center_layout)
        self.setCentralWidget(cw)

        # state
        self.columns: List[List[BitBubble]] = [[] for _ in range(2*self.N - 1)]
        self.stages: List[StageLog] = []
        self.curr: Optional[StageLog] = None
        self.stage_by_name: Dict[str, StageLog] = {}
        self.focus_stage: Optional[str] = None
        self.wires: List[WireRef] = []

        # 历史与母盘（结构化）
        self.history: List[StageHistory] = []
        self.board_states: List[List[NodeState]] = []

        # PP 持久化位置
        self.pp_positions: Dict[str, float] = {}

        
        self._in_history_view: bool = False
        self._history_overlay_items: List[object] = []  # QGraphicsItem*
        self._history_overlay_stage: Optional[str] = None

        # events
        self.btnRebuild.clicked.connect(self.on_rebuild)
        self.btnNewStage.clicked.connect(self.on_new_stage)
        self.btnFinishStage.clicked.connect(self.on_finish_stage)
        self.btnResetStage.clicked.connect(self.on_reset_stage)
        self.btnExportJSON.clicked.connect(self.on_export_json)
        self.btnLoadJSON.clicked.connect(self.on_load_json)
        self.btnBatchApply.clicked.connect(self.on_batch_apply)
        self.chkWires.toggled.connect(self._update_wires_visibility)
        self.btnUndo.clicked.connect(self.on_undo)
        self.btnRedo.clicked.connect(self.on_redo)
        self.btnPreviewV.clicked.connect(self.on_preview_verilog)
        self.btnDeleteStages.clicked.connect(self.on_delete_stage_and_followers)
        self.btnHistory.clicked.connect(self.on_view_history)

        self.listStages.currentItemChanged.connect(self.on_stage_selected)
        self.listStages.itemDoubleClicked.connect(self.on_stage_double_clicked)

        self.make_actions()
        self.build_pp_bubbles()
        self.layout_parallelogram()
        self.update_stage_list()
        self._update_dragability()

    # ---------- helpers ----------
    def _next_stage_name(self) -> str:
        used = {st.name for st in self.stages}
        if self.curr: used.add(self.curr.name)
        n = 1
        while True:
            cand = f"stg{n}"
            if cand not in used: return cand
            n += 1

    def _stage_color_for_index(self, idx: int) -> str:
        return STAGE_PALETTE[idx % len(STAGE_PALETTE)]

    def _allowed_y_positions(self) -> List[float]:
        allowed: List[float] = []
        for r in range(self.N): allowed.append(-r * ROW_GAP)
        for r in range(self.N - 1): allowed.append(-(r + 0.5) * ROW_GAP)
        allowed = [yy for yy in allowed if yy <= 0.0]
        allowed.sort()
        return allowed

    def snap_y_for(self, b: BitBubble, y: float) -> float:
        allowed = self._allowed_y_positions()
        if not allowed: return min(y, 0.0)
        return min(allowed, key=lambda yy: abs(yy - y))

    def _apply_stage_focus(self):
        name = self.focus_stage
        for col in self.columns:
            for b in col:
                if b.kind == "PP": b.setOpacity(1.0)
                elif name is None: b.setOpacity(1.0)
                else: b.setOpacity(1.0 if b.stage_name == name else 0.35)
        for w in self.wires:
            if not self.chkWires.isChecked():
                w.item.setVisible(False); continue
            w.item.setVisible(True if name is None else (w.to_bubble.stage_name == name))

    def _update_dragability(self):
        # 历史回看或非编辑：都不允许拖动
        editing = (self.curr is not None) and (not self._in_history_view)
        for col in self.columns:
            for b in col:
                movable = editing
                b.setFlag(QGraphicsEllipseItem.ItemIsMovable, movable)
                if not movable:
                    b._dragging = False
        self.view.viewport().setCursor(Qt.ArrowCursor)

    
    def _set_live_items_visible(self, visible: bool):
        # 只隐藏“现场气泡/现场连线”，不动轴标签/提示文字
        for col in self.columns:
            for b in col:
                b.setVisible(visible)
        for w in self.wires:
            w.item.setVisible(visible and self.chkWires.isChecked())

    def _clear_history_overlay(self):
        for it in list(self._history_overlay_items):
            try:
                if hasattr(it, "scene") and it.scene():
                    self.scene.removeItem(it)
            except Exception:
                pass
        self._history_overlay_items.clear()
        self._history_overlay_stage = None

    def _enter_history_view(self, stage_name: str):
        if self.curr is not None:
            return  # 编辑中不进入回看
        hist = next((h for h in self.history if h.stage_name == stage_name), None)
        if hist is None:
            self._exit_history_view()
            return

        # 若已经在看同一个阶段，直接返回
        if self._in_history_view and self._history_overlay_stage == stage_name:
            return

        # 切入：清 overlay -> 隐藏现场 -> 画 overlay
        self._clear_history_overlay()
        self._set_live_items_visible(False)
        self._in_history_view = True
        self._history_overlay_stage = stage_name

        # 网格线（只画横线，类似历史回看）
        grid_pen = QPen(QColor(230, 230, 230), 1)
        grid_pen.setCosmetic(True)
        x1 = -30
        x2 = (2*self.N - 2) * COL_GAP + 30
        for r in range(self.N):
            y = -r * ROW_GAP
            ln = self.scene.addLine(x1, y, x2, y, grid_pen)
            ln.setZValue(-5)
            ln.setData(0, ("HIST_GRID", r))
            self._history_overlay_items.append(ln)

        # 标题
        title = QGraphicsSimpleTextItem(f"{hist.stage_name}  对比  {hist.compare_to}")
        title.setBrush(QBrush(QColor("#555")))
        title.setPos(40, -ROW_GAP*self.N - 60)
        title.setZValue(20)
        title.setData(0, ("HIST_TITLE", stage_name))
        self.scene.addItem(title)
        self._history_overlay_items.append(title)

        # 画节点：prev（淡）-> curr（实）
        def add_node(ns: NodeState, opacity: float, z: float):
            b = BitBubble(ns.label, ns.kind, col=ns.col, row=ns.row, radius=RADIUS)
            b._owner = None
            b.setFlag(QGraphicsEllipseItem.ItemIsSelectable, False)
            b.setAcceptHoverEvents(False)
            b.stage_name = ns.stage_name
            b.stage_color_hex = ns.color_hex
            b.updateColors()
            b.setOpacity(opacity)
            b.setZValue(z)
            b.setPos(QPointF(ns.col * COL_GAP, ns.y))  # ✅坐标系与编辑一致：y 仍为负向上
            self.scene.addItem(b)
            self._history_overlay_items.append(b)

        for ns in hist.prev_nodes:
            add_node(ns, opacity=0.35, z=0.0)
        for ns in hist.curr_nodes:
            add_node(ns, opacity=1.00, z=1.0)

        # 画本阶段连线
        pen = QPen(COL_WIRE, 1)
        pen.setCosmetic(True)
        for ws in hist.wires:
            ln = self.scene.addLine(ws.x1, ws.y1, ws.x2, ws.y2, pen)
            ln.setZValue(0.5)
            ln.setData(0, ("HIST_WIRE", stage_name))
            ln.setVisible(self.chkWires.isChecked())
            self._history_overlay_items.append(ln)

        self.view.setSceneRect(self.scene.itemsBoundingRect().adjusted(-80, -120, 80, 120))
        self._update_dragability()

    def _exit_history_view(self):
        if not self._in_history_view:
            return
        self._clear_history_overlay()
        self._in_history_view = False
        self._set_live_items_visible(True)
        self.layout_parallelogram()
        self._apply_stage_focus()
        self._update_dragability()

    # ---------- build / layout ----------
    def make_actions(self):
        actFA = QAction(self); actFA.setShortcut("F"); actFA.triggered.connect(self.place_fa)
        actHA = QAction(self); actHA.setShortcut("H"); actHA.triggered.connect(self.place_ha)
        actUndo = QAction(self); actUndo.setShortcut("Ctrl+Z"); actUndo.triggered.connect(self.on_undo)
        actRedo = QAction(self); actRedo.setShortcut("Ctrl+Y"); actRedo.triggered.connect(self.on_redo)
        actZoomReset = QAction(self)
        actZoomReset.setShortcut("Ctrl+0")
        actZoomReset.triggered.connect(self.view.reset_zoom)
        self.addAction(actFA); self.addAction(actHA); self.addAction(actUndo); self.addAction(actRedo)
        self.addAction(actZoomReset)
    def build_pp_bubbles(self):
        self.scene.clear()
        self.wires.clear()
        self.columns = [[] for _ in range(2*self.N - 1)]
        for i in range(self.N):
            for j in range(self.N):
                col = i + j
                b = BitBubble(f"pp{i}[{j}]", "PP", col=col, row=i, radius=RADIUS)
                b._owner = self
                self.columns[col].append(b)
                self.scene.addItem(b)
        self.add_axis_labels()

    def add_axis_labels(self):
        for it in list(self.scene.items()):
            if isinstance(it, QGraphicsSimpleTextItem) and it.data(0):
                tag = it.data(0)
                if tag[0] in ("COL_LABEL", "ROW_LABEL"):
                    self.scene.removeItem(it)
        for c in range(len(self.columns)):
            t = QGraphicsSimpleTextItem(str(c)); t.setBrush(QBrush(QColor("#999")))
            t.setData(0, ("COL_LABEL", c)); self.scene.addItem(t)
        for r in range(self.N):
            t = QGraphicsSimpleTextItem(f"r{r}"); t.setBrush(QBrush(QColor("#999")))
            t.setData(0, ("ROW_LABEL", r)); self.scene.addItem(t)
        hint = QGraphicsSimpleTextItem("单击查看禁拖，双击编辑；编辑时垂直拖动吸附行/中线（最低 r0），位置会被记住")
        hint.setBrush(QBrush(QColor("#666"))); hint.setPos(40, -60); self.scene.addItem(hint)

    def _target_y_of(self, b: BitBubble) -> float:
        if b.manual_y is not None:
            return min(b.manual_y, 0.0)
        if b.kind == "PP":
            if b.label in self.pp_positions:
                return min(self.pp_positions[b.label], 0.0)
        y_nom = -b.row * ROW_GAP
        if b.anchor_y is None and b.kind == "PP":
            return y_nom
        if b.kind == "S":
            return min(b.anchor_y if b.anchor_y is not None else y_nom, 0.0)
        allowed = self._allowed_y_positions()
        existing_all = [t.pos().y() for t in self.columns[b.col] if t is not b]
        for yy in allowed:
            if all(abs(yy - ex) > EPS for ex in existing_all):
                return yy
        anchor = b.anchor_y if b.anchor_y is not None else y_nom
        return min(anchor, 0.0)

    def layout_parallelogram(self):
        if self._in_history_view:
            return  # 回看模式不重排现场
        for it in list(self.scene.items()):
            if isinstance(it, QGraphicsSimpleTextItem) and it.data(0):
                tag = it.data(0)
                if tag[0]=="COL_LABEL": it.setPos(tag[1]*COL_GAP-4, -ROW_GAP*self.N - 28)
                elif tag[0]=="ROW_LABEL": it.setPos(-40, -tag[1]*ROW_GAP-8)
        for col, lst in enumerate(self.columns):
            x = col * COL_GAP
            for b in lst:
                if getattr(b, "_owner", None) is None: b._owner = self
                if b.stage_name:
                    st = self.stage_by_name.get(b.stage_name)
                    if st and (b.label in st.positions):
                        b.manual_y = min(st.positions[b.label], 0.0)
                y = self._target_y_of(b) if (b.kind != "PP" or b.anchor_y is not None) else (-b.row*ROW_GAP)
                b._dragging = False
                b.setPos(QPointF(x, y))
                b.setZValue(1.0 + 0.001 * y)
        self._update_wires()
        self.view.setSceneRect(self.scene.itemsBoundingRect().adjusted(-80, -120, 80, 120))
        self._apply_stage_focus()
        self._update_dragability()

    # ---------- stages ----------
    def on_rebuild(self):
        self._exit_history_view()
        n = int(self.spN.value())
        if n != self.N:
            if QMessageBox.question(self, "确认", f"重建为 {n}×{n}？这会清空当前方案。",
                                    QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes:
                return
            self.N = n; self.stages = []; self.curr = None; self.stage_by_name.clear()
            self.history.clear(); self.board_states.clear(); self.pp_positions.clear()
            self.build_pp_bubbles(); self.layout_parallelogram(); self.update_stage_list()

    def _snapshot_baseline(self) -> Dict[int, List[float]]:
        base: Dict[int, List[float]] = {}
        for col, lst in enumerate(self.columns):
            nums = [b for b in lst if b.kind != 'C']
            base[col] = sorted([b.pos().y() for b in nums], reverse=True)
        return base

    def on_new_stage(self):
        self._exit_history_view()
        if self.curr:
            QMessageBox.warning(self, "提示", "请先结束当前阶段"); return
        name = self._next_stage_name(); self.edStage.setText(name)
        color_hex = self._stage_color_for_index(len(self.stages))
        st = StageLog(
            name=name,
            ha_fmt=self.edHAfmt.text().strip() or "{stage}ha{idx}",
            fa_fmt=self.edFAfmt.text().strip() or "{stage}fa{idx}",
            sum_bus=self.edSumBus.text().strip() or "{stage}_S",
            carry_bus=self.edCarBus.text().strip() or "{stage}_C",
            color_hex=color_hex,
        )
        st.baseline = self._snapshot_baseline()
        self.curr = st
        self.stage_by_name[st.name] = st

        if not self.stages and not self.board_states:
            self.board_states.append(self._collect_current_nodes(pp_only=True))

        QMessageBox.information(self, "OK", f"开始阶段 {name}")
        self.update_stage_list()
        self.focus_stage = name; self._apply_stage_focus()
        self._update_dragability()

    def on_finish_stage(self):
        self._exit_history_view()
        if not self.curr:
            QMessageBox.information(self, "提示", "当前没有正在编辑的阶段"); return
        self.stages.append(self.curr)
        self.curr = None
        self._recompute_history_from_scratch()
        QMessageBox.information(self, "OK", "阶段已结束")
        self.update_stage_list()
        self.focus_stage = None; self._apply_stage_focus()
        self._update_dragability()

    def on_reset_stage(self):
        self._exit_history_view()
        if not (self.curr or self.stages): return
        cur = self.curr
        keep = list(self.stages) + ([cur] if cur and cur not in self.stages else [])
        self.curr = None
        self.build_pp_bubbles(); self.layout_parallelogram()
        self.stage_by_name.clear(); self.stages = []
        for st in keep:
            shadow = StageLog(st.name, st.ha_fmt, st.fa_fmt, st.sum_bus, st.carry_bus, st.color_hex)
            shadow.positions = dict(st.positions)
            shadow.baseline = self._snapshot_baseline()
            self.stage_by_name[shadow.name] = shadow
            self.curr = shadow; self.replay_stage(st); self.stages.append(shadow); self.curr = None
        self.layout_parallelogram(); self.update_stage_list()
        self._update_dragability()
        self._recompute_history_from_scratch()

    def on_stage_selected(self, cur: QListWidgetItem, prev: QListWidgetItem):
        name = None if not cur else cur.text().replace(" (editing)", "")

        
        if self.curr is None and name:
            if any(h.stage_name == name for h in self.history):
                self._enter_history_view(name)
                return

        # 其他情况：退出历史回看，回到现场
        self._exit_history_view()
        self.focus_stage = name
        self._apply_stage_focus()
        self._update_dragability()

    def on_stage_double_clicked(self, item: QListWidgetItem):
        """双击进入编辑：若不是最后一个，先弹窗确认是否删除其后所有阶段；取消则不做任何事。"""
        self._exit_history_view()
        name = item.text().replace(" (editing)", "")
        if self.curr and self.curr.name == name:
            return

        idx = None
        for i, st in enumerate(self.stages):
            if st.name == name:
                idx = i; break
        if idx is None:
            return

        need_trim = (idx != len(self.stages) - 1) or (self.curr is not None)

        if need_trim:
            ret = QMessageBox.warning(
                self, "警告",
                "你正在尝试编辑一个位于前面的阶段。\n"
                "继续将会删除该阶段之后的所有阶段。\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ret != QMessageBox.Yes:
                return

        if self.curr:
            self.stages.append(self.curr)
            self.curr = None

        removed_count = len(self.stages) - (idx + 1)
        keep_final = self.stages[:idx]
        edit_stage = self.stages[idx]
        self.stages = keep_final

        self.build_pp_bubbles()
        self.stage_by_name.clear()
        for st in keep_final:
            self.stage_by_name[st.name] = st
            prevc = self.curr; self.curr = st
            self.replay_stage(st)
            self.curr = prevc

        edit_stage.undo_actions.clear(); edit_stage.redo_actions.clear()
        self.stage_by_name[edit_stage.name] = edit_stage
        self.curr = edit_stage
        self.replay_stage(edit_stage)
        self.update_stage_list()
        self.focus_stage = edit_stage.name
        self._apply_stage_focus()
        self._update_dragability()

        self._refresh_history_preserving_current_edit()

        if removed_count > 0:
            QMessageBox.information(self, "提示", f"已删除阶段 {edit_stage.name} 之后的 {removed_count} 个阶段，并进入编辑模式。")

    # ---------- selection helpers ----------
    def _selected_bubbles_by_col(self) -> Dict[int, List[BitBubble]]:
        sel = [it for it in self.scene.selectedItems() if isinstance(it, BitBubble)]
        d: Dict[int, List[BitBubble]] = {}
        for b in sel: d.setdefault(b.col, []).append(b)
        return d

    # ---------- drag record ----------
    def _record_drag_move(self, b: BitBubble, old_y: float, new_y: float):
        if not self.curr: return
        act = MoveAction(stage_name=b.stage_name, label=b.label, col=b.col, old_y=old_y, new_y=new_y)
        self.curr.undo_actions.append(act)
        self.curr.redo_actions.clear()

    # ---------- place FA/HA ----------
    def place_fa(self): self._place_from_selection("FA")
    def place_ha(self): self._place_from_selection("HA")

    def _place_from_selection(self, kind: str):
        if not self.curr:
            QMessageBox.warning(self, "提示", "请先新建或双击进入某个阶段的编辑"); return
        d = self._selected_bubbles_by_col()
        if len(d) != 1:
            QMessageBox.warning(self, "提示", "请只选择同一列的位"); return
        col, blist = next(iter(d.items()))
        blist.sort(key=lambda b: b.pos().y(), reverse=True)
        need = 2 if kind=="HA" else 3
        if len(blist) != need:
            QMessageBox.warning(self, "提示", f"{'半' if need==2 else '全'}加器需要选择 {need} 个位")
            return
        action = self._place_one_adder(col, blist, kind, allow_pad0=False, forced_labels=None)
        if action:
            self.curr.undo_actions.append(action); self.curr.redo_actions.clear()
        self.layout_parallelogram()

    def on_batch_apply(self):
        if not self.curr:
            QMessageBox.warning(self, "提示", "请先新建或双击进入某个阶段的编辑"); return
        mode = self.cmbBatch.currentText()
        if mode == "无批量":
            QMessageBox.information(self, "提示", "请选择批量模式（HA/FA）"); return
        pad0 = self.chkPad0.isChecked()
        d = self._selected_bubbles_by_col()
        if not d:
            QMessageBox.information(self, "提示", "请框选至少一个列上的若干气泡"); return

        changed = False
        if "智能" in mode:
            for col, blist in d.items():
                blist.sort(key=lambda b: b.pos().y(), reverse=True)
                count = len(blist)
                if count == 2:
                    action = self._place_one_adder(col, blist, "HA", allow_pad0=False, forced_labels=None)
                    if action:
                        self.curr.undo_actions.append(action); self.curr.redo_actions.clear(); changed = True
                elif count == 3:
                    action = self._place_one_adder(col, blist, "FA", allow_pad0=False, forced_labels=None)
                    if action:
                        self.curr.undo_actions.append(action); self.curr.redo_actions.clear(); changed = True
        else:
            kind = "HA" if "HA" in mode else "FA"
            need = 2 if kind=="HA" else 3
            for col, blist in d.items():
                blist.sort(key=lambda b: b.pos().y(), reverse=True)
                idx = 0
                while idx < len(blist):
                    take = blist[idx:idx+need]
                    if len(take) < need and not pad0: break
                    action = self._place_one_adder(col, take, kind, allow_pad0=pad0, forced_labels=None)
                    if action:
                        self.curr.undo_actions.append(action); self.curr.redo_actions.clear(); changed = True
                    idx += need

        if changed: self.layout_parallelogram()

    # ---------- helpers for placement ----------
    def _highest_free_y_in_col(self, col: int) -> float:
        allowed = self._allowed_y_positions()
        existing = [b.pos().y() for b in self.columns[col]] if col < len(self.columns) else []
        for yy in allowed:
            if all(abs(yy - ex) > EPS for ex in existing):
                return yy
        return 0.0

    # ---------- core: place one adder ----------
    def _place_one_adder(self, col: int, blist: List[BitBubble], kind: str,
                         allow_pad0=False, forced_labels: Optional[Tuple[str,str]]=None) -> Optional[AdderAction]:
        need = 2 if kind=="HA" else 3
        inputs = [b.label for b in blist]
        label_pos = [(b.label, QPointF(b.pos().x(), b.pos().y())) for b in blist]

        if len(blist) < need and allow_pad0:
            inputs += ["1'b0"] * (need - len(blist))
        if not blist and not allow_pad0: return None

        ys = sorted([p.y() for _, p in label_pos], reverse=True)
        y_bot = ys[0] if ys else 0.0
        bottom_bub = max(blist, key=lambda b: b.pos().y()) if blist else None
        row_for_sc = bottom_bub.row if bottom_bub else 0

        removed_snaps: List[BubbleSnapshot] = []
        for b in list(blist):
            removed_snaps.append(BubbleSnapshot(
                label=b.label, kind=b.kind, col=b.col, row=b.row, y=b.pos().y(),
                stage_name=b.stage_name, anchor_y=b.anchor_y, snap_between=b.snap_between,
                stage_color=b.stage_color_hex
            ))
            if b in self.columns[col]: self.columns[col].remove(b)
            self.scene.removeItem(b)

        st = self.curr
        sum_bus = st.sum_bus.replace("{stage}", st.name)
        car_bus = st.carry_bus.replace("{stage}", st.name)

        if forced_labels is None:
            s_name = f"{sum_bus}[{st.s_seq}]"; st.s_seq += 1
            c_name = f"{car_bus}[{st.c_seq}]"; st.c_seq += 1
        else:
            s_name, c_name = forced_labels

        s_bub = BitBubble(s_name, "S", col=col,   row=row_for_sc)
        c_bub = BitBubble(c_name, "C", col=col+1, row=row_for_sc)
        for bb in (s_bub, c_bub):
            bb.stage_name = st.name
            bb.stage_color_hex = st.color_hex
            bb._owner = self
            bb.updateColors()

        s_bub.anchor_y = y_bot
        s_bub.manual_y = min(y_bot, 0.0)

        if col+1 >= len(self.columns):
            self.columns.append([])
        c_best = self._highest_free_y_in_col(col+1)
        c_bub.manual_y = min(c_best, 0.0)
        c_bub.snap_between = True

        self.columns[col].append(s_bub)
        self.columns[col+1].append(c_bub)
        self.scene.addItem(s_bub); self.scene.addItem(c_bub)

        wires: List[WireRef] = []
        pen = QPen(COL_WIRE, 1); pen.setCosmetic(True)
        for _, from_pos in label_pos:
            xS = s_bub.col * COL_GAP; yS = self._target_y_of(s_bub)
            l1 = self.scene.addLine(from_pos.x(), from_pos.y(), xS, yS, pen)
            l1.setZValue(0); l1.setVisible(self.chkWires.isChecked())
            wires.append(WireRef(item=l1, from_label="", to_bubble=s_bub,
                                 last_from_pos=QPointF(from_pos.x(), from_pos.y())))
            xC = c_bub.col * COL_GAP; yC = self._target_y_of(c_bub)
            l2 = self.scene.addLine(from_pos.x(), from_pos.y(), xC, yC, pen)
            l2.setZValue(0); l2.setVisible(self.chkWires.isChecked())
            wires.append(WireRef(item=l2, from_label="", to_bubble=c_bub,
                                 last_from_pos=QPointF(from_pos.x(), from_pos.y())))
        self.wires.extend(wires)

        st.adders.append(PlacedAdder(type=kind, col=col, inputs=inputs, stage=st.name))

        return AdderAction(
            stage_name=st.name, kind=kind, col=col, removed=removed_snaps,
            s_label=s_name, c_label=c_name, anchor_y=y_bot, wires=wires
        )

    # ---------- wires update ----------
    def _update_wires(self):
        show_all = self.chkWires.isChecked()
        for w in list(self.wires):
            if not w.to_bubble.scene():
                w.item.setVisible(False); continue
            p1 = w.last_from_pos if w.last_from_pos is not None else QPointF(0,0)
            p2 = w.to_bubble.pos()
            w.item.setLine(p1.x(), p1.y(), p2.x(), p2.y())
            vis = show_all and (self.focus_stage is None or w.to_bubble.stage_name == self.focus_stage)
            w.item.setVisible(vis)

    def _update_wires_visibility(self):
        if self._in_history_view:
            # 只控制 HIST_WIRE，网格线不关
            on = self.chkWires.isChecked()
            for it in self._history_overlay_items:
                if isinstance(it, QGraphicsLineItem) and it.data(0) and it.data(0)[0] == "HIST_WIRE":
                    it.setVisible(on)
            return
        self._apply_stage_focus()

    # ---------- replay stage ----------
    def replay_stage(self, from_st: StageLog):
        to = self.curr
        if not to: return
        if not to.baseline: to.baseline = self._snapshot_baseline()
        if not to.positions: to.positions = dict(from_st.positions)
        self.layout_parallelogram()

        to.s_seq = 0; to.c_seq = 0
        for a in from_st.adders:
            col = a.col
            blist: List[BitBubble] = []
            for lab in a.inputs:
                if lab == "1'b0": continue
                found = None
                for b in self.columns[col]:
                    if b.label == lab:
                        found = b; break
                if found: blist.append(found)
            blist.sort(key=lambda b: b.pos().y(), reverse=True)
            act = self._place_one_adder(col, blist, a.type, allow_pad0=("1'b0" in a.inputs), forced_labels=None)
            if act: to.undo_actions.append(act)
        self.layout_parallelogram()

    # ---------- export / import ----------
    def on_export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存方案", "plan.json", "JSON (*.json)")
        if not path: return
        obj = {"N": self.N, "module_name": f"WallaceTree{self.N}x{self.N}", "stages": [],
               "pp_positions": {k: float(v) for k, v in self.pp_positions.items()}}
        for st in self.stages + ([self.curr] if self.curr else []):
            if st is None: continue
            stage = {
                "name": st.name,
                "ha_name_fmt": st.ha_fmt,
                "fa_name_fmt": st.fa_fmt,
                "sum_bus": st.sum_bus,
                "carry_bus": st.carry_bus,
                "color": st.color_hex,
                "baseline": {str(k): v for k, v in st.baseline.items()},
                "positions": {k: float(min(v, 0.0)) for k, v in st.positions.items()},
                "adders": [{"type": ad.type, "col": ad.col, "inputs": ad.inputs} for ad in st.adders]
            }
            obj["stages"].append(stage)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "OK", f"已保存：{path}")

    def on_load_json(self):
        self._exit_history_view()
        path, _ = QFileDialog.getOpenFileName(self, "加载方案", "", "JSON (*.json)")
        if not path: return
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        self.N = int(obj.get("N", 32)); self.spN.setValue(self.N)
        self.pp_positions = {k: float(v) for k, v in obj.get("pp_positions", {}).items()}

        self.build_pp_bubbles(); self.layout_parallelogram()
        self.stages = []; self.curr = None; self.stage_by_name.clear()
        for w in list(self.wires):
            try:
                if w.item.scene(): self.scene.removeItem(w.item)
            except Exception: pass
        self.wires.clear()
        self.history.clear(); self.board_states.clear()

        srcs: List[StageLog] = []
        for idx, s in enumerate(obj.get("stages", [])):
            st = StageLog(
                name=s["name"],
                ha_fmt=s.get("ha_name_fmt","{stage}ha{idx}"),
                fa_fmt=s.get("fa_name_fmt","{stage}fa{idx}"),
                sum_bus=s.get("sum_bus","{stage}_S"),
                carry_bus=s.get("carry_bus","{stage}_C"),
                color_hex=s.get("color", self._stage_color_for_index(idx))
            )
            st.baseline = {int(k): v for k, v in s.get("baseline", {}).items()}
            st.positions = {k: float(v) for k, v in s.get("positions", {}).items()}
            st.adders = [PlacedAdder(type=a["type"], col=int(a["col"]), inputs=list(a["inputs"]), stage=st.name)
                         for a in s.get("adders", [])]
            srcs.append(st)

        for st in srcs:
            shadow = StageLog(st.name, st.ha_fmt, st.fa_fmt, st.sum_bus, st.carry_bus, st.color_hex)
            shadow.positions = dict(st.positions)
            shadow.baseline = self._snapshot_baseline()
            self.stage_by_name[shadow.name] = shadow
            self.curr = shadow; self.replay_stage(st); self.stages.append(shadow); self.curr = None

        self.layout_parallelogram(); self.update_stage_list()
        self._update_dragability()
        self._recompute_history_from_scratch()
        QMessageBox.information(self, "OK", f"已加载：{path}")

    # ---------- per-stage undo/redo ----------
    def on_undo(self):
        if not self.curr:
            QMessageBox.information(self, "提示", "撤销/重做仅作用于当前编辑阶段。"); return
        if not self.curr.undo_actions: return
        act = self.curr.undo_actions.pop()

        if isinstance(act, MoveAction):
            b = None
            for bb in self.columns[act.col]:
                if bb.label == act.label:
                    b = bb; break
            if b is not None:
                y = min(act.old_y, 0.0)
                b.manual_y = y
                b.setPos(QPointF(b.col * COL_GAP, y))
                b.setZValue(1.0 + 0.001 * y)
                st_log = self.stage_by_name.get(act.stage_name) if act.stage_name else None
                if st_log is not None:
                    st_log.positions[act.label] = y
                self.curr.redo_actions.append(act)
                self._update_wires()
                return

        def remove_label(lbl):
            for col in range(len(self.columns)):
                for b in list(self.columns[col]):
                    if b.label == lbl:
                        self.columns[col].remove(b)
                        try: self.scene.removeItem(b)
                        except Exception: pass
                        return
        remove_label(act.s_label); remove_label(act.c_label)

        for w in list(act.wires):
            try:
                if w.item.scene(): self.scene.removeItem(w.item)
            except Exception: pass
            if w in self.wires: self.wires.remove(w)

        for s in act.removed:
            b = BitBubble(s.label, s.kind, col=s.col, row=s.row)
            b.stage_name = s.stage_name; b.anchor_y = s.anchor_y
            y = min(s.y, 0.0)
            b.manual_y = y
            b.snap_between = s.snap_between; b.stage_color_hex = s.stage_color; b._owner = self
            while s.col >= len(self.columns): self.columns.append([])
            self.columns[s.col].append(b); self.scene.addItem(b)
            b.setPos(QPointF(b.col * COL_GAP, y)); b.setZValue(1.0 + 0.001 * y)
            if s.stage_name:
                st_log = self.stage_by_name.get(s.stage_name)
                if st_log is not None:
                    st_log.positions[s.label] = y
        st = self.curr
        if st.adders: st.adders.pop()
        if st.s_seq > 0: st.s_seq -= 1
        if st.c_seq > 0: st.c_seq -= 1
        st.redo_actions.append(act)
        self.layout_parallelogram(); self.update_stage_list()

    def on_redo(self):
        if not self.curr:
            QMessageBox.information(self, "提示", "撤销/重做仅作用于当前编辑阶段。"); return
        if not self.curr.redo_actions: return
        act = self.curr.redo_actions.pop()

        if isinstance(act, MoveAction):
            b = None
            for bb in self.columns[act.col]:
                if bb.label == act.label:
                    b = bb; break
            if b is not None:
                y = min(act.new_y, 0.0)
                b.manual_y = y
                b.setPos(QPointF(b.col * COL_GAP, y))
                b.setZValue(1.0 + 0.001 * y)
                st_log = self.stage_by_name.get(act.stage_name) if act.stage_name else None
                if st_log is not None:
                    st_log.positions[act.label] = y
                self.curr.undo_actions.append(act)
                self._update_wires()
                return

        st = self.curr
        blist: List[BitBubble] = []
        for s in act.removed:
            found = None
            for b in self.columns[s.col]:
                if b.label == s.label:
                    found = b; break
            if found: blist.append(found)
        blist.sort(key=lambda b: b.pos().y(), reverse=True)
        action2 = self._place_one_adder(
            act.col, blist, act.kind,
            allow_pad0=(len(blist) < (2 if act.kind=="HA" else 3)),
            forced_labels=(act.s_label, act.c_label)
        )
        if action2:
            st.undo_actions.append(action2)
            st.s_seq += 1; st.c_seq += 1
        self.layout_parallelogram(); self.update_stage_list()

    # ---------- verilog preview ----------
    def generate_verilog_text(self) -> str:
        lines = []
        lines.append("// ---- Auto-generated by Wallace Bubble Planner ----")
        lines.append(f"// Width: {self.N}x{self.N}")
        lines.append("// 模块 HalfAdder / FullAdder 须外部提供")
        lines.append("")
        all_stages = self.stages + ([self.curr] if self.curr else [])
        for st in all_stages:
            if not st: continue
            sbus = st.sum_bus.replace("{stage}", st.name)
            cbus = st.carry_bus.replace("{stage}", st.name)
            s_w = len(st.adders)
            c_w = len(st.adders)
            lines.append(f"// ===== Stage {st.name} =====")
            if s_w > 0:
                lines.append(f"wire [{s_w-1}:0] {sbus};")
            else:
                lines.append(f"wire {sbus}; // empty")
            if c_w > 0:
                lines.append(f"wire [{c_w-1}:0] {cbus};")
            else:
                lines.append(f"wire {cbus}; // empty")
            s_idx = 0
            c_idx = 0
            ha_idx = 0
            fa_idx = 0
            for ad in st.adders:
                if ad.type == "HA":
                    inst = st.ha_fmt.replace("{stage}", st.name).replace("{idx}", str(ha_idx))
                    ha_idx += 1
                    inps = list(ad.inputs[:2])
                    while len(inps) < 2:
                        inps.append("1'b0")
                    lines.append(f"HalfAdder {inst} ( {inps[0]}, {inps[1]}, {sbus}[{s_idx}], {cbus}[{c_idx}] );")
                else:
                    inst = st.fa_fmt.replace("{stage}", st.name).replace("{idx}", str(fa_idx))
                    fa_idx += 1
                    inps = list(ad.inputs[:3])
                    while len(inps) < 3:
                        inps.append("1'b0")
                    lines.append(f"FullAdder {inst} ( {inps[0]}, {inps[1]}, {inps[2]}, {sbus}[{s_idx}], {cbus}[{c_idx}] );")
                s_idx += 1
                c_idx += 1
            lines.append("")
        lines.append("// 提示：将本阶段 S/C 作为下一阶段的输入即可")
        return "\n".join(lines)

    def on_preview_verilog(self):
        text = self.generate_verilog_text()
        dlg = QDialog(self); dlg.setWindowTitle("Verilog 预览")
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg); edit.setReadOnly(True); edit.setPlainText(text); lay.addWidget(edit)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close, parent=dlg); lay.addWidget(btns)
        def do_save():
            path, _ = QFileDialog.getSaveFileName(self, "另存 Verilog", "wallace_snippet.v", "Verilog (*.v)")
            if not path: return
            with open(path, "w", encoding="utf-8") as f: f.write(text)
            QMessageBox.information(self, "OK", f"已保存：{path}")
        btns.accepted.connect(do_save); btns.rejected.connect(dlg.close)
        dlg.resize(900, 600); dlg.exec()

    # ---------- stage list / delete ----------
    def update_stage_list(self):
        self.listStages.clear()
        for st in self.stages:
            item = QListWidgetItem(st.name); item.setForeground(QBrush(QColor(st.color_hex)))
            self.listStages.addItem(item)
        if self.curr:
            item = QListWidgetItem(f"{self.curr.name} (editing)")
            item.setForeground(QBrush(QColor(self.curr.color_hex))); self.listStages.addItem(item)

    def on_delete_stage_and_followers(self):
        self._exit_history_view()
        sel = self.listStages.currentItem()
        if not sel:
            QMessageBox.information(self, "提示", "请先选择要删除的阶段"); return
        name = sel.text().replace(" (editing)", "")
        idx = None
        for i, st in enumerate(self.stages):
            if st.name == name: idx = i; break
        if idx is None:
            if self.curr and self.curr.name == name:
                self.curr = None; self.update_stage_list()
                QMessageBox.information(self, "OK", f"已删除当前编辑阶段 {name}")
                self._recompute_history_from_scratch()
                return
            else:
                QMessageBox.warning(self, "错误", "未找到所选阶段"); return
        keep = self.stages[:idx]
        self.stages = []; self.curr = None
        self.build_pp_bubbles(); self.stage_by_name.clear(); self.layout_parallelogram()
        for st in keep:
            self.stage_by_name[st.name] = st
            shadow = StageLog(st.name, st.ha_fmt, st.fa_fmt, st.sum_bus, st.carry_bus, st.color_hex)
            shadow.positions = dict(st.positions)
            shadow.baseline = self._snapshot_baseline()
            self.curr = shadow; self.replay_stage(st); self.stages.append(shadow); self.curr = None
        self.layout_parallelogram(); self.update_stage_list()
        self._update_dragability()
        self._recompute_history_from_scratch()
        QMessageBox.information(self, "OK", f"已删除阶段 {name} 及其后续")

    # ---------- history recompute & view ----------
    def _collect_current_nodes(self, pp_only: bool=False) -> List[NodeState]:
        nodes: List[NodeState] = []
        for col, lst in enumerate(self.columns):
            for b in lst:
                if pp_only and b.kind != "PP":
                    continue
                color = b.stage_color_hex
                if b.kind == "PP":
                    color = "#4C8BF5"
                nodes.append(NodeState(
                    label=b.label, kind=b.kind, col=b.col, row=b.row,
                    y=float(b.pos().y()),
                    stage_name=b.stage_name, color_hex=color
                ))
        return nodes

    def _recompute_history_from_scratch(self):
        self._exit_history_view()
        saved_pp = dict(self.pp_positions)
        self.history.clear()
        self.board_states.clear()

        self.build_pp_bubbles()
        self.pp_positions = saved_pp
        self.layout_parallelogram()

        prev_nodes = self._collect_current_nodes()
        self.board_states.append(prev_nodes)

        self.stage_by_name.clear()
        for idx, src in enumerate(self.stages):
            shadow = StageLog(src.name, src.ha_fmt, src.fa_fmt, src.sum_bus, src.carry_bus, src.color_hex)
            shadow.positions = dict(src.positions)
            self.stage_by_name[shadow.name] = shadow
            self.curr = shadow

            new_labels: List[str] = []
            for a in src.adders:
                col = a.col
                blist: List[BitBubble] = []
                for lab in a.inputs:
                    if lab == "1'b0": continue
                    found = None
                    if 0 <= col < len(self.columns):
                        for b in self.columns[col]:
                            if b.label == lab:
                                found = b; break
                    if found: blist.append(found)
                blist.sort(key=lambda b: b.pos().y(), reverse=True)
                act = self._place_one_adder(col, blist, a.type, allow_pad0=("1'b0" in a.inputs), forced_labels=None)
                if act:
                    new_labels.extend([act.s_label, act.c_label])

            self.layout_parallelogram()

            wires: List[WireState] = []
            for w in list(self.wires):
                if not w.to_bubble.scene(): continue
                if w.to_bubble.stage_name != shadow.name: continue
                ln = w.item.line()
                wires.append(WireState(ln.x1(), ln.y1(), ln.x2(), ln.y2(), w.to_bubble.label, shadow.name))

            curr_nodes = self._collect_current_nodes()
            compare_to = "PP" if idx == 0 else self.stages[idx-1].name
            self.history.append(StageHistory(
                stage_name=shadow.name, compare_to=compare_to,
                curr_nodes=curr_nodes, prev_nodes=prev_nodes,
                wires=wires, new_labels=new_labels
            ))
            self.board_states.append(curr_nodes)

            prev_nodes = curr_nodes
            self.curr = None

        self.focus_stage = None
        self._apply_stage_focus()
        self._update_dragability()

    def _refresh_history_preserving_current_edit(self):
        if not self.curr:
            self._recompute_history_from_scratch()
            return

        src = self.curr
        edit_src = StageLog(src.name, src.ha_fmt, src.fa_fmt, src.sum_bus, src.carry_bus, src.color_hex)
        edit_src.positions = dict(src.positions)
        edit_src.adders = [PlacedAdder(type=a.type, col=a.col, inputs=list(a.inputs), stage=a.stage)
                           for a in src.adders]

        self.curr = None
        self._recompute_history_from_scratch()

        shadow = StageLog(edit_src.name, edit_src.ha_fmt, edit_src.fa_fmt,
                          edit_src.sum_bus, edit_src.carry_bus, edit_src.color_hex)
        shadow.positions = dict(edit_src.positions)
        self.stage_by_name[shadow.name] = shadow
        self.curr = shadow
        self.replay_stage(edit_src)
        self.focus_stage = shadow.name
        self.update_stage_list()
        self._apply_stage_focus()
        self._update_dragability()

    # ---------- history render（保留你原来的对话框导出功能） ----------
    def _render_history_item_to_image(self, hist: StageHistory) -> QImage:
        margin = 40
        width = int((2*self.N - 1) * COL_GAP + margin*2)
        ys = [n.y for n in hist.prev_nodes + hist.curr_nodes] if (hist.prev_nodes or hist.curr_nodes) else []
        min_y = min(ys) if ys else -self.N*ROW_GAP
        max_y = 0.0
        height = int((max_y - min_y) + margin*2)
        if width < 600: width = 600
        if height < 400: height = 400

        img = QImage(width, height, QImage.Format_ARGB32)
        img.fill(QColor("#ffffff"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(230,230,230), 1))
        for r in range(self.N):
            y_scene = -r*ROW_GAP
            y_img = margin + (-y_scene)
            p.drawLine(margin-10, y_img, width-margin+10, y_img)
            p.setPen(QPen(QColor(150,150,150), 1))
            p.drawText(10, int(y_img+4), f"r{r}")
            p.setPen(QPen(QColor(230,230,230), 1))
        p.setPen(QPen(QColor(150,150,150), 1))
        for c in range(2*self.N - 1):
            x_img = margin + c*COL_GAP
            p.drawText(int(x_img-3), 20, str(c))

        def draw_node(ns: NodeState, alpha: int):
            x_img = margin + ns.col * COL_GAP
            y_img = margin + (-ns.y)
            if ns.kind == "PP":
                base = QColor(COL_PP)
            else:
                base = QColor(ns.color_hex or "#1E88E5")
            base.setAlpha(alpha)
            pen = QPen(base.darker(130), 1.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QBrush(base.lighter(140)))
            p.drawEllipse(QPointF(x_img, y_img), RADIUS, RADIUS)
            p.setPen(QPen(QColor(0,0,0,130), 1))
            p.drawLine(x_img-3, y_img, x_img+3, y_img)
            p.drawLine(x_img, y_img-3, x_img, y_img+3)

        for ns in hist.prev_nodes:
            draw_node(ns, alpha=90)
        for ns in hist.curr_nodes:
            draw_node(ns, alpha=255)

        p.setPen(QPen(COL_WIRE, 1))
        for w in hist.wires:
            x1 = margin + w.x1
            y1 = margin + (-w.y1)
            x2 = margin + w.x2
            y2 = margin + (-w.y2)
            p.drawLine(x1, y1, x2, y2)

        p.setPen(QPen(QColor(60,60,60), 1))
        p.drawText(margin, height-15, f"{hist.stage_name}  对比  {hist.compare_to}")
        p.end()
        return img

    def on_view_history(self):
        if not self.history:
            QMessageBox.information(self, "提示", "当前没有已完成的阶段历史可查看")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("阶段历史查看（结构化渲染 & 导出 PNG）")
        dlg.resize(1100, 800)
        layout = QVBoxLayout(dlg)

        top = QHBoxLayout()
        list_widget = QListWidget(dlg)
        img_label = QLabel(dlg); img_label.setAlignment(Qt.AlignCenter)
        scroll_area = QScrollArea(dlg)
        scroll_area.setWidget(img_label); scroll_area.setWidgetResizable(True)
        top.addWidget(list_widget, 2)
        top.addWidget(scroll_area, 5)
        layout.addLayout(top)

        btns = QHBoxLayout()
        btnExport = QPushButton("导出当前对比为 PNG")
        btnClose  = QPushButton("关闭")
        btns.addStretch(1)
        btns.addWidget(btnExport)
        btns.addWidget(btnClose)
        layout.addLayout(btns)

        for h in self.history:
            list_widget.addItem(f"{h.stage_name}（对比 {h.compare_to}）")

        state = {"idx": 0, "img": None}

        def refresh(idx: int):
            if idx < 0 or idx >= len(self.history):
                img_label.clear(); state["img"] = None; return
            img = self._render_history_item_to_image(self.history[idx])
            state["idx"] = idx
            state["img"] = img
            img_label.setPixmap(QPixmap.fromImage(img))

        list_widget.currentRowChanged.connect(refresh)
        list_widget.setCurrentRow(0)

        def do_export():
            if state["img"] is None:
                QMessageBox.information(dlg, "提示", "没有可导出的图像")
                return
            path, _ = QFileDialog.getSaveFileName(dlg, "导出历史对比 PNG", "history_stage.png", "PNG (*.png)")
            if not path: return
            ok = state["img"].save(path, "PNG")
            if ok:
                QMessageBox.information(dlg, "OK", f"已导出：{path}")
            else:
                QMessageBox.warning(dlg, "失败", "导出失败，请重试")

        btnExport.clicked.connect(do_export)
        btnClose.clicked.connect(dlg.close)
        dlg.exec()

def main():
    app = QApplication(sys.argv)
    w = MainWin(N=32)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
