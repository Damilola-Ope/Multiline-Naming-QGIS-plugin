# -*- coding: utf-8 -*-
import os
import re
import networkx as nx

from qgis.core import (
    QgsProject, QgsSpatialIndex, QgsField, QgsPointXY, QgsFeature, QgsVectorLayer
)
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QRadioButton, QGroupBox,
    QLabel, QComboBox, QPushButton, QMessageBox, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QCheckBox
)


# ==========================================
# --- HELPER FUNCTIONS & LOGIC ---
# ==========================================

def clean_hubbox_name(name_str: str) -> str:
    return re.sub(r'(_H)0([1-9])(?!\d)', r'\1\2', str(name_str))


def format_cable_name(u_name: str, v_name: str) -> str:
    common_prefix = os.path.commonprefix([u_name, v_name])
    if '_' in common_prefix:
        clean_prefix = common_prefix[:common_prefix.rfind('_') + 1]
        if v_name.startswith(clean_prefix):
            v_trimmed = v_name[len(clean_prefix):]
            return f"{u_name} - {v_trimmed}"
    return f"{u_name} - {v_name}"


def get_layers_from_group(group_name: str):
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    line_layers = []
    
    if group:
        for child in group.findLayers():
            layer = child.layer()
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 1:
                line_layers.append(layer)
    return line_layers


def run_topology_naming(origin_layers_cfg, other_point_layers_cfg, line_layers, tolerance=2.0, name_field="Name", autosave=False):
    node_index = QgsSpatialIndex()
    node_dict = {}
    global_node_id = 1

    all_cfgs = [(cfg, True) for cfg in origin_layers_cfg] + [(cfg, False) for cfg in other_point_layers_cfg]
    
    for cfg, is_origin in all_cfgs:
        lyrs = QgsProject.instance().mapLayersByName(cfg["layer_name"])
        if not lyrs: continue
        pt_lyr = lyrs[0]
        
        for feat in pt_lyr.getFeatures():
            if feat.geometry() and not feat.geometry().isEmpty():
                raw_name = str(feat[cfg["id_field"]])
                cleaned_name = clean_hubbox_name(raw_name) if cfg.get("clean_hb", False) else raw_name
                
                node_dict[global_node_id] = (cleaned_name, feat.geometry().asPoint(), is_origin)
                tf = QgsFeature(global_node_id)
                tf.setGeometry(feat.geometry())
                node_index.insertFeature(tf)
                global_node_id += 1

    def snap_to_node(pt: QgsPointXY):
        cands = node_index.nearestNeighbor(pt, 1)
        if cands:
            nid = cands[0]
            name, coords, is_orig = node_dict[nid]
            if pt.sqrDist(coords) <= (tolerance ** 2):
                return nid
        return None

    G = nx.Graph()
    cable_layer_map = {}

    for cable_layer in line_layers:
        for cable_feat in cable_layer.getFeatures():
            geom = cable_feat.geometry()
            if not geom or geom.isEmpty(): continue
            
            pts = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
            start_nid = snap_to_node(pts[0])
            end_nid = snap_to_node(pts[-1])
            
            if start_nid and end_nid and start_nid != end_nid:
                edge_key = (cable_layer.id(), cable_feat.id())
                G.add_edge(start_nid, end_nid, key=edge_key)
                cable_layer_map[edge_key] = cable_layer

    for lyr in line_layers:
        if not lyr.isEditable():
            lyr.startEditing()
        fields = lyr.fields()
        if name_field not in [f.name() for f in fields]:
            lyr.addAttribute(QgsField(name_field, QVariant.String, len=100))
            lyr.updateFields()

    processed_edges = set()
    total_named = 0

    for component in nx.connected_components(G):
        subG = G.subgraph(component)
        root_nodes = [nid for nid in subG.nodes() if node_dict[nid][2]]
        
        if not root_nodes:
            root_nodes = list(subG.nodes())
            
        if not root_nodes:
            continue

        primary_root = root_nodes[0]
        paths = nx.single_source_shortest_path(subG, primary_root)
        
        for target_id, path in paths.items():
            if len(path) < 2: continue
            
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                edge_data = subG.get_edge_data(u, v)
                edge_key = edge_data["key"]
                
                if edge_key not in processed_edges:
                    u_name = node_dict[u][0]
                    v_name = node_dict[v][0]
                    formatted_name = format_cable_name(u_name, v_name)
                    
                    target_lyr = cable_layer_map[edge_key]
                    field_idx = target_lyr.fields().indexOf(name_field)
                    target_lyr.changeAttributeValue(edge_key[1], field_idx, formatted_name)
                    
                    processed_edges.add(edge_key)
                    total_named += 1

    if autosave:
        for lyr in line_layers:
            lyr.commitChanges()

    return total_named


# ==========================================
# --- GUI DIALOG CLASS ---
# ==========================================

class NetworkNamingDialog(QDialog):
    BASE_WIDTH = 500
    BASE_HEIGHT = 280
    EXPANDED_HEIGHT = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multipoint Line Naming")
        self.resize(self.BASE_WIDTH, self.BASE_HEIGHT)
        
        layout = QVBoxLayout()
        
        self.group_box = QGroupBox("Select Naming Option")
        gb_layout = QVBoxLayout()
        
        self.radio_sfc = QRadioButton("SFC Naming (Hubbox Origin)")
        self.radio_trench = QRadioButton("Trench Naming (06_New Trenching Group radiating from OLT)")
        self.radio_custom = QRadioButton("Custom Selection / Overrides")
        self.radio_sfc.setChecked(True)
        
        gb_layout.addWidget(self.radio_sfc)
        gb_layout.addWidget(self.radio_trench)
        gb_layout.addWidget(self.radio_custom)
        self.group_box.setLayout(gb_layout)
        layout.addWidget(self.group_box)

        self.btn_toggle_details = QPushButton("▶ Show Layer Override Details")
        self.btn_toggle_details.setCheckable(True)
        layout.addWidget(self.btn_toggle_details)

        self.custom_box = QGroupBox("Layer Configuration Overrides")
        cb_layout = QVBoxLayout()
        
        cb_layout.addWidget(QLabel("Target Line Layer:"))
        self.combo_line_layer = QComboBox()
        cb_layout.addWidget(self.combo_line_layer)
        
        cb_layout.addWidget(QLabel("Origin Point Layer (Root):"))
        self.combo_origin_layer = QComboBox()
        cb_layout.addWidget(self.combo_origin_layer)
        
        sec_header_layout = QHBoxLayout()
        sec_header_layout.addWidget(QLabel("Secondary Point Layers (Nodes):"))
        sec_header_layout.addStretch()
        
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setFixedWidth(80)
        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.setFixedWidth(80)
        
        sec_header_layout.addWidget(self.btn_select_all)
        sec_header_layout.addWidget(self.btn_deselect_all)
        cb_layout.addLayout(sec_header_layout)
        
        self.list_other_layers = QListWidget()
        self.list_other_layers.setMinimumHeight(200)
        cb_layout.addWidget(self.list_other_layers)
        
        self.custom_box.setLayout(cb_layout)
        self.custom_box.setVisible(False)
        layout.addWidget(self.custom_box)

        tol_layout = QHBoxLayout()
        tol_layout.addWidget(QLabel("Snapping Distance Tolerance (meters):"))
        self.spin_tolerance = QDoubleSpinBox()
        self.spin_tolerance.setValue(2.0)
        self.spin_tolerance.setRange(0.1, 50.0)
        tol_layout.addWidget(self.spin_tolerance)
        layout.addLayout(tol_layout)

        self.chk_autosave = QCheckBox("Autosave changes to layers?")
        self.chk_autosave.setChecked(False)
        layout.addWidget(self.chk_autosave)

        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("Run Naming Process")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        self.radio_sfc.toggled.connect(self.on_mode_change)
        self.radio_trench.toggled.connect(self.on_mode_change)
        self.radio_custom.toggled.connect(self.on_mode_change)
        self.btn_toggle_details.clicked.connect(self.toggle_details_panel)
        self.btn_select_all.clicked.connect(lambda: self.set_all_check_states(Qt.Checked))
        self.btn_deselect_all.clicked.connect(lambda: self.set_all_check_states(Qt.Unchecked))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_run.clicked.connect(self.execute)

        self.populate_layers()

    def set_all_check_states(self, state):
        for i in range(self.list_other_layers.count()):
            item = self.list_other_layers.item(i)
            item.setCheckState(state)

    def populate_layers(self):
        all_layers = QgsProject.instance().mapLayers().values()
        default_line = "03_SFC"
        default_origin = "02_Hub-Box "
        default_secondaries = ["01_FATs", "13_SFC Closure", "HH", "L2T(500*500*600MM)", "L3T(800*800*900MM)", "Cabinet-Small", "Cabinet-Big"]

        line_idx_to_select = 0
        origin_idx_to_select = 0

        for layer in all_layers:
            if isinstance(layer, QgsVectorLayer):
                if layer.geometryType() == 1:
                    self.combo_line_layer.addItem(layer.name(), layer)
                    if layer.name() == default_line:
                        line_idx_to_select = self.combo_line_layer.count() - 1
                
                elif layer.geometryType() == 0:
                    self.combo_origin_layer.addItem(layer.name(), layer)
                    if layer.name() == default_origin:
                        origin_idx_to_select = self.combo_origin_layer.count() - 1

                    item = QListWidgetItem(layer.name())
                    item.setData(Qt.UserRole, layer)
                    if layer.name() in default_secondaries:
                        item.setCheckState(Qt.Checked)
                    else:
                        item.setCheckState(Qt.Unchecked)
                    self.list_other_layers.addItem(item)

        if self.combo_line_layer.count() > 0:
            self.combo_line_layer.setCurrentIndex(line_idx_to_select)
        if self.combo_origin_layer.count() > 0:
            self.combo_origin_layer.setCurrentIndex(origin_idx_to_select)

    def toggle_details_panel(self, checked):
        self.group_box.setVisible(not checked)
        self.custom_box.setVisible(checked)
        self.btn_toggle_details.setText("▼ Hide Layer Override Details" if checked else "▶ Show Layer Override Details")
        
        if checked:
            self.resize(self.BASE_WIDTH, self.EXPANDED_HEIGHT)
        else:
            self.resize(self.BASE_WIDTH, self.BASE_HEIGHT)
            self.adjustSize()

    def on_mode_change(self):
        if self.radio_custom.isChecked():
            self.btn_toggle_details.setChecked(True)
            self.toggle_details_panel(True)

    def execute(self):
        tolerance = self.spin_tolerance.value()
        autosave = self.chk_autosave.isChecked()
        
        try:
            if self.radio_sfc.isChecked() and not self.btn_toggle_details.isChecked():
                origin_cfg = [{"layer_name": "02_Hub-Box ", "id_field": "Name", "clean_hb": True}]
                other_cfg = [
                    {"layer_name": "01_FATs", "id_field": "Name"},
                    {"layer_name": "13_SFC Closure", "id_field": "Name"}
                ]
                line_layers = QgsProject.instance().mapLayersByName("03_SFC")
                if not line_layers:
                    raise ValueError("Target layer '03_SFC' not found in project. Expand details to override.")
                
                count = run_topology_naming(origin_cfg, other_cfg, [line_layers[0]], tolerance, autosave=autosave)

            elif self.radio_trench.isChecked() and not self.btn_toggle_details.isChecked():
                origin_cfg = [{"layer_name": "03_OLT Site", "id_field": "Name", "clean_hb": False}]
                other_cfg = [
                    {"layer_name": "HH", "id_field": "Name", "clean_hb": True},
                    {"layer_name": "L2T(500*500*600MM)", "id_field": "Name"},
                    {"layer_name": "L3T(800*800*900MM)", "id_field": "Name"},
                    {"layer_name": "Cabinet-Small", "id_field": "Name"},
                    {"layer_name": "Cabinet-Big", "id_field": "Name"},
                    {"layer_name": "10_Rising Pipe", "id_field": "Name"}
                ]
                
                trench_group_name = "06_New Trenching"
                line_layers = get_layers_from_group(trench_group_name)
                
                if not line_layers:
                    raise ValueError(f"No line vector layers found in group '{trench_group_name}'.")

                count = run_topology_naming(origin_cfg, other_cfg, line_layers, tolerance, autosave=autosave)

            else:
                line_lyr = self.combo_line_layer.currentData()
                orig_lyr = self.combo_origin_layer.currentData()
                
                if not line_lyr or not orig_lyr:
                    raise ValueError("Please select a valid line layer and origin point layer.")
                
                origin_cfg = [{"layer_name": orig_lyr.name(), "id_field": "Name", "clean_hb": True}]
                
                other_cfg = []
                for i in range(self.list_other_layers.count()):
                    item = self.list_other_layers.item(i)
                    if item.checkState() == Qt.Checked:
                        sec_lyr = item.data(Qt.UserRole)
                        other_cfg.append({"layer_name": sec_lyr.name(), "id_field": "Name"})

                count = run_topology_naming(origin_cfg, other_cfg, [line_lyr], tolerance, autosave=autosave)

            status_msg = "Saved to layers." if autosave else "Layers remain in edit mode."
            QMessageBox.information(self, "Success", f"Network naming complete!\n\nUpdated {count} segment(s).\n\n{status_msg}")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to execute:\n\n{str(e)}")


# ==========================================
# --- QGIS PLUGIN ENTRY CLASS ---
# ==========================================

class MultipointLineNaming:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction("Multipoint Line Naming", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("Multipoint Line Naming", self.action)
        self.iface.addVectorToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginVectorMenu("Multipoint Line Naming", self.action)
        self.iface.removeVectorToolBarIcon(self.action)

    def run(self):
        dialog = NetworkNamingDialog(self.iface.mainWindow())
        dialog.exec_()