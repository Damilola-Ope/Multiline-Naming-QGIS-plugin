# -*- coding: utf-8 -*-
import os
import re
import networkx as nx  # type:ignore

from qgis.core import (  # type:ignore
    QgsProject, QgsSpatialIndex, QgsField, QgsPointXY, QgsFeature, QgsVectorLayer
)
from qgis.PyQt.QtCore import QVariant, Qt  # type:ignore
from qgis.PyQt.QtGui import QIcon  # type:ignore
from qgis.PyQt.QtWidgets import (  # type:ignore
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QComboBox, QPushButton, QMessageBox, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QCheckBox
)


# ==========================================
# --- HELPER FUNCTIONS & LOGIC ---
# ==========================================

def clean_hubbox_name(name_str: str) -> str:
    """Formats node names if standardized formatting is needed."""
    return re.sub(r'(_H)0([1-9])(?!\d)', r'\1\2', str(name_str))


def format_cable_name(u_name: str, v_name: str) -> str:
    """Strips redundant prefix structures from upstream/downstream node names."""
    common_prefix = os.path.commonprefix([u_name, v_name])
    if '_' in common_prefix:
        clean_prefix = common_prefix[:common_prefix.rfind('_') + 1]
        if v_name.startswith(clean_prefix):
            v_trimmed = v_name[len(clean_prefix):]
            return f"{u_name} - {v_trimmed}"
    return f"{u_name} - {v_name}"


def run_topology_naming(origin_layers_cfg, other_point_layers_cfg, line_layers, tolerance=2.0, name_field="Name", autosave=False):
    """
    Executes shortest path network graph tracing and names line features.
    """
    node_index = QgsSpatialIndex()
    node_dict = {}
    global_node_id = 1

    all_cfgs = [(cfg, True) for cfg in origin_layers_cfg] + [(cfg, False) for cfg in other_point_layers_cfg]
    
    # 1. LOAD ALL POINT FEATURES INTO SPATIAL INDEX
    for cfg, is_origin in all_cfgs:
        lyrs = QgsProject.instance().mapLayersByName(cfg["layer_name"])
        if not lyrs: 
            continue
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

    # 2. BUILD GRAPH TOPOLOGY ACROSS LINE LAYERS
    G = nx.Graph()
    cable_layer_map = {}

    for cable_layer in line_layers:
        for cable_feat in cable_layer.getFeatures():
            geom = cable_feat.geometry()
            if not geom or geom.isEmpty(): 
                continue
            
            pts = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
            start_nid = snap_to_node(pts[0])
            end_nid = snap_to_node(pts[-1])
            
            if start_nid and end_nid and start_nid != end_nid:
                edge_key = (cable_layer.id(), cable_feat.id())
                G.add_edge(start_nid, end_nid, key=edge_key)
                cable_layer_map[edge_key] = cable_layer

    # 3. PREPARE EDIT SESSIONS AND ATTRIBUTE FIELDS
    for lyr in line_layers:
        if not lyr.isEditable():
            lyr.startEditing()
        fields = lyr.fields()
        if name_field not in [f.name() for f in fields]:
            lyr.addAttribute(QgsField(name_field, QVariant.String, len=100))
            lyr.updateFields()

    processed_edges = set()
    total_named = 0

    # 4. PROCESS NETWORK COMPONENTS
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
            if len(path) < 2: 
                continue
            
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

    # 5. COMMIT EDITS (IF APPLICABLE)
    if autosave:
        for lyr in line_layers:
            lyr.commitChanges()

    return total_named


# ==========================================
# --- GUI DIALOG CLASS ---
# ==========================================

class NetworkNamingDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multipoint Line Naming")
        self.resize(500, 520)
        
        layout = QVBoxLayout()
        
        # Layer Configuration Panel
        self.config_box = QGroupBox("Layer & Topology Configuration")
        cb_layout = QVBoxLayout()
        
        # Line Layer Selector
        cb_layout.addWidget(QLabel("Target Line Layer:"))
        self.combo_line_layer = QComboBox()
        cb_layout.addWidget(self.combo_line_layer)
        
        # Origin Point Layer Selector
        cb_layout.addWidget(QLabel("Origin Point Layer (Root Origin):"))
        self.combo_origin_layer = QComboBox()
        cb_layout.addWidget(self.combo_origin_layer)
        
        # Secondary Point Layers Header
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
        
        # Secondary Point Layers Selector
        self.list_other_layers = QListWidget()
        self.list_other_layers.setMinimumHeight(180)
        cb_layout.addWidget(self.list_other_layers)
        
        self.config_box.setLayout(cb_layout)
        layout.addWidget(self.config_box)

        # Snapping Settings
        tol_layout = QHBoxLayout()
        tol_layout.addWidget(QLabel("Snapping Distance Tolerance (meters):"))
        self.spin_tolerance = QDoubleSpinBox()
        self.spin_tolerance.setValue(2.0)
        self.spin_tolerance.setRange(0.1, 100.0)
        tol_layout.addWidget(self.spin_tolerance)
        layout.addLayout(tol_layout)

        # Autosave Checkbox Option
        self.chk_autosave = QCheckBox("Autosave changes to layers?")
        self.chk_autosave.setChecked(False)
        layout.addWidget(self.chk_autosave)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("Run Naming Process")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # Connections
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
        """Populates combo boxes and lists with all vector layers in the active QGIS project."""
        all_layers = QgsProject.instance().mapLayers().values()

        for layer in all_layers:
            if isinstance(layer, QgsVectorLayer):
                # Line layers
                if layer.geometryType() == 1:
                    self.combo_line_layer.addItem(layer.name(), layer)
                
                # Point layers
                elif layer.geometryType() == 0:
                    self.combo_origin_layer.addItem(layer.name(), layer)

                    item = QListWidgetItem(layer.name())
                    item.setData(Qt.UserRole, layer)
                    item.setCheckState(Qt.Unchecked)
                    self.list_other_layers.addItem(item)

    def execute(self):
        tolerance = self.spin_tolerance.value()
        autosave = self.chk_autosave.isChecked()
        
        line_lyr = self.combo_line_layer.currentData()
        orig_lyr = self.combo_origin_layer.currentData()
        
        if not line_lyr:
            QMessageBox.warning(self, "Warning", "Please select a valid Target Line Layer.")
            return
            
        if not orig_lyr:
            QMessageBox.warning(self, "Warning", "Please select a valid Origin Point Layer.")
            return

        try:
            origin_cfg = [{"layer_name": orig_lyr.name(), "id_field": "Name", "clean_hb": True}]
            
            other_cfg = []
            for i in range(self.list_other_layers.count()):
                item = self.list_other_layers.item(i)
                if item.checkState() == Qt.Checked:
                    sec_lyr = item.data(Qt.UserRole)
                    other_cfg.append({"layer_name": sec_lyr.name(), "id_field": "Name"})

            count = run_topology_naming(origin_cfg, other_cfg, [line_lyr], tolerance, autosave=autosave)

            status_msg = "Saved to layers." if autosave else "Layers remain in edit mode so you can review changes."
            QMessageBox.information(self, "Success", f"Network naming complete!\n\nSuccessfully updated {count} line segment(s).\n\n{status_msg}")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to execute naming process:\n\n{str(e)}")


# ==========================================
# --- QGIS PLUGIN ENTRY CLASS ---
# ==========================================

class MultipointLineNaming:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        # 1. Resolve path to icon.png in the current plugin directory
        plugin_dir = os.path.dirname(__file__)
        icon_path = os.path.join(plugin_dir, "icon.svg")
        
        # 2. Create QIcon object (falls back cleanly if file is missing)
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        # 3. Create QAction with Icon + Label
        self.action = QAction(icon, "Multipoint Line Naming", self.iface.mainWindow())
        self.action.setStatusTip("Automate network line naming along point nodes")
        self.action.triggered.connect(self.run)

        # 4. Add to Vector Menu and Vector Toolbar
        self.iface.addPluginToVectorMenu("Multipoint Line Naming", self.action)
        self.iface.addVectorToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginVectorMenu("Multipoint Line Naming", self.action)
        self.iface.removeVectorToolBarIcon(self.action)

    def run(self):
        dialog = NetworkNamingDialog(self.iface.mainWindow())
        dialog.exec_()