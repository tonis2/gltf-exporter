"""KHR_interactivity importer.

Reads `extensions.KHR_interactivity.graphs` from the root and, for each
object whose node references a graph index, rebuilds an Interactivity
NodeTree and binds it to `obj.gltf_interactivity`.

Round-trips the layout produced by `export.interactivity.InteractivityExporter`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy
    from ..gltf.types import Gltf, Node
    from ..importer import ImportSettings


EXT_INTERACTIVITY = "KHR_interactivity"
TREE_BL_IDNAME = "GLTFInteractivityTreeType"
FLOW_SOCKET_BL_IDNAME = "GLTFFlowSocketType"


_TRS_TO_PROP = {
    ("translation", 0): "TRANSLATION_X", ("translation", 1): "TRANSLATION_Y",
    ("translation", 2): "TRANSLATION_Z",
    ("rotation", 0): "ROTATION_X", ("rotation", 1): "ROTATION_Y",
    ("rotation", 2): "ROTATION_Z", ("rotation", 3): "ROTATION_W",
    ("scale", 0): "SCALE_X", ("scale", 1): "SCALE_Y", ("scale", 2): "SCALE_Z",
}
_BASE_COLOR_PROP = {
    0: "BASE_COLOR_R", 1: "BASE_COLOR_G", 2: "BASE_COLOR_B", 3: "BASE_COLOR_A",
}
_EMISSIVE_PROP = {0: "EMISSIVE_R", 1: "EMISSIVE_G", 2: "EMISSIVE_B"}
_LIGHT_COLOR_PROP = {0: "COLOR_R", 1: "COLOR_G", 2: "COLOR_B"}


def _parse_pointer(p: str):
    """Parse a JSON pointer into (kind, idx, property, sub_idx) or None."""
    if not isinstance(p, str) or not p.startswith("/"):
        return None
    parts = p.strip("/").split("/")
    try:
        if parts[0] == "nodes" and len(parts) >= 3:
            idx = int(parts[1])
            if len(parts) == 4 and parts[2] in ("translation", "rotation", "scale"):
                comp = int(parts[3])
                key = (parts[2], comp)
                if key in _TRS_TO_PROP:
                    return ("OBJECT", idx, _TRS_TO_PROP[key], None)
            if len(parts) == 4 and parts[2] == "weights":
                return ("OBJECT", idx, "WEIGHT", int(parts[3]))
            if len(parts) == 5 and parts[2] == "extensions" \
                    and parts[3] == "KHR_node_visibility" and parts[4] == "visible":
                return ("OBJECT", idx, "VISIBLE", None)
        if parts[0] == "materials" and len(parts) >= 3:
            idx = int(parts[1])
            if len(parts) == 5 and parts[2] == "pbrMetallicRoughness" \
                    and parts[3] == "baseColorFactor":
                comp = int(parts[4])
                if comp in _BASE_COLOR_PROP:
                    return ("MATERIAL", idx, _BASE_COLOR_PROP[comp], None)
            if len(parts) == 4 and parts[2] == "pbrMetallicRoughness":
                if parts[3] == "metallicFactor":
                    return ("MATERIAL", idx, "METALLIC", None)
                if parts[3] == "roughnessFactor":
                    return ("MATERIAL", idx, "ROUGHNESS", None)
            if len(parts) == 4 and parts[2] == "emissiveFactor":
                comp = int(parts[3])
                if comp in _EMISSIVE_PROP:
                    return ("MATERIAL", idx, _EMISSIVE_PROP[comp], None)
            if len(parts) == 3 and parts[2] == "alphaCutoff":
                return ("MATERIAL", idx, "ALPHA_CUTOFF", None)
            if len(parts) == 5 and parts[2] == "extensions" \
                    and parts[3] == "KHR_materials_emissive_strength" \
                    and parts[4] == "emissiveStrength":
                return ("MATERIAL", idx, "EMISSIVE_STRENGTH", None)
        if (len(parts) >= 5 and parts[0] == "extensions"
                and parts[1] == "KHR_lights_punctual" and parts[2] == "lights"):
            idx = int(parts[3])
            if len(parts) == 5:
                if parts[4] == "intensity":
                    return ("LIGHT", idx, "INTENSITY", None)
                if parts[4] == "range":
                    return ("LIGHT", idx, "RANGE", None)
            if len(parts) == 6 and parts[4] == "color":
                comp = int(parts[5])
                if comp in _LIGHT_COLOR_PROP:
                    return ("LIGHT", idx, _LIGHT_COLOR_PROP[comp], None)
            if len(parts) == 6 and parts[4] == "spot":
                if parts[5] == "innerConeAngle":
                    return ("LIGHT", idx, "INNER_CONE", None)
                if parts[5] == "outerConeAngle":
                    return ("LIGHT", idx, "OUTER_CONE", None)
        if parts[0] == "cameras" and len(parts) == 4:
            idx = int(parts[1])
            if parts[2] == "perspective":
                if parts[3] == "yfov":
                    return ("CAMERA", idx, "YFOV", None)
                if parts[3] == "znear":
                    return ("CAMERA", idx, "ZNEAR", None)
                if parts[3] == "zfar":
                    return ("CAMERA", idx, "ZFAR", None)
            if parts[2] == "orthographic":
                if parts[3] == "xmag":
                    return ("CAMERA", idx, "XMAG", None)
                if parts[3] == "ymag":
                    return ("CAMERA", idx, "YMAG", None)
    except (ValueError, IndexError):
        return None
    return None


def _find_light_by_index(gltf, node_to_blender, light_idx: int):
    """Find the Blender Light datablock for KHR_lights_punctual lights[idx]."""
    import bpy
    if gltf.nodes is not None:
        for i, gn in enumerate(gltf.nodes):
            ext = gn.extensions
            if ext is None:
                continue
            lp = ext.get("KHR_lights_punctual")
            if lp is None:
                continue
            if lp.get("light") == light_idx:
                obj = node_to_blender.get(i)
                if obj is not None and obj.data is not None and obj.type == "LIGHT":
                    return obj.data
    if 0 <= light_idx < len(bpy.data.lights):
        return bpy.data.lights[light_idx]
    return None


def _find_camera_by_index(gltf, node_to_blender, camera_idx: int):
    """Find the Blender Camera datablock for gltf cameras[camera_idx]."""
    import bpy
    if gltf.nodes is not None:
        for i, gn in enumerate(gltf.nodes):
            if gn.camera == camera_idx:
                obj = node_to_blender.get(i)
                if obj is not None and obj.data is not None and obj.type == "CAMERA":
                    return obj.data
    if 0 <= camera_idx < len(bpy.data.cameras):
        return bpy.data.cameras[camera_idx]
    return None


class InteractivityImporter:
    def __init__(self, gltf: "Gltf", settings: "ImportSettings") -> None:
        self.gltf = gltf
        self.settings = settings
        self._graphs = self._extract_graphs()
        self._declarations = self._extract_declarations()
        # Lazy: graph index -> instantiated NodeTree, so two objects sharing
        # the same graph share the same tree.
        self._cached_trees: dict[int, "bpy.types.NodeTree"] = {}

    def has_interactivity(self) -> bool:
        if not self._graphs:
            return False
        if self.gltf.nodes is None:
            return False
        for n in self.gltf.nodes:
            if n.extensions and EXT_INTERACTIVITY in n.extensions:
                return True
        return False

    def import_node(
        self,
        context: "bpy.types.Context",
        obj: "bpy.types.Object",
        node: "Node",
    ) -> None:
        if node.extensions is None:
            return
        ext = node.extensions.get(EXT_INTERACTIVITY)
        if ext is None:
            return
        graph_idx = ext.get("graph")
        if graph_idx is None or graph_idx >= len(self._graphs):
            return

        tree = self._cached_trees.get(graph_idx)
        if tree is None:
            tree = self._build_tree(context, graph_idx, obj.name)
            self._cached_trees[graph_idx] = tree
        obj.gltf_interactivity = tree

    # ------- Helpers ------------------------------------------------------

    def _extract_graphs(self) -> list[dict]:
        ext_root = self._root_ext()
        if ext_root is None:
            return []
        return list(ext_root.get("graphs", []))

    def _extract_declarations(self) -> list[dict]:
        ext_root = self._root_ext()
        if ext_root is None:
            return []
        return list(ext_root.get("declarations", []))

    def _root_ext(self) -> dict | None:
        ext = getattr(self.gltf, "extensions", None) or {}
        return ext.get(EXT_INTERACTIVITY)

    def _op_for_decl(self, decl_idx: int) -> str:
        if 0 <= decl_idx < len(self._declarations):
            return self._declarations[decl_idx].get("op", "")
        return ""

    def _build_tree(
        self,
        context: "bpy.types.Context",
        graph_idx: int,
        obj_name: str,
    ) -> "bpy.types.NodeTree":
        import bpy
        from ..interactivity_nodes import OP_TO_BLIDNAME, BLIDNAME_TO_CLASS

        graph = self._graphs[graph_idx]
        tree = bpy.data.node_groups.new(
            f"{obj_name} Interactivity", TREE_BL_IDNAME,
        )

        # Pass 1: create nodes.
        graph_nodes = graph.get("nodes", [])
        bl_nodes: list = []
        for i, raw in enumerate(graph_nodes):
            op = self._op_for_decl(raw.get("declaration", -1))
            blidname = OP_TO_BLIDNAME.get(op)
            if blidname is None:
                bl_nodes.append(None)
                continue
            n = tree.nodes.new(blidname)
            n.location = (i * 220, 0)
            self._restore_configuration(n, raw)
            self._restore_value_defaults(n, raw)
            bl_nodes.append(n)

        # Pass 2: link flows and value references.
        for i, raw in enumerate(graph_nodes):
            src = bl_nodes[i]
            if src is None:
                continue
            self._restore_flows(tree, src, raw, bl_nodes)
            self._restore_value_links(tree, src, raw, bl_nodes)

        return tree

    def _restore_configuration(self, n, raw: dict) -> None:
        for cfg in raw.get("configuration", []):
            cid = cfg.get("id")
            val = cfg.get("value")
            if cid == "pointer" and isinstance(val, list) and val:
                n.pointer = str(val[0])
                # Default to CUSTOM until fixup_pointers resolves a structured form.
                if hasattr(n, "target_kind"):
                    n.target_kind = "CUSTOM"

    # ------- Pointer parsing (post-pass, after scene import) --------------

    def fixup_pointers(
        self,
        node_to_blender: "dict[int, bpy.types.Object]",
        material_importer=None,
    ) -> None:
        for tree in self._cached_trees.values():
            for n in tree.nodes:
                if n.bl_idname != "GLTFNode_pointer_set":
                    continue
                self._apply_parsed_pointer(n, node_to_blender, material_importer)

    def _apply_parsed_pointer(
        self,
        n,
        node_to_blender: "dict[int, bpy.types.Object]",
        material_importer,
    ) -> None:
        parsed = _parse_pointer(n.pointer)
        if parsed is None:
            n.target_kind = "CUSTOM"
            return
        kind, idx, prop, sub_idx = parsed
        if kind == "OBJECT":
            obj = node_to_blender.get(idx)
            if obj is None:
                n.target_kind = "CUSTOM"
                return
            n.target_kind = "OBJECT"
            n.target_object = obj
            n.object_property = prop
            if prop == "WEIGHT" and sub_idx is not None:
                n.weight_index = sub_idx
        elif kind == "MATERIAL":
            mat = None
            if material_importer is not None:
                mat = material_importer.get_blender_material(idx)
            if mat is None:
                n.target_kind = "CUSTOM"
                return
            n.target_kind = "MATERIAL"
            n.target_material = mat
            n.material_property = prop
        elif kind == "LIGHT":
            light = _find_light_by_index(self.gltf, node_to_blender, idx)
            if light is None:
                n.target_kind = "CUSTOM"
                return
            n.target_kind = "LIGHT"
            n.target_light = light
            n.light_property = prop
        elif kind == "CAMERA":
            cam = _find_camera_by_index(self.gltf, node_to_blender, idx)
            if cam is None:
                n.target_kind = "CUSTOM"
                return
            n.target_kind = "CAMERA"
            n.target_camera = cam
            n.camera_property = prop
        else:
            n.target_kind = "CUSTOM"

    def _restore_value_defaults(self, n, raw: dict) -> None:
        for v in raw.get("values", []):
            if "value" not in v:
                continue
            sock = n.inputs.get(v.get("id", ""))
            if sock is None or sock.bl_idname == FLOW_SOCKET_BL_IDNAME:
                continue
            payload = v["value"]
            if not isinstance(payload, list) or not payload:
                continue
            try:
                if sock.bl_idname == "NodeSocketBool":
                    sock.default_value = bool(payload[0])
                elif sock.bl_idname == "NodeSocketInt":
                    sock.default_value = int(payload[0])
                else:
                    sock.default_value = float(payload[0])
            except (TypeError, ValueError):
                pass

    def _restore_flows(self, tree, src_node, raw: dict, bl_nodes: list) -> None:
        for flow in raw.get("flows", []):
            out_id = flow.get("id")
            tgt_idx = flow.get("node")
            tgt_socket = flow.get("socket")
            if out_id is None or tgt_idx is None or tgt_socket is None:
                continue
            if not (0 <= tgt_idx < len(bl_nodes)) or bl_nodes[tgt_idx] is None:
                continue
            out_sock = src_node.outputs.get(out_id)
            in_sock = bl_nodes[tgt_idx].inputs.get(tgt_socket)
            if out_sock is None or in_sock is None:
                continue
            tree.links.new(out_sock, in_sock)

    def _restore_value_links(self, tree, dst_node, raw: dict, bl_nodes: list) -> None:
        for v in raw.get("values", []):
            if "node" not in v:
                continue
            src_idx = v["node"]
            src_socket = v.get("socket")
            if not (0 <= src_idx < len(bl_nodes)) or bl_nodes[src_idx] is None:
                continue
            in_sock = dst_node.inputs.get(v.get("id", ""))
            out_sock = bl_nodes[src_idx].outputs.get(src_socket)
            if in_sock is None or out_sock is None:
                continue
            tree.links.new(out_sock, in_sock)
