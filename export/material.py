from __future__ import annotations

from typing import TYPE_CHECKING

from ..gltf.types import Material, MaterialPBRMetallicRoughness, NormalTextureInfo
from .texture import TextureExporter

if TYPE_CHECKING:
    import bpy
    from ..exporter import ExportSettings


EXT_MATERIALS_UNLIT = "KHR_materials_unlit"
EXT_MATERIALS_LAYERS = "CUSTOM_materials_layers"
LAYER_NODE_GROUP_NAME = "glTF Material Layer"
UCUPAINT_GROUP_PREFIX = "Ucupaint "
UCUPAINT_LAYER_PREFIX = ".yP Layer "
_VALID_BLEND_MODES = {"MIX", "ADD", "MULTIPLY"}
_VALID_MASK_CHANNELS = {"R", "G", "B", "A"}


class MaterialExporter:
    def __init__(self, texture_exporter: TextureExporter, settings: "ExportSettings") -> None:
        self.texture_exporter = texture_exporter
        self.settings = settings
        self.materials: list[Material] = []
        self._cache: dict[str, int] = {}
        self.extensions_used: set[str] = set()

    def gather(self, blender_material: "bpy.types.Material") -> int | None:
        """Export a Blender material. Returns material index or None."""
        if blender_material is None:
            return None

        if blender_material.name in self._cache:
            return self._cache[blender_material.name]

        material = self._extract(blender_material)
        index = len(self.materials)
        self.materials.append(material)
        self._cache[blender_material.name] = index
        return index

    def _extract(self, blender_material: "bpy.types.Material") -> Material:
        pbr = None
        normal_texture = None
        emissive_texture = None
        emissive_factor = None
        alpha_mode = None
        alpha_cutoff = None
        double_sided = None

        principled = self._find_principled_bsdf(blender_material)

        if principled is not None:
            pbr = self._gather_pbr(principled)
            normal_texture = self._gather_normal(principled)
            emissive_texture, emissive_factor = self._gather_emission(principled)
            alpha_mode, alpha_cutoff = self._gather_alpha(blender_material, principled)
        else:
            # No Principled BSDF: try to recover base color + alpha from a
            # custom shader group plugged directly into Material Output.Surface
            # (e.g. tree-leaf shaders).
            pbr, alpha_mode, alpha_cutoff = self._gather_from_surface_group(blender_material)

        if blender_material.use_backface_culling is False:
            double_sided = True

        # KHR_materials_unlit
        extensions = None
        gltf_props = getattr(blender_material, "gltf_props", None)
        if gltf_props and gltf_props.unlit:
            extensions = {EXT_MATERIALS_UNLIT: {}}
            self.extensions_used.add(EXT_MATERIALS_UNLIT)

        # CUSTOM_materials_layers
        layers = self._gather_layers(blender_material)
        if layers:
            if extensions is None:
                extensions = {}
            extensions[EXT_MATERIALS_LAYERS] = {"layers": layers}
            self.extensions_used.add(EXT_MATERIALS_LAYERS)

        return Material(
            name=blender_material.name,
            pbr_metallic_roughness=pbr,
            normal_texture=normal_texture,
            emissive_texture=emissive_texture,
            emissive_factor=emissive_factor,
            alpha_mode=alpha_mode,
            alpha_cutoff=alpha_cutoff,
            double_sided=double_sided,
            extensions=extensions,
        )

    def _find_principled_bsdf(
        self, blender_material: "bpy.types.Material"
    ) -> "bpy.types.ShaderNodeBsdfPrincipled | None":
        if not blender_material.use_nodes or blender_material.node_tree is None:
            return None

        for node in blender_material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                return node
        return None

    def _get_socket_default(self, node: "bpy.types.ShaderNode", name: str):
        """Get the default value of a socket input."""
        socket = node.inputs.get(name)
        if socket is None:
            return None
        return socket.default_value

    def _get_connected_image_node(
        self, node: "bpy.types.ShaderNode", socket_name: str
    ) -> "bpy.types.ShaderNodeTexImage | None":
        """Resolve `node.inputs[socket_name]` to an upstream Image Texture,
        following pass-through nodes (Reroute, Normal Map, Group boundaries).
        """
        socket = node.inputs.get(socket_name)
        return self._walk_to_image(socket)

    def _walk_to_image(
        self, socket, _group_stack=None, _visited=None, _depth=0,
    ) -> "bpy.types.ShaderNodeTexImage | None":
        """Generic socket walker that returns the first Image Texture node
        reachable upstream through Reroute / Normal Map / node-group I/O.
        `_group_stack` tracks GROUP nodes we've descended into so we can hop
        back out via GROUP_INPUT.
        """
        if socket is None or _depth > 16:
            return None
        if not socket.is_linked:
            return None
        if _visited is None:
            _visited = set()

        link = socket.links[0]
        upstream = link.from_node
        from_socket = link.from_socket
        key = (id(upstream), getattr(from_socket, "identifier", from_socket.name))
        if key in _visited:
            return None
        _visited.add(key)

        t = upstream.type
        if t == "TEX_IMAGE":
            return upstream
        if t == "REROUTE":
            return self._walk_to_image(
                upstream.inputs[0] if upstream.inputs else None,
                _group_stack, _visited, _depth + 1,
            )
        if t == "NORMAL_MAP":
            return self._walk_to_image(
                upstream.inputs.get("Color"),
                _group_stack, _visited, _depth + 1,
            )
        if t == "GROUP":
            tree = getattr(upstream, "node_tree", None)
            if tree is None:
                return None
            gout = next((n for n in tree.nodes if n.type == "GROUP_OUTPUT"), None)
            if gout is None:
                return None
            inner = gout.inputs.get(from_socket.name)
            if inner is None:
                # Fall back to matching by output index
                for i, o in enumerate(upstream.outputs):
                    if o is from_socket and i < len(gout.inputs):
                        inner = gout.inputs[i]
                        break
            if inner is None:
                return None
            return self._walk_to_image(
                inner, (_group_stack or ()) + (upstream,), _visited, _depth + 1,
            )
        if t == "GROUP_INPUT":
            if not _group_stack:
                return None
            parent = _group_stack[-1]
            parent_in = parent.inputs.get(from_socket.name)
            if parent_in is None:
                return None
            return self._walk_to_image(
                parent_in, _group_stack[:-1], _visited, _depth + 1,
            )
        if t in ("MIX", "MIX_RGB"):
            # Best-effort: return whichever color input traces to an image.
            # Skip the Factor/Mask input. Try inputs in declared order; first
            # hit wins. Used for Ucupaint clamps and unbaked layer chains.
            for inp in upstream.inputs:
                n = inp.name.lower()
                if n in ("fac", "factor"):
                    continue
                # MIX node has typed sockets; only follow color-ish ones
                if hasattr(inp, "type") and inp.type not in ("RGBA", "VECTOR"):
                    continue
                if not inp.is_linked:
                    continue
                hit = self._walk_to_image(
                    inp, _group_stack, _visited, _depth + 1,
                )
                if hit is not None:
                    return hit
        return None

    def _gather_pbr(
        self, principled: "bpy.types.ShaderNodeBsdfPrincipled"
    ) -> MaterialPBRMetallicRoughness:
        # Base color — if Principled.Base Color is fed by a layer chain,
        # the base material's color comes from the deepest layer's "Below Color".
        base_color_socket = self._resolve_base_color_socket(principled)
        base_color_factor, base_color_texture = self._read_color_socket(base_color_socket)

        # Metallic
        metallic = self._get_socket_default(principled, "Metallic")
        metallic_factor = float(metallic) if metallic is not None else None

        # Roughness
        roughness = self._get_socket_default(principled, "Roughness")
        roughness_factor = float(roughness) if roughness is not None else None

        # Metallic/Roughness texture (if connected)
        mr_texture = None
        mr_node = self._get_connected_image_node(principled, "Metallic")
        if mr_node is None:
            mr_node = self._get_connected_image_node(principled, "Roughness")
        if mr_node:
            mr_texture = self.texture_exporter.gather_texture_info(mr_node)

        return MaterialPBRMetallicRoughness(
            base_color_factor=base_color_factor,
            base_color_texture=base_color_texture,
            metallic_factor=metallic_factor,
            roughness_factor=roughness_factor,
            metallic_roughness_texture=mr_texture,
        )

    def _gather_normal(
        self, principled: "bpy.types.ShaderNodeBsdfPrincipled"
    ) -> NormalTextureInfo | None:
        image_node = self._get_connected_image_node(principled, "Normal")
        if image_node is None:
            return None

        tex_info = self.texture_exporter.gather_texture_info(image_node)
        if tex_info is None:
            return None

        # Get normal strength from Normal Map node
        scale = None
        normal_socket = principled.inputs.get("Normal")
        if normal_socket and normal_socket.is_linked:
            normal_map_node = normal_socket.links[0].from_node
            if normal_map_node.type == "NORMAL_MAP":
                strength = normal_map_node.inputs.get("Strength")
                if strength and strength.default_value != 1.0:
                    scale = float(strength.default_value)

        return NormalTextureInfo(
            index=tex_info.index,
            tex_coord=tex_info.tex_coord,
            scale=scale,
            extensions=tex_info.extensions,
        )

    def _gather_emission(
        self, principled: "bpy.types.ShaderNodeBsdfPrincipled"
    ) -> tuple["TextureInfo | None", list[float] | None]:
        emission_color = self._get_socket_default(principled, "Emission Color")
        emission_strength = self._get_socket_default(principled, "Emission Strength")

        if emission_color is None:
            return None, None

        strength = float(emission_strength) if emission_strength is not None else 1.0

        # Check if emission is effectively zero
        r, g, b = float(emission_color[0]), float(emission_color[1]), float(emission_color[2])
        if (r * strength == 0 and g * strength == 0 and b * strength == 0):
            return None, None

        emissive_factor = [r * strength, g * strength, b * strength]

        # Emission texture
        emissive_texture = None
        image_node = self._get_connected_image_node(principled, "Emission Color")
        if image_node:
            emissive_texture = self.texture_exporter.gather_texture_info(image_node)

        return emissive_texture, emissive_factor

    def _gather_alpha(
        self,
        blender_material: "bpy.types.Material",
        principled: "bpy.types.ShaderNodeBsdfPrincipled",
    ) -> tuple[str | None, float | None]:
        blend_method = blender_material.surface_render_method if hasattr(
            blender_material, "surface_render_method"
        ) else getattr(blender_material, "blend_method", "OPAQUE")

        if blend_method == "OPAQUE":
            return None, None

        alpha = self._get_socket_default(principled, "Alpha")

        if blend_method == "CLIP" or blend_method == "HASHED":
            threshold = getattr(blender_material, "alpha_threshold", 0.5)
            cutoff = float(threshold) if threshold != 0.5 else None
            return "MASK", cutoff

        return "BLEND", None

    # Common input names that custom shader groups use for the diffuse/base
    # color and alpha sockets. Ordered by preference.
    _GROUP_BASE_COLOR_INPUTS = ("Base Color", "BaseColor", "Color", "Diffuse", "Albedo")
    _GROUP_ALPHA_INPUTS = ("Alpha", "Opacity")
    _GROUP_NORMAL_INPUTS = ("Normal", "Normal Map")

    def _gather_from_surface_group(
        self, blender_material: "bpy.types.Material",
    ) -> tuple["MaterialPBRMetallicRoughness | None", str | None, float | None]:
        """Fallback for materials with no Principled BSDF: walk
        Material Output.Surface, and if it's a custom shader group, try to
        recover (base color factor + texture, alpha mode) from common input
        names (Diffuse, Color, Alpha, …).
        Returns (pbr or None, alpha_mode, alpha_cutoff).
        """
        if not blender_material.use_nodes or blender_material.node_tree is None:
            return None, None, None

        out_node = next(
            (n for n in blender_material.node_tree.nodes if n.type == "OUTPUT_MATERIAL"),
            None,
        )
        if out_node is None:
            return None, None, None
        surface = out_node.inputs.get("Surface")
        if surface is None or not surface.is_linked:
            return None, None, None

        group_node = surface.links[0].from_node
        if group_node.type != "GROUP" or getattr(group_node, "node_tree", None) is None:
            return None, None, None

        bc_socket = None
        for name in self._GROUP_BASE_COLOR_INPUTS:
            s = group_node.inputs.get(name)
            if s is not None:
                bc_socket = s
                break
        bc_factor, bc_tex = self._read_color_socket(bc_socket) if bc_socket else (None, None)

        # Alpha: only emit a mode if the group exposes an alpha socket.
        alpha_mode = None
        alpha_cutoff = None
        for name in self._GROUP_ALPHA_INPUTS:
            a = group_node.inputs.get(name)
            if a is None:
                continue
            blend_method = (
                blender_material.surface_render_method
                if hasattr(blender_material, "surface_render_method")
                else getattr(blender_material, "blend_method", "OPAQUE")
            )
            if blend_method in ("CLIP", "HASHED"):
                threshold = getattr(blender_material, "alpha_threshold", 0.5)
                alpha_mode, alpha_cutoff = "MASK", (
                    float(threshold) if threshold != 0.5 else None
                )
            elif blend_method != "OPAQUE":
                alpha_mode = "BLEND"
            break

        if bc_factor is None and bc_tex is None:
            return None, alpha_mode, alpha_cutoff

        pbr = MaterialPBRMetallicRoughness(
            base_color_factor=bc_factor,
            base_color_texture=bc_tex,
        )
        return pbr, alpha_mode, alpha_cutoff

    def _gather_layers(
        self, blender_material: "bpy.types.Material",
    ) -> list[dict] | None:
        """Walk the layer chain from the Principled BSDF's Base Color back."""
        principled = self._find_principled_bsdf(blender_material)
        if principled is None:
            return None
        chain = self._collect_layer_chain(principled)
        if chain:
            layers: list[dict] = []
            for node in chain:
                layer = self._gather_one_layer(node)
                if layer is not None:
                    layers.append(layer)
            return layers or None

        # Ucupaint integration: when Principled.Base Color is fed by a Ucupaint
        # group with >=2 painted layers, export each `.yP Layer …` sub-group as
        # a glTF material layer. Single-layer Ucupaint already round-trips via
        # the regular pbrMetallicRoughness path through the walker.
        bc_socket = principled.inputs.get("Base Color")
        if bc_socket is None or not bc_socket.is_linked:
            return None
        upstream = bc_socket.links[0].from_node
        if not self._is_ucupaint_group(upstream):
            return None
        layer_nodes = self._collect_ucupaint_layers(upstream)
        if len(layer_nodes) < 2:
            return None
        layers = []
        for ln in layer_nodes:
            layer = self._gather_one_ucupaint_layer(ln)
            if layer is not None:
                layers.append(layer)
        # Only emit the extension when at least 2 non-empty layers survive —
        # a single layer would round-trip as plain pbrMetallicRoughness.
        if len(layers) < 2:
            return None
        return layers

    def _is_ucupaint_group(self, node) -> bool:
        return (
            getattr(node, "type", None) == "GROUP"
            and getattr(node, "node_tree", None) is not None
            and node.node_tree.name.startswith(UCUPAINT_GROUP_PREFIX)
        )

    def _is_ucupaint_layer_group(self, node) -> bool:
        return (
            getattr(node, "type", None) == "GROUP"
            and getattr(node, "node_tree", None) is not None
            and node.node_tree.name.startswith(UCUPAINT_LAYER_PREFIX)
        )

    def _collect_ucupaint_layers(self, ucu_group_node) -> list:
        """Return `.yP Layer …` sub-group nodes inside the Ucupaint group, in
        base→top order. Walks back from GROUP_OUTPUT.Color through the layer
        chain (passing through MIX/Reroute clamps); falls back to node-list
        order if the chain can't be resolved.
        """
        tree = ucu_group_node.node_tree
        if tree is None:
            return []

        gout = next((n for n in tree.nodes if n.type == "GROUP_OUTPUT"), None)
        layers: list = []
        seen_layer_ids: set[int] = set()
        if gout is not None:
            sock = gout.inputs.get("Color")
            depth = 0
            while sock is not None and sock.is_linked and depth < 64:
                depth += 1
                up = sock.links[0].from_node
                if self._is_ucupaint_layer_group(up):
                    if id(up) in seen_layer_ids:
                        break
                    seen_layer_ids.add(id(up))
                    layers.append(up)
                    # Find this layer's "below"/background input to continue
                    # walking down the stack.
                    below = None
                    for cand in (
                        "Background", "Below Color", "Color Below",
                        "Below", "Bottom",
                    ):
                        s = up.inputs.get(cand)
                        if s is not None and s.is_linked:
                            below = s
                            break
                    if below is None:
                        for inp in up.inputs:
                            if inp.is_linked and getattr(inp, "type", None) == "RGBA":
                                below = inp
                                break
                    sock = below
                    continue
                if up.type in ("MIX", "MIX_RGB", "REROUTE"):
                    next_sock = None
                    for inp in up.inputs:
                        n = inp.name.lower()
                        if n in ("fac", "factor"):
                            continue
                        if hasattr(inp, "type") and inp.type not in ("RGBA", "VECTOR"):
                            continue
                        if inp.is_linked:
                            next_sock = inp
                            break
                    sock = next_sock
                    continue
                break

        if layers:
            layers.reverse()  # base → top
            return layers
        # Fallback: enumerate all `.yP Layer …` groups (order undefined).
        return [n for n in tree.nodes if self._is_ucupaint_layer_group(n)]

    def _find_labeled_tex_image(self, tree, label_prefix):
        if tree is None:
            return None
        for n in tree.nodes:
            if n.type == "TEX_IMAGE" and n.label.startswith(label_prefix):
                return n
        return None

    @staticmethod
    def _tex_info_to_dict(ti) -> dict:
        d = {"index": ti.index}
        if getattr(ti, "tex_coord", None) is not None:
            d["texCoord"] = ti.tex_coord
        if getattr(ti, "extensions", None):
            d["extensions"] = ti.extensions
        return d

    def _gather_one_ucupaint_layer(self, layer_node) -> dict | None:
        """Build a glTF material-layer dict from a `.yP Layer …` sub-group."""
        tree = getattr(layer_node, "node_tree", None)
        if tree is None:
            return None

        layer: dict = {}
        if layer_node.label:
            layer["name"] = layer_node.label
        else:
            name = tree.name
            if name.startswith(UCUPAINT_LAYER_PREFIX):
                name = name[len(UCUPAINT_LAYER_PREFIX):]
            layer["name"] = name

        pbr: dict = {}

        src = self._find_labeled_tex_image(tree, "Source")
        if src is not None:
            ti = self.texture_exporter.gather_texture_info(src)
            if ti is not None:
                pbr["baseColorTexture"] = self._tex_info_to_dict(ti)

        mr_node = (
            self._find_labeled_tex_image(tree, "Metallic Override")
            or self._find_labeled_tex_image(tree, "Roughness Override")
        )
        if mr_node is not None:
            ti = self.texture_exporter.gather_texture_info(mr_node)
            if ti is not None:
                pbr["metallicRoughnessTexture"] = self._tex_info_to_dict(ti)

        if pbr:
            layer["pbrMetallicRoughness"] = pbr

        normal_node = (
            self._find_labeled_tex_image(tree, "Normal Override 1")
            or self._find_labeled_tex_image(tree, "Normal Override")
        )
        if normal_node is not None:
            ti = self.texture_exporter.gather_texture_info(normal_node)
            if ti is not None:
                layer["normalTexture"] = self._tex_info_to_dict(ti)

        # Mask: Ucupaint stores mask images as TEX_IMAGE nodes labeled like
        # "Mask : IMAGE" or starting with "Mask".
        mask_node = self._find_labeled_tex_image(tree, "Mask")
        if mask_node is not None:
            ti = self.texture_exporter.gather_texture_info(mask_node)
            if ti is not None:
                layer["mask"] = {
                    "source": "TEXTURE",
                    "texture": self._tex_info_to_dict(ti),
                }

        # Blend mode from the layer-internal Color blend node.
        blend_node = next(
            (
                n for n in tree.nodes
                if n.type in ("MIX", "MIX_RGB") and n.label == "Blend"
            ),
            None,
        )
        if blend_node is not None:
            bt = getattr(blend_node, "blend_type", "MIX")
            if bt in _VALID_BLEND_MODES and bt != "MIX":
                layer["blendMode"] = bt

        # Drop empty layers — a layer with no images and no mask carries no
        # information that the renderer can act on. This filters out
        # Ucupaint's "Solid Color" (no image) layers.
        has_content = (
            "pbrMetallicRoughness" in layer
            or "normalTexture" in layer
            or "mask" in layer
        )
        if not has_content:
            return None

        return layer

    def _is_layer_node(self, node) -> bool:
        return (
            getattr(node, "type", None) == "GROUP"
            and getattr(node, "node_tree", None) is not None
            and node.node_tree.name == LAYER_NODE_GROUP_NAME
        )

    def _resolve_base_color_socket(self, principled):
        """Walk through any layer chain on Principled.Base Color and return the
        socket that feeds the base material's color (deepest 'Below Color', or
        the Principled socket itself if there is no chain).
        """
        socket = principled.inputs.get("Base Color")
        seen: set[int] = set()
        while socket is not None and socket.is_linked:
            upstream = socket.links[0].from_node
            if id(upstream) in seen:
                break
            seen.add(id(upstream))
            if not self._is_layer_node(upstream):
                break
            below = upstream.inputs.get("Below Color")
            if below is None:
                break
            socket = below
        return socket

    def _image_node_from_socket(self, socket):
        """Like _get_connected_image_node but takes a socket directly."""
        return self._walk_to_image(socket)

    def _read_color_socket(self, socket):
        """Resolve a color socket to (factor, TextureInfo).

        Handles the common upstream cases: Image Texture (texture wins, factor
        is the socket's local default), RGB node (read its output value as the
        factor), or unlinked (read socket default).

        When a socket is linked through pass-through nodes (Reroute, Group)
        but no image texture is reachable, the upstream output's
        `default_value` is often a stale evaluated value (commonly black for
        unevaluated group outputs). The Principled-side socket's
        `default_value` is a more reliable user-visible factor, so prefer it.
        """
        if socket is None:
            return None, None

        image_node = self._image_node_from_socket(socket)
        if image_node is not None:
            v = socket.default_value
            tex_info = self.texture_exporter.gather_texture_info(image_node)
            return [v[0], v[1], v[2], v[3]], tex_info

        if socket.is_linked:
            from_socket = socket.links[0].from_socket
            from_node = socket.links[0].from_node
            # For RGB-style upstreams (RGB, Value->Combine, Color attribute),
            # the output default is meaningful. For Group / Reroute / shader
            # nodes, prefer the Principled-side default.
            if getattr(from_node, "type", None) in {"RGB", "VALUE", "COMBINE_COLOR", "COMBINE_RGB"}:
                v = getattr(from_socket, "default_value", None)
                if v is not None and hasattr(v, "__len__") and len(v) >= 3:
                    a = v[3] if len(v) >= 4 else 1.0
                    return [v[0], v[1], v[2], a], None

        v = socket.default_value
        return [v[0], v[1], v[2], v[3]], None

    def _collect_layer_chain(self, principled) -> list:
        """Walk Principled.Base Color → ML.Color → ML.Below Color → ML.Color → …
        Returns layers in BASE→TOP order.
        """
        chain: list = []
        socket = principled.inputs.get("Base Color")
        seen: set[int] = set()
        while socket is not None and socket.is_linked:
            upstream = socket.links[0].from_node
            if id(upstream) in seen:
                break
            seen.add(id(upstream))
            if not self._is_layer_node(upstream):
                break
            chain.append(upstream)
            below = upstream.inputs.get("Below Color")
            if below is None:
                break
            socket = below
        return list(reversed(chain))

    def _gather_one_layer(self, group_node) -> dict | None:
        layer: dict = {}

        if group_node.label:
            layer["name"] = group_node.label

        pbr: dict = {}

        # Base color (this layer's color)
        bc = self._get_socket_default(group_node, "Color")
        if bc is not None:
            r, g, b, a = float(bc[0]), float(bc[1]), float(bc[2]), float(bc[3])
            if (r, g, b, a) != (1.0, 1.0, 1.0, 1.0):
                pbr["baseColorFactor"] = [r, g, b, a]
        bc_node = self._get_connected_image_node(group_node, "Color")
        if bc_node:
            ti = self.texture_exporter.gather_texture_info(bc_node)
            if ti is not None:
                pbr["baseColorTexture"] = ti

        # Metallic / roughness
        m = self._get_socket_default(group_node, "Metallic")
        if m is not None and float(m) != 1.0:
            pbr["metallicFactor"] = float(m)
        r = self._get_socket_default(group_node, "Roughness")
        if r is not None and float(r) != 1.0:
            pbr["roughnessFactor"] = float(r)

        mr_node = self._get_connected_image_node(group_node, "Metallic")
        if mr_node is None:
            mr_node = self._get_connected_image_node(group_node, "Roughness")
        if mr_node:
            ti = self.texture_exporter.gather_texture_info(mr_node)
            if ti is not None:
                pbr["metallicRoughnessTexture"] = ti

        if pbr:
            layer["pbrMetallicRoughness"] = pbr

        # Normal
        n_node = self._get_connected_image_node(group_node, "Normal")
        if n_node is not None:
            ti = self.texture_exporter.gather_texture_info(n_node)
            if ti is not None:
                normal: dict = {"index": ti.index}
                if ti.tex_coord is not None:
                    normal["texCoord"] = ti.tex_coord
                if ti.extensions:
                    normal["extensions"] = ti.extensions
                layer["normalTexture"] = normal

        # Mask (required)
        mask = self._gather_layer_mask(group_node)
        if mask is None:
            return None  # layers without a mask have no meaning
        layer["mask"] = mask

        # Blend mode (optional, custom property on the group node)
        blend = group_node.get("blend_mode")
        if blend:
            blend = str(blend).upper()
            if blend in _VALID_BLEND_MODES and blend != "MIX":
                layer["blendMode"] = blend

        return layer

    def _gather_layer_mask(self, group_node) -> dict | None:
        socket = group_node.inputs.get("Mask")
        if socket is None or not socket.is_linked:
            return None

        link = socket.links[0]
        src = link.from_node
        from_socket_name = link.from_socket.name

        if src.type == "TEX_IMAGE":
            ti = self.texture_exporter.gather_texture_info(src)
            if ti is None:
                return None
            tex: dict = {"index": ti.index}
            if ti.tex_coord is not None:
                tex["texCoord"] = ti.tex_coord
            if ti.extensions:
                tex["extensions"] = ti.extensions
            mask = {"source": "TEXTURE", "texture": tex}
            channel = "A" if from_socket_name == "Alpha" else "R"
            if channel != "R":
                mask["channel"] = channel
            return mask

        # Separate Color/RGB driven by an image — write the channel
        if src.type in ("SEPARATE_COLOR", "SEPRGB"):
            channel = _socket_to_channel(from_socket_name)
            color_in = src.inputs.get("Color") or src.inputs.get("Image")
            if color_in is not None and color_in.is_linked:
                inner = color_in.links[0].from_node
                if inner.type == "TEX_IMAGE":
                    ti = self.texture_exporter.gather_texture_info(inner)
                    if ti is None:
                        return None
                    tex = {"index": ti.index}
                    if ti.tex_coord is not None:
                        tex["texCoord"] = ti.tex_coord
                    if ti.extensions:
                        tex["extensions"] = ti.extensions
                    mask = {"source": "TEXTURE", "texture": tex}
                    if channel != "R":
                        mask["channel"] = channel
                    return mask
                if inner.type in ("VERTEX_COLOR", "ATTRIBUTE"):
                    return _vertex_color_mask(inner, channel)

        if src.type in ("VERTEX_COLOR", "ATTRIBUTE"):
            channel = "A" if from_socket_name == "Alpha" else "R"
            return _vertex_color_mask(src, channel)

        return None


def _socket_to_channel(name: str) -> str:
    name = name.upper()
    if name in _VALID_MASK_CHANNELS:
        return name
    if name == "RED":
        return "R"
    if name == "GREEN":
        return "G"
    if name == "BLUE":
        return "B"
    if name == "ALPHA":
        return "A"
    return "R"


def _vertex_color_mask(src, channel: str) -> dict:
    attr = ""
    if src.type == "VERTEX_COLOR":
        attr = getattr(src, "layer_name", "") or ""
    else:
        attr = getattr(src, "attribute_name", "") or ""
    mask = {"source": "VERTEX_COLOR"}
    if attr and attr != "COLOR_0":
        mask["attribute"] = attr
    if channel != "R":
        mask["channel"] = channel
    return mask
