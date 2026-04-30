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
        """If a socket has a linked Image Texture node, return it."""
        socket = node.inputs.get(socket_name)
        if socket is None or not socket.is_linked:
            return None

        linked_node = socket.links[0].from_node
        if linked_node.type == "TEX_IMAGE":
            return linked_node

        # Handle Normal Map node -> Image Texture
        if linked_node.type == "NORMAL_MAP":
            color_socket = linked_node.inputs.get("Color")
            if color_socket and color_socket.is_linked:
                inner_node = color_socket.links[0].from_node
                if inner_node.type == "TEX_IMAGE":
                    return inner_node

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

    def _gather_layers(
        self, blender_material: "bpy.types.Material",
    ) -> list[dict] | None:
        """Walk the layer chain from the Principled BSDF's Base Color back."""
        principled = self._find_principled_bsdf(blender_material)
        if principled is None:
            return None
        chain = self._collect_layer_chain(principled)
        if not chain:
            return None

        layers: list[dict] = []
        for node in chain:
            layer = self._gather_one_layer(node)
            if layer is not None:
                layers.append(layer)
        return layers or None

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
        if socket is None or not socket.is_linked:
            return None
        linked = socket.links[0].from_node
        if linked.type == "TEX_IMAGE":
            return linked
        if linked.type == "NORMAL_MAP":
            color_socket = linked.inputs.get("Color")
            if color_socket and color_socket.is_linked:
                inner = color_socket.links[0].from_node
                if inner.type == "TEX_IMAGE":
                    return inner
        return None

    def _read_color_socket(self, socket):
        """Resolve a color socket to (factor, TextureInfo).

        Handles the common upstream cases: Image Texture (texture wins, factor
        is the socket's local default), RGB node (read its output value as the
        factor), or unlinked (read socket default).
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
